"""Einnahmenverwaltung: Soll/Ist je Mietverhältnis und Monat, Hand-Erfassung von Zahlungen."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from .. import income, income_pdf
from ..db import audit, get_setting, now, row, set_setting, tx
from .common import ApiError, body, endpoint, int_param, path_int

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


@endpoint()
async def overview(request: Request, conn, user):
    year = int_param(request, "year") or date.today().year
    with tx(conn):
        return income.overview(conn, year, int_param(request, "property_id"))


class ReceiptIn(BaseModel):
    tenancy_id: int
    paid_on: str = Field(pattern=DATE_RE)
    amount_cents: int = Field(gt=0, le=10_000_000)
    for_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    note: str = Field(default="", max_length=200)


@endpoint()
async def add_receipt(request: Request, conn, user):
    data = await body(request, ReceiptIn)
    if not conn.execute("SELECT 1 FROM tenancies WHERE id = ?", (data.tenancy_id,)).fetchone():
        raise ApiError(400, "Das Mietverhältnis existiert nicht.")
    with tx(conn):
        rid = conn.execute("INSERT INTO rent_receipts(tenancy_id, paid_on, amount_cents, for_month, note, created_at) VALUES(?,?,?,?,?,?)",
                           (data.tenancy_id, data.paid_on, data.amount_cents, data.for_month, data.note.strip(), now())).lastrowid
        audit(conn, "created", "rent_receipt", rid, {"tenancy_id": data.tenancy_id, "amount_cents": data.amount_cents, "paid_on": data.paid_on})
    return {"id": rid}


@endpoint()
async def delete_receipt(request: Request, conn, user):
    rid = path_int(request)
    r = row(conn.execute("SELECT * FROM rent_receipts WHERE id = ?", (rid,)))
    if not r:
        raise KeyError(rid)
    with tx(conn):
        conn.execute("DELETE FROM rent_receipts WHERE id = ?", (rid,))
        audit(conn, "deleted", "rent_receipt", rid, {"tenancy_id": r["tenancy_id"], "amount_cents": r["amount_cents"]})
    return {"ok": True}


class ExceptionIn(BaseModel):
    tenancy_id: int
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    paid_cents: int = Field(default=0, ge=0, le=10_000_000)
    note: str = Field(default="", max_length=200)


@endpoint()
async def set_exception(request: Request, conn, user):
    """Ausbleibende (oder nur teilweise geleistete) Zahlung für einen Monat hinterlegen."""
    data = await body(request, ExceptionIn)
    if not conn.execute("SELECT 1 FROM tenancies WHERE id = ?", (data.tenancy_id,)).fetchone():
        raise ApiError(400, "Das Mietverhältnis existiert nicht.")
    with tx(conn):
        conn.execute("INSERT INTO rent_exceptions(tenancy_id, month, paid_cents, note, created_at) VALUES(?,?,?,?,?) "
                     "ON CONFLICT(tenancy_id, month) DO UPDATE SET paid_cents = excluded.paid_cents, note = excluded.note",
                     (data.tenancy_id, data.month, data.paid_cents, data.note.strip(), now()))
        audit(conn, "payment_missing", "tenancy", data.tenancy_id, {"month": data.month, "paid_cents": data.paid_cents})
    return {"ok": True}


@endpoint()
async def delete_exception(request: Request, conn, user):
    eid = path_int(request)
    r = row(conn.execute("SELECT * FROM rent_exceptions WHERE id = ?", (eid,)))
    if not r:
        raise KeyError(eid)
    with tx(conn):
        conn.execute("DELETE FROM rent_exceptions WHERE id = ?", (eid,))
        audit(conn, "payment_missing_removed", "tenancy", r["tenancy_id"], {"month": r["month"]})
    return {"ok": True}


class SettingsIn(BaseModel):
    assume_paid: bool


@endpoint()
async def settings(request: Request, conn, user):
    data = await body(request, SettingsIn)
    with tx(conn):
        set_setting(conn, "rent_assume_paid", data.assume_paid)
        audit(conn, "updated", "setting", None, {"rent_assume_paid": data.assume_paid})
    return {"assume_paid": data.assume_paid}


@endpoint()
async def export_pdf(request: Request, conn, user):
    """Einnahmenübersicht als PDF (z. B. für das Finanzamt)."""
    year = int_param(request, "year") or date.today().year
    prop = int_param(request, "property_id")
    scope = "Alle Objekte"
    if prop:
        p = row(conn.execute("SELECT name FROM properties WHERE id = ?", (prop,)))
        if not p:
            raise KeyError(prop)
        scope = p["name"]
    ov = income.overview(conn, year, prop)
    data = await run_in_threadpool(income_pdf.render, ov, get_setting(conn, "landlord", {}) or {}, scope)
    audit(conn, "exported", "income", None, {"format": "pdf", "year": year, "property_id": prop})
    name = f"Einnahmen_Vermietung_{year}" + (f"_Objekt{prop}" if prop else "")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


routes = [
    Route("/api/export/income.pdf", export_pdf, methods=["GET"]),
    Route("/api/income/exceptions", set_exception, methods=["PUT"]),
    Route("/api/income/exceptions/{id:int}", delete_exception, methods=["DELETE"]),
    Route("/api/income/settings", settings, methods=["PUT"]),
    Route("/api/income", overview, methods=["GET"]),
    Route("/api/income/receipts", add_receipt, methods=["POST"]),
    Route("/api/income/receipts/{id:int}", delete_receipt, methods=["DELETE"]),
]
