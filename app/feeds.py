import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import feedparser
import flask

from app.db import get_db_direct

log = logging.getLogger(__name__)


class _StripHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def strip_html(raw: str) -> str:
    parser = _StripHTML()
    parser.feed(raw)
    return parser.get_text()


AUTO_PAUSE_AFTER_FAILURES = 5


def poll_all_feeds(app: flask.Flask) -> None:
    """Fetch all non-paused feeds and insert new articles. Called by APScheduler."""
    with app.app_context():
        db = get_db_direct()
        try:
            feeds = db.execute(
                "SELECT id, url, etag, last_modified FROM feeds WHERE paused=0"
            ).fetchall()
            for feed in feeds:
                _poll_feed(db, feed["id"], feed["url"],
                           feed["etag"], feed["last_modified"])
        finally:
            db.close()


def _poll_feed(db, feed_id: int, url: str,
               prev_etag: str | None = None,
               prev_modified: str | None = None) -> None:
    try:
        kwargs = {"request_headers": {"User-Agent": "rss-reader/1.0"}}
        if prev_etag:
            kwargs["etag"] = prev_etag
        if prev_modified:
            kwargs["modified"] = prev_modified
        parsed = feedparser.parse(url, **kwargs)

        # Conditional GET hit — server says "nothing changed".
        if getattr(parsed, "status", None) == 304:
            db.execute(
                "UPDATE feeds SET last_polled_at=?, last_success_at=?, "
                "last_error=NULL, consecutive_failures=0 WHERE id=?",
                (_utcnow(), _utcnow(), feed_id),
            )
            db.commit()
            log.info("Feed %s: 304 not modified", url)
            return

        if parsed.bozo and not parsed.entries:
            _record_failure(db, feed_id, str(parsed.bozo_exception))
            log.warning("Feed %s: parse error %s", url, parsed.bozo_exception)
            return

        new_etag = getattr(parsed, "etag", None)
        new_modified = getattr(parsed, "modified", None)
        feed_title = parsed.feed.get("title", url)
        now = _utcnow()
        db.execute(
            "UPDATE feeds SET title=?, last_polled_at=?, last_success_at=?, "
            "last_error=NULL, consecutive_failures=0, etag=?, last_modified=? "
            "WHERE id=?",
            (feed_title, now, now, new_etag, new_modified, feed_id),
        )

        new_count = 0
        for entry in parsed.entries:
            guid = entry.get("id") or entry.get("link", "")
            link = entry.get("link", "")
            title = entry.get("title", "(no title)")
            snippet = strip_html(entry.get("summary", entry.get("description", "")))[:500]
            feed_content = _extract_full_content(entry)
            published = _parse_date(entry)
            thumbnail = _extract_thumbnail(entry)

            cursor = db.execute(
                """INSERT OR IGNORE INTO articles
                   (feed_id, guid, url, title, published_at,
                    raw_snippet, feed_content, thumbnail_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (feed_id, guid, link, title, published,
                 snippet, feed_content, thumbnail),
            )
            if cursor.rowcount:
                new_count += 1

        db.commit()
        log.info("Feed %s: %d new articles", url, new_count)
    except Exception as exc:
        _record_failure(db, feed_id, str(exc))
        log.error("Feed %s: unexpected error: %s", url, exc)


def _record_failure(db, feed_id: int, error: str) -> None:
    """Bump consecutive_failures, store error, auto-pause if over threshold."""
    short = (error or "")[:300]
    db.execute(
        "UPDATE feeds SET last_polled_at=?, last_error=?, "
        "consecutive_failures=consecutive_failures+1 WHERE id=?",
        (_utcnow(), short, feed_id),
    )
    row = db.execute(
        "SELECT consecutive_failures FROM feeds WHERE id=?", (feed_id,)
    ).fetchone()
    if row and row["consecutive_failures"] >= AUTO_PAUSE_AFTER_FAILURES:
        db.execute("UPDATE feeds SET paused=1 WHERE id=?", (feed_id,))
        log.warning(
            "Feed id=%d auto-paused after %d consecutive failures",
            feed_id, row["consecutive_failures"],
        )
    db.commit()


def _extract_full_content(entry) -> str | None:
    """Return the richest body the feed offers, HTML-stripped.

    feedparser normalises both Atom <content> and RSS <content:encoded>
    into entry.content (a list of dicts with 'value'). Falls back to
    summary/description if no content tag exists.
    """
    contents = None
    if hasattr(entry, "get"):
        contents = entry.get("content")
    if contents is None:
        contents = getattr(entry, "content", None)
    for item in contents or []:
        html = item.get("value") if hasattr(item, "get") else None
        if html:
            text = strip_html(html).strip()
            if text:
                return text
    fallback = ""
    if hasattr(entry, "get"):
        fallback = entry.get("summary") or entry.get("description") or ""
    text = strip_html(fallback).strip()
    return text or None


def _extract_thumbnail(entry) -> str | None:
    for thumb in getattr(entry, 'media_thumbnail', []):
        if thumb.get('url'):
            return thumb['url']
    for mc in getattr(entry, 'media_content', []):
        t = mc.get('type', '')
        if mc.get('medium') == 'image' or t.startswith('image/'):
            if mc.get('url'):
                return mc['url']
    for enc in getattr(entry, 'enclosures', []):
        if enc.get('type', '').startswith('image/') and enc.get('href'):
            return enc['href']
    # Fall back to first <img> in the HTML content or summary
    for html in [
        next((c.get('value', '') for c in getattr(entry, 'content', [])), ''),
        entry.get('summary', ''),
        entry.get('description', ''),
    ]:
        if not html:
            continue
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            url = m.group(1)
            if url.startswith('http') and not url.lower().endswith('.gif'):
                return url
    return None


def _parse_date(entry) -> str | None:
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        try:
            return datetime(*published[:6], tzinfo=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except Exception:
            pass
    return None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
