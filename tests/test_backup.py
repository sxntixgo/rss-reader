"""Tests for scripts/backup.py — the SQLite online-backup helper."""
import sqlite3
import sys
from pathlib import Path

import pytest

# Add scripts/ to import path.
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import backup as backup_mod  # noqa: E402


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);"
        "INSERT INTO t(v) VALUES ('a'),('b'),('c');"
    )
    conn.commit()
    conn.close()


def test_backup_writes_copy_with_same_data(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    dst_dir = tmp_path / "out"
    dst = backup_mod.backup(src, dst_dir, keep=3)
    assert dst.exists()
    rows = sqlite3.connect(str(dst)).execute("SELECT v FROM t ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["a", "b", "c"]


def test_backup_retention_keeps_only_n_newest(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    dst_dir = tmp_path / "out"
    # Pre-create 5 fake older backup files; their mtime ordering is filename-based
    # because the script uses sorted(glob).
    for i in range(5):
        (dst_dir).mkdir(parents=True, exist_ok=True)
        (dst_dir / f"rss-2020010{i}-000000Z.db").write_bytes(b"x")
    backup_mod.backup(src, dst_dir, keep=3)
    remaining = sorted(dst_dir.glob("rss-*.db"))
    assert len(remaining) == 3
    # The newest one is the just-written file (lexically last).
    assert remaining[-1].name.startswith("rss-")


def test_backup_main_missing_source_returns_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "nope.db"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "out"))
    rc = backup_mod.main([])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_backup_main_writes_path_to_stdout(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src.db"
    _make_db(src)
    monkeypatch.setenv("DB_PATH", str(src))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("KEEP", "2")
    rc = backup_mod.main([])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert Path(out).exists()


def test_backup_main_invalid_keep_falls_back_to_default(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src.db"
    _make_db(src)
    monkeypatch.setenv("DB_PATH", str(src))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("KEEP", "not-a-number")
    assert backup_mod.main([]) == 0


def test_backup_main_handles_sqlite_error(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src.db"
    _make_db(src)
    monkeypatch.setenv("DB_PATH", str(src))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(backup_mod, "backup",
                        lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
    rc = backup_mod.main([])
    assert rc == 2
    assert "disk full" in capsys.readouterr().err
