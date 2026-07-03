"""Shared fixtures for the test suite."""
import os
import sqlite3
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "app" / "schema.sql"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("FLASK_SECRET_KEY", "test")
    # Prevent scheduler from starting in tests
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "")

    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db_conn(app):
    from app.db import get_db_direct
    with app.app_context():
        conn = get_db_direct()
        yield conn
        conn.close()


@pytest.fixture
def memory_db():
    """Stand-alone in-memory DB with the full schema applied."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA_PATH.read_text())
    yield db
    db.close()


def add_feed(db, url: str = "https://example.com/rss", title: str | None = None) -> int:
    cur = db.execute("INSERT INTO feeds(url, title) VALUES(?, ?)", (url, title))
    db.commit()
    return cur.lastrowid


def add_article(db, feed_id: int, **kwargs) -> int:
    defaults = dict(
        guid=f"g-{feed_id}-{kwargs.get('seq', 1)}",
        url="https://example.com/a/1",
        title="Test Title",
        raw_snippet="Snippet",
        feed_content=None,
        full_text=None,
        summary="A summary.",
        score=0.8,
        status="summarized",
        thumbnail_url=None,
        read_at=None,
    )
    defaults.update(kwargs)
    cur = db.execute(
        """INSERT INTO articles(feed_id, guid, url, title, raw_snippet,
                                feed_content, full_text, summary, score,
                                status, thumbnail_url, read_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            feed_id,
            defaults["guid"],
            defaults["url"],
            defaults["title"],
            defaults["raw_snippet"],
            defaults["feed_content"],
            defaults["full_text"],
            defaults["summary"],
            defaults["score"],
            defaults["status"],
            defaults["thumbnail_url"],
            defaults["read_at"],
        ),
    )
    db.commit()
    return cur.lastrowid
