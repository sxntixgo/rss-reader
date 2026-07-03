import logging
import re
import xml.etree.ElementTree as ET
from html import escape

from flask import Blueprint, current_app, render_template, request, Response

from app import ollama_client
from app.db import get_db, get_setting, set_setting
from app.pipeline import DEFAULT_SCORING_MODEL, DEFAULT_SUMMARY_MODEL

log = logging.getLogger(__name__)

bp = Blueprint("main", __name__)

_READ_TIME_RE = re.compile(
    r'-?\s*(\d+)\s*min(?:uto)?s?\s*(?:de\s+)?(?:read(?:ing)?|lectura)'
    r'|lectura\s*[:\-]?\s*\d+\s*min'
    r'|tiempo\s+de\s+lectura\b',
    re.IGNORECASE,
)

_JUNK_SECTION_RE = re.compile(
    r'^(related\b|more from\b|see also\b|you might\b|read next\b|'
    r'recommended\b|advertisement\b|sponsored\b|sign up\b|subscribe\b|'
    r'newsletter\b|follow us\b|share this\b|también\s+te\s+puede\b|'
    r'te\s+puede\s+interesar\b|más\s+noticias\b|más\s+información\b|'
    r'otras\s+noticias\b|noticias\s+relacionadas\b|sigue\s+leyendo\b)',
    re.IGNORECASE,
)

_STANDALONE_NUM_RE = re.compile(r'^-?\s*\d{1,2}\s*$')

_BULLET_RE = re.compile(r'^[-*•‣◦∙·–—]\s+(.+)$')

# Detect a line that is *only* a tweet permalink — `(?:www\.|mobile\.)?` covers
# both bare `twitter.com` and the `www.` / `mobile.` variants; `x.com` is the
# rebranded host. Matches plain text URLs left over after trafilatura strips
# `<blockquote class="twitter-tweet">` wrappers down to text.
_TWITTER_URL_RE = re.compile(
    r'^https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/'
    r'[A-Za-z0-9_]+/status/\d+(?:\?[^\s]*)?/?$',
    re.IGNORECASE,
)
_INSTAGRAM_URL_RE = re.compile(
    r'^https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/'
    r'[A-Za-z0-9_-]+/?(?:\?[^\s]*)?$',
    re.IGNORECASE,
)


def _embed_match(line: str) -> tuple[str, str] | None:
    if _TWITTER_URL_RE.match(line):
        return ("twitter", line)
    if _INSTAGRAM_URL_RE.match(line):
        return ("instagram", line)
    return None


def _extract_reading_time(text: str) -> str | None:
    m = _READ_TIME_RE.search(text or "")
    if not m:
        return None
    num = re.search(r'\d+', m.group(0))
    return num.group(0) if num else None


def _clean_content(text: str, title: str = "", description: str = "") -> str:
    cleaned, skip = [], False
    words_seen = 0
    title_norm = (title or "").strip().lower()
    desc_norm = (description or "").strip().lower()[:120]
    for line in text.split('\n'):
        s = line.strip()
        if not s:
            continue
        if _READ_TIME_RE.search(s):
            continue
        s_norm = s.lower()
        # Skip a leading line that duplicates the title or description
        if not cleaned and title_norm and (s_norm == title_norm or s_norm.startswith(title_norm)):
            continue
        if not cleaned and desc_norm and s_norm.startswith(desc_norm[:60]):
            continue
        if _JUNK_SECTION_RE.match(s):
            skip = True
        if _STANDALONE_NUM_RE.match(s) and re.search(r'^-?\s*1\s*$', s) and words_seen > 80:
            skip = True
        if skip:
            continue
        words_seen += len(s.split())
        cleaned.append(s)
    return '\n'.join(cleaned)


def _to_blocks(text: str, embeds_enabled: bool = False) -> list[dict]:
    """Group consecutive bullet-prefixed lines into list blocks for rendering.

    Lines starting with ``-``, ``*``, ``•`` (and similar marks) followed by a
    space are turned into ``<li>`` items grouped under a single ``<ul>``.

    When ``embeds_enabled`` is true, a line that is *only* a Twitter/X or
    Instagram permalink becomes an ``embed`` block; the modal turns those into
    proper blockquotes the official scripts can hydrate. When disabled the URL
    falls through as a normal paragraph.
    """
    blocks: list[dict] = []
    current: list[str] | None = None
    for line in text.split('\n'):
        s = line.strip()
        if not s:
            continue
        if embeds_enabled:
            em = _embed_match(s)
            if em:
                current = None
                blocks.append({"type": "embed", "platform": em[0], "url": em[1]})
                continue
        m = _BULLET_RE.match(s)
        if m:
            if current is None:
                current = []
                blocks.append({"type": "ul", "items": current})
            current.append(m.group(1).strip())
        else:
            current = None
            blocks.append({"type": "p", "text": s})
    return blocks


def _row_to_article(row) -> dict:
    d = dict(row)
    text = (d.get('full_text_head') or '') + ' ' + (d.get('raw_snippet') or '')
    d['reading_time'] = _extract_reading_time(text)
    return d


# ── Pages ──────────────────────────────────────────────────────────────────────

@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/settings")
def settings():
    return render_template("settings.html")


@bp.get("/manage-feeds")
def manage_feeds():
    return render_template("manage_feeds.html")


# ── Article fragments ──────────────────────────────────────────────────────────

_PAGE_SIZE = 50


@bp.get("/articles")
def articles():
    sort = request.args.get("sort", "date")
    order = "published_at DESC" if sort == "date" else "score DESC, published_at DESC"
    # ?hidden=1 means show ONLY hidden articles (the sidebar "Hidden" group),
    # not "include hidden in the normal list". ?saved=1 means show ONLY saved.
    show_hidden = request.args.get("hidden") == "1"
    show_saved = request.args.get("saved") == "1"
    statuses = (
        "('hidden')"
        if show_hidden else
        "('summarized', 'liked', 'disliked')"
    )
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        offset = 0
    params: list = []
    feed_filter = ""
    feed_arg = request.args.get("feed", "").strip()
    if feed_arg.isdigit():
        feed_filter = " AND feed_id=?"
        params.append(int(feed_arg))
    saved_filter = " AND saved_at IS NOT NULL" if show_saved else ""
    db = get_db()
    rows = db.execute(
        f"""SELECT id, url, title, summary, score, score_reason, status, thumbnail_url,
                  raw_snippet, read_at, saved_at,
                  SUBSTR(full_text, 1, 400) as full_text_head
           FROM articles
           WHERE status IN {statuses}{feed_filter}{saved_filter}
           ORDER BY {order}
           LIMIT ? OFFSET ?""",
        params + [_PAGE_SIZE, offset],
    ).fetchall()
    next_offset = offset + _PAGE_SIZE if len(rows) == _PAGE_SIZE else None
    next_qs = ""
    if next_offset is not None:
        parts = [f"sort={sort}", f"offset={next_offset}"]
        if show_hidden:
            parts.append("hidden=1")
        if show_saved:
            parts.append("saved=1")
        if feed_arg.isdigit():
            parts.append(f"feed={int(feed_arg)}")
        next_qs = "&".join(parts)
    return render_template(
        "_articles.html",
        articles=[_row_to_article(r) for r in rows],
        next_qs=next_qs,
        is_first_page=(offset == 0),
    )


@bp.get("/search")
def search():
    """Full-text search over title/summary/full_text using FTS5."""
    q = request.args.get("q", "").strip()
    if not q:
        return render_template(
            "_articles.html", articles=[], next_qs="", is_first_page=True
        )
    db = get_db()
    # FTS5 query — escape user input by wrapping in quotes (treat as a phrase).
    fts_query = '"' + q.replace('"', '""') + '"'
    try:
        rows = db.execute(
            """SELECT a.id, a.url, a.title, a.summary, a.score, a.score_reason,
                      a.status, a.thumbnail_url, a.raw_snippet, a.read_at, a.saved_at,
                      SUBSTR(a.full_text, 1, 400) as full_text_head
               FROM articles_fts f
               JOIN articles a ON a.id = f.rowid
               WHERE articles_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (fts_query, _PAGE_SIZE),
        ).fetchall()
    except Exception as exc:
        log.warning("FTS search failed for %r: %s", q, exc)
        rows = []
    return render_template(
        "_articles.html",
        articles=[_row_to_article(r) for r in rows],
        next_qs="",
        is_first_page=True,
    )


@bp.post("/article/<int:article_id>/save")
def article_save(article_id: int):
    """Toggle the saved/read-later flag on an article and return the refreshed card."""
    db = get_db()
    row = db.execute(
        "SELECT saved_at FROM articles WHERE id=?", (article_id,)
    ).fetchone()
    if row is None:
        return Response("not found", status=404)
    if row["saved_at"]:
        db.execute("UPDATE articles SET saved_at=NULL WHERE id=?", (article_id,))
    else:
        db.execute(
            "UPDATE articles SET saved_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE id=?", (article_id,)
        )
    db.commit()
    card = db.execute(
        "SELECT id, url, title, summary, score, score_reason, status, thumbnail_url, "
        "raw_snippet, read_at, saved_at, "
        "SUBSTR(full_text, 1, 400) as full_text_head FROM articles WHERE id=?",
        (article_id,),
    ).fetchone()
    return render_template("_article_card.html", article=_row_to_article(card))


@bp.post("/article/<int:article_id>/dismiss")
def article_dismiss(article_id: int):
    """Mark a single article as dismissed. Used by the swipe-left gesture."""
    db = get_db()
    exists = db.execute(
        "SELECT 1 FROM articles WHERE id=?", (article_id,)
    ).fetchone()
    if exists is None:
        return Response("not found", status=404)
    db.execute(
        "UPDATE articles SET status='dismissed' WHERE id=?", (article_id,)
    )
    db.commit()
    return Response("", status=200)


@bp.post("/vote/<int:article_id>/<value>")
def vote(article_id: int, value: str):
    try:
        value = int(value)
    except ValueError:
        return Response("invalid vote", status=400)
    if value not in (1, -1):
        return Response("invalid vote", status=400)
    db = get_db()
    db.execute(
        "INSERT INTO votes(article_id, value) VALUES(?, ?)", (article_id, value)
    )
    status = "liked" if value == 1 else "disliked"
    db.execute(
        "UPDATE articles SET status=? WHERE id=?", (status, article_id)
    )
    db.commit()
    row = db.execute(
        "SELECT id, url, title, summary, score, score_reason, status, thumbnail_url, "
        "raw_snippet, read_at, saved_at, "
        "SUBSTR(full_text, 1, 400) as full_text_head FROM articles WHERE id=?",
        (article_id,),
    ).fetchone()
    return render_template("_article_card.html", article=_row_to_article(row))


# ── Feed management ────────────────────────────────────────────────────────────

@bp.get("/sidebar/feeds")
def sidebar_feeds():
    """Feed list with per-feed unread + hidden + saved counts for the left sidebar.
    Feeds are grouped by tag; feeds with no tags appear under 'Untagged'."""
    db = get_db()
    rows = db.execute(
        """SELECT f.id, COALESCE(f.title, f.url) AS title, f.paused, f.tags,
                  SUM(CASE WHEN a.status='summarized' AND a.read_at IS NULL THEN 1 ELSE 0 END) AS unread,
                  SUM(CASE WHEN a.status='hidden' THEN 1 ELSE 0 END) AS hidden,
                  SUM(CASE WHEN a.saved_at IS NOT NULL THEN 1 ELSE 0 END) AS saved
           FROM feeds f
           LEFT JOIN articles a ON a.feed_id=f.id
           GROUP BY f.id
           ORDER BY title"""
    ).fetchall()
    total_unread = sum((r["unread"] or 0) for r in rows)
    total_hidden = sum((r["hidden"] or 0) for r in rows)
    total_saved = sum((r["saved"] or 0) for r in rows)

    by_tag: dict[str, list] = {}
    untagged: list = []
    for r in rows:
        tags = _split_tags(r["tags"])
        if not tags:
            untagged.append(r)
            continue
        for t in tags:
            by_tag.setdefault(t, []).append(r)
    tag_groups = [(tag, by_tag[tag]) for tag in sorted(by_tag.keys())]

    return render_template(
        "_sidebar_feeds.html",
        feeds=rows,
        tag_groups=tag_groups,
        untagged=untagged,
        total_unread=total_unread,
        total_hidden=total_hidden,
        total_saved=total_saved,
    )


@bp.get("/feeds")
def feeds_list():
    db = get_db()
    return render_template("_feeds.html", feeds=_all_feeds(db))


@bp.post("/feeds")
def feeds_add():
    url = request.form.get("url", "").strip()
    if not url:
        return Response("url required", status=400)
    db = get_db()
    try:
        db.execute("INSERT INTO feeds(url) VALUES(?)", (url,))
        db.commit()
    except Exception:
        return Response("feed already exists", status=409)
    return render_template("_feeds.html", feeds=_all_feeds(db))


@bp.get("/preferences")
def preferences_get():
    db = get_db()
    row = db.execute(
        "SELECT profile_text, updated_at FROM preferences WHERE id=1"
    ).fetchone()
    return render_template(
        "_preferences.html",
        profile_text=row["profile_text"] if row else "",
        updated_at=row["updated_at"] if row else None,
    )


@bp.post("/preferences")
def preferences_save():
    text = request.form.get("profile_text", "").strip()
    db = get_db()
    db.execute(
        """INSERT INTO preferences(id, profile_text, updated_at)
           VALUES(1, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
           ON CONFLICT(id) DO UPDATE
             SET profile_text=excluded.profile_text,
                 updated_at=excluded.updated_at""",
        (text,),
    )
    db.commit()
    row = db.execute(
        "SELECT profile_text, updated_at FROM preferences WHERE id=1"
    ).fetchone()
    return render_template(
        "_preferences.html",
        profile_text=row["profile_text"],
        updated_at=row["updated_at"],
        saved=True,
    )


@bp.post("/preferences/regenerate")
def preferences_regenerate():
    import threading
    from app.pipeline import regenerate_preferences

    app = current_app._get_current_object()

    def _run():
        try:
            regenerate_preferences(app)
        except Exception as exc:
            log.error("Manual preference regeneration failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return Response("ok", status=200)


@bp.get("/status")
def status():
    db = get_db()
    counts = {
        row["status"]: row["n"]
        for row in db.execute(
            "SELECT status, COUNT(*) AS n FROM articles GROUP BY status"
        ).fetchall()
    }
    last_poll = db.execute(
        "SELECT MAX(last_polled_at) AS t FROM feeds"
    ).fetchone()["t"]
    last_pipeline = get_setting(db, "last_pipeline_run_at", "") or None
    feed_count = db.execute("SELECT COUNT(*) AS n FROM feeds").fetchone()["n"]
    wants_json = "application/json" in request.headers.get("Accept", "")
    if wants_json:
        from flask import jsonify
        return jsonify({
            "last_poll_at": last_poll,
            "last_pipeline_run_at": last_pipeline,
            "feed_count": feed_count,
            "article_counts": counts,
        })
    return render_template(
        "_status.html",
        last_poll=last_poll,
        last_pipeline=last_pipeline,
        feed_count=feed_count,
        counts=counts,
    )


@bp.get("/settings/models")
def models_form():
    db = get_db()
    installed = ollama_client.list_models()
    scoring = get_setting(db, "scoring_model", DEFAULT_SCORING_MODEL) or DEFAULT_SCORING_MODEL
    summary = get_setting(db, "summary_model", DEFAULT_SUMMARY_MODEL) or DEFAULT_SUMMARY_MODEL
    return render_template(
        "_models.html",
        installed=installed,
        scoring_model=scoring,
        summary_model=summary,
    )


@bp.get("/settings/embeds")
def embeds_form():
    db = get_db()
    enabled = get_setting(db, "embeds_enabled", "") == "1"
    return render_template("_embeds_setting.html", enabled=enabled)


@bp.post("/settings/embeds")
def embeds_save():
    enabled = request.form.get("embeds_enabled") == "1"
    db = get_db()
    set_setting(db, "embeds_enabled", "1" if enabled else "")
    db.commit()
    return render_template("_embeds_setting.html", enabled=enabled, saved=True)


@bp.post("/settings/models")
def models_save():
    scoring = request.form.get("scoring_model", "").strip()
    summary = request.form.get("summary_model", "").strip()
    if not scoring or not summary:
        return Response("both models required", status=400)
    db = get_db()
    set_setting(db, "scoring_model", scoring)
    set_setting(db, "summary_model", summary)
    db.commit()
    installed = ollama_client.list_models()
    return render_template(
        "_models.html",
        installed=installed,
        scoring_model=scoring,
        summary_model=summary,
        saved=True,
    )


@bp.get("/feeds/opml")
def feeds_export_opml():
    db = get_db()
    rows = db.execute("SELECT url, title FROM feeds ORDER BY id").fetchall()
    body = "\n".join(
        f'      <outline type="rss" text="{escape(r["title"] or r["url"])}" '
        f'title="{escape(r["title"] or r["url"])}" xmlUrl="{escape(r["url"])}"/>'
        for r in rows
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<opml version="2.0">\n'
        '  <head><title>Better Read feeds</title></head>\n'
        '  <body>\n'
        f'{body}\n'
        '  </body>\n'
        '</opml>\n'
    )
    return Response(
        xml,
        mimetype="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="feeds.opml"'},
    )


@bp.post("/feeds/opml")
def feeds_import_opml():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return Response("file required", status=400)
    try:
        tree = ET.parse(upload.stream)
    except ET.ParseError as exc:
        return Response(f"invalid OPML: {exc}", status=400)
    urls = [
        outline.attrib["xmlUrl"].strip()
        for outline in tree.iter("outline")
        if outline.attrib.get("xmlUrl")
    ]
    if not urls:
        return Response("no feeds found in OPML", status=400)
    db = get_db()
    added = 0
    for url in urls:
        try:
            db.execute("INSERT INTO feeds(url) VALUES(?)", (url,))
            added += 1
        except Exception:
            pass
    db.commit()
    return render_template("_feeds.html", feeds=_all_feeds(db), opml_added=added)


@bp.delete("/feeds/<int:feed_id>")
def feeds_delete(feed_id: int):
    db = get_db()
    db.execute("DELETE FROM feeds WHERE id=?", (feed_id,))
    db.commit()
    rows = _all_feeds(db)
    return render_template("_feeds.html", feeds=rows)


def _all_feeds(db):
    return db.execute(
        "SELECT id, url, title, last_polled_at, last_success_at, last_error, "
        "consecutive_failures, paused, score_threshold, tags "
        "FROM feeds ORDER BY id"
    ).fetchall()


def _normalize_tags(raw: str) -> str:
    """Normalize a free-form tags string to canonical comma-separated form.
    Splits on commas, trims, lowercases, drops empties, dedupes, sorts.
    Returns '' for input that produces no tags."""
    if not raw:
        return ""
    seen: list[str] = []
    for part in raw.split(","):
        t = part.strip().lower()
        if t and t not in seen:
            seen.append(t)
    seen.sort()
    return ",".join(seen)


def _split_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p for p in raw.split(",") if p]


@bp.post("/feeds/<int:feed_id>/pause")
def feed_pause(feed_id: int):
    """Pause polling for a feed. Idempotent."""
    db = get_db()
    db.execute("UPDATE feeds SET paused=1 WHERE id=?", (feed_id,))
    db.commit()
    rows = _all_feeds(db)
    return render_template("_feeds.html", feeds=rows)


@bp.post("/feeds/<int:feed_id>/resume")
def feed_resume(feed_id: int):
    """Resume polling for a feed and reset its failure counter."""
    db = get_db()
    db.execute(
        "UPDATE feeds SET paused=0, consecutive_failures=0, last_error=NULL "
        "WHERE id=?",
        (feed_id,),
    )
    db.commit()
    rows = _all_feeds(db)
    return render_template("_feeds.html", feeds=rows)


@bp.post("/feeds/<int:feed_id>/threshold")
def feed_set_threshold(feed_id: int):
    """Set per-feed score threshold. Empty string clears the override."""
    raw = request.form.get("score_threshold", "").strip()
    db = get_db()
    if raw == "":
        db.execute("UPDATE feeds SET score_threshold=NULL WHERE id=?", (feed_id,))
    else:
        try:
            value = float(raw)
        except ValueError:
            return Response("threshold must be a number 0.0-1.0", status=400)
        if not 0.0 <= value <= 1.0:
            return Response("threshold must be 0.0-1.0", status=400)
        db.execute(
            "UPDATE feeds SET score_threshold=? WHERE id=?", (value, feed_id)
        )
    db.commit()
    rows = _all_feeds(db)
    return render_template("_feeds.html", feeds=rows)


@bp.post("/feeds/<int:feed_id>/tags")
def feed_set_tags(feed_id: int):
    """Set comma-separated tags on a feed. Empty string clears all tags."""
    raw = request.form.get("tags", "")
    normalized = _normalize_tags(raw)
    db = get_db()
    db.execute(
        "UPDATE feeds SET tags=? WHERE id=?",
        (normalized or None, feed_id),
    )
    db.commit()
    rows = _all_feeds(db)
    return render_template("_feeds.html", feeds=rows)


# ── Article reader ─────────────────────────────────────────────────────────────

@bp.get("/article/<int:article_id>/content")
def article_content(article_id: int):
    db = get_db()
    row = db.execute(
        "SELECT title, url, full_text, raw_snippet, feed_content "
        "FROM articles WHERE id=?",
        (article_id,)
    ).fetchone()
    if not row:
        return Response("Article not found.", status=404)
    db.execute(
        "UPDATE articles SET read_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE id=? AND read_at IS NULL",
        (article_id,),
    )
    db.commit()
    description = (row["raw_snippet"] or "").strip()
    full_text = row["full_text"] or row["feed_content"] or ""
    content = _clean_content(full_text, title=row["title"], description=description)
    embeds_enabled = get_setting(db, "embeds_enabled", "") == "1"
    return render_template(
        "_article_content.html",
        title=row["title"],
        description=description,
        blocks=_to_blocks(content, embeds_enabled=embeds_enabled),
    )


@bp.get("/count")
def article_count():
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) as n FROM articles WHERE status IN ('summarized', 'liked', 'disliked')"
    ).fetchone()
    return str(row["n"])


# ── Manual triggers ────────────────────────────────────────────────────────────

@bp.post("/poll")
def manual_poll():
    import threading
    from app.feeds import poll_all_feeds
    from app.pipeline import run_pipeline

    app = current_app._get_current_object()

    def _run():
        try:
            poll_all_feeds(app)
            run_pipeline(app)
        except Exception as exc:
            log.error("Manual poll failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return Response("ok", status=200)


@bp.post("/dismiss-all")
def dismiss_all():
    """Mark every currently-listed article (summarized/liked/disliked) as
    'dismissed' so they disappear from the main view. Respects the current
    feed filter when ?feed=<id> is provided. Votes remain in the votes table
    so the preference signal is preserved."""
    db = get_db()
    params: list = []
    feed_filter = ""
    feed_arg = request.args.get("feed", "").strip()
    if feed_arg.isdigit():
        feed_filter = " AND feed_id=?"
        params.append(int(feed_arg))
    cursor = db.execute(
        "UPDATE articles SET status='dismissed' "
        f"WHERE status IN ('summarized','liked','disliked'){feed_filter}",
        params,
    )
    db.commit()
    return Response(f"dismissed {cursor.rowcount} articles", status=200)


@bp.post("/rescore-hidden")
def rescore_hidden():
    """Reset all hidden articles to 'new' so the next pipeline run re-scores them
    against the current preference profile."""
    import threading
    from app.pipeline import run_pipeline

    db = get_db()
    cursor = db.execute(
        "UPDATE articles SET status='new', score=NULL, score_reason=NULL "
        "WHERE status='hidden'"
    )
    db.commit()
    n = cursor.rowcount

    app = current_app._get_current_object()

    def _run():
        try:
            run_pipeline(app)
        except Exception as exc:
            log.error("Rescore failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return Response(f"requeued {n} articles", status=200)
