from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.feeds import (
    poll_all_feeds,
    strip_html,
    _extract_full_content,
    _extract_thumbnail,
    _parse_date,
    _utcnow,
)


# ── _extract_full_content ──────────────────────────────────────────────────────

def test_extract_full_content_uses_content_value():
    entry = SimpleNamespace(content=[{"value": "<p>Hello</p>"}])
    assert _extract_full_content(entry) == "Hello"


def test_extract_full_content_handles_atom_object_items():
    # feedparser passes FeedParserDict-like objects; subscript via .get works
    entry = SimpleNamespace(content=[{"value": "<div>From Atom</div>"}])
    assert _extract_full_content(entry) == "From Atom"


def test_extract_full_content_falls_back_to_summary():
    entry = SimpleNamespace(content=[])
    entry.get = lambda k, d=None: {"summary": "<p>Just summary</p>"}.get(k, d)
    assert _extract_full_content(entry) == "Just summary"


def test_extract_full_content_returns_none_when_empty():
    entry = SimpleNamespace(content=[])
    entry.get = lambda k, d=None: None
    assert _extract_full_content(entry) is None


# ── _extract_thumbnail ─────────────────────────────────────────────────────────

def test_extract_thumbnail_media_thumbnail():
    entry = SimpleNamespace(media_thumbnail=[{"url": "https://x.example/img.jpg"}])
    entry.get = lambda k, d=None: d
    assert _extract_thumbnail(entry) == "https://x.example/img.jpg"


def test_extract_thumbnail_media_content_image():
    entry = SimpleNamespace(
        media_thumbnail=[],
        media_content=[{"url": "https://x.example/m.png", "medium": "image"}],
    )
    entry.get = lambda k, d=None: d
    assert _extract_thumbnail(entry) == "https://x.example/m.png"


def test_extract_thumbnail_enclosure():
    entry = SimpleNamespace(
        media_thumbnail=[], media_content=[],
        enclosures=[{"type": "image/jpeg", "href": "https://x.example/e.jpg"}],
    )
    entry.get = lambda k, d=None: d
    assert _extract_thumbnail(entry) == "https://x.example/e.jpg"


def test_extract_thumbnail_html_fallback():
    entry = SimpleNamespace(
        media_thumbnail=[], media_content=[], enclosures=[],
        content=[{"value": '<p><img src="https://x.example/in.jpg"></p>'}],
    )
    entry.get = lambda k, d=None: d
    assert _extract_thumbnail(entry) == "https://x.example/in.jpg"


def test_extract_thumbnail_skips_gif():
    entry = SimpleNamespace(
        media_thumbnail=[], media_content=[], enclosures=[],
        content=[{"value": '<img src="https://x.example/spinner.gif">'}],
    )
    entry.get = lambda k, d=None: d
    assert _extract_thumbnail(entry) is None


def test_extract_thumbnail_none_when_nothing_present():
    entry = SimpleNamespace(media_thumbnail=[], media_content=[], enclosures=[])
    entry.get = lambda k, d=None: ""
    assert _extract_thumbnail(entry) is None


# ── _parse_date / _utcnow ──────────────────────────────────────────────────────

def test_parse_date_published():
    entry = SimpleNamespace()
    entry.get = lambda k, d=None: (2026, 4, 19, 12, 0, 0, 0, 0, 0) if k == "published_parsed" else d
    assert _parse_date(entry).startswith("2026-04-19")


def test_parse_date_missing():
    entry = SimpleNamespace()
    entry.get = lambda k, d=None: None
    assert _parse_date(entry) is None


def test_parse_date_invalid_struct_returns_none():
    entry = SimpleNamespace()
    # struct_time-like but with out-of-range month → datetime() raises
    entry.get = lambda k, d=None: (2026, 13, 40, 99, 99, 99, 0, 0, 0) if k == "published_parsed" else d
    assert _parse_date(entry) is None


def test_utcnow_iso_format():
    s = _utcnow()
    assert s.endswith("Z") and "T" in s


# ── poll_all_feeds (integration with feedparser mocked) ────────────────────────

@patch("app.feeds.feedparser.parse")
def test_poll_all_feeds_inserts_new_articles(mock_parse, app):
    fake_entry = {
        "id": "guid-1",
        "link": "https://example.com/article-1",
        "title": "First",
        "summary": "<p>summary</p>",
        "content": [{"value": "<p>full body</p>"}],
        "published_parsed": (2026, 4, 19, 0, 0, 0, 0, 0, 0),
    }
    parsed = MagicMock()
    parsed.bozo = 0
    parsed.entries = [fake_entry]
    parsed.feed = {"title": "ExampleFeed"}
    parsed.status = 200
    parsed.etag = None
    parsed.modified = None
    mock_parse.return_value = parsed

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute("INSERT INTO feeds(url) VALUES(?)", ("https://example.com/rss",))
        db.commit()
        db.close()

    poll_all_feeds(app)

    with app.app_context():
        db = get_db_direct()
        rows = db.execute(
            "SELECT title, raw_snippet, feed_content FROM articles"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "First"
        assert "summary" in rows[0]["raw_snippet"]
        assert "full body" in rows[0]["feed_content"]
        db.close()


@patch("app.feeds.feedparser.parse")
def test_poll_all_feeds_skips_bozo_with_no_entries(mock_parse, app, caplog):
    parsed = MagicMock()
    parsed.bozo = 1
    parsed.bozo_exception = ValueError("bad feed")
    parsed.entries = []
    mock_parse.return_value = parsed

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute("INSERT INTO feeds(url) VALUES(?)", ("https://broken.example/rss",))
        db.commit()
        db.close()

    poll_all_feeds(app)
    assert "parse error" in caplog.text


@patch("app.feeds.feedparser.parse")
def test_poll_all_feeds_handles_unexpected_error(mock_parse, app, caplog):
    mock_parse.side_effect = RuntimeError("network kaboom")

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute("INSERT INTO feeds(url) VALUES(?)", ("https://broken.example/rss",))
        db.commit()
        db.close()

    poll_all_feeds(app)
    assert "unexpected error" in caplog.text


@patch("app.feeds.feedparser.parse")
def test_poll_skips_paused_feed(mock_parse, app):
    """Paused feeds are excluded from the poll loop."""
    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute(
            "INSERT INTO feeds(url, paused) VALUES(?, 1)",
            ("https://paused.example/rss",),
        )
        db.commit()
        db.close()
    poll_all_feeds(app)
    mock_parse.assert_not_called()


@patch("app.feeds.feedparser.parse")
def test_poll_passes_etag_and_modified(mock_parse, app):
    """Stored etag/last_modified are forwarded to feedparser as conditional-GET tokens."""
    parsed = MagicMock()
    parsed.bozo = 0
    parsed.entries = []
    parsed.feed = {"title": "X"}
    parsed.status = 200
    parsed.etag = None
    parsed.modified = None
    mock_parse.return_value = parsed

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute(
            "INSERT INTO feeds(url, etag, last_modified) VALUES(?, ?, ?)",
            ("https://x.example/rss", "W/\"abc\"", "Wed, 01 Jan 2026 00:00:00 GMT"),
        )
        db.commit()
        db.close()

    poll_all_feeds(app)

    call = mock_parse.call_args
    assert call.kwargs["etag"] == "W/\"abc\""
    assert call.kwargs["modified"] == "Wed, 01 Jan 2026 00:00:00 GMT"


@patch("app.feeds.feedparser.parse")
def test_poll_handles_304_not_modified(mock_parse, app):
    """A 304 response updates last_polled/last_success but inserts no articles."""
    parsed = MagicMock()
    parsed.status = 304
    parsed.entries = []
    mock_parse.return_value = parsed

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute("INSERT INTO feeds(url) VALUES(?)", ("https://x.example/rss",))
        db.commit()
        db.close()

    poll_all_feeds(app)

    with app.app_context():
        db = get_db_direct()
        row = db.execute(
            "SELECT last_polled_at, last_success_at, consecutive_failures FROM feeds"
        ).fetchone()
        n = db.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
        db.close()
    assert row["last_polled_at"] is not None
    assert row["last_success_at"] is not None
    assert row["consecutive_failures"] == 0
    assert n == 0


@patch("app.feeds.feedparser.parse")
def test_poll_stores_etag_and_modified_from_response(mock_parse, app):
    parsed = MagicMock()
    parsed.bozo = 0
    parsed.entries = []
    parsed.feed = {"title": "X"}
    parsed.status = 200
    parsed.etag = "new-etag"
    parsed.modified = "Thu, 02 Jan 2026 00:00:00 GMT"
    mock_parse.return_value = parsed

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute("INSERT INTO feeds(url) VALUES(?)", ("https://x.example/rss",))
        db.commit()
        db.close()

    poll_all_feeds(app)

    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT etag, last_modified FROM feeds").fetchone()
        db.close()
    assert row["etag"] == "new-etag"
    assert row["last_modified"] == "Thu, 02 Jan 2026 00:00:00 GMT"


@patch("app.feeds.feedparser.parse")
def test_poll_failure_increments_counter(mock_parse, app):
    """Bozo errors with no entries record an error and bump consecutive_failures."""
    parsed = MagicMock()
    parsed.bozo = 1
    parsed.bozo_exception = ValueError("malformed")
    parsed.entries = []
    mock_parse.return_value = parsed

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute("INSERT INTO feeds(url) VALUES(?)", ("https://broken.example/rss",))
        db.commit()
        db.close()

    poll_all_feeds(app)

    with app.app_context():
        db = get_db_direct()
        row = db.execute(
            "SELECT consecutive_failures, last_error FROM feeds"
        ).fetchone()
        db.close()
    assert row["consecutive_failures"] == 1
    assert "malformed" in row["last_error"]


@patch("app.feeds.feedparser.parse")
def test_poll_auto_pauses_after_threshold(mock_parse, app):
    """After AUTO_PAUSE_AFTER_FAILURES consecutive failures, the feed is paused."""
    from app.feeds import AUTO_PAUSE_AFTER_FAILURES
    parsed = MagicMock()
    parsed.bozo = 1
    parsed.bozo_exception = ValueError("oops")
    parsed.entries = []
    mock_parse.return_value = parsed

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        # Pre-seed at threshold-1 so a single poll trips the pause.
        db.execute(
            "INSERT INTO feeds(url, consecutive_failures) VALUES(?, ?)",
            ("https://x.example/rss", AUTO_PAUSE_AFTER_FAILURES - 1),
        )
        db.commit()
        db.close()

    poll_all_feeds(app)

    with app.app_context():
        db = get_db_direct()
        row = db.execute("SELECT paused, consecutive_failures FROM feeds").fetchone()
        db.close()
    assert row["paused"] == 1
    assert row["consecutive_failures"] == AUTO_PAUSE_AFTER_FAILURES


@patch("app.feeds.feedparser.parse")
def test_poll_all_feeds_dedup_via_unique(mock_parse, app):
    fake_entry = {
        "id": "guid-1",
        "link": "https://example.com/a",
        "title": "Dup",
        "summary": "x",
        "content": [],
    }
    parsed = MagicMock()
    parsed.bozo = 0
    parsed.entries = [fake_entry]
    parsed.feed = {"title": "Ex"}
    parsed.status = 200
    parsed.etag = None
    parsed.modified = None
    mock_parse.return_value = parsed

    from app.db import get_db_direct
    with app.app_context():
        db = get_db_direct()
        db.execute("INSERT INTO feeds(url) VALUES(?)", ("https://example.com/rss",))
        db.commit()
        db.close()

    poll_all_feeds(app)
    poll_all_feeds(app)

    with app.app_context():
        db = get_db_direct()
        n = db.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
        assert n == 1
        db.close()
