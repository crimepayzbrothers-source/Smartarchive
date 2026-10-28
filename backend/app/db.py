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
        tokenize = '__FTS_TOKENIZE__'
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
    # 2 – Import (Herkunft, Voreinstellung), Auftragspriorität
    """
    ALTER TABLE documents ADD COLUMN source_path TEXT;
    ALTER TABLE documents ADD COLUMN preset TEXT;
    ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX ix_jobs_priority ON jobs(status, priority, id);
    CREATE TABLE import_items (
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        path TEXT NOT NULL,
        size INTEGER,
        sha256 TEXT,
        document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
        status TEXT NOT NULL,              -- imported | duplicate | skipped | failed
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (source, source_id)
    );
    """,
    # 3 – Herkunft einer Ausgabe (user | auto), damit System-Entwürfe erkennbar bleiben
    """
    ALTER TABLE expenses ADD COLUMN origin TEXT NOT NULL DEFAULT 'user';
    """,
    # 4 – Kontoumsätze (CSV-Import), Abgleich mit Belegen
    """
    CREATE TABLE bank_accounts (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        iban TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE bank_imports (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES bank_accounts(id) ON DELETE CASCADE,
        filename TEXT NOT NULL DEFAULT '',
        rows_total INTEGER NOT NULL DEFAULT 0,
        rows_new INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE transactions (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES bank_accounts(id) ON DELETE CASCADE,
        import_id INTEGER REFERENCES bank_imports(id) ON DELETE SET NULL,
        fingerprint TEXT NOT NULL UNIQUE,
        booking_date TEXT NOT NULL,
        value_date TEXT,
        amount_cents INTEGER NOT NULL,              -- negativ = Ausgabe
        currency TEXT NOT NULL DEFAULT 'EUR',
        counterparty TEXT NOT NULL DEFAULT '',
        counterparty_iban TEXT NOT NULL DEFAULT '',
        creditor_id TEXT NOT NULL DEFAULT '',
        purpose TEXT NOT NULL DEFAULT '',
        booking_kind TEXT NOT NULL DEFAULT '',
        party_key TEXT NOT NULL DEFAULT '',         -- Wiedererkennung: Gläubiger-ID, IBAN oder Name
        status TEXT NOT NULL DEFAULT 'open',        -- open | auto | done
        kind TEXT,                                  -- ausgabe | einnahme | ignoriert
        property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
        unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
        category_key TEXT,
        expense_id INTEGER REFERENCES expenses(id) ON DELETE SET NULL,
        document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
        source TEXT,                                -- beleg | history | ai | keyword | user
        confidence REAL,
        reason TEXT NOT NULL DEFAULT '',
        suggestion TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        decided_at TEXT
    );
    CREATE INDEX ix_tx_status ON transactions(status, booking_date);
    CREATE INDEX ix_tx_party ON transactions(party_key, status);
    """,
    # 5 – Rechnung bewusst ohne Ausgabe (z. B. Angebot, Doppel, privat bezahlt)
    """
    ALTER TABLE documents ADD COLUMN no_expense INTEGER NOT NULL DEFAULT 0;
    """,
    # 6 – Zahlungsdatum angenommen (= Rechnungsdatum), bis Sie oder ein Kontoumsatz es bestätigen
    """
    ALTER TABLE expenses ADD COLUMN paid_assumed INTEGER NOT NULL DEFAULT 0;
    """,
    # 7 – Sicherheit des erkannten Belegdatums (sure | unsure) und gefundene Kandidaten zur Auswahl
    """
    ALTER TABLE documents ADD COLUMN date_status TEXT;
    ALTER TABLE documents ADD COLUMN date_candidates TEXT NOT NULL DEFAULT '[]';
    """,
    # 8 – mögliche inhaltliche Dublette (gleiche Rechnung erneut hochgeladen)
    """
    ALTER TABLE documents ADD COLUMN duplicate_of INTEGER REFERENCES documents(id) ON DELETE SET NULL;
    ALTER TABLE documents ADD COLUMN duplicate_reason TEXT;
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


# Bevorzugter Tokenizer zuerst. Ältere SQLite-Versionen (z. B. 3.40 in Debian Bookworm)
# kennen „remove_diacritics“ beim Trigramm-Tokenizer nicht, sehr alte gar keinen Trigramm-Tokenizer.
FTS_TOKENIZERS = ("trigram remove_diacritics 1", "trigram", "unicode61 remove_diacritics 2")


def fts_tokenizer() -> str:
    probe = sqlite3.connect(":memory:")
    try:
        for tok in FTS_TOKENIZERS:
            try:
                probe.execute(f"CREATE VIRTUAL TABLE t USING fts5(x, tokenize = '{tok}')")
                return tok
            except sqlite3.OperationalError:
                continue
    finally:
        probe.close()
    raise RuntimeError("SQLite ohne FTS5-Unterstützung")


def migrate(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tokenizer = fts_tokenizer()
    for idx, script in enumerate(MIGRATIONS[version:], start=version + 1):
        script = script.replace("__FTS_TOKENIZE__", tokenizer)
        try:
            conn.executescript("BEGIN;" + script + f"; PRAGMA user_version = {idx}; COMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
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
