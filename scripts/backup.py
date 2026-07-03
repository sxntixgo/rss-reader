#!/usr/bin/env python3
"""Online SQLite backup using the `.backup` API (safe with WAL + open writers).

Env vars:
  DB_PATH     source database (default /app/data/rss.db)
  BACKUP_DIR  destination directory (default <DB_PATH dir>/backups)
  KEEP        retention count, oldest files deleted beyond this (default 7)

Exit code 0 on success, non-zero on error. Writes the final path to stdout.
Recommended: wire into cron, e.g.
    0 4 * * * /usr/bin/python3 /app/scripts/backup.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path


def backup(src: Path, dst_dir: Path, keep: int) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    dst = dst_dir / f"rss-{ts}.db"
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    # Retention: keep newest `keep`, delete older rss-*.db files only.
    existing = sorted(dst_dir.glob("rss-*.db"))
    for old in existing[:-keep] if keep > 0 else []:
        old.unlink()
    return dst


def main(argv: list[str]) -> int:
    src = Path(os.environ.get("DB_PATH", "/app/data/rss.db"))
    if not src.exists():
        print(f"ERROR: source DB not found: {src}", file=sys.stderr)
        return 1
    dst_dir = Path(os.environ.get("BACKUP_DIR", str(src.parent / "backups")))
    try:
        keep = int(os.environ.get("KEEP", "7"))
    except ValueError:
        keep = 7
    try:
        dst = backup(src, dst_dir, keep)
    except sqlite3.Error as exc:
        print(f"ERROR: backup failed: {exc}", file=sys.stderr)
        return 2
    print(dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
