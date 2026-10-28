"""Hintergrund-Worker: verarbeitet Uploads (OCR, Einordnung).

Start: python -m app.worker
"""
from __future__ import annotations

import logging
import signal
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from . import documents
from .db import audit, connect, migrate, now, tx
from .seed import seed

log = logging.getLogger("smartarchive.worker")
MAX_ATTEMPTS = 3
_stop = False


def _later(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def recover_stale(conn: sqlite3.Connection) -> None:
    """Jobs, die bei einem Absturz auf „running“ hängen geblieben sind, neu einplanen."""
    conn.execute("UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'", (now(),))


def claim(conn: sqlite3.Connection) -> dict | None:
    with tx(conn):
        r = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' AND run_after <= ? ORDER BY priority, id LIMIT 1", (now(),)
        ).fetchone()
        if not r:
            return None
        conn.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?",
                     (now(), r["id"]))
        return dict(r) | {"attempts": r["attempts"] + 1}


def run_job(conn: sqlite3.Connection, job: dict) -> None:
    doc_id = job["document_id"]
    try:
        if job["kind"] == "process":
            status = documents.process(conn, doc_id)
        elif job["kind"] == "reclassify":
            status = documents.reclassify(conn, doc_id)
        else:
            raise RuntimeError(f"Unbekannter Auftrag {job['kind']}")
        conn.execute("UPDATE jobs SET status = 'done', error = NULL, updated_at = ? WHERE id = ?", (now(), job["id"]))
        log.info("Dokument %s verarbeitet: %s", doc_id, status)
    except Exception as exc:  # noqa: BLE001 – jeder Fehler landet in „Nacharbeit“
        log.exception("Verarbeitung von Dokument %s fehlgeschlagen", doc_id)
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        msg = str(exc)[:500] or exc.__class__.__name__
        if job["attempts"] >= MAX_ATTEMPTS:
            with tx(conn):
                conn.execute("UPDATE jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                             (msg, now(), job["id"]))
                conn.execute("UPDATE documents SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                             (msg, now(), doc_id))
                audit(conn, "processing_failed", "document", doc_id, {"error": msg}, actor="system")
        else:
            conn.execute("UPDATE jobs SET status = 'queued', error = ?, run_after = ?, updated_at = ? WHERE id = ?",
                         (msg, _later(job["attempts"]), now(), job["id"]))


def run_pending(conn: sqlite3.Connection, limit: int = 100) -> int:
    done = 0
    while done < limit:
        job = claim(conn)
        if not job:
            break
        run_job(conn, job)
        done += 1
    return done


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def _handle(*_: object) -> None:
        global _stop
        _stop = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    conn = connect()
    migrate(conn)
    seed(conn)
    recover_stale(conn)
    log.info("Worker gestartet")
    while not _stop:
        if run_pending(conn, limit=5) == 0:
            time.sleep(2)
    conn.close()


if __name__ == "__main__":
    main()
