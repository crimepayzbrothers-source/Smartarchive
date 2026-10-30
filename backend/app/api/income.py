"""Einnahmenverwaltung: Soll/Ist je Mietverhältnis und Monat, Hand-Erfassung von Zahlungen."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from .. import income
from ..db import audit, now, row, tx
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


routes = [
    Route("/api/income", overview, methods=["GET"]),
    Route("/api/income/receipts", add_receipt, methods=["POST"]),
    Route("/api/income/receipts/{id:int}", delete_receipt, methods=["DELETE"]),
]
