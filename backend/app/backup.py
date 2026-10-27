"""Konsistente Sicherung der Datenbank im laufenden Betrieb.

Aufruf: python -m app.backup  -> schreibt DATA_DIR/backup/smartarchive.sqlite3
Die Dateien (Originale) sind unveränderlich und werden direkt von restic gesichert.
"""
from __future__ import annotations

import sqlite3
import sys

from . import config


def main() -> int:
    src_path = config.settings.db_path
    target_dir = config.settings.data_dir / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "smartarchive.sqlite3"
    tmp = target.with_suffix(".tmp")
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            print(f"Integritätsprüfung fehlgeschlagen: {ok}", file=sys.stderr)
            return 1
    finally:
        dst.close()
        src.close()
    tmp.replace(target)
    print(f"Datenbank gesichert nach {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
