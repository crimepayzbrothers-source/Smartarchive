"""Dokumente: Aufnahme, Verarbeitung, Suchindex, Dateinamen."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from . import classify, ocr, storage
from .db import audit, now, row, rows, tx

log = logging.getLogger("smartarchive.documents")

ACCEPTED = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/tiff"}


class IngestError(ValueError):
    pass


def _stem(name: str) -> str:
    return Path(name or "Dokument").stem[:120] or "Dokument"


def find_duplicate(conn: sqlite3.Connection, sha: str) -> dict | None:
    return row(conn.execute(
        "SELECT d.id, d.title FROM files f JOIN documents d ON d.id = f.document_id "
        "WHERE f.sha256 = ? AND f.role = 'original' AND d.deleted_at IS NULL LIMIT 1", (sha,)))


def ingest(conn: sqlite3.Connection, uploads: list[tuple[str, bytes]], upload_area: str = "auto",
           combine_images: bool = False) -> dict[str, list]:
    """Nimmt Dateien entgegen. Bilder können zu einem Dokument zusammengefasst werden."""
    if upload_area not in ("auto", "privat", "vermietung"):
        raise IngestError("Unbekannter Bereich")
    created: list[int] = []
    duplicates: list[dict] = []
    rejected: list[dict] = []

    prepared = []
    for name, data in uploads:
        mime = storage.sniff_mime(data)
        if mime == "image/heic":
            rejected.append({"name": name, "reason": "HEIC wird nicht unterstützt. Bitte als JPEG aufnehmen."})
            continue
        if mime not in ACCEPTED:
            rejected.append({"name": name, "reason": "Nur PDF und Bilder (JPEG, PNG, WebP, TIFF) sind erlaubt."})
            continue
        prepared.append((name, data, mime))

    groups: list[list[tuple[str, bytes, str]]]
    images = [p for p in prepared if p[2] != "application/pdf"]
    if combine_images and len(images) > 1:
        groups = [[p] for p in prepared if p[2] == "application/pdf"] + [images]
    else:
        groups = [[p] for p in prepared]

    for group in groups:
        shas = [storage.sha256_bytes(d) for _, d, _ in group]
        if len(group) == 1:
            dup = find_duplicate(conn, shas[0])
            if dup:
                duplicates.append({"name": group[0][0], "document_id": dup["id"], "title": dup["title"]})
                continue
        ts = now()
        with tx(conn):
            title = _stem(group[0][0]) if len(group) == 1 else f"Scan {ts[:10]} ({len(group)} Seiten)"
            doc_id = conn.execute(
                "INSERT INTO documents(title, original_name, upload_area, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (title, group[0][0], upload_area, "processing", ts, ts),
            ).lastrowid
            for pos, (name, data, mime) in enumerate(group):
                sha, rel = storage.store_original(data, mime)
                conn.execute(
                    "INSERT INTO files(document_id, role, sha256, mime, size, path, original_name, position, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (doc_id, "original", sha, mime, len(data), rel, name, pos, ts),
                )
            conn.execute("INSERT INTO document_text(document_id, content) VALUES(?, '')", (doc_id,))
            enqueue(conn, doc_id, "process")
            audit(conn, "uploaded", "document", doc_id, {"files": [g[0] for g in group], "area": upload_area})
        created.append(doc_id)
    return {"created": created, "duplicates": duplicates, "rejected": rejected}


def enqueue(conn: sqlite3.Connection, document_id: int, kind: str) -> None:
    ts = now()
    conn.execute(
        "INSERT INTO jobs(document_id, kind, status, run_after, created_at, updated_at) VALUES(?,?,?,?,?,?)",
        (document_id, kind, "queued", ts, ts, ts),
    )


def files_of(conn: sqlite3.Connection, document_id: int, role: str | None = None) -> list[dict]:
    if role:
        return rows(conn.execute("SELECT * FROM files WHERE document_id = ? AND role = ? ORDER BY position, id",
                                 (document_id, role)))
    return rows(conn.execute("SELECT * FROM files WHERE document_id = ? ORDER BY role, position, id", (document_id,)))


def best_pdf(conn: sqlite3.Connection, document_id: int) -> dict | None:
    for role in ("searchable", "pdf", "original"):
        for f in files_of(conn, document_id, role):
            if f["mime"] == "application/pdf":
                return f
    return None


def _replace_derived(conn: sqlite3.Connection, document_id: int, role: str, src: Path, name: str, mime: str) -> None:
    sha, rel, size = storage.store_derived(document_id, name, src)
    conn.execute("DELETE FROM files WHERE document_id = ? AND role = ?", (document_id, role))
    conn.execute(
        "INSERT INTO files(document_id, role, sha256, mime, size, path, original_name, position, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (document_id, role, sha, mime, size, rel, name, 0, now()),
    )


def extract_text(conn: sqlite3.Connection, document_id: int) -> None:
    """Schritt 1: PDF bilden, Text gewinnen (digital oder OCR), Vorschaubild erzeugen."""
    originals = files_of(conn, document_id, "original")
    if not originals:
        raise RuntimeError("Keine Originaldatei vorhanden")
    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir)
        if len(originals) == 1 and originals[0]["mime"] == "application/pdf":
            src = storage.abs_path(originals[0]["path"])
        else:
            pdf_bytes = ocr.images_to_pdf([storage.abs_path(f["path"]).read_bytes() for f in originals])
            src = work / "document.pdf"
            src.write_bytes(pdf_bytes)
            with tx(conn):
                _replace_derived(conn, document_id, "pdf", src, "document.pdf", "application/pdf")

        n_pages = ocr.page_count(src)
        pages = ocr.extract_text_pages(src)
        if ocr.has_text_layer(pages, n_pages):
            source = "digital"
            view_pdf = src
        else:
            searchable, pages = ocr.ocr_pdf(src, work)
            source = "ocr" if searchable and any(p.strip() for p in pages) else "none"
            view_pdf = src
            if searchable:
                with tx(conn):
                    _replace_derived(conn, document_id, "searchable", searchable, "searchable.pdf", "application/pdf")
                view_pdf = searchable
                n_pages = ocr.page_count(searchable) or n_pages

        thumb = work / "thumb.png"
        try:
            ocr.render_page(view_pdf, 1, 40, thumb)
        except Exception as exc:  # noqa: BLE001 – Vorschau ist optional
            log.warning("Vorschaubild fehlgeschlagen: %s", exc)
            thumb = None

        content = "\n\n".join(p.strip() for p in pages if p.strip())
        with tx(conn):
            if thumb and thumb.exists():
                _replace_derived(conn, document_id, "thumb", thumb, "thumb.png", "image/png")
            conn.execute(
                "UPDATE document_text SET content = ?, pages_json = ? WHERE document_id = ?",
                (content, json.dumps(pages, ensure_ascii=False), document_id),
            )
            conn.execute("UPDATE documents SET page_count = ?, text_source = ?, updated_at = ? WHERE id = ?",
                         (n_pages, source, now(), document_id))
            index_document(conn, document_id)


def process(conn: sqlite3.Connection, document_id: int) -> str:
    extract_text(conn, document_id)
    cand = classify.analyze(conn, document_id)
    with tx(conn):
        status = classify.decide(conn, document_id, cand)
        index_document(conn, document_id)
    return status


def reclassify(conn: sqlite3.Connection, document_id: int) -> str:
    cand = classify.analyze(conn, document_id)
    with tx(conn):
        status = classify.decide(conn, document_id, cand)
        index_document(conn, document_id)
    return status


def index_document(conn: sqlite3.Connection, document_id: int) -> None:
    d = row(conn.execute(
        "SELECT d.title, d.doc_type, d.original_name, c.name AS corr, cat.name AS cat, p.name AS prop, p.address AS addr, "
        "t.content FROM documents d LEFT JOIN correspondents c ON c.id = d.correspondent_id "
        "LEFT JOIN categories cat ON cat.id = d.category_id LEFT JOIN properties p ON p.id = d.property_id "
        "LEFT JOIN document_text t ON t.document_id = d.id WHERE d.id = ?", (document_id,)))
    if not d:
        return
    tags = [r[0] for r in conn.execute(
        "SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id = t.id WHERE dt.document_id = ?", (document_id,))]
    meta = " ".join(filter(None, [d["corr"], d["cat"], d["prop"], d["addr"], d["doc_type"], d["original_name"], *tags]))
    conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (document_id,))
    conn.execute("INSERT INTO docs_fts(rowid, title, content, meta) VALUES(?,?,?,?)",
                 (document_id, d["title"] or "", d["content"] or "", meta))


def _slug(value: str, limit: int = 40) -> str:
    value = value.replace("ß", "ss")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return value[:limit].strip("-")


def display_filename(conn: sqlite3.Connection, document_id: int) -> str:
    """Sprechender Dateiname, z. B. 2026-10-27_Heizungswartung_Hauptstrasse-15_245EUR.pdf"""
    d = row(conn.execute(
        "SELECT d.*, p.name AS prop_name, p.address AS prop_address FROM documents d "
        "LEFT JOIN properties p ON p.id = d.property_id WHERE d.id = ?", (document_id,)))
    if not d:
        return "dokument.pdf"
    parts = [d["doc_date"] or d["created_at"][:10], _slug(d["title"] or "Dokument")]
    if d["prop_name"] or d["prop_address"]:
        street = (d["prop_address"] or "").split(",")[0] or d["prop_name"]
        parts.append(_slug(street, 30))
    total = conn.execute("SELECT SUM(amount_cents) FROM expenses WHERE document_id = ?", (document_id,)).fetchone()[0]
    if total:
        euros = total / 100
        parts.append((f"{euros:.0f}" if total % 100 == 0 else f"{euros:.2f}".replace(".", ",")) + "EUR")
    return "_".join(p for p in parts if p) + ".pdf"


def apply_fields(conn: sqlite3.Connection, document_id: int, fields: dict[str, Any], actor: str = "user") -> dict:
    """Ändert Metadaten, protokolliert vorher/nachher und merkt sich Korrekturen."""
    allowed = {"title", "doc_type", "doc_date", "area_id", "category_id", "correspondent_id", "property_id",
               "unit_id", "favorite"}
    current = row(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)))
    if not current:
        raise KeyError(document_id)
    changes = {k: v for k, v in fields.items() if k in allowed and current.get(k) != v}
    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE documents SET {sets}, updated_at = ? WHERE id = ?", (*changes.values(), now(), document_id))
        audit(conn, "updated", "document", document_id,
              {k: {"vorher": current.get(k), "nachher": v} for k, v in changes.items()}, actor=actor)
    return changes
