from app.db import get_db_direct, get_setting, set_setting


def test_settings_get_default(app):
    with app.app_context():
        db = get_db_direct()
        assert get_setting(db, "missing", "fallback") == "fallback"
        assert get_setting(db, "missing") == ""
        db.close()


def test_settings_set_and_get(app):
    with app.app_context():
        db = get_db_direct()
        set_setting(db, "k", "v1")
        db.commit()
        assert get_setting(db, "k") == "v1"
        # upsert
        set_setting(db, "k", "v2")
        db.commit()
        assert get_setting(db, "k") == "v2"
        db.close()


def test_init_db_idempotent(app):
    """Calling init_db twice (start-up + ALTERs) should not raise."""
    from app.db import init_db
    with app.app_context():
        init_db()
        init_db()


def test_close_db_handles_no_connection(app):
    from app.db import close_db
    with app.app_context():
        close_db()  # no connection on g — should be a no-op


def test_init_db_tolerates_fts_creation_failure(monkeypatch, tmp_path):
    """If FTS5 isn't compiled into SQLite, init_db should still complete."""
    import sqlite3
    monkeypatch.setenv("DB_PATH", str(tmp_path / "no-fts.db"))
    from app import db as db_module
    real_open = db_module._open_connection

    class _ConnWrapper:
        def __init__(self, real): self._real = real
        def __getattr__(self, name): return getattr(self._real, name)
        def executescript(self, script):
            if "VIRTUAL TABLE" in script:
                raise sqlite3.OperationalError("no such module: fts5")
            return self._real.executescript(script)

    monkeypatch.setattr(db_module, "_open_connection",
                        lambda: _ConnWrapper(real_open()))
    db_module.init_db()  # should not raise
