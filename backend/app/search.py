"""Volltextsuche über FTS5 (Trigramme: findet auch Wortteile, ab 3 Zeichen)."""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from .db import rows

_TOKEN = re.compile(r"[\wäöüÄÖÜß.\-/]+", re.UNICODE)


def build_match(query: str) -> str | None:
    terms = [t.strip(".-/") for t in _TOKEN.findall(query or "")]
    terms = [t for t in terms if len(t) >= 3]
    if not terms:
        return None
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)


def short_terms(query: str) -> list[str]:
    return [t for t in _TOKEN.findall(query or "") if 0 < len(t) < 3]


def search(conn: sqlite3.Connection, query: str, *, area: str | None = None, category_id: int | None = None,
           property_id: int | None = None, year: int | None = None, tag: str | None = None,
           date_from: str | None = None, date_to: str | None = None,
           limit: int = 50, offset: int = 0) -> dict[str, Any]:
    match = build_match(query)
    where = ["d.deleted_at IS NULL", "d.status != 'processing'"]
    params: list[Any] = []
    if area:
        where.append("a.key = ?")
        params.append(area)
    if category_id:
        where.append("(d.category_id = ? OR d.category_id IN (SELECT id FROM categories WHERE parent_id = ?))")
        params += [category_id, category_id]
    if property_id:
        where.append("d.property_id = ?")
        params.append(property_id)
    if year:
        where.append("substr(COALESCE(d.doc_date, d.created_at), 1, 4) = ?")
        params.append(str(year))
    if date_from and date_to:
        where.append("COALESCE(d.doc_date, substr(d.created_at, 1, 10)) BETWEEN ? AND ?")
        params += [date_from, date_to]
    if tag:
        where.append("d.id IN (SELECT dt.document_id FROM document_tags dt JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)")
        params.append(tag)

    select = (
        "SELECT d.id, d.title, d.doc_date, d.doc_type, d.status, d.created_at, d.page_count, d.favorite, "
        "a.key AS area_key, cat.name AS category_name, p.name AS property_name, c.name AS correspondent_name"
    )
    joins = (
        " FROM documents d LEFT JOIN areas a ON a.id = d.area_id LEFT JOIN categories cat ON cat.id = d.category_id"
        " LEFT JOIN properties p ON p.id = d.property_id LEFT JOIN correspondents c ON c.id = d.correspondent_id"
    )
    if match:
        sql = (select + ", snippet(docs_fts, 1, '\u0002', '\u0003', ' … ', 18) AS snippet, "
               "highlight(docs_fts, 0, '\u0002', '\u0003') AS title_hl, bm25(docs_fts, 8.0, 1.0, 3.0) AS rank"
               + joins + " JOIN docs_fts ON docs_fts.rowid = d.id WHERE docs_fts MATCH ? AND " + " AND ".join(where)
               + " ORDER BY rank LIMIT ? OFFSET ?")
        try:
            results = rows(conn.execute(sql, [match, *params, limit, offset]))
        except sqlite3.OperationalError:
            results = []
        total = conn.execute(
            "SELECT COUNT(*)" + joins + " JOIN docs_fts ON docs_fts.rowid = d.id WHERE docs_fts MATCH ? AND " + " AND ".join(where),
            [match, *params]).fetchone()[0] if results else 0
    else:
        sql = select + ", '' AS snippet, d.title AS title_hl" + joins + " WHERE " + " AND ".join(where) + \
            " ORDER BY COALESCE(d.doc_date, d.created_at) DESC LIMIT ? OFFSET ?"
        results = rows(conn.execute(sql, [*params, limit, offset]))
        total = conn.execute("SELECT COUNT(*)" + joins + " WHERE " + " AND ".join(where), params).fetchone()[0]
    for r in results:
        r["snippet"] = _mark(r.get("snippet") or "")
        r["title_hl"] = _mark(r.get("title_hl") or r["title"])
    hint = None
    if short_terms(query) and not match:
        hint = "Suchbegriffe brauchen mindestens 3 Zeichen."
    return {"results": results, "total": total, "hint": hint}


def _mark(text: str) -> list[dict[str, Any]]:
    """Zerlegt hervorgehobenen Text in Abschnitte – das Frontend rendert <mark> ohne HTML-Einschleusung."""
    parts: list[dict[str, Any]] = []
    for i, chunk in enumerate(re.split(r"[\u0002\u0003]", text)):
        if chunk:
            parts.append({"t": re.sub(r"\s+", " ", chunk), "m": i % 2 == 1})
    return parts
