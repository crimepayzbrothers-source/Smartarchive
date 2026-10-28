"""Mietobjekte, Einheiten, Ausgaben und Exporte."""
from __future__ import annotations

import os
import tempfile
from datetime import date

from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from .. import config, exports, invoices
from ..db import audit, now, row, rows, tx
from ..seed import EXPENSE_KEYS
from .common import ApiError, body, endpoint, int_param, path_int

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


# --- Objekte --------------------------------------------------------------

class PropertyIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    address: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, max_length=60)
    living_area: float | None = Field(default=None, ge=0, le=100000)
    ownership_share: str | None = Field(default=None, max_length=40)
    rental_status: str | None = Field(default=None, max_length=40)
    acquired_on: str | None = Field(default=None, pattern=DATE_RE)
    acquisition_cost_cents: int | None = Field(default=None, ge=0)
    keywords: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)
    archived: bool | None = None


@endpoint()
async def list_properties(request: Request, conn, user):
    year = int_param(request, "year") or date.today().year
    props = rows(conn.execute("SELECT * FROM properties WHERE archived = 0 ORDER BY name"))
    for p in props:
        p["document_count"] = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE property_id = ? AND deleted_at IS NULL AND status = 'filed'", (p["id"],)).fetchone()[0]
        p["expenses_year"] = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM expenses WHERE property_id = ? AND substr(paid_on,1,4) = ?",
            (p["id"], str(year))).fetchone()[0]
        p["units"] = rows(conn.execute("SELECT * FROM units WHERE property_id = ? ORDER BY name", (p["id"],)))
    return {"year": year, "properties": props}


@endpoint()
async def get_property(request: Request, conn, user):
    pid = path_int(request)
    p = row(conn.execute("SELECT * FROM properties WHERE id = ?", (pid,)))
    if not p:
        raise KeyError(pid)
    p["units"] = rows(conn.execute("SELECT * FROM units WHERE property_id = ? ORDER BY name", (pid,)))
    p["years"] = rows(conn.execute(
        "SELECT substr(paid_on,1,4) AS year, SUM(amount_cents) AS total, COUNT(*) AS count FROM expenses "
        "WHERE property_id = ? AND paid_on IS NOT NULL GROUP BY year ORDER BY year DESC", (pid,)))
    p["recent_documents"] = rows(conn.execute(
        "SELECT id, title, doc_date, created_at FROM documents WHERE property_id = ? AND deleted_at IS NULL "
        "ORDER BY COALESCE(doc_date, created_at) DESC LIMIT 12", (pid,)))
    return p


@endpoint()
async def create_property(request: Request, conn, user):
    data = await body(request, PropertyIn)
    if not data.name:
        raise ApiError(422, "Objektname fehlt.")
    fields = data.model_dump(exclude_unset=True, exclude={"archived"})
    fields["created_at"] = now()
    pid = conn.execute(f"INSERT INTO properties({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
                       tuple(fields.values())).lastrowid
    audit(conn, "created", "property", pid, fields)
    return {"id": pid}


@endpoint()
async def update_property(request: Request, conn, user):
    pid = path_int(request)
    data = await body(request, PropertyIn)
    fields = data.model_dump(exclude_unset=True)
    if "archived" in fields:
        fields["archived"] = int(fields["archived"])
    if fields:
        conn.execute(f"UPDATE properties SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), pid))
        audit(conn, "updated", "property", pid, fields)
    return {"ok": True}


class UnitIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    living_area: float | None = Field(default=None, ge=0)
    rental_status: str | None = Field(default=None, max_length=40)


@endpoint()
async def create_unit(request: Request, conn, user):
    pid = path_int(request)
    data = await body(request, UnitIn)
    if not data.name:
        raise ApiError(422, "Bezeichnung fehlt.")
    uid = conn.execute("INSERT INTO units(property_id, name, living_area, rental_status) VALUES(?,?,?,?)",
                       (pid, data.name, data.living_area, data.rental_status or "vermietet")).lastrowid
    audit(conn, "created", "unit", uid, data.model_dump())
    return {"id": uid}


@endpoint()
async def update_unit(request: Request, conn, user):
    data = await body(request, UnitIn)
    fields = data.model_dump(exclude_unset=True)
    if fields:
        conn.execute(f"UPDATE units SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), path_int(request)))
    return {"ok": True}


@endpoint()
async def delete_unit(request: Request, conn, user):
    conn.execute("DELETE FROM units WHERE id = ?", (path_int(request),))
    return {"ok": True}


# --- Ausgaben -------------------------------------------------------------

class ExpenseIn(BaseModel):
    document_id: int | None = None
    property_id: int | None = None
    unit_id: int | None = None
    invoice_date: str | None = Field(default=None, pattern=DATE_RE)
    paid_on: str | None = Field(default=None, pattern=DATE_RE)
    amount_cents: int | None = Field(default=None, gt=-100_000_000_00, lt=100_000_000_00)
    payee: str | None = Field(default=None, max_length=120)
    category_key: str | None = None
    note: str | None = Field(default=None, max_length=1000)
    page: int | None = Field(default=None, ge=1)
    status: str | None = Field(default=None, pattern=r"^(entwurf|bestaetigt|ungeklaert)$")

    @field_validator("category_key")
    @classmethod
    def _cat(cls, v: str | None) -> str | None:
        if v is not None and v not in EXPENSE_KEYS:
            raise ValueError("unbekannte Kategorie")
        return v

    @field_validator("invoice_date", "paid_on", mode="before")
    @classmethod
    def _empty(cls, v):
        return v or None


@endpoint()
async def list_expenses(request: Request, conn, user):
    doc = int_param(request, "document_id")
    if doc:
        return rows(conn.execute("SELECT * FROM expenses WHERE document_id = ? ORDER BY id", (doc,)))
    return exports.expense_rows(conn, int_param(request, "year"), int_param(request, "property_id"),
                                request.query_params.get("status") or None,
                                open_payment=request.query_params.get("open") == "1")


@endpoint()
async def create_expense(request: Request, conn, user):
    data = await body(request, ExpenseIn)
    if data.amount_cents is None or not data.category_key:
        raise ApiError(422, "Betrag und Kategorie sind nötig.")
    fields = data.model_dump(exclude_unset=True)
    if data.document_id and not data.property_id:
        prop = conn.execute("SELECT property_id FROM documents WHERE id = ?", (data.document_id,)).fetchone()
        if prop and prop[0]:
            fields["property_id"] = prop[0]
    fields.setdefault("status", "entwurf")
    fields["created_at"] = fields["updated_at"] = now()
    with tx(conn):
        eid = conn.execute(f"INSERT INTO expenses({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
                           tuple(fields.values())).lastrowid
        audit(conn, "created", "expense", eid, fields)
        if data.document_id:
            audit(conn, "expense_added", "document", data.document_id, {"expense_id": eid, "betrag": exports.euro(data.amount_cents)})
    return row(conn.execute("SELECT * FROM expenses WHERE id = ?", (eid,)))


@endpoint()
async def update_expense(request: Request, conn, user):
    eid = path_int(request)
    data = await body(request, ExpenseIn)
    before = row(conn.execute("SELECT * FROM expenses WHERE id = ?", (eid,)))
    if not before:
        raise KeyError(eid)
    fields = {k: v for k, v in data.model_dump(exclude_unset=True).items() if before.get(k) != v}
    if fields:
        with tx(conn):
            conn.execute(f"UPDATE expenses SET {', '.join(k + ' = ?' for k in fields)}, updated_at = ? WHERE id = ?",
                         (*fields.values(), now(), eid))
            audit(conn, "updated", "expense", eid, {k: {"vorher": before.get(k), "nachher": v} for k, v in fields.items()})
    return row(conn.execute("SELECT * FROM expenses WHERE id = ?", (eid,)))


@endpoint()
async def delete_expense(request: Request, conn, user):
    eid = path_int(request)
    before = row(conn.execute("SELECT * FROM expenses WHERE id = ?", (eid,)))
    with tx(conn):
        conn.execute("DELETE FROM expenses WHERE id = ?", (eid,))
        audit(conn, "deleted", "expense", eid, before or {})
    return {"ok": True}


@endpoint()
async def expense_summary(request: Request, conn, user):
    year = int_param(request, "year") or date.today().year
    data = exports.summary(conn, year, int_param(request, "property_id"))
    data["issues"] = exports.find_issues(conn, year)
    data["years"] = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT substr(paid_on,1,4) FROM expenses WHERE paid_on IS NOT NULL ORDER BY 1 DESC") if r[0]]
    if year not in data["years"]:
        data["years"] = sorted(set(data["years"]) | {year}, reverse=True)
    return data


@endpoint()
async def export_expenses(request: Request, conn, user):
    fmt = request.path_params["fmt"]
    year = int_param(request, "year") or date.today().year
    prop = int_param(request, "property_id")
    name = f"Ausgaben_Vermietung_{year}" + (f"_Objekt{prop}" if prop else "")
    if fmt == "csv":
        data, mime = await run_in_threadpool(exports.to_csv, conn, year, prop), "text/csv; charset=utf-8"
    elif fmt == "xlsx":
        data, mime = await run_in_threadpool(exports.to_xlsx, conn, year, prop), \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "pdf":
        data, mime = await run_in_threadpool(exports.to_pdf_bundle, conn, year, prop), "application/pdf"
        name = f"Belegpaket_Vermietung_{year}" + (f"_Objekt{prop}" if prop else "")
    else:
        raise ApiError(404, "Unbekanntes Format")
    audit(conn, "exported", "expenses", None, {"format": fmt, "year": year, "property_id": prop})
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})


@endpoint()
async def export_archive(request: Request, conn, user):
    """Gesamtexport über eine Temp-Datei – auch große Archive belegen keinen Arbeitsspeicher."""
    config.settings.cache_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=config.settings.cache_dir, prefix="export-", suffix=".zip")
    os.close(fd)

    def build() -> None:
        with open(tmp, "wb") as fh:
            exports.full_archive_zip(conn, fh)

    await run_in_threadpool(build)
    audit(conn, "exported", "archive", None)
    stamp = date.today().isoformat()
    return FileResponse(tmp, media_type="application/zip", filename=f"SmartArchive_Export_{stamp}.zip",
                        background=BackgroundTask(os.unlink, tmp))


@endpoint()
async def missing_expenses(request: Request, conn, user):
    return invoices.missing_expenses(conn, int_param(request, "year"))


@endpoint()
async def create_missing_drafts(request: Request, conn, user):
    with tx(conn):
        return invoices.create_drafts(conn, int_param(request, "year"))


@endpoint()
async def set_no_expense(request: Request, conn, user):
    doc_id = path_int(request)
    with tx(conn):
        conn.execute("UPDATE documents SET no_expense = 1, updated_at = ? WHERE id = ?", (now(), doc_id))
        audit(conn, "no_expense", "document", doc_id)
    return {"ok": True}


routes = [
    Route("/api/expenses/missing", missing_expenses, methods=["GET"]),
    Route("/api/expenses/missing/drafts", create_missing_drafts, methods=["POST"]),
    Route("/api/documents/{id:int}/no-expense", set_no_expense, methods=["POST"]),
    Route("/api/properties", list_properties, methods=["GET"]),
    Route("/api/properties", create_property, methods=["POST"]),
    Route("/api/properties/{id:int}", get_property, methods=["GET"]),
    Route("/api/properties/{id:int}", update_property, methods=["PATCH"]),
    Route("/api/properties/{id:int}/units", create_unit, methods=["POST"]),
    Route("/api/units/{id:int}", update_unit, methods=["PATCH"]),
    Route("/api/units/{id:int}", delete_unit, methods=["DELETE"]),
    Route("/api/expenses", list_expenses, methods=["GET"]),
    Route("/api/expenses", create_expense, methods=["POST"]),
    Route("/api/expenses/summary", expense_summary, methods=["GET"]),
    Route("/api/expenses/{id:int}", update_expense, methods=["PATCH"]),
    Route("/api/expenses/{id:int}", delete_expense, methods=["DELETE"]),
    Route("/api/export/expenses.{fmt}", export_expenses, methods=["GET"]),
    Route("/api/export/archive.zip", export_archive, methods=["GET"]),
]
