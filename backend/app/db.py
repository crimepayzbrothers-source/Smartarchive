"""SQLite-Datenbank: Verbindung und versionierte Migrationen.

Eine Datei, kein eigener Datenbankserver: Für einen Einzelnutzer ist das
robuster und leichter zu sichern als PostgreSQL. Die Volltextsuche läuft über
FTS5 mit Trigramm-Tokenizer, damit auch Wortteile wie „wartung“ in
„Heizungswartung“ gefunden werden.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

MIGRATIONS: list[str] = [
    # 1 – Grundschema
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        totp_secret TEXT,
        totp_enabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,               -- SHA-256 des Cookie-Tokens
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        user_agent TEXT
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE areas (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,          -- privat | vermietung
        name TEXT NOT NULL,
        ai_enabled INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE categories (
        id INTEGER PRIMARY KEY,
        area_id INTEGER NOT NULL REFERENCES areas(id),
        parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        keywords TEXT NOT NULL DEFAULT '',  -- kommagetrennt, für lokale Erkennung
        ai_enabled INTEGER NOT NULL DEFAULT 1,
        sort INTEGER NOT NULL DEFAULT 0,
        UNIQUE(area_id, parent_id, name)
    );
    CREATE TABLE correspondents (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        aliases TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE properties (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        address TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT '',
        living_area REAL,
        ownership_share TEXT NOT NULL DEFAULT '',
        rental_status TEXT NOT NULL DEFAULT 'vermietet',
        acquired_on TEXT,
        acquisition_cost_cents INTEGER,
        keywords TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE units (
        id INTEGER PRIMARY KEY,
        property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        living_area REAL,
        rental_status TEXT NOT NULL DEFAULT 'vermietet'
    );
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY,
        area_id INTEGER REFERENCES areas(id),
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        correspondent_id INTEGER REFERENCES correspondents(id) ON DELETE SET NULL,
        property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
        unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
        title TEXT NOT NULL DEFAULT '',
        doc_type TEXT NOT NULL DEFAULT '',
        doc_date TEXT,
        original_name TEXT NOT NULL DEFAULT '',
        upload_area TEXT NOT NULL DEFAULT 'auto',   -- auto | privat | vermietung
        status TEXT NOT NULL DEFAULT 'processing',  -- processing | inbox | filed | error
        filed_by TEXT,                              -- user | rule | history | ai | keyword
        filed_reason TEXT NOT NULL DEFAULT '',
        confirmed INTEGER NOT NULL DEFAULT 0,       -- 1 = vom Nutzer bestätigt
        confidence REAL,
        favorite INTEGER NOT NULL DEFAULT 0,
        page_count INTEGER,
        text_source TEXT,                           -- digital | ocr | none
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        filed_at TEXT,
        deleted_at TEXT
    );
    CREATE INDEX ix_documents_area ON documents(area_id, status);
    CREATE INDEX ix_documents_category ON documents(category_id);
    CREATE INDEX ix_documents_property ON documents(property_id);
    CREATE TABLE files (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        role TEXT NOT NULL,                 -- original | pdf | searchable | thumb
        sha256 TEXT NOT NULL,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        path TEXT NOT NULL,
        original_name TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX ix_files_doc ON files(document_id, role);
    CREATE INDEX ix_files_sha ON files(sha256, role);
    CREATE TABLE document_text (
        document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
        content TEXT NOT NULL DEFAULT '',
        pages_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE VIRTUAL TABLE docs_fts USING fts5(
        title, content, meta,
        tokenize = 'trigram remove_diacritics 1'
    );
    CREATE TABLE tags (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE
    );
    CREATE TABLE document_tags (
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (document_id, tag_id)
    );
    CREATE TABLE suggestions (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        source TEXT NOT NULL,               -- rule | history | ai | keyword
        payload TEXT NOT NULL,              -- JSON mit vorgeschlagenen Feldern
        confidence REAL NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        evidence TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open', -- open | applied | accepted | rejected | undone
        created_at TEXT NOT NULL,
        decided_at TEXT
    );
    CREATE INDEX ix_suggestions_doc ON suggestions(document_id, status);
    CREATE TABLE expenses (
        id INTEGER PRIMARY KEY,
        document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
        property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
        unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
        invoice_date TEXT,
        paid_on TEXT,
        amount_cents INTEGER NOT NULL,
        payee TEXT NOT NULL DEFAULT '',
        category_key TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        page INTEGER,
        status TEXT NOT NULL DEFAULT 'entwurf', -- entwurf | bestaetigt | ungeklaert
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX ix_expenses_prop ON expenses(property_id, paid_on);
    CREATE INDEX ix_expenses_doc ON expenses(document_id);
    CREATE TABLE rules (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        match_field TEXT NOT NULL DEFAULT 'text',  -- text | correspondent | filename
        pattern TEXT NOT NULL,
        area_id INTEGER REFERENCES areas(id),
        category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
        property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
        correspondent_id INTEGER REFERENCES correspondents(id) ON DELETE SET NULL,
        doc_type TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        origin TEXT NOT NULL DEFAULT 'user',       -- user | learned
        hits INTEGER NOT NULL DEFAULT 0,
        sort INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE corrections (
        id INTEGER PRIMARY KEY,
        document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
        correspondent_id INTEGER,
        suggested_category_id INTEGER,
        chosen_category_id INTEGER,
        chosen_property_id INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        run_after TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX ix_jobs_status ON jobs(status, run_after);
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        entity TEXT NOT NULL,
        entity_id INTEGER,
        details TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX ix_audit_entity ON audit_log(entity, entity_id);
    """,
]


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    path = config.settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explizite Transaktion (die Verbindung läuft sonst im Autocommit)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for idx, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript("BEGIN;" + script + f"; PRAGMA user_version = {idx}; COMMIT;")
    return conn.execute("PRAGMA user_version").fetchone()[0]


def rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def row(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    r = cur.fetchone()
    return dict(r) if r else None


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    r = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def audit(conn: sqlite3.Connection, action: str, entity: str, entity_id: int | None,
          details: dict[str, Any] | None = None, actor: str = "user") -> None:
    conn.execute(
        "INSERT INTO audit_log(ts, actor, action, entity, entity_id, details) VALUES(?,?,?,?,?,?)",
        (now(), actor, action, entity, entity_id, json.dumps(details or {}, ensure_ascii=False, default=str)),
    )
