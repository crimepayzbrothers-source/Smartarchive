"""Dokumente: Upload, Eingang, Archiv, Detail, Ablage, Dateien."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from .. import config, documents, ocr, search, storage
from ..classify import category_paths, get_or_create_correspondent
from ..db import audit, now, row, rows, tx
from .common import ApiError, body, endpoint, int_param, path_int

LIST_SELECT = (
    "SELECT d.id, d.title, d.doc_date, d.doc_type, d.status, d.created_at, d.updated_at, d.page_count, d.favorite, "
    "d.filed_by, d.confirmed, d.confidence, d.category_id, d.property_id, d.upload_area, d.error, d.original_name, "
    "a.key AS area_key, p.name AS property_name, c.name AS correspondent_name, "
    "EXISTS(SELECT 1 FROM files f WHERE f.document_id = d.id AND f.role = 'thumb') AS has_thumb "
    "FROM documents d LEFT JOIN areas a ON a.id = d.area_id LEFT JOIN properties p ON p.id = d.property_id "
    "LEFT JOIN correspondents c ON c.id = d.correspondent_id"
)


def _paths(conn) -> dict[int, str]:
    return {c["id"]: c["path"] for c in category_paths(conn)}


def _decorate(conn, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths = _paths(conn)
    ids = [i["id"] for i in items]
    tags: dict[int, list[str]] = {}
    if ids:
        q = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT dt.document_id, t.name FROM document_tags dt JOIN tags t ON t.id = dt.tag_id WHERE dt.document_id IN ({q})", ids):
            tags.setdefault(r[0], []).append(r[1])
    for i in items:
        i["category_path"] = paths.get(i.get("category_id")) if i.get("category_id") else None
        i["tags"] = sorted(tags.get(i["id"], []))
        i["has_thumb"] = bool(i.get("has_thumb"))
    return items


def _suggestion(conn, document_id: int) -> dict | None:
    s = row(conn.execute(
        "SELECT * FROM suggestions WHERE document_id = ? AND status != 'superseded' ORDER BY id DESC LIMIT 1", (document_id,)))
    if not s:
        return None
    p = json.loads(s["payload"])
    paths = _paths(conn)
    s["payload"] = p
    s["labels"] = {
        "category": paths.get(p.get("category_id")),
        "property": (conn.execute("SELECT name FROM properties WHERE id = ?", (p["property_id"],)).fetchone() or [None])[0] if p.get("property_id") else None,
        "correspondent": (conn.execute("SELECT name FROM correspondents WHERE id = ?", (p["correspondent_id"],)).fetchone() or [None])[0] if p.get("correspondent_id") else p.get("correspondent_name"),
        "area": (conn.execute("SELECT name FROM areas WHERE id = ?", (p["area_id"],)).fetchone() or [None])[0] if p.get("area_id") else None,
    }
    return s


def detail(conn, document_id: int) -> dict[str, Any]:
    d = row(conn.execute(
        "SELECT d.*, a.key AS area_key, a.name AS area_name, p.name AS property_name, u.name AS unit_name, "
        "c.name AS correspondent_name FROM documents d LEFT JOIN areas a ON a.id = d.area_id "
        "LEFT JOIN properties p ON p.id = d.property_id LEFT JOIN units u ON u.id = d.unit_id "
        "LEFT JOIN correspondents c ON c.id = d.correspondent_id WHERE d.id = ?", (document_id,)))
    if not d:
        raise KeyError(document_id)
    d["category_path"] = _paths(conn).get(d["category_id"]) if d["category_id"] else None
    d["tags"] = [r[0] for r in conn.execute(
        "SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id = t.id WHERE dt.document_id = ? ORDER BY t.name", (document_id,))]
    d["files"] = [{k: f[k] for k in ("id", "role", "mime", "size", "original_name", "position")}
                  for f in documents.files_of(conn, document_id)]
    d["suggestion"] = _suggestion(conn, document_id)
    d["expenses"] = rows(conn.execute(
        "SELECT e.*, p.name AS property_name FROM expenses e LEFT JOIN properties p ON p.id = e.property_id "
        "WHERE e.document_id = ? ORDER BY e.id", (document_id,)))
    d["history"] = rows(conn.execute(
        "SELECT ts, actor, action, details FROM audit_log WHERE entity = 'document' AND entity_id = ? ORDER BY id DESC LIMIT 40",
        (document_id,)))
    for h in d["history"]:
        h["details"] = json.loads(h["details"])
    d["display_filename"] = documents.display_filename(conn, document_id)
    d["has_pdf"] = documents.best_pdf(conn, document_id) is not None
    return d


# --- Upload ---------------------------------------------------------------

@endpoint()
async def upload(request: Request, conn, user):
    limit = config.settings.max_upload_mb * 1024 * 1024
    form = await request.form(max_files=50, max_fields=10)
    area = str(form.get("area") or "auto")
    combine = str(form.get("combine") or "") in ("1", "true", "ja")
    uploads: list[tuple[str, bytes]] = []
    for item in form.getlist("files"):
        if not hasattr(item, "read"):
            continue
        data = await item.read()
        if len(data) > limit:
            raise ApiError(413, f"„{item.filename}“ ist größer als {config.settings.max_upload_mb} MB.")
        if data:
            uploads.append((item.filename or "Dokument", data))
    await form.close()
    if not uploads:
        raise ApiError(400, "Keine Datei erhalten.")
    try:
        result = await run_in_threadpool(documents.ingest, conn, uploads, area, combine)
    except documents.IngestError as exc:
        raise ApiError(400, str(exc)) from None
    return result


# --- Listen ---------------------------------------------------------------

@endpoint()
async def list_documents(request: Request, conn, user):
    qp = request.query_params
    q = qp.get("q", "").strip()
    limit = min(int_param(request, "limit") or 60, 200)
    offset = int_param(request, "offset") or 0
    if q:
        res = search.search(conn, q, area=qp.get("area") or None, category_id=int_param(request, "category_id"),
                            property_id=int_param(request, "property_id"), year=int_param(request, "year"),
                            tag=qp.get("tag") or None, limit=limit, offset=offset)
        ids = [r["id"] for r in res["results"]]
        extra = {}
        if ids:
            for r in conn.execute(f"SELECT id, category_id, filed_by, confirmed, EXISTS(SELECT 1 FROM files f WHERE f.document_id = documents.id AND f.role='thumb') FROM documents WHERE id IN ({','.join('?' * len(ids))})", ids):
                extra[r[0]] = {"category_id": r[1], "filed_by": r[2], "confirmed": r[3], "has_thumb": r[4]}
        for r in res["results"]:
            r.update(extra.get(r["id"], {}))
        res["results"] = _decorate(conn, res["results"])
        return res
    where = ["d.deleted_at IS NULL" if qp.get("trash") != "1" else "d.deleted_at IS NOT NULL"]
    params: list[Any] = []
    status = qp.get("status")
    if status:
        where.append("d.status = ?")
        params.append(status)
    elif qp.get("trash") != "1":
        where.append("d.status = 'filed'")
    if qp.get("area"):
        where.append("a.key = ?")
        params.append(qp["area"])
    cat = int_param(request, "category_id")
    if cat:
        where.append("(d.category_id = ? OR d.category_id IN (SELECT id FROM categories WHERE parent_id = ?))")
        params += [cat, cat]
    prop = int_param(request, "property_id")
    if prop:
        where.append("d.property_id = ?")
        params.append(prop)
    year = int_param(request, "year")
    if year:
        where.append("substr(COALESCE(d.doc_date, d.created_at), 1, 4) = ?")
        params.append(str(year))
    if qp.get("favorite") == "1":
        where.append("d.favorite = 1")
    if qp.get("tag"):
        where.append("d.id IN (SELECT dt.document_id FROM document_tags dt JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)")
        params.append(qp["tag"])
    clause = " WHERE " + " AND ".join(where)
    order = " ORDER BY COALESCE(d.doc_date, substr(d.created_at,1,10)) DESC, d.id DESC"
    items = rows(conn.execute(LIST_SELECT + clause + order + " LIMIT ? OFFSET ?", [*params, limit, offset]))
    total = conn.execute("SELECT COUNT(*) FROM documents d LEFT JOIN areas a ON a.id = d.area_id" + clause, params).fetchone()[0]
    return {"results": _decorate(conn, items), "total": total}


@endpoint()
async def inbox(request: Request, conn, user):
    pending = rows(conn.execute(LIST_SELECT + " WHERE d.deleted_at IS NULL AND d.status IN ('inbox','processing','error') "
                                "ORDER BY CASE d.status WHEN 'error' THEN 0 WHEN 'inbox' THEN 1 ELSE 2 END, d.id DESC"))
    auto = rows(conn.execute(LIST_SELECT + " WHERE d.deleted_at IS NULL AND d.status = 'filed' AND d.confirmed = 0 "
                             "AND d.filed_by IS NOT NULL AND d.filed_by != 'user' ORDER BY d.filed_at DESC LIMIT 50"))
    for item in pending + auto:
        item["suggestion"] = _suggestion(conn, item["id"])
    return {"pending": _decorate(conn, pending), "auto_filed": _decorate(conn, auto)}


@endpoint()
async def get_document(request: Request, conn, user):
    return detail(conn, path_int(request))


@endpoint()
async def get_text(request: Request, conn, user):
    r = conn.execute("SELECT content, pages_json FROM document_text WHERE document_id = ?", (path_int(request),)).fetchone()
    if not r:
        raise KeyError
    return {"content": r[0], "pages": json.loads(r[1])}


# --- Ändern und Ablegen ---------------------------------------------------

class DocPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    doc_type: str | None = Field(default=None, max_length=60)
    doc_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$|^$")
    category_id: int | None = None
    property_id: int | None = None
    unit_id: int | None = None
    correspondent_id: int | None = None
    correspondent_name: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = None
    favorite: bool | None = None
    file: bool = False          # True = jetzt ablegen (aus dem Eingang)
    confirm: bool = False       # True = automatische Ablage bestätigen


def _set_tags(conn, document_id: int, tags: list[str]) -> None:
    clean = sorted({t.strip()[:40] for t in tags if t.strip()})
    conn.execute("DELETE FROM document_tags WHERE document_id = ?", (document_id,))
    for name in clean:
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO document_tags(document_id, tag_id) VALUES(?,?)", (document_id, tag_id))


def _file_document(conn, document_id: int, chosen_category: int | None, chosen_property: int | None) -> None:
    """Ablage durch den Nutzer: bestätigt, Korrektur gegenüber Vorschlag merken."""
    d = row(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)))
    if not d["category_id"]:
        raise ApiError(400, "Bitte zuerst eine Kategorie wählen.")
    s = row(conn.execute("SELECT * FROM suggestions WHERE document_id = ? AND status IN ('open','applied','info') ORDER BY id DESC LIMIT 1",
                         (document_id,)))
    if s:
        p = json.loads(s["payload"])
        same = p.get("category_id") == d["category_id"] and (p.get("property_id") or None) == (d["property_id"] or None)
        conn.execute("UPDATE suggestions SET status = ?, decided_at = ? WHERE id = ?",
                     ("accepted" if same else "rejected", now(), s["id"]))
        if not same:
            conn.execute(
                "INSERT INTO corrections(document_id, correspondent_id, suggested_category_id, chosen_category_id, chosen_property_id, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (document_id, d["correspondent_id"], p.get("category_id"), d["category_id"], d["property_id"], now()))
    conn.execute("UPDATE documents SET status = 'filed', confirmed = 1, filed_by = COALESCE(CASE WHEN status = 'filed' THEN filed_by END, 'user'), "
                 "filed_at = COALESCE(filed_at, ?), updated_at = ? WHERE id = ?", (now(), now(), document_id))
    audit(conn, "filed", "document", document_id, {"category_id": d["category_id"], "property_id": d["property_id"]})


@endpoint()
async def patch_document(request: Request, conn, user):
    doc_id = path_int(request)
    data = await body(request, DocPatch)
    current = row(conn.execute("SELECT * FROM documents WHERE id = ? AND deleted_at IS NULL", (doc_id,)))
    if not current:
        raise KeyError(doc_id)
    sent = data.model_dump(exclude_unset=True)
    fields: dict[str, Any] = {}
    with tx(conn):
        for key in ("title", "doc_type", "doc_date", "property_id", "unit_id", "correspondent_id"):
            if key in sent:
                fields[key] = sent[key] or None if key != "title" else (sent[key] or "").strip()
        if data.correspondent_name:
            fields["correspondent_id"] = get_or_create_correspondent(conn, data.correspondent_name)
        if "category_id" in sent:
            if data.category_id:
                cat = conn.execute("SELECT area_id FROM categories WHERE id = ?", (data.category_id,)).fetchone()
                if not cat:
                    raise ApiError(400, "Kategorie existiert nicht.")
                fields["category_id"] = data.category_id
                fields["area_id"] = cat[0]
            else:
                fields["category_id"] = None
        if data.favorite is not None:
            fields["favorite"] = int(data.favorite)
        new_area = fields.get("area_id")
        if new_area and current["area_id"] and new_area != current["area_id"] and current["status"] == "filed":
            audit(conn, "area_changed", "document", doc_id, {"vorher": current["area_id"], "nachher": new_area})
        target_area = new_area or current["area_id"]
        if target_area:
            area_key = conn.execute("SELECT key FROM areas WHERE id = ?", (target_area,)).fetchone()[0]
            if area_key == "privat":
                fields["property_id"] = None
                fields["unit_id"] = None
        documents.apply_fields(conn, doc_id, fields)
        if data.tags is not None:
            _set_tags(conn, doc_id, data.tags)
        if data.file or data.confirm or (current["status"] == "filed" and "category_id" in sent):
            _file_document(conn, doc_id, fields.get("category_id"), fields.get("property_id"))
        documents.index_document(conn, doc_id)
    return detail(conn, doc_id)


@endpoint()
async def accept_suggestion(request: Request, conn, user):
    doc_id = path_int(request)
    s = row(conn.execute("SELECT * FROM suggestions WHERE document_id = ? AND status IN ('open','applied') ORDER BY id DESC LIMIT 1", (doc_id,)))
    if not s:
        raise ApiError(404, "Kein offener Vorschlag.")
    p = json.loads(s["payload"])
    fields = {k: p[k] for k in ("area_id", "category_id", "property_id", "correspondent_id", "doc_date", "doc_type")
              if p.get(k)}
    if p.get("title"):
        fields["title"] = p["title"]
    with tx(conn):
        if p.get("correspondent_name") and not p.get("correspondent_id"):
            fields["correspondent_id"] = get_or_create_correspondent(conn, p["correspondent_name"])
        documents.apply_fields(conn, doc_id, fields)
        _file_document(conn, doc_id, p.get("category_id"), p.get("property_id"))
        documents.index_document(conn, doc_id)
    return detail(conn, doc_id)


@endpoint()
async def undo_auto(request: Request, conn, user):
    """Automatische Ablage zurücknehmen: Dokument zurück in den Eingang."""
    doc_id = path_int(request)
    d = row(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)))
    if not d:
        raise KeyError(doc_id)
    with tx(conn):
        conn.execute("UPDATE documents SET status = 'inbox', confirmed = 0, filed_by = NULL, filed_at = NULL, updated_at = ? WHERE id = ?",
                     (now(), doc_id))
        conn.execute("UPDATE suggestions SET status = 'open', decided_at = ? WHERE document_id = ? AND status = 'applied'",
                     (now(), doc_id))
        audit(conn, "auto_filing_undone", "document", doc_id, {"category_id": d["category_id"]})
    return detail(conn, doc_id)


@endpoint()
async def reprocess(request: Request, conn, user):
    doc_id = path_int(request)
    kind = request.path_params.get("kind", "process")
    if kind not in ("process", "reclassify"):
        raise ApiError(400, "Unbekannter Vorgang")
    with tx(conn):
        conn.execute("UPDATE documents SET status = CASE WHEN ? = 'process' THEN 'processing' ELSE status END, error = NULL, updated_at = ? WHERE id = ?",
                     (kind, now(), doc_id))
        documents.enqueue(conn, doc_id, kind)
        audit(conn, "reprocess_requested", "document", doc_id, {"kind": kind})
    return {"ok": True}


@endpoint()
async def delete_document(request: Request, conn, user):
    doc_id = path_int(request)
    with tx(conn):
        conn.execute("UPDATE documents SET deleted_at = ?, updated_at = ? WHERE id = ?", (now(), now(), doc_id))
        audit(conn, "deleted", "document", doc_id)
    return {"ok": True}


@endpoint()
async def restore_document(request: Request, conn, user):
    doc_id = path_int(request)
    with tx(conn):
        conn.execute("UPDATE documents SET deleted_at = NULL, updated_at = ? WHERE id = ?", (now(), doc_id))
        audit(conn, "restored", "document", doc_id)
    return detail(conn, doc_id)


# --- Dateien --------------------------------------------------------------

_SECURE_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}


@endpoint()
async def get_file(request: Request, conn, user):
    doc_id = path_int(request)
    variant = request.query_params.get("variant", "pdf")
    download = request.query_params.get("download") == "1"
    if variant == "original":
        pos = int_param(request, "position") or 0
        files = [f for f in documents.files_of(conn, doc_id, "original") if f["position"] == pos]
        f = files[0] if files else None
        filename = f["original_name"] if f else "original"
    else:
        f = documents.best_pdf(conn, doc_id)
        filename = documents.display_filename(conn, doc_id)
    if not f:
        raise KeyError(doc_id)
    audit(conn, "file_downloaded" if download else "file_viewed", "document", doc_id, {"variant": variant})
    return FileResponse(storage.abs_path(f["path"]), media_type=f["mime"], filename=filename,
                        content_disposition_type="attachment" if download else "inline", headers=_SECURE_HEADERS)


@endpoint()
async def get_thumb(request: Request, conn, user):
    files = documents.files_of(conn, path_int(request), "thumb")
    if not files:
        return Response(status_code=404)
    return FileResponse(storage.abs_path(files[0]["path"]), media_type="image/png", headers=_SECURE_HEADERS)


@endpoint()
async def get_page(request: Request, conn, user):
    doc_id = path_int(request)
    page = int(request.path_params["page"])
    dpi = max(50, min(int_param(request, "dpi") or 110, 200))
    f = documents.best_pdf(conn, doc_id)
    if not f:
        raise KeyError(doc_id)
    count = conn.execute("SELECT page_count FROM documents WHERE id = ?", (doc_id,)).fetchone()[0] or 1
    if page < 1 or page > count:
        raise ApiError(404, "Seite existiert nicht.")
    out = storage.page_cache_dir(f["sha256"]) / f"{page}-{dpi}.png"
    if not out.exists():
        await run_in_threadpool(ocr.render_page, storage.abs_path(f["path"]), page, dpi, out)
    return FileResponse(out, media_type="image/png", headers=_SECURE_HEADERS)


routes = [
    Route("/api/upload", upload, methods=["POST"]),
    Route("/api/documents", list_documents, methods=["GET"]),
    Route("/api/inbox", inbox, methods=["GET"]),
    Route("/api/documents/{id:int}", get_document, methods=["GET"]),
    Route("/api/documents/{id:int}", patch_document, methods=["PATCH"]),
    Route("/api/documents/{id:int}", delete_document, methods=["DELETE"]),
    Route("/api/documents/{id:int}/text", get_text, methods=["GET"]),
    Route("/api/documents/{id:int}/accept", accept_suggestion, methods=["POST"]),
    Route("/api/documents/{id:int}/undo", undo_auto, methods=["POST"]),
    Route("/api/documents/{id:int}/restore", restore_document, methods=["POST"]),
    Route("/api/documents/{id:int}/run/{kind}", reprocess, methods=["POST"]),
    Route("/api/documents/{id:int}/file", get_file, methods=["GET"]),
    Route("/api/documents/{id:int}/thumb", get_thumb, methods=["GET"]),
    Route("/api/documents/{id:int}/pages/{page:int}", get_page, methods=["GET"]),
]
