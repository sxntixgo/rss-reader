from unittest.mock import MagicMock, patch

import pytest

from app.feeds import strip_html
from app.pipeline import (
    score_new_articles,
    summarize_scored_articles,
    run_pipeline,
    regenerate_preferences,
    fetch_full_text,
    _PIPELINE_LOCK,
)
from tests.conftest import add_article, add_feed


# ── strip_html ─────────────────────────────────────────────────────────────────

def test_strip_html_removes_tags():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_collapses_whitespace():
    assert strip_html("<p>a\n   b\t c</p>") == "a b c"


def test_strip_html_empty():
    assert strip_html("") == ""


# ── score_new_articles ─────────────────────────────────────────────────────────

@patch("app.pipeline.ollama_client.generate")
def test_score_sets_status_scored(mock_gen, memory_db):
    mock_gen.return_value = {"score": 0.8, "reason": "relevant"}
    feed_id = add_feed(memory_db)
    article_id = add_article(memory_db, feed_id, status="new", summary=None, score=None)

    score_new_articles(memory_db, "I like tech news")

    row = memory_db.execute("SELECT score, status FROM articles WHERE id=?", (article_id,)).fetchone()
    assert row["status"] == "scored"
    assert abs(row["score"] - 0.8) < 0.001


@patch("app.pipeline.ollama_client.generate")
def test_score_below_threshold_sets_hidden(mock_gen, memory_db):
    mock_gen.return_value = {"score": 0.1, "reason": "not relevant"}
    feed_id = add_feed(memory_db)
    article_id = add_article(memory_db, feed_id, status="new")

    score_new_articles(memory_db, "")

    row = memory_db.execute("SELECT status FROM articles WHERE id=?", (article_id,)).fetchone()
    assert row["status"] == "hidden"


@patch("app.pipeline.ollama_client.generate")
def test_score_llm_none_skips_article(mock_gen, memory_db):
    mock_gen.return_value = None
    feed_id = add_feed(memory_db)
    article_id = add_article(memory_db, feed_id, status="new")

    score_new_articles(memory_db, "")

    row = memory_db.execute("SELECT status FROM articles WHERE id=?", (article_id,)).fetchone()
    assert row["status"] == "new"


@patch("app.pipeline.ollama_client.generate")
def test_score_clamps_out_of_range(mock_gen, memory_db):
    mock_gen.return_value = {"score": 1.5, "reason": "high"}
    feed_id = add_feed(memory_db)
    add_article(memory_db, feed_id, status="new")

    score_new_articles(memory_db, "")

    row = memory_db.execute("SELECT score FROM articles").fetchone()
    assert row["score"] <= 1.0


@patch("app.pipeline.ollama_client.generate")
def test_score_handles_exception(mock_gen, memory_db, caplog):
    mock_gen.side_effect = RuntimeError("boom")
    feed_id = add_feed(memory_db)
    add_article(memory_db, feed_id, status="new")
    score_new_articles(memory_db, "")
    assert "Error scoring article" in caplog.text


@patch("app.pipeline.ollama_client.generate")
def test_score_uses_dynamic_model(mock_gen, memory_db):
    from app.db import set_setting
    mock_gen.return_value = {"score": 0.9, "reason": "ok"}
    set_setting(memory_db, "scoring_model", "custom-model:1b")
    memory_db.commit()
    feed_id = add_feed(memory_db)
    add_article(memory_db, feed_id, status="new")
    score_new_articles(memory_db, "")
    assert mock_gen.call_args.kwargs["model"] == "custom-model:1b"


# ── summarize_scored_articles ──────────────────────────────────────────────────

@patch("app.pipeline.fetch_full_text_and_image")
@patch("app.pipeline.ollama_client.generate")
def test_summarize_sets_status_summarized(mock_gen, mock_fetch, memory_db):
    mock_fetch.return_value = ("Full article text here.", None)
    mock_gen.return_value = "This is the summary."
    feed_id = add_feed(memory_db)
    article_id = add_article(memory_db, feed_id, status="scored")

    summarize_scored_articles(memory_db)

    row = memory_db.execute(
        "SELECT summary, status FROM articles WHERE id=?", (article_id,)
    ).fetchone()
    assert row["status"] == "summarized"
    assert row["summary"] == "This is the summary."


@patch("app.pipeline.fetch_full_text_and_image")
@patch("app.pipeline.ollama_client.generate")
def test_summarize_falls_back_to_feed_content(mock_gen, mock_fetch, memory_db):
    mock_fetch.return_value = ("", None)
    mock_gen.return_value = "OK"
    feed_id = add_feed(memory_db)
    add_article(
        memory_db, feed_id,
        status="scored",
        feed_content="Body from feed XML",
        raw_snippet="snippet",
    )
    summarize_scored_articles(memory_db)
    call_prompt = mock_gen.call_args.kwargs.get("prompt") or mock_gen.call_args.args[1]
    assert "Body from feed XML" in call_prompt


@patch("app.pipeline.fetch_full_text_and_image")
@patch("app.pipeline.ollama_client.generate")
def test_summarize_falls_back_to_snippet(mock_gen, mock_fetch, memory_db):
    mock_fetch.return_value = ("", None)
    mock_gen.return_value = "OK"
    feed_id = add_feed(memory_db)
    add_article(
        memory_db, feed_id,
        status="scored",
        feed_content=None,
        raw_snippet="snippet only",
    )
    summarize_scored_articles(memory_db)
    call_prompt = mock_gen.call_args.kwargs.get("prompt") or mock_gen.call_args.args[1]
    assert "snippet only" in call_prompt


@patch("app.pipeline.fetch_full_text_and_image")
@patch("app.pipeline.ollama_client.generate")
def test_summarize_llm_none_skips(mock_gen, mock_fetch, memory_db):
    mock_fetch.return_value = ("text", None)
    mock_gen.return_value = None
    feed_id = add_feed(memory_db)
    article_id = add_article(memory_db, feed_id, status="scored")

    summarize_scored_articles(memory_db)

    row = memory_db.execute("SELECT status FROM articles WHERE id=?", (article_id,)).fetchone()
    assert row["status"] == "scored"


@patch("app.pipeline.fetch_full_text_and_image")
@patch("app.pipeline.ollama_client.generate")
def test_summarize_handles_exception(mock_gen, mock_fetch, memory_db, caplog):
    mock_fetch.side_effect = RuntimeError("boom")
    feed_id = add_feed(memory_db)
    add_article(memory_db, feed_id, status="scored")
    summarize_scored_articles(memory_db)
    assert "Error summarizing article" in caplog.text


# ── run_pipeline + lock ────────────────────────────────────────────────────────

@patch("app.pipeline.score_new_articles")
@patch("app.pipeline.summarize_scored_articles")
def test_run_pipeline_writes_last_run(mock_sum, mock_score, app):
    assert run_pipeline(app) is True
    from app.db import get_db_direct, get_setting
    with app.app_context():
        db = get_db_direct()
        ts = get_setting(db, "last_pipeline_run_at")
        db.close()
    assert ts


def test_run_pipeline_skips_when_locked(app):
    _PIPELINE_LOCK.acquire()
    try:
        assert run_pipeline(app) is False
    finally:
        _PIPELINE_LOCK.release()


# ── regenerate_preferences ─────────────────────────────────────────────────────

@patch("app.pipeline.ollama_client.generate")
def test_regenerate_preferences_writes_profile(mock_gen, app):
    mock_gen.return_value = "You like Rust news."
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        a1 = add_article(db, feed_id, seq=1, guid="g1", title="Rust 2025")
        db.execute("INSERT INTO votes(article_id, value) VALUES(?, 1)", (a1,))
        db.commit()
        db.close()
    regenerate_preferences(app)
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT profile_text FROM preferences WHERE id=1").fetchone()
        assert "Rust news" in row["profile_text"]
        db.close()


def test_regenerate_preferences_no_votes_short_circuits(app, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="app.pipeline")
    regenerate_preferences(app)
    assert "skipping preference regeneration" in caplog.text


@patch("app.pipeline.ollama_client.generate")
def test_regenerate_preferences_llm_none(mock_gen, app, caplog):
    mock_gen.return_value = None
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        a = add_article(db, feed_id)
        db.execute("INSERT INTO votes(article_id, value) VALUES(?, 1)", (a,))
        db.commit()
        db.close()
    regenerate_preferences(app)
    assert "Preference regeneration failed" in caplog.text


# ── fetch_full_text ────────────────────────────────────────────────────────────

@patch("app.pipeline.trafilatura.extract")
@patch("app.pipeline.httpx.get")
def test_fetch_full_text_success(mock_get, mock_extract):
    resp = MagicMock()
    resp.text = "<html>hi</html>"
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp
    mock_extract.return_value = "extracted"
    assert fetch_full_text("https://x.example.com") == "extracted"


@patch("app.pipeline.httpx.get")
def test_fetch_full_text_failure_returns_empty(mock_get, caplog):
    mock_get.side_effect = RuntimeError("network down")
    assert fetch_full_text("https://x.example.com") == ""
    assert "fetch_full_text failed" in caplog.text


# ── per-feed score threshold override ──────────────────────────────────────────

@patch("app.pipeline.ollama_client.generate")
def test_score_uses_per_feed_threshold_override(mock_gen, memory_db):
    """A feed with a lower threshold keeps articles that the global threshold would hide."""
    mock_gen.return_value = {"score": 0.2, "reason": "borderline"}
    feed_id = add_feed(memory_db)
    memory_db.execute("UPDATE feeds SET score_threshold=0.1 WHERE id=?", (feed_id,))
    add_article(memory_db, feed_id, status="new")

    score_new_articles(memory_db, "")

    row = memory_db.execute("SELECT status FROM articles").fetchone()
    assert row["status"] == "scored"  # 0.2 >= per-feed 0.1


@patch("app.pipeline.ollama_client.generate")
def test_score_falls_back_to_global_threshold(mock_gen, memory_db):
    """No per-feed override → uses module SCORE_THRESHOLD (0.35 default)."""
    mock_gen.return_value = {"score": 0.2, "reason": "below default"}
    feed_id = add_feed(memory_db)
    add_article(memory_db, feed_id, status="new")

    score_new_articles(memory_db, "")

    row = memory_db.execute("SELECT status FROM articles").fetchone()
    assert row["status"] == "hidden"


# ── OG image fallback ─────────────────────────────────────────────────────────

@patch("app.pipeline.trafilatura.extract")
@patch("app.pipeline.httpx.get")
def test_fetch_full_text_and_image_extracts_og(mock_get, mock_extract):
    from app.pipeline import fetch_full_text_and_image
    resp = MagicMock()
    resp.text = '<html><head><meta property="og:image" content="https://x/o.jpg"></head></html>'
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp
    mock_extract.return_value = "extracted body"
    text, og = fetch_full_text_and_image("https://x.example.com")
    assert text == "extracted body"
    assert og == "https://x/o.jpg"


@patch("app.pipeline.httpx.get")
def test_fetch_full_text_and_image_failure(mock_get):
    from app.pipeline import fetch_full_text_and_image
    mock_get.side_effect = RuntimeError("nope")
    assert fetch_full_text_and_image("https://x.example.com") == ("", None)


def test_extract_embed_urls_from_twitter_blockquote():
    from app.pipeline import _extract_embed_urls
    html = (
        '<article><p>Lead.</p>'
        '<blockquote class="twitter-tweet"><p>Tweet body</p>'
        '— SportsCenter (@SC_ESPN) '
        '<a href="https://twitter.com/SC_ESPN/status/1234567890?ref_src=foo">'
        'April 19, 2026</a></blockquote>'
        '<p>Closing.</p></article>'
    )
    # Query-string is dropped — the embed widget doesn't need it.
    assert _extract_embed_urls(html) == [
        "https://twitter.com/SC_ESPN/status/1234567890"
    ]


def test_extract_embed_urls_from_instagram_blockquote():
    from app.pipeline import _extract_embed_urls
    html = (
        '<blockquote class="instagram-media" '
        'data-instgrm-permalink="https://www.instagram.com/p/AbCd123/?utm=copy">'
        '<a href="https://www.instagram.com/p/AbCd123/">view</a>'
        '</blockquote>'
    )
    out = _extract_embed_urls(html)
    assert out == ["https://www.instagram.com/p/AbCd123"]


def test_extract_embed_urls_dedupes_across_blockquotes():
    from app.pipeline import _extract_embed_urls
    bq = (
        '<blockquote class="twitter-tweet">'
        '<a href="https://twitter.com/u/status/1">x</a></blockquote>'
    )
    assert _extract_embed_urls(bq + bq) == ["https://twitter.com/u/status/1"]


def test_extract_embed_urls_ignores_unrelated_blockquotes():
    from app.pipeline import _extract_embed_urls
    html = (
        '<blockquote><a href="https://twitter.com/u/status/9">tweet</a></blockquote>'
        '<a href="https://twitter.com/u/status/9">page chrome link</a>'
    )
    assert _extract_embed_urls(html) == []


@patch("app.pipeline.trafilatura.extract")
@patch("app.pipeline.httpx.get")
def test_fetch_full_text_appends_embed_urls(mock_get, mock_extract):
    from app.pipeline import fetch_full_text_and_image
    resp = MagicMock()
    resp.text = (
        '<html><body><p>Body before tweet.</p>'
        '<blockquote class="twitter-tweet">'
        '<p>Tweet body — SportsCenter (@SC_ESPN) </p>'
        '<a href="https://twitter.com/SC_ESPN/status/42">April 19, 2026</a>'
        '</blockquote>'
        '<p>Body after tweet.</p></body></html>'
    )
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp
    # Trafilatura strips the permalink — only plain text comes back.
    mock_extract.return_value = (
        "Body before tweet.\nTweet body — SportsCenter (@SC_ESPN) April 19, 2026\nBody after tweet."
    )
    text, _ = fetch_full_text_and_image("https://example.com/article")
    # The recovered permalink should be appended on its own line so the reader
    # turns it into an embed.
    assert "https://twitter.com/SC_ESPN/status/42" in text
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    assert "https://twitter.com/SC_ESPN/status/42" in lines


@patch("app.pipeline.trafilatura.extract")
@patch("app.pipeline.httpx.get")
def test_fetch_full_text_skips_embed_url_already_in_text(mock_get, mock_extract):
    from app.pipeline import fetch_full_text_and_image
    resp = MagicMock()
    resp.text = (
        '<blockquote class="twitter-tweet">'
        '<a href="https://twitter.com/u/status/1">x</a></blockquote>'
    )
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp
    mock_extract.return_value = "https://twitter.com/u/status/1"
    text, _ = fetch_full_text_and_image("https://example.com")
    # No duplicate appended.
    assert text.count("https://twitter.com/u/status/1") == 1


@patch("app.pipeline.fetch_full_text_and_image")
@patch("app.pipeline.ollama_client.generate")
def test_summarize_writes_og_image_when_thumbnail_missing(mock_gen, mock_fetch, memory_db):
    mock_fetch.return_value = ("body text", "https://x/og.jpg")
    mock_gen.return_value = "Summary."
    feed_id = add_feed(memory_db)
    add_article(memory_db, feed_id, status="scored", thumbnail_url=None)

    summarize_scored_articles(memory_db)

    row = memory_db.execute("SELECT thumbnail_url FROM articles").fetchone()
    assert row["thumbnail_url"] == "https://x/og.jpg"


@patch("app.pipeline.fetch_full_text_and_image")
@patch("app.pipeline.ollama_client.generate")
def test_summarize_keeps_existing_thumbnail(mock_gen, mock_fetch, memory_db):
    mock_fetch.return_value = ("body", "https://og/replacement.jpg")
    mock_gen.return_value = "Summary."
    feed_id = add_feed(memory_db)
    add_article(memory_db, feed_id, status="scored", thumbnail_url="https://orig/thumb.jpg")

    summarize_scored_articles(memory_db)

    row = memory_db.execute("SELECT thumbnail_url FROM articles").fetchone()
    assert row["thumbnail_url"] == "https://orig/thumb.jpg"


@patch("app.pipeline.trafilatura.extract")
@patch("app.pipeline.httpx.get")
def test_fetch_full_text_and_image_no_og_returns_none(mock_get, mock_extract):
    from app.pipeline import fetch_full_text_and_image
    resp = MagicMock()
    resp.text = "<html><body>no meta tags</body></html>"
    resp.raise_for_status = MagicMock()
    mock_get.return_value = resp
    mock_extract.return_value = "body"
    text, og = fetch_full_text_and_image("https://x.example.com")
    assert text == "body"
    assert og is None
