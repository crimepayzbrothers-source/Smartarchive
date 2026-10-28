"""Stammdaten: Bereiche, Kategorien, Absender, Tags, Regeln, Einstellungen, Startseite."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from .. import config, exports, invoices
from ..classify import category_paths
from ..db import audit, get_setting, now, rows, set_setting, tx
from ..seed import EXPENSE_CATEGORIES
from .common import ApiError, body, endpoint, path_int


@endpoint()
async def meta(request: Request, conn, user):
    cats = category_paths(conn)
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT category_id, COUNT(*) FROM documents WHERE deleted_at IS NULL AND status = 'filed' GROUP BY category_id")}
    for c in cats:
        c["count"] = counts.get(c["id"], 0)
        c["ai_enabled"] = bool(c["ai_enabled"])
    props = rows(conn.execute("SELECT * FROM properties WHERE archived = 0 ORDER BY name"))
    units = rows(conn.execute("SELECT * FROM units ORDER BY name"))
    for p in props:
        p["units"] = [u for u in units if u["property_id"] == p["id"]]
    return {
        "areas": [dict(r) | {"ai_enabled": bool(r["ai_enabled"])} for r in conn.execute("SELECT * FROM areas ORDER BY id")],
        "categories": [{k: c[k] for k in ("id", "area_id", "area_key", "parent_id", "name", "path", "keywords",
                                          "ai_enabled", "ai_blocked", "sort", "count", "depth")} for c in cats],
        "properties": props,
        "correspondents": rows(conn.execute("SELECT * FROM correspondents ORDER BY name")),
        "tags": rows(conn.execute("SELECT t.id, t.name, COUNT(dt.document_id) AS count FROM tags t "
                                  "LEFT JOIN document_tags dt ON dt.tag_id = t.id GROUP BY t.id ORDER BY t.name")),
        "expense_categories": EXPENSE_CATEGORIES,
        "settings": {k: get_setting(conn, k) for k in ("auto_file", "auto_threshold", "suggest_threshold", "ai_enabled")},
        "ai_available": config.settings.ai_available,
        "ai_model": config.settings.mistral_model if config.settings.ai_available else None,
    }


@endpoint()
async def dashboard(request: Request, conn, user):
    year = date.today().year
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM documents WHERE deleted_at IS NULL GROUP BY status")}
    per_area = {r[0]: r[1] for r in conn.execute(
        "SELECT a.key, COUNT(*) FROM documents d JOIN areas a ON a.id = d.area_id WHERE d.deleted_at IS NULL AND d.status = 'filed' GROUP BY a.key")}
    to_review = conn.execute("SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL AND status = 'filed' AND confirmed = 0 "
                             "AND filed_by IS NOT NULL AND filed_by != 'user'").fetchone()[0]
    recent = rows(conn.execute(
        "SELECT d.id, d.title, d.doc_date, d.created_at, d.status, a.key AS area_key, "
        "EXISTS(SELECT 1 FROM files f WHERE f.document_id = d.id AND f.role = 'thumb') AS has_thumb "
        "FROM documents d LEFT JOIN areas a ON a.id = d.area_id WHERE d.deleted_at IS NULL ORDER BY d.id DESC LIMIT 8"))
    return {
        "counts": {"inbox": counts.get("inbox", 0), "processing": counts.get("processing", 0),
                   "error": counts.get("error", 0), "filed": counts.get("filed", 0), "to_review": to_review,
                   "transactions": conn.execute("SELECT COUNT(*) FROM transactions WHERE status IN ('open','auto')").fetchone()[0],
                   "undated": len(invoices.undated(conn))},
        "per_area": per_area,
        "recent": recent,
        "year": year,
        "expenses": exports.summary(conn, year),
        "issues": len(exports.find_issues(conn, year)),
    }


# --- Kategorien -----------------------------------------------------------

class CategoryIn(BaseModel):
    area_id: int | None = None
    parent_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    keywords: str | None = Field(default=None, max_length=1000)
    ai_enabled: bool | None = None
    sort: int | None = None


@endpoint()
async def create_category(request: Request, conn, user):
    data = await body(request, CategoryIn)
    if not data.name:
        raise ApiError(422, "Name fehlt.")
    area_id = data.area_id
    if data.parent_id:
        parent = conn.execute("SELECT area_id FROM categories WHERE id = ?", (data.parent_id,)).fetchone()
        if not parent:
            raise ApiError(400, "Übergeordneter Ordner fehlt.")
        area_id = parent[0]
    if not area_id:
        raise ApiError(422, "Bereich fehlt.")
    try:
        cid = conn.execute(
            "INSERT INTO categories(area_id, parent_id, name, keywords, ai_enabled, sort) VALUES(?,?,?,?,?,?)",
            (area_id, data.parent_id, data.name.strip(), data.keywords or "", int(data.ai_enabled if data.ai_enabled is not None else True),
             data.sort or 99)).lastrowid
    except Exception as exc:
        raise ApiError(409, "Ordner mit diesem Namen existiert bereits.") from exc
    audit(conn, "created", "category", cid, {"name": data.name})
    return {"id": cid}


@endpoint()
async def update_category(request: Request, conn, user):
    cid = path_int(request)
    data = await body(request, CategoryIn)
    fields = {k: v for k, v in data.model_dump(exclude_unset=True).items() if k in ("name", "keywords", "ai_enabled", "sort")}
    if "ai_enabled" in fields:
        fields["ai_enabled"] = int(fields["ai_enabled"])
    if fields:
        conn.execute(f"UPDATE categories SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), cid))
        audit(conn, "updated", "category", cid, fields)
    return {"ok": True}


@endpoint()
async def delete_category(request: Request, conn, user):
    cid = path_int(request)
    n = conn.execute("SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL AND (category_id = ? OR category_id IN "
                     "(SELECT id FROM categories WHERE parent_id = ?))", (cid, cid)).fetchone()[0]
    if n:
        raise ApiError(409, f"Ordner enthält noch {n} Dokument(e). Bitte zuerst verschieben.")
    conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
    audit(conn, "deleted", "category", cid)
    return {"ok": True}


class AreaIn(BaseModel):
    ai_enabled: bool


@endpoint()
async def update_area(request: Request, conn, user):
    data = await body(request, AreaIn)
    conn.execute("UPDATE areas SET ai_enabled = ? WHERE id = ?", (int(data.ai_enabled), path_int(request)))
    audit(conn, "updated", "area", path_int(request), {"ai_enabled": data.ai_enabled})
    return {"ok": True}


# --- Absender und Tags ----------------------------------------------------

class CorrespondentIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    aliases: str | None = Field(default=None, max_length=500)


@endpoint()
async def create_correspondent(request: Request, conn, user):
    data = await body(request, CorrespondentIn)
    if not data.name:
        raise ApiError(422, "Name fehlt.")
    try:
        cid = conn.execute("INSERT INTO correspondents(name, aliases) VALUES(?,?)", (data.name.strip(), data.aliases or "")).lastrowid
    except Exception as exc:
        raise ApiError(409, "Absender existiert bereits.") from exc
    return {"id": cid}


@endpoint()
async def update_correspondent(request: Request, conn, user):
    data = await body(request, CorrespondentIn)
    fields = data.model_dump(exclude_unset=True)
    if fields:
        conn.execute(f"UPDATE correspondents SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                     (*fields.values(), path_int(request)))
    return {"ok": True}


@endpoint()
async def delete_correspondent(request: Request, conn, user):
    conn.execute("DELETE FROM correspondents WHERE id = ?", (path_int(request),))
    return {"ok": True}


@endpoint()
async def delete_tag(request: Request, conn, user):
    conn.execute("DELETE FROM tags WHERE id = ?", (path_int(request),))
    return {"ok": True}


# --- Regeln ---------------------------------------------------------------

class RuleIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    match_field: str | None = Field(default=None, pattern=r"^(text|correspondent|filename)$")
    pattern: str | None = Field(default=None, min_length=2, max_length=300)
    category_id: int | None = None
    property_id: int | None = None
    correspondent_id: int | None = None
    doc_type: str | None = Field(default=None, max_length=60)
    enabled: bool | None = None
    sort: int | None = None
    origin: str | None = Field(default=None, pattern=r"^(user|learned)$")


@endpoint()
async def list_rules(request: Request, conn, user):
    rules = rows(conn.execute(
        "SELECT r.*, c.name AS correspondent_name, p.name AS property_name FROM rules r "
        "LEFT JOIN correspondents c ON c.id = r.correspondent_id LEFT JOIN properties p ON p.id = r.property_id "
        "ORDER BY r.sort, r.id"))
    paths = {c["id"]: c["path"] for c in category_paths(conn)}
    for r in rules:
        r["category_path"] = paths.get(r["category_id"])
        r["enabled"] = bool(r["enabled"])
    # Vorschläge: mind. zwei gleiche Korrekturen für denselben Absender, noch ohne Regel
    proposals = rows(conn.execute(
        "SELECT k.correspondent_id, c.name AS correspondent_name, k.chosen_category_id AS category_id, "
        "k.chosen_property_id AS property_id, COUNT(*) AS n FROM corrections k JOIN correspondents c ON c.id = k.correspondent_id "
        "WHERE k.correspondent_id IS NOT NULL GROUP BY k.correspondent_id, k.chosen_category_id, k.chosen_property_id "
        "HAVING n >= 2 AND NOT EXISTS (SELECT 1 FROM rules r WHERE r.match_field = 'correspondent' AND r.pattern = c.name COLLATE NOCASE)"))
    for p in proposals:
        p["category_path"] = paths.get(p["category_id"])
    return {"rules": rules, "proposals": proposals}


@endpoint()
async def create_rule(request: Request, conn, user):
    data = await body(request, RuleIn)
    if not (data.name and data.pattern and data.category_id):
        raise ApiError(422, "Name, Suchmuster und Ziel-Ordner sind nötig.")
    area_id = conn.execute("SELECT area_id FROM categories WHERE id = ?", (data.category_id,)).fetchone()
    if not area_id:
        raise ApiError(400, "Ordner existiert nicht.")
    rid = conn.execute(
        "INSERT INTO rules(name, match_field, pattern, area_id, category_id, property_id, correspondent_id, doc_type, enabled, origin, sort, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (data.name, data.match_field or "text", data.pattern, area_id[0], data.category_id, data.property_id,
         data.correspondent_id, data.doc_type or "", 1, data.origin or "user", data.sort or 0, now())).lastrowid
    audit(conn, "created", "rule", rid, data.model_dump(exclude_unset=True))
    return {"id": rid}


@endpoint()
async def update_rule(request: Request, conn, user):
    data = await body(request, RuleIn)
    fields = data.model_dump(exclude_unset=True)
    fields.pop("origin", None)
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    if fields.get("category_id"):
        fields["area_id"] = conn.execute("SELECT area_id FROM categories WHERE id = ?", (fields["category_id"],)).fetchone()[0]
    if fields:
        conn.execute(f"UPDATE rules SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), path_int(request)))
        audit(conn, "updated", "rule", path_int(request), fields)
    return {"ok": True}


@endpoint()
async def delete_rule(request: Request, conn, user):
    conn.execute("DELETE FROM rules WHERE id = ?", (path_int(request),))
    audit(conn, "deleted", "rule", path_int(request))
    return {"ok": True}


# --- Einstellungen --------------------------------------------------------

class SettingsIn(BaseModel):
    auto_file: bool | None = None
    auto_threshold: float | None = Field(default=None, ge=0.6, le=1.0)
    suggest_threshold: float | None = Field(default=None, ge=0.2, le=0.9)
    ai_enabled: bool | None = None


@endpoint()
async def update_settings(request: Request, conn, user):
    data = await body(request, SettingsIn)
    with tx(conn):
        for k, v in data.model_dump(exclude_unset=True).items():
            set_setting(conn, k, v)
        audit(conn, "updated", "settings", None, data.model_dump(exclude_unset=True))
    return {k: get_setting(conn, k) for k in ("auto_file", "auto_threshold", "suggest_threshold", "ai_enabled")}


@endpoint()
async def audit_log(request: Request, conn, user):
    return rows(conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200"))


routes = [
    Route("/api/meta", meta, methods=["GET"]),
    Route("/api/dashboard", dashboard, methods=["GET"]),
    Route("/api/categories", create_category, methods=["POST"]),
    Route("/api/categories/{id:int}", update_category, methods=["PATCH"]),
    Route("/api/categories/{id:int}", delete_category, methods=["DELETE"]),
    Route("/api/areas/{id:int}", update_area, methods=["PATCH"]),
    Route("/api/correspondents", create_correspondent, methods=["POST"]),
    Route("/api/correspondents/{id:int}", update_correspondent, methods=["PATCH"]),
    Route("/api/correspondents/{id:int}", delete_correspondent, methods=["DELETE"]),
    Route("/api/tags/{id:int}", delete_tag, methods=["DELETE"]),
    Route("/api/rules", list_rules, methods=["GET"]),
    Route("/api/rules", create_rule, methods=["POST"]),
    Route("/api/rules/{id:int}", update_rule, methods=["PATCH"]),
    Route("/api/rules/{id:int}", delete_rule, methods=["DELETE"]),
    Route("/api/settings", update_settings, methods=["PATCH"]),
    Route("/api/audit", audit_log, methods=["GET"]),
]
