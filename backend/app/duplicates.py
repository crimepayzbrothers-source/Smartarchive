"""Inhaltliche Dubletten: dieselbe Rechnung zweimal hochgeladen (z. B. neu fotografiert, als PDF und als Foto).

Byte-gleiche Dateien fängt schon der Upload ab. Hier geht es um gleichen Inhalt bei anderer Datei:
- gleiche Rechnungsnummer (und Betrag oder Text passen),
- gleicher Endbetrag und gleiches Datum (und der Text ist ähnlich),
- nahezu gleicher Text bei gleichem Datum.
Ein Treffer setzt documents.duplicate_of; bis zur Entscheidung wird nichts abgelegt und keine Ausgabe gezählt.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import extract, invoices
from .db import audit, now, row, rows


def _shingles(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[a-zäöüß0-9]+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a[:6000]), _shingles(b[:6000])
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _norm_no(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find(conn: sqlite3.Connection, document_id: int) -> dict[str, Any] | None:
    """Sucht ein vorhandenes Dokument mit gleichem Inhalt. Liefert {id, reason} oder None."""
    me = row(conn.execute("SELECT d.id, t.content FROM documents d JOIN document_text t ON t.document_id = d.id WHERE d.id = ?",
                          (document_id,)))
    text = (me or {}).get("content") or ""
    if len(text.strip()) < 30:
        return None
    number = invoices.detect_invoice_number(text)
    amount, _ = extract.detect_amount(text)
    date_ = extract.date_analysis(text)["date"]

    others = "SELECT d.id, d.title, d.doc_date, t.content FROM documents d JOIN document_text t ON t.document_id = d.id " \
             "WHERE d.id != ? AND d.deleted_at IS NULL AND d.duplicate_of IS NULL AND d.status != 'processing' "

    best: tuple[float, dict[str, Any]] | None = None

    def consider(o: dict[str, Any], reasons: list[str], score: float) -> None:
        nonlocal best
        if best is None or score > best[0]:
            best = (score, {"id": o["id"], "reason": ", ".join(reasons)})

    # 1. gleiche Rechnungsnummer
    if number and len(_norm_no(number)) >= 4:
        for o in rows(conn.execute(others + "AND t.content LIKE ?", (document_id, f"%{number}%"))):
            if invoices.detect_invoice_number(o["content"] or "") is None:
                continue
            if _norm_no(invoices.detect_invoice_number(o["content"]) or "") != _norm_no(number):
                continue
            o_amount, _ = extract.detect_amount(o["content"] or "")
            sim = similarity(text, o["content"] or "")
            if (amount and o_amount == amount) or sim >= 0.35:
                reasons = [f"gleiche Rechnungsnummer {number}"] + (["gleicher Betrag"] if amount and o_amount == amount else [])
                consider(o, reasons, 3 + sim)

    # 2. gleicher Betrag und gleiches Datum
    if amount and date_:
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT d.id FROM documents d LEFT JOIN expenses e ON e.document_id = d.id "
            "WHERE d.id != ? AND d.deleted_at IS NULL AND d.duplicate_of IS NULL AND d.status != 'processing' "
            "AND (d.doc_date = ? OR e.invoice_date = ?) AND (e.amount_cents = ? OR e.id IS NULL)",
            (document_id, date_, date_, amount))]
        for oid in ids:
            o = row(conn.execute(others + "AND d.id = ?", (document_id, oid)))
            if not o:
                continue
            o_amount, _ = extract.detect_amount(o["content"] or "")
            if o_amount != amount:
                continue
            sim = similarity(text, o["content"] or "")
            if sim >= 0.3:
                consider(o, ["gleicher Betrag", "gleiches Datum", f"Text zu {round(sim * 100)} % gleich"], 2 + sim)

    # 3. nahezu gleicher Text bei gleichem Datum (z. B. Briefe ohne Betrag)
    if date_ and best is None:
        for o in rows(conn.execute(others + "AND d.doc_date = ?", (document_id, date_))):
            sim = similarity(text, o["content"] or "")
            if sim >= 0.8:
                consider(o, [f"Text zu {round(sim * 100)} % gleich", "gleiches Datum"], 1 + sim)

    return best[1] if best else None


def mark(conn: sqlite3.Connection, document_id: int, dup: dict[str, Any]) -> None:
    """Als mögliche Dublette markieren: im Eingang lassen, keine Ausgabe zählen."""
    conn.execute("UPDATE documents SET duplicate_of = ?, duplicate_reason = ?, status = 'inbox', filed_by = NULL, filed_at = NULL, "
                 "confirmed = 0, updated_at = ? WHERE id = ?", (dup["id"], dup["reason"], now(), document_id))
    conn.execute("DELETE FROM expenses WHERE document_id = ? AND origin = 'auto'", (document_id,))
    audit(conn, "duplicate_suspected", "document", document_id, {"vorhanden": dup["id"], "grund": dup["reason"]}, actor="system")


def resolve(conn: sqlite3.Connection, document_id: int, action: str) -> int:
    """Entscheidung: replace (vorhandenes überschreiben) | discard (Upload verwerfen) | keep (kein Duplikat).

    Liefert die ID des Dokuments, das danach gilt.
    """
    d = row(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)))
    if not d or not d["duplicate_of"]:
        raise ValueError("Dieses Dokument ist nicht als mögliches Duplikat markiert.")
    target = d["duplicate_of"]
    ts = now()
    if action == "keep":
        conn.execute("UPDATE documents SET duplicate_of = NULL, duplicate_reason = NULL, updated_at = ? WHERE id = ?",
                     (ts, document_id))
        audit(conn, "duplicate_kept", "document", document_id, {"vorhanden": target})
        return document_id
    if action == "discard":
        conn.execute("DELETE FROM expenses WHERE document_id = ? AND origin = 'auto'", (document_id,))
        conn.execute("UPDATE documents SET deleted_at = ?, updated_at = ? WHERE id = ?", (ts, ts, document_id))
        audit(conn, "duplicate_discarded", "document", document_id, {"vorhanden": target})
        return target
    if action == "replace":
        # Dateien und Text des neuen Uploads gehen an das vorhandene Dokument; dessen Angaben, Ordner und
        # Ausgaben bleiben. Die bisherigen Originale bleiben als „ersetzt“ erhalten (nichts geht verloren).
        conn.execute("UPDATE files SET role = 'replaced' WHERE document_id = ? AND role IN ('original', 'capture')", (target,))
        conn.execute("DELETE FROM files WHERE document_id = ? AND role IN ('pdf', 'searchable', 'thumb')", (target,))
        conn.execute("UPDATE files SET document_id = ? WHERE document_id = ?", (target, document_id))
        new_text = row(conn.execute("SELECT content, pages_json FROM document_text WHERE document_id = ?", (document_id,)))
        if new_text:
            conn.execute("UPDATE document_text SET content = ?, pages_json = ? WHERE document_id = ?",
                         (new_text["content"], new_text["pages_json"], target))
        conn.execute("UPDATE documents SET page_count = ?, text_source = ?, updated_at = ? WHERE id = ?",
                     (d["page_count"], d["text_source"], ts, target))
        conn.execute("DELETE FROM expenses WHERE document_id = ? AND origin = 'auto'", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        audit(conn, "replaced_by_upload", "document", target, {"ersetzt_durch_upload": document_id,
                                                                "datei": d["original_name"]})
        return target
    raise ValueError("Unbekannte Aktion.")


def info(conn: sqlite3.Connection, document_id: int) -> dict[str, Any] | None:
    d = row(conn.execute("SELECT duplicate_of, duplicate_reason FROM documents WHERE id = ?", (document_id,)))
    if not d or not d["duplicate_of"]:
        return None
    o = row(conn.execute(
        "SELECT d.id, d.title, d.doc_date, d.created_at, p.name AS property_name, "
        "(SELECT SUM(amount_cents) FROM expenses e WHERE e.document_id = d.id) AS amount_cents "
        "FROM documents d LEFT JOIN properties p ON p.id = d.property_id WHERE d.id = ?", (d["duplicate_of"],)))
    return {"reason": d["duplicate_reason"], "existing": o}


__all__ = ["find", "mark", "resolve", "info", "similarity", "json"]
