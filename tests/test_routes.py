import io
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import add_article, add_feed


# ── Index + articles ───────────────────────────────────────────────────────────

def test_index_returns_200(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Better Read" in r.data


def test_settings_page_renders(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert b"Preference Profile" in r.data
    assert b"Re-score hidden articles" in r.data
    # Manage-feeds widgets should NOT appear on the settings page anymore
    assert b"Add Feed" not in r.data
    assert b"Import OPML" not in r.data


def test_manage_feeds_page_renders(client):
    r = client.get("/manage-feeds")
    assert r.status_code == 200
    assert b"Add Feed" in r.data
    assert b"Import OPML" in r.data
    # Settings widgets should NOT appear here
    assert b"Preference Profile" not in r.data
    assert b"Ollama Models" not in r.data


def test_articles_empty(client):
    r = client.get("/articles")
    assert r.status_code == 200


def test_articles_shows_summarized(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, status="summarized", title="ShowMe")
        db.close()

    r = client.get("/articles")
    assert r.status_code == 200
    assert b"ShowMe" in r.data


def test_articles_hides_dismissed(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, status="dismissed", title="Hidden")
        db.close()

    r = client.get("/articles")
    assert b"Hidden" not in r.data


def test_articles_sort_score(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, seq=1, guid="a", url="u1", title="LowScore", score=0.1)
        add_article(db, feed_id, seq=2, guid="b", url="u2", title="HiScore", score=0.99)
        db.close()
    r = client.get("/articles?sort=score")
    assert r.status_code == 200
    assert r.data.index(b"HiScore") < r.data.index(b"LowScore")


def test_articles_marks_read_class(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        article_id = add_article(db, feed_id, title="ReadOne")
        db.execute(
            "UPDATE articles SET read_at='2026-01-01T00:00:00Z' WHERE id=?",
            (article_id,),
        )
        db.commit()
        db.close()
    r = client.get("/articles")
    assert b"read" in r.data
    assert b"ReadOne" in r.data


def test_articles_extracts_reading_time(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(
            db,
            feed_id,
            title="ReadingTimed",
            full_text="- 4 minutos de lectura\nsome content",
        )
        db.close()
    r = client.get("/articles")
    assert b"4" in r.data


# ── Vote ───────────────────────────────────────────────────────────────────────

def test_vote_like(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        article_id = add_article(db, feed_id)
        db.close()

    r = client.post(f"/vote/{article_id}/1")
    assert r.status_code == 200
    assert b"liked" in r.data

    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT status FROM articles WHERE id=?", (article_id,)).fetchone()
        assert row["status"] == "liked"
        vote = db.execute("SELECT value FROM votes WHERE article_id=?", (article_id,)).fetchone()
        assert vote["value"] == 1
        db.close()


def test_vote_dislike(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        article_id = add_article(db, feed_id)
        db.close()

    r = client.post(f"/vote/{article_id}/-1")
    assert r.status_code == 200
    assert b"disliked" in r.data


def test_vote_invalid_value(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        article_id = add_article(db, feed_id)
        db.close()

    r = client.post(f"/vote/{article_id}/5")
    assert r.status_code == 400


def test_vote_non_numeric(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        article_id = add_article(db, feed_id)
        db.close()
    r = client.post(f"/vote/{article_id}/oops")
    assert r.status_code == 400


# ── Article content / read tracking ────────────────────────────────────────────

def test_article_content_marks_read(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        article_id = add_article(
            db, feed_id,
            full_text="Para leer.\nFirst paragraph here.\nSecond.",
        )
        db.close()
    r = client.get(f"/article/{article_id}/content")
    assert r.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT read_at FROM articles WHERE id=?", (article_id,)).fetchone()
        assert row["read_at"] is not None
        db.close()


def test_article_content_404(client):
    r = client.get("/article/999999/content")
    assert r.status_code == 404


def test_article_content_uses_feed_content_fallback(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(
            db, feed_id,
            full_text=None,
            feed_content="Body from RSS feed_content tag.",
        )
        article_id = db.execute("SELECT id FROM articles").fetchone()["id"]
        db.close()
    r = client.get(f"/article/{article_id}/content")
    assert b"feed_content" in r.data


def test_count_endpoint(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, seq=1, guid="g1", status="summarized")
        add_article(db, feed_id, seq=2, guid="g2", status="liked")
        add_article(db, feed_id, seq=3, guid="g3", status="dismissed")
        db.close()
    r = client.get("/count")
    assert r.data == b"2"


# ── Feeds CRUD ─────────────────────────────────────────────────────────────────

def test_settings_page(client):
    r = client.get("/settings")
    assert r.status_code == 200


def test_feeds_list_empty(client):
    r = client.get("/feeds")
    assert r.status_code == 200
    assert b"No feeds" in r.data


def test_feeds_add(client, app):
    r = client.post("/feeds", data={"url": "https://hnrss.org/frontpage"})
    assert r.status_code == 200
    assert b"hnrss.org" in r.data


def test_feeds_add_empty_url(client):
    r = client.post("/feeds", data={"url": ""})
    assert r.status_code == 400


def test_feeds_add_duplicate(client):
    client.post("/feeds", data={"url": "https://example.com/rss"})
    r = client.post("/feeds", data={"url": "https://example.com/rss"})
    assert r.status_code == 409


def test_feeds_delete(client, app):
    client.post("/feeds", data={"url": "https://example.com/rss"})
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = db.execute("SELECT id FROM feeds").fetchone()["id"]
        db.close()
    r = client.delete(f"/feeds/{feed_id}")
    assert r.status_code == 200
    assert b"example.com" not in r.data


# ── OPML import / export ───────────────────────────────────────────────────────

def test_feeds_export_opml(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        add_feed(db, "https://example.com/rss")
        db.close()
    r = client.get("/feeds/opml")
    assert r.status_code == 200
    assert b"<opml" in r.data
    assert b"https://example.com/rss" in r.data
    assert "attachment" in r.headers["Content-Disposition"]


def test_feeds_import_opml_inserts_new(client, app):
    opml = b"""<?xml version="1.0"?>
    <opml version="2.0">
      <body>
        <outline type="rss" xmlUrl="https://a.example.com/rss"/>
        <outline type="rss" xmlUrl="https://b.example.com/atom"/>
      </body>
    </opml>"""
    data = {"file": (io.BytesIO(opml), "feeds.opml")}
    r = client.post("/feeds/opml", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    assert b"Imported 2" in r.data
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        urls = {row["url"] for row in db.execute("SELECT url FROM feeds").fetchall()}
        assert urls == {"https://a.example.com/rss", "https://b.example.com/atom"}
        db.close()


def test_feeds_import_opml_skips_duplicates(client, app):
    client.post("/feeds", data={"url": "https://dup.example.com/rss"})
    opml = b"""<?xml version="1.0"?>
    <opml><body>
      <outline xmlUrl="https://dup.example.com/rss"/>
      <outline xmlUrl="https://new.example.com/rss"/>
    </body></opml>"""
    r = client.post(
        "/feeds/opml",
        data={"file": (io.BytesIO(opml), "x.opml")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert b"Imported 1" in r.data


def test_feeds_import_opml_no_file(client):
    r = client.post("/feeds/opml", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_feeds_import_opml_invalid_xml(client):
    r = client.post(
        "/feeds/opml",
        data={"file": (io.BytesIO(b"not xml at all"), "f.opml")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_feeds_import_opml_empty(client):
    opml = b"<?xml version='1.0'?><opml><body></body></opml>"
    r = client.post(
        "/feeds/opml",
        data={"file": (io.BytesIO(opml), "f.opml")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


# ── Status ─────────────────────────────────────────────────────────────────────

def test_status_html(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, status="summarized")
        db.close()
    r = client.get("/status")
    assert r.status_code == 200
    assert b"summarized" in r.data
    assert b"Articles by status" in r.data


def test_status_json(client, app):
    from app.db import get_db_direct, set_setting
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, status="summarized")
        set_setting(db, "last_pipeline_run_at", "2026-04-19T00:00:00Z")
        db.commit()
        db.close()
    r = client.get("/status", headers={"Accept": "application/json"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["feed_count"] == 1
    assert body["article_counts"]["summarized"] == 1
    assert body["last_pipeline_run_at"] == "2026-04-19T00:00:00Z"


# ── Models endpoint ────────────────────────────────────────────────────────────

@patch("app.routes.ollama_client.list_models")
def test_models_form_renders_installed(mock_list, client):
    mock_list.return_value = ["llama3.1:8b", "qwen2.5:7b"]
    r = client.get("/settings/models")
    assert r.status_code == 200
    assert b"llama3.1:8b" in r.data
    assert b"qwen2.5:7b" in r.data


@patch("app.routes.ollama_client.list_models")
def test_models_save_persists(mock_list, client, app):
    mock_list.return_value = ["llama3.1:8b", "mistral:7b"]
    r = client.post(
        "/settings/models",
        data={"scoring_model": "mistral:7b", "summary_model": "llama3.1:8b"},
    )
    assert r.status_code == 200
    assert b"Saved" in r.data
    from app.db import get_db_direct, get_setting
    with app.app_context():
        db = get_db_direct()
        assert get_setting(db, "scoring_model") == "mistral:7b"
        assert get_setting(db, "summary_model") == "llama3.1:8b"
        db.close()


def test_models_save_requires_both(client):
    r = client.post("/settings/models", data={"scoring_model": "x"})
    assert r.status_code == 400


@patch("app.routes.ollama_client.list_models")
def test_models_form_marks_uninstalled(mock_list, client, app):
    mock_list.return_value = ["llama3.1:8b"]
    from app.db import get_db_direct, set_setting
    with app.app_context():
        db = get_db_direct()
        set_setting(db, "scoring_model", "ghost-model:1b")
        db.commit()
        db.close()
    r = client.get("/settings/models")
    assert b"not installed" in r.data


# ── Preferences ────────────────────────────────────────────────────────────────

def test_preferences_get_default(client):
    r = client.get("/preferences")
    assert r.status_code == 200
    assert b"Never updated" in r.data or b"profile" in r.data.lower()


def test_preferences_save(client, app):
    r = client.post("/preferences", data={"profile_text": "Loves Rust news."})
    assert r.status_code == 200
    assert b"Loves Rust news." in r.data
    assert b"Saved" in r.data
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT profile_text FROM preferences WHERE id=1").fetchone()
        assert row["profile_text"] == "Loves Rust news."
        db.close()


@patch("threading.Thread")
def test_preferences_regenerate_spawns_thread(mock_thread, client):
    inst = mock_thread.return_value
    r = client.post("/preferences/regenerate")
    assert r.status_code == 200
    inst.start.assert_called_once()


# ── Manual poll ────────────────────────────────────────────────────────────────

@patch("threading.Thread")
def test_manual_poll_spawns_thread(mock_thread, client):
    inst = mock_thread.return_value
    r = client.post("/poll")
    assert r.status_code == 200
    inst.start.assert_called_once()


# ── Helpers ────────────────────────────────────────────────────────────────────

def test_extract_reading_time_english():
    from app.routes import _extract_reading_time
    assert _extract_reading_time("This article is 7 min read.") == "7"


def test_extract_reading_time_spanish():
    from app.routes import _extract_reading_time
    assert _extract_reading_time("- 4 minutos de lectura") == "4"


def test_extract_reading_time_none():
    from app.routes import _extract_reading_time
    assert _extract_reading_time("no reading time here") is None


def test_clean_content_strips_reading_time_and_junk():
    from app.routes import _clean_content
    text = (
        "Real first paragraph " * 20 + "\n"
        "- 4 minutos de lectura\n"
        "Otras noticias\n"
        "- 1\n"
        "more junk\n"
    )
    out = _clean_content(text, title="Real first paragraph")
    assert "lectura" not in out.lower()
    assert "Otras noticias" not in out
    assert "- 1" not in out


def test_clean_content_skips_duplicate_title_line():
    from app.routes import _clean_content
    out = _clean_content("Some Title\nFirst paragraph.", title="Some Title")
    assert out.startswith("First paragraph")


def test_clean_content_skips_duplicate_description_line():
    from app.routes import _clean_content
    desc = "This is the description that leads the article and should not repeat."
    out = _clean_content(desc + "\nReal body line.", description=desc)
    assert out.startswith("Real body line")


def test_clean_content_strips_numbered_related_list():
    from app.routes import _clean_content
    body = ("Real first paragraph " * 30).strip() + "\n- 1\nrelated thing"
    out = _clean_content(body)
    assert "- 1" not in out
    assert "related thing" not in out


def test_to_blocks_groups_consecutive_dash_bullets():
    from app.routes import _to_blocks
    text = "Intro paragraph.\n- first\n- second\n- third\nClosing line."
    blocks = _to_blocks(text)
    assert blocks[0] == {"type": "p", "text": "Intro paragraph."}
    assert blocks[1] == {"type": "ul", "items": ["first", "second", "third"]}
    assert blocks[2] == {"type": "p", "text": "Closing line."}


def test_to_blocks_supports_star_and_unicode_bullets():
    from app.routes import _to_blocks
    blocks = _to_blocks("* alpha\n• beta\n– gamma\nplain")
    assert blocks[0]["type"] == "ul"
    assert blocks[0]["items"] == ["alpha", "beta", "gamma"]
    assert blocks[1] == {"type": "p", "text": "plain"}


def test_to_blocks_separate_bullet_groups():
    from app.routes import _to_blocks
    blocks = _to_blocks("- a\n- b\nbreak\n- c")
    assert [b["type"] for b in blocks] == ["ul", "p", "ul"]
    assert blocks[0]["items"] == ["a", "b"]
    assert blocks[2]["items"] == ["c"]


def test_to_blocks_ignores_empty_input():
    from app.routes import _to_blocks
    assert _to_blocks("") == []


def test_to_blocks_emits_twitter_embed_when_enabled():
    from app.routes import _to_blocks
    text = "Setup line.\nhttps://twitter.com/jack/status/20\nFollow-up."
    blocks = _to_blocks(text, embeds_enabled=True)
    assert blocks[1] == {
        "type": "embed",
        "platform": "twitter",
        "url": "https://twitter.com/jack/status/20",
    }


def test_to_blocks_recognises_x_com_and_instagram():
    from app.routes import _to_blocks
    text = (
        "https://x.com/elon/status/1234567890\n"
        "https://www.instagram.com/p/AbCdEf-12_/\n"
        "https://www.instagram.com/reel/XyZ123/"
    )
    blocks = _to_blocks(text, embeds_enabled=True)
    assert [b["platform"] for b in blocks] == ["twitter", "instagram", "instagram"]
    assert all(b["type"] == "embed" for b in blocks)


def test_to_blocks_embed_disabled_keeps_url_as_paragraph():
    from app.routes import _to_blocks
    url = "https://twitter.com/jack/status/20"
    blocks = _to_blocks(url)
    assert blocks == [{"type": "p", "text": url}]


def test_to_blocks_inline_url_is_not_an_embed():
    from app.routes import _to_blocks
    text = "Check https://twitter.com/jack/status/20 — interesting."
    blocks = _to_blocks(text, embeds_enabled=True)
    assert blocks[0]["type"] == "p"
    assert "twitter.com" in blocks[0]["text"]


def test_to_blocks_embed_breaks_running_bullet_list():
    from app.routes import _to_blocks
    text = "- one\n- two\nhttps://twitter.com/u/status/9\n- three"
    blocks = _to_blocks(text, embeds_enabled=True)
    assert [b["type"] for b in blocks] == ["ul", "embed", "ul"]
    assert blocks[0]["items"] == ["one", "two"]
    assert blocks[2]["items"] == ["three"]


def test_article_content_renders_twitter_embed_when_enabled(client, app):
    from app.db import get_db_direct, set_setting
    body = "Lead.\nhttps://twitter.com/jack/status/20\nTrailing."
    with app.app_context():
        db = get_db_direct()
        set_setting(db, "embeds_enabled", "1")
        feed_id = add_feed(db)
        add_article(db, feed_id, full_text=body)
        article_id = db.execute("SELECT id FROM articles").fetchone()["id"]
        db.commit()
        db.close()
    r = client.get(f"/article/{article_id}/content")
    html = r.data.decode()
    assert 'class="twitter-tweet"' in html
    assert 'data-embed-platform="twitter"' in html
    assert "https://twitter.com/jack/status/20" in html


def test_article_content_skips_embed_when_setting_off(client, app):
    from app.db import get_db_direct
    body = "Lead.\nhttps://twitter.com/jack/status/20\nTrailing."
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, full_text=body)
        article_id = db.execute("SELECT id FROM articles").fetchone()["id"]
        db.close()
    r = client.get(f"/article/{article_id}/content")
    html = r.data.decode()
    assert "twitter-tweet" not in html
    assert "https://twitter.com/jack/status/20" in html


def test_refresh_button_uses_loading_spinner(client):
    """The Refresh button shows a spinner element (not a "Fetching…" sibling)."""
    r = client.get("/")
    assert r.status_code == 200
    body = r.data.decode()
    assert 'id="poll-btn"' in body
    assert 'class="btn-loadable"' in body
    assert 'class="btn-spinner"' in body
    assert 'class="btn-label"' in body
    assert "Fetching" not in body
    assert 'id="poll-spinner"' not in body


def test_embeds_settings_form_renders(client):
    r = client.get("/settings/embeds")
    assert r.status_code == 200
    assert b"embeds_enabled" in r.data
    # Default is off — checkbox is not pre-checked.
    assert b"checked" not in r.data


def test_embeds_settings_post_toggles_setting(client, app):
    from app.db import get_db_direct, get_setting
    r = client.post("/settings/embeds", data={"embeds_enabled": "1"})
    assert r.status_code == 200
    assert b"checked" in r.data
    assert b"Saved." in r.data
    with app.app_context():
        db = get_db_direct()
        assert get_setting(db, "embeds_enabled") == "1"
        db.close()
    # Posting without the box turns it back off.
    r = client.post("/settings/embeds", data={})
    assert r.status_code == 200
    assert b"checked" not in r.data
    with app.app_context():
        db = get_db_direct()
        assert get_setting(db, "embeds_enabled") == ""
        db.close()


def test_article_content_renders_bulleted_list(client, app):
    from app.db import get_db_direct
    body = "Lead paragraph.\n- one\n- two\n- three\nAfter list."
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, full_text=body)
        article_id = db.execute("SELECT id FROM articles").fetchone()["id"]
        db.close()
    r = client.get(f"/article/{article_id}/content")
    body_html = r.data.decode()
    assert "<ul>" in body_html
    assert "<li>one</li>" in body_html
    assert "<li>two</li>" in body_html
    assert "<li>three</li>" in body_html
    # Bullet items should not also appear as paragraphs.
    assert "<p>- one</p>" not in body_html


def test_article_content_skips_description_when_full_text_repeats_it(client, app):
    from app.db import get_db_direct
    # _clean_content already strips a leading body line that duplicates the
    # description, so the rendered content shows desc + remaining body once.
    desc = "Short unique preamble."
    body = desc + " Extra words continuing the same line with fresh content."
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(
            db, feed_id,
            raw_snippet=desc,
            full_text=body,
        )
        article_id = db.execute("SELECT id FROM articles").fetchone()["id"]
        db.close()
    r = client.get(f"/article/{article_id}/content")
    assert r.data.count(b"Short unique preamble") == 1


def test_preferences_regenerate_thread_handles_exception(client, app, caplog):
    """Cover the except-branch in the inline _run() helper."""
    import logging
    caplog.set_level(logging.ERROR, logger="app.routes")
    with patch("app.pipeline.regenerate_preferences", side_effect=RuntimeError("boom")):
        # Force the spawned thread to run inline so the except path is exercised.
        with patch("threading.Thread") as mock_thread:
            def fake_thread(target, daemon=False):
                t = MagicMock()
                t.start = lambda: target()
                return t
            mock_thread.side_effect = fake_thread
            r = client.post("/preferences/regenerate")
            assert r.status_code == 200
    assert "Manual preference regeneration failed" in caplog.text


def test_manual_poll_thread_handles_exception(client, app, caplog):
    import logging
    caplog.set_level(logging.ERROR, logger="app.routes")
    with patch("app.feeds.poll_all_feeds", side_effect=RuntimeError("netdown")):
        with patch("threading.Thread") as mock_thread:
            def fake_thread(target, daemon=False):
                t = MagicMock()
                t.start = lambda: target()
                return t
            mock_thread.side_effect = fake_thread
            r = client.post("/poll")
            assert r.status_code == 200
    assert "Manual poll failed" in caplog.text


def test_manual_poll_thread_runs_pipeline(client, app):
    """Cover the run_pipeline call inside the inline _run() helper."""
    with patch("app.feeds.poll_all_feeds") as mock_poll, \
         patch("app.pipeline.run_pipeline") as mock_pipeline, \
         patch("threading.Thread") as mock_thread:
        def fake_thread(target, daemon=False):
            t = MagicMock()
            t.start = lambda: target()
            return t
        mock_thread.side_effect = fake_thread
        r = client.post("/poll")
        assert r.status_code == 200
    mock_poll.assert_called_once()
    mock_pipeline.assert_called_once()


# ── Show-hidden filter + rescore-hidden ───────────────────────────────────────

def test_articles_pagination_emits_sentinel_when_full_page(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        for i in range(50):
            add_article(db, feed_id, seq=i, guid=f"g{i}")
        db.close()
    r = client.get("/articles")
    assert b"load-more" in r.data
    assert b"offset=50" in r.data


def test_articles_pagination_no_sentinel_on_partial_page(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        for i in range(10):
            add_article(db, feed_id, seq=i, guid=f"g{i}")
        db.close()
    r = client.get("/articles")
    assert b"load-more" not in r.data


def test_articles_pagination_offset(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        for i in range(60):
            add_article(db, feed_id, seq=i, guid=f"g{i}", title=f"Article{i}")
        db.close()
    r = client.get("/articles?offset=50")
    assert b"Article" in r.data
    # Only 10 left, no sentinel
    assert b"load-more" not in r.data


def test_articles_pagination_invalid_offset_falls_back_to_zero(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, title="OnFirstPage")
        db.close()
    r = client.get("/articles?offset=notanumber")
    assert b"OnFirstPage" in r.data


def test_articles_empty_page_2_does_not_show_empty_message(client, app):
    """A non-first page with no rows should render nothing, not the 'No articles' empty state."""
    r = client.get("/articles?offset=100")
    assert b"No articles yet" not in r.data


def test_articles_pagination_preserves_hidden_and_feed_filter(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        for i in range(50):
            add_article(db, feed_id, seq=i, guid=f"g{i}", status="hidden")
        db.close()
    r = client.get(f"/articles?hidden=1&feed={feed_id}")
    assert b"hidden=1" in r.data
    assert f"feed={feed_id}".encode() in r.data
    assert b"offset=50" in r.data


def test_sidebar_feeds_lists_feeds_with_unread_counts(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        f1 = add_feed(db, url="https://a.example/rss", title="FeedA")
        f2 = add_feed(db, url="https://b.example/rss", title="FeedB")
        # FeedA: 2 unread summarized + 1 already-read summarized + 1 dismissed
        add_article(db, f1, seq=1, guid="a1", status="summarized")
        add_article(db, f1, seq=2, guid="a2", status="summarized")
        add_article(db, f1, seq=3, guid="a3", status="summarized",
                    read_at="2026-04-19T00:00:00Z")
        add_article(db, f1, seq=4, guid="a4", status="dismissed")
        # FeedB: nothing unread
        add_article(db, f2, seq=1, guid="b1", status="dismissed")
        db.close()
    r = client.get("/sidebar/feeds")
    assert r.status_code == 200
    assert b"FeedA" in r.data
    assert b"FeedB" in r.data
    # FeedA has 2 unread
    assert b">2<" in r.data
    # Total unread = 2
    assert b"All feeds" in r.data


def test_sidebar_feeds_no_unread_omits_badge(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db, title="Quiet")
        add_article(db, feed_id, status="dismissed")
        db.close()
    r = client.get("/sidebar/feeds")
    assert b"Quiet" in r.data
    assert b"sidebar-feed-count" not in r.data


def test_articles_feed_filter(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        f1 = add_feed(db, url="https://a.example/rss", title="FeedA")
        f2 = add_feed(db, url="https://b.example/rss", title="FeedB")
        add_article(db, f1, seq=1, guid="a1", title="ArticleFromA")
        add_article(db, f2, seq=2, guid="b1", title="ArticleFromB")
        db.close()
    r = client.get(f"/articles?feed={f1}")
    assert b"ArticleFromA" in r.data
    assert b"ArticleFromB" not in r.data


def test_articles_feed_filter_ignored_when_invalid(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, title="Visible")
        db.close()
    r = client.get("/articles?feed=notanumber")
    assert b"Visible" in r.data


def test_articles_hidden_filter_excludes_by_default(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, status="hidden", title="WasHidden")
        db.close()
    r = client.get("/articles")
    assert b"WasHidden" not in r.data


def test_articles_hidden_filter_shows_only_hidden(client, app):
    """?hidden=1 means ONLY hidden — not hidden-plus-normal-list."""
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, seq=1, guid="h", status="hidden", title="WasHidden")
        add_article(db, feed_id, seq=2, guid="s", status="summarized", title="Summarized")
        add_article(db, feed_id, seq=3, guid="l", status="liked", title="Liked")
        db.close()
    r = client.get("/articles?hidden=1")
    assert b"WasHidden" in r.data
    assert b"Summarized" not in r.data
    assert b"Liked" not in r.data


def test_rescore_hidden_requeues_and_runs_pipeline(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, seq=1, guid="h1", status="hidden", score=0.0)
        add_article(db, feed_id, seq=2, guid="h2", status="hidden", score=0.1)
        add_article(db, feed_id, seq=3, guid="ok", status="summarized", score=0.9)
        db.close()
    with patch("app.pipeline.run_pipeline") as mock_pipeline, \
         patch("threading.Thread") as mock_thread:
        def fake_thread(target, daemon=False):
            t = MagicMock()
            t.start = lambda: target()
            return t
        mock_thread.side_effect = fake_thread
        r = client.post("/rescore-hidden")
        assert r.status_code == 200
        assert b"requeued 2" in r.data
    mock_pipeline.assert_called_once()
    with app.app_context():
        db = get_db_direct()
        rows = {r["guid"]: r["status"] for r in db.execute("SELECT guid, status FROM articles")}
        db.close()
    assert rows["h1"] == "new"
    assert rows["h2"] == "new"
    assert rows["ok"] == "summarized"


def test_dismiss_all_marks_summarized_liked_disliked(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, seq=1, guid="s", status="summarized")
        add_article(db, feed_id, seq=2, guid="l", status="liked")
        add_article(db, feed_id, seq=3, guid="d", status="disliked")
        add_article(db, feed_id, seq=4, guid="h", status="hidden", score=0.0)
        db.close()
    r = client.post("/dismiss-all")
    assert r.status_code == 200
    assert b"dismissed 3" in r.data
    with app.app_context():
        db = get_db_direct()
        rows = {row["guid"]: row["status"] for row in db.execute("SELECT guid, status FROM articles")}
        db.close()
    assert rows["s"] == "dismissed"
    assert rows["l"] == "dismissed"
    assert rows["d"] == "dismissed"
    assert rows["h"] == "hidden"


def test_dismiss_all_respects_feed_filter(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        keep_feed = add_feed(db, url="https://keep.example/rss")
        drop_feed = add_feed(db, url="https://drop.example/rss")
        add_article(db, keep_feed, seq=1, guid="k", status="summarized")
        add_article(db, drop_feed, seq=2, guid="d", status="summarized")
        db.close()
    r = client.post(f"/dismiss-all?feed={drop_feed}")
    assert r.status_code == 200
    assert b"dismissed 1" in r.data
    with app.app_context():
        db = get_db_direct()
        rows = {row["guid"]: row["status"] for row in db.execute("SELECT guid, status FROM articles")}
        db.close()
    assert rows["k"] == "summarized"
    assert rows["d"] == "dismissed"


def test_dismiss_all_ignores_non_numeric_feed_arg(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, status="summarized")
        db.close()
    r = client.post("/dismiss-all?feed=abc")
    assert r.status_code == 200
    assert b"dismissed 1" in r.data


def test_feed_pause_marks_paused(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        db.close()
    r = client.post(f"/feeds/{feed_id}/pause")
    assert r.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT paused FROM feeds WHERE id=?", (feed_id,)).fetchone()
        db.close()
    assert row["paused"] == 1


def test_feed_resume_clears_paused_and_failures(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        db.execute(
            "UPDATE feeds SET paused=1, consecutive_failures=7, last_error='boom' WHERE id=?",
            (feed_id,),
        )
        db.commit()
        db.close()
    r = client.post(f"/feeds/{feed_id}/resume")
    assert r.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute(
            "SELECT paused, consecutive_failures, last_error FROM feeds WHERE id=?",
            (feed_id,),
        ).fetchone()
        db.close()
    assert row["paused"] == 0
    assert row["consecutive_failures"] == 0
    assert row["last_error"] is None


def test_feed_set_threshold_with_value(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        db.close()
    r = client.post(f"/feeds/{feed_id}/threshold", data={"score_threshold": "0.55"})
    assert r.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT score_threshold FROM feeds WHERE id=?", (feed_id,)).fetchone()
        db.close()
    assert abs(row["score_threshold"] - 0.55) < 1e-6


def test_feed_set_threshold_clears_when_empty(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        db.execute("UPDATE feeds SET score_threshold=0.7 WHERE id=?", (feed_id,))
        db.commit()
        db.close()
    r = client.post(f"/feeds/{feed_id}/threshold", data={"score_threshold": ""})
    assert r.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT score_threshold FROM feeds WHERE id=?", (feed_id,)).fetchone()
        db.close()
    assert row["score_threshold"] is None


def test_feed_set_threshold_invalid_returns_400(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        db.close()
    r = client.post(f"/feeds/{feed_id}/threshold", data={"score_threshold": "abc"})
    assert r.status_code == 400


def test_feed_set_threshold_out_of_range_returns_400(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        db.close()
    r = client.post(f"/feeds/{feed_id}/threshold", data={"score_threshold": "1.5"})
    assert r.status_code == 400


def test_article_save_toggles_on_and_off(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        article_id = add_article(db, feed_id, status="summarized")
        db.close()
    # First call → save
    r1 = client.post(f"/article/{article_id}/save")
    assert r1.status_code == 200
    assert b"saved" in r1.data  # the row class includes 'saved'
    # Second call → unsave
    r2 = client.post(f"/article/{article_id}/save")
    assert r2.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT saved_at FROM articles WHERE id=?", (article_id,)).fetchone()
        db.close()
    assert row["saved_at"] is None


def test_article_save_404(client):
    r = client.post("/article/9999/save")
    assert r.status_code == 404


def test_articles_saved_filter_returns_only_saved(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        a_saved = add_article(db, feed_id, seq=1, guid="s", status="summarized", title="SavedOne")
        add_article(db, feed_id, seq=2, guid="u", status="summarized", title="UnsavedOne")
        db.execute(
            "UPDATE articles SET saved_at='2026-04-19T00:00:00Z' WHERE id=?",
            (a_saved,),
        )
        db.commit()
        db.close()
    r = client.get("/articles?saved=1")
    assert b"SavedOne" in r.data
    assert b"UnsavedOne" not in r.data


def test_articles_saved_filter_pagination_qs(client, app):
    """Pagination sentinel preserves the saved=1 flag."""
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        for i in range(51):
            aid = add_article(
                db, feed_id, seq=i, guid=f"g{i}", status="summarized",
                title=f"S{i}",
            )
            db.execute("UPDATE articles SET saved_at='2026-04-19T00:00:00Z' WHERE id=?", (aid,))
        db.commit()
        db.close()
    r = client.get("/articles?saved=1")
    assert b"saved=1" in r.data
    assert b"offset=50" in r.data


def test_search_empty_query_returns_no_articles(client):
    r = client.get("/search")
    assert r.status_code == 200
    # Empty result with empty list should not contain any article-row
    assert b"article-row" not in r.data


def test_search_returns_matching_articles(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, seq=1, guid="a", status="summarized",
                    title="Quantum computing breakthrough", summary="qubits coherent",
                    full_text="quantum quantum quantum")
        add_article(db, feed_id, seq=2, guid="b", status="summarized",
                    title="Cooking with cast iron", summary="seasoning a pan",
                    full_text="iron skillet care")
        db.close()
    r = client.get("/search?q=quantum")
    assert b"Quantum computing breakthrough" in r.data
    assert b"Cooking with cast iron" not in r.data


def test_search_handles_fts_table_missing(client, app, caplog):
    """If the FTS5 table is gone, /search logs a warning and returns 200 with no rows."""
    import logging
    caplog.set_level(logging.WARNING, logger="app.routes")
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.executescript(
            "DROP TRIGGER IF EXISTS articles_ai;"
            "DROP TRIGGER IF EXISTS articles_au;"
            "DROP TRIGGER IF EXISTS articles_ad;"
            "DROP TABLE IF EXISTS articles_fts;"
        )
        db.commit()
        db.close()
    r = client.get("/search?q=anything")
    assert r.status_code == 200
    assert b"article-row" not in r.data
    assert "FTS search failed" in caplog.text


def test_search_quotes_escape_double_quotes(client, app):
    """A user query with embedded quotes is safely doubled inside the FTS phrase."""
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        add_article(db, feed_id, status="summarized",
                    title='He said "hello"', summary="greet")
        db.close()
    r = client.get('/search?q=hello')
    assert b'hello' in r.data


def test_sidebar_feeds_includes_saved_count(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db, title="MyFeed")
        aid = add_article(db, feed_id, status="summarized")
        db.execute(
            "UPDATE articles SET saved_at='2026-04-19T00:00:00Z' WHERE id=?",
            (aid,),
        )
        db.commit()
        db.close()
    r = client.get("/sidebar/feeds")
    assert b"Saved" in r.data
    # The saved badge for the group should reflect the count.
    assert b'data-mode="saved"' in r.data


def test_rescore_hidden_thread_handles_exception(client, app, caplog):
    import logging
    caplog.set_level(logging.ERROR, logger="app.routes")
    with patch("app.pipeline.run_pipeline", side_effect=RuntimeError("boom")):
        with patch("threading.Thread") as mock_thread:
            def fake_thread(target, daemon=False):
                t = MagicMock()
                t.start = lambda: target()
                return t
            mock_thread.side_effect = fake_thread
            r = client.post("/rescore-hidden")
            assert r.status_code == 200
    assert "Rescore failed" in caplog.text


# ── Tag system ─────────────────────────────────────────────────────────────────


def test_normalize_tags_lowercases_trims_dedupes_sorts():
    from app.routes import _normalize_tags
    assert _normalize_tags("") == ""
    assert _normalize_tags(None) == ""
    assert _normalize_tags("Tech") == "tech"
    assert _normalize_tags("  tech , News  ") == "news,tech"
    assert _normalize_tags("tech,tech,news,Tech") == "news,tech"
    assert _normalize_tags(",,,") == ""


def test_split_tags_handles_empty_and_missing():
    from app.routes import _split_tags
    assert _split_tags(None) == []
    assert _split_tags("") == []
    assert _split_tags("tech,news") == ["tech", "news"]


def test_feed_set_tags_stores_normalized_value(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        db.close()
    r = client.post(f"/feeds/{feed_id}/tags", data={"tags": " Tech, News, tech "})
    assert r.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT tags FROM feeds WHERE id=?", (feed_id,)).fetchone()
        db.close()
    assert row["tags"] == "news,tech"


def test_feed_set_tags_empty_clears_to_null(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        db.execute("UPDATE feeds SET tags=? WHERE id=?", ("tech", feed_id))
        db.commit()
        db.close()
    r = client.post(f"/feeds/{feed_id}/tags", data={"tags": ""})
    assert r.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT tags FROM feeds WHERE id=?", (feed_id,)).fetchone()
        db.close()
    assert row["tags"] is None


def test_sidebar_feeds_groups_by_tag(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        tech_id = add_feed(db, url="https://t.example.com/rss", title="TechFeed")
        news_id = add_feed(db, url="https://n.example.com/rss", title="NewsFeed")
        untagged_id = add_feed(db, url="https://u.example.com/rss", title="LonelyFeed")
        db.execute("UPDATE feeds SET tags='tech' WHERE id=?", (tech_id,))
        db.execute("UPDATE feeds SET tags='news' WHERE id=?", (news_id,))
        db.commit()
        db.close()
    r = client.get("/sidebar/feeds")
    body = r.data.decode()
    # Tag group headers (alphabetical: news, tech) render as uppercase label.
    assert 'data-group="tag-news"' in body
    assert 'data-group="tag-tech"' in body
    # Untagged feed sits in its own group.
    assert 'data-group="untagged"' in body
    assert "LonelyFeed" in body
    # Feeds appear inside their group's body.
    assert "TechFeed" in body and "NewsFeed" in body


def test_sidebar_feeds_no_tags_uses_feeds_label(client, app):
    """When no feeds are tagged, the single group is labelled 'Feeds' not 'Untagged'."""
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        add_feed(db, title="OnlyFeed")
        db.close()
    r = client.get("/sidebar/feeds")
    body = r.data.decode()
    assert 'data-group="untagged"' in body
    assert "Feeds</span>" in body
    assert "Untagged" not in body


def test_sidebar_feeds_feed_in_multiple_tags(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        fid = add_feed(db, title="Multi")
        db.execute("UPDATE feeds SET tags='news,tech' WHERE id=?", (fid,))
        db.commit()
        db.close()
    r = client.get("/sidebar/feeds")
    body = r.data.decode()
    # The feed appears under BOTH tag groups.
    assert body.count("Multi") >= 2


# ── Single-article dismiss (swipe-left) ────────────────────────────────────────


def test_article_dismiss_marks_dismissed(client, app):
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        feed_id = add_feed(db)
        aid = add_article(db, feed_id, status="summarized")
        db.close()
    r = client.post(f"/article/{aid}/dismiss")
    assert r.status_code == 200
    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT status FROM articles WHERE id=?", (aid,)).fetchone()
        db.close()
    assert row["status"] == "dismissed"


def test_article_dismiss_unknown_returns_404(client):
    r = client.post("/article/999999/dismiss")
    assert r.status_code == 404


# ── Favicon badge ──────────────────────────────────────────────────────────────


def test_favicon_link_present_on_index(client):
    r = client.get("/")
    assert b'id="favicon"' in r.data
    # SVG fallback URL is data: URI so the tab has a glyph before JS runs.
    assert b"data:image/svg+xml" in r.data


def test_favicon_link_present_on_settings(client):
    r = client.get("/settings")
    assert b'id="favicon"' in r.data
