import logging
import os
import re
import threading

import httpx
import trafilatura
import flask

from datetime import datetime, timezone

from app import prompts, ollama_client
from app.db import get_db_direct, get_setting, set_setting

log = logging.getLogger(__name__)

DEFAULT_SCORING_MODEL = os.environ.get("SCORING_MODEL", "llama3.2:3b")
DEFAULT_SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "llama3.2:3b")
SCORE_THRESHOLD = float(os.environ.get("SCORE_THRESHOLD", "0.35"))
SCORING_SNIPPET_CHARS = int(os.environ.get("SCORING_SNIPPET_CHARS", "2000"))

# Process-wide lock — prevents concurrent /poll clicks from running the pipeline
# in parallel and double-summarizing the same scored articles.
_PIPELINE_LOCK = threading.Lock()


def _scoring_model(db) -> str:
    return get_setting(db, "scoring_model", DEFAULT_SCORING_MODEL) or DEFAULT_SCORING_MODEL


def _summary_model(db) -> str:
    return get_setting(db, "summary_model", DEFAULT_SUMMARY_MODEL) or DEFAULT_SUMMARY_MODEL


def run_pipeline(app: flask.Flask) -> bool:
    """Score new articles then summarize scored ones. Called by APScheduler.

    Returns True if the pipeline ran, False if a previous run was still in
    flight and this call was skipped.
    """
    if not _PIPELINE_LOCK.acquire(blocking=False):
        log.info("Pipeline already running — skipping this trigger")
        return False
    try:
        with app.app_context():
            db = get_db_direct()
            try:
                profile_text = db.execute(
                    "SELECT profile_text FROM preferences WHERE id=1"
                ).fetchone()["profile_text"]
                score_new_articles(db, profile_text)
                summarize_scored_articles(db)
                set_setting(
                    db,
                    "last_pipeline_run_at",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
                db.commit()
            finally:
                db.close()
        return True
    finally:
        _PIPELINE_LOCK.release()


def score_new_articles(db, profile_text: str) -> None:
    articles = db.execute(
        """SELECT a.id, a.title, a.raw_snippet, f.score_threshold
           FROM articles a JOIN feeds f ON f.id = a.feed_id
           WHERE a.status='new' LIMIT 50"""
    ).fetchall()
    model = _scoring_model(db)

    for article in articles:
        try:
            snippet = (article["raw_snippet"] or "")[:SCORING_SNIPPET_CHARS]
            prompt = prompts.scoring_prompt(
                profile_text, article["title"], snippet
            )
            result = ollama_client.generate(
                model=model, prompt=prompt, expect_json=True
            )
            if result is None:
                log.warning("Scoring skipped for article id=%d (no LLM response)", article["id"])
                continue

            score = max(0.0, min(1.0, float(result.get("score", 0.5))))
            reason = str(result.get("reason", ""))
            threshold = (
                article["score_threshold"]
                if article["score_threshold"] is not None
                else SCORE_THRESHOLD
            )
            status = "hidden" if score < threshold else "scored"

            db.execute(
                "UPDATE articles SET score=?, score_reason=?, status=? WHERE id=?",
                (score, reason, status, article["id"]),
            )
            db.commit()
            log.info("Scored article id=%d score=%.2f status=%s", article["id"], score, status)
        except Exception as exc:
            log.error("Error scoring article id=%d: %s", article["id"], exc)


def summarize_scored_articles(db) -> None:
    articles = db.execute(
        "SELECT id, url, raw_snippet, feed_content, thumbnail_url "
        "FROM articles WHERE status='scored' LIMIT 20"
    ).fetchall()
    model = _summary_model(db)

    for article in articles:
        try:
            fetched_text, og_image = fetch_full_text_and_image(article["url"])
            full_text = (
                fetched_text
                or (article["feed_content"] if "feed_content" in article.keys() else None)
                or article["raw_snippet"]
                or ""
            )
            prompt = prompts.summarization_prompt(full_text)
            summary = ollama_client.generate(
                model=model, prompt=prompt, expect_json=False
            )
            if summary is None:
                log.warning("Summarization skipped for article id=%d", article["id"])
                continue

            new_thumb = article["thumbnail_url"] or og_image
            db.execute(
                "UPDATE articles SET full_text=?, summary=?, "
                "thumbnail_url=?, status='summarized' WHERE id=?",
                (full_text, summary.strip(), new_thumb, article["id"]),
            )
            db.commit()
            log.info("Summarized article id=%d", article["id"])
        except Exception as exc:
            log.error("Error summarizing article id=%d: %s", article["id"], exc)


def regenerate_preferences(app: flask.Flask) -> None:
    """Rebuild the user preference profile from recent votes. Called by APScheduler."""
    with app.app_context():
        db = get_db_direct()
        try:
            rows = db.execute(
                """SELECT v.value, a.title, a.summary
                   FROM votes v JOIN articles a ON a.id = v.article_id
                   ORDER BY v.created_at DESC LIMIT 200"""
            ).fetchall()

            liked = [
                f"{r['title']}: {r['summary'] or ''}"
                for r in rows if r["value"] == 1
            ]
            disliked = [
                f"{r['title']}: {r['summary'] or ''}"
                for r in rows if r["value"] == -1
            ]

            if not liked and not disliked:
                log.info("No votes yet — skipping preference regeneration")
                return

            prompt = prompts.profile_prompt(liked, disliked)
            new_profile = ollama_client.generate(
                model=_summary_model(db), prompt=prompt, expect_json=False
            )
            if new_profile is None:
                log.error("Preference regeneration failed — LLM returned None")
                return

            db.execute(
                """INSERT OR REPLACE INTO preferences(id, profile_text, updated_at)
                   VALUES(1, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))""",
                (new_profile.strip(),),
            )
            db.commit()
            log.info("Preference profile updated (%d chars)", len(new_profile))
        finally:
            db.close()


_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# Tweet permalink: `https://twitter.com/<user>/status/<id>` or the `x.com`
# rebrand. Trafilatura strips the surrounding `<blockquote class="twitter-tweet">`
# down to plain text and drops this anchor entirely, so we have to recover
# permalinks from the raw HTML before extraction.
_EMBED_TWITTER_RE = re.compile(
    r'https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/'
    r'[A-Za-z0-9_]+/status/\d+',
    re.IGNORECASE,
)
_EMBED_INSTAGRAM_RE = re.compile(
    r'https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+/?',
    re.IGNORECASE,
)


def _extract_embed_urls(html: str) -> list[str]:
    """Pull tweet / Instagram permalinks out of raw article HTML in source order.

    Only collects URLs inside ``<blockquote class="twitter-tweet">`` or
    ``<blockquote class="instagram-media">`` markers — the same wrappers the
    official embed scripts hydrate. Avoids matching unrelated tweet links in
    the page chrome (related-articles widgets, footers, sharing buttons).
    """
    found: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<blockquote\b[^>]*class=["\'][^"\']*'
        r'(twitter-tweet|instagram-media)[^"\']*["\'][^>]*>(.*?)</blockquote>',
        html, flags=re.IGNORECASE | re.DOTALL,
    ):
        cls, body = m.group(1).lower(), m.group(2)
        # Instagram stores the permalink on the blockquote itself when present;
        # fall back to scanning the body for both platforms.
        outer = m.group(0)
        candidates: list[str] = []
        if cls == "instagram-media":
            candidates.extend(_EMBED_INSTAGRAM_RE.findall(outer))
            candidates.extend(_EMBED_INSTAGRAM_RE.findall(body))
        else:
            candidates.extend(_EMBED_TWITTER_RE.findall(body))
        for url in candidates:
            url = url.rstrip('/')
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def _merge_embed_urls(text: str, urls: list[str]) -> str:
    """Append embed permalinks as standalone-line URLs so the reader can detect
    them. Skip URLs whose tweet/post id already appears in the extracted text
    (e.g. when trafilatura kept the link)."""
    if not urls:
        return text
    additions = [u for u in urls if u not in text]
    if not additions:
        return text
    suffix = "\n".join(additions)
    return f"{text}\n\n{suffix}" if text else suffix


def fetch_full_text(url: str) -> str:
    """Backward-compatible wrapper used by tests/older callers."""
    return fetch_full_text_and_image(url)[0]


def fetch_full_text_and_image(url: str) -> tuple[str, str | None]:
    """Fetch the article URL once and return (extracted_text, og_image_url)."""
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": "rss-reader/1.0"})
        r.raise_for_status()
        text = trafilatura.extract(r.text) or ""
        text = _merge_embed_urls(text, _extract_embed_urls(r.text))
        m = _OG_IMAGE_RE.search(r.text)
        og = m.group(1) if m else None
        return text, og
    except Exception as exc:
        log.warning("fetch_full_text failed for %s: %s", url, exc)
        return "", None
