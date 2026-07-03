import os
import sqlite3
from pathlib import Path

import flask

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _db_path() -> Path:
    return Path(os.environ.get("DB_PATH", "/app/data/rss.db"))


def get_db() -> sqlite3.Connection:
    """Return request-scoped DB connection. Use inside Flask request context."""
    if "db" not in flask.g:
        flask.g.db = _open_connection()
    return flask.g.db


def get_db_direct() -> sqlite3.Connection:
    """Open a standalone connection. Caller is responsible for closing it."""
    return _open_connection()


def close_db(e: BaseException | None = None) -> None:
    db = flask.g.pop("db", None)
    if db is not None:
        db.close()


_ARTICLE_MIGRATIONS = (
    "ALTER TABLE articles ADD COLUMN thumbnail_url TEXT",
    "ALTER TABLE articles ADD COLUMN read_at TEXT",
    "ALTER TABLE articles ADD COLUMN feed_content TEXT",
    "ALTER TABLE articles ADD COLUMN saved_at TEXT",
)
_FEED_MIGRATIONS = (
    "ALTER TABLE feeds ADD COLUMN last_success_at TEXT",
    "ALTER TABLE feeds ADD COLUMN last_error TEXT",
    "ALTER TABLE feeds ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE feeds ADD COLUMN paused INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE feeds ADD COLUMN score_threshold REAL",
    "ALTER TABLE feeds ADD COLUMN etag TEXT",
    "ALTER TABLE feeds ADD COLUMN last_modified TEXT",
    "ALTER TABLE feeds ADD COLUMN tags TEXT",
)


def init_db() -> None:
    db = get_db_direct()
    db.executescript(SCHEMA_PATH.read_text())
    for stmt in _ARTICLE_MIGRATIONS + _FEED_MIGRATIONS:
        try:
            db.execute(stmt)
        except Exception:
            pass
    # FTS5 virtual table mirrors articles for full-text search.
    try:
        db.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                title, summary, full_text,
                content='articles', content_rowid='id', tokenize='porter unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
                INSERT INTO articles_fts(rowid, title, summary, full_text)
                VALUES (new.id, new.title, new.summary, new.full_text);
            END;
            CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
                INSERT INTO articles_fts(articles_fts, rowid, title, summary, full_text)
                VALUES('delete', old.id, old.title, old.summary, old.full_text);
            END;
            CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
                INSERT INTO articles_fts(articles_fts, rowid, title, summary, full_text)
                VALUES('delete', old.id, old.title, old.summary, old.full_text);
                INSERT INTO articles_fts(rowid, title, summary, full_text)
                VALUES (new.id, new.title, new.summary, new.full_text);
            END;
            """
        )
    except Exception:
        pass
    db.commit()
    db.close()


def get_setting(db, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(db, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _open_connection() -> sqlite3.Connection:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
