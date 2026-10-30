"""Bankkonten, CSV-Import und Zuordnung von Kontoumsätzen."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from .. import banking
from ..db import audit, now, row, rows, tx
from .common import ApiError, body, endpoint, int_param, path_int

TX_SELECT = (
    "SELECT t.*, p.name AS property_name, u.name AS unit_name, a.name AS account_name, "
    "e.amount_cents AS expense_amount, e.status AS expense_status, e.payee AS expense_payee, e.origin AS expense_origin, "
    "d.title AS document_title FROM transactions t JOIN bank_accounts a ON a.id = t.account_id "
    "LEFT JOIN properties p ON p.id = t.property_id LEFT JOIN units u ON u.id = t.unit_id "
    "LEFT JOIN expenses e ON e.id = t.expense_id LEFT JOIN documents d ON d.id = t.document_id"
)


def decorate(conn, items: list[dict[str, Any]], with_candidates: bool = False) -> list[dict[str, Any]]:
    for t in items:
        t["suggestion"] = json.loads(t["suggestion"] or "{}")
        if with_candidates and t["status"] != "done" and t["amount_cents"] < 0:  # nur für Offenes/Automatisches
            t["candidates"] = [{k: c[k] for k in ("id", "payee", "amount_cents", "invoice_date", "category_key", "property_id",
                                                  "property_name", "document_id", "document_title", "score", "why", "status")}
                               for c in banking.expense_candidates(conn, t)[:5]]
    return items


def review_lists(conn) -> dict[str, list]:
    open_items = rows(conn.execute(TX_SELECT + " WHERE t.status = 'open' ORDER BY t.booking_date DESC, t.id DESC LIMIT 100"))
    auto = rows(conn.execute(TX_SELECT + " WHERE t.status = 'auto' ORDER BY t.booking_date DESC, t.id DESC LIMIT 100"))
    return {"open": decorate(conn, open_items, True), "auto": decorate(conn, auto, True)}


# --- Konten ------------------------------------------------------------------

class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    iban: str | None = Field(default=None, max_length=40)


@endpoint()
async def list_accounts(request: Request, conn, user):
    accs = rows(conn.execute("SELECT * FROM bank_accounts ORDER BY id"))
    for a in accs:
        a["last_import"] = row(conn.execute("SELECT filename, rows_total, rows_new, created_at FROM bank_imports "
                                            "WHERE account_id = ? ORDER BY id DESC LIMIT 1", (a["id"],)))
        a["range"] = banking.coverage(conn, a["id"])
    return accs


@endpoint()
async def create_account(request: Request, conn, user):
    data = await body(request, AccountIn)
    aid = conn.execute("INSERT INTO bank_accounts(name, iban, created_at) VALUES(?,?,?)",
                       (data.name.strip(), (data.iban or "").replace(" ", "").upper(), now())).lastrowid
    audit(conn, "created", "bank_account", aid, {"name": data.name})
    return {"id": aid}


# --- Import ------------------------------------------------------------------

@endpoint()
async def import_csv(request: Request, conn, user):
    form = await request.form(max_files=1, max_fields=5)
    try:
        account_id = int(str(form.get("account_id") or "0"))
    except ValueError:
        account_id = 0
    upload = form.get("file")
    if not upload or not hasattr(upload, "read"):
        raise ApiError(400, "Keine Datei erhalten.")
    data = await upload.read()
    name = upload.filename or "umsaetze.csv"
    await form.close()
    if len(data) > 10 * 1024 * 1024:
        raise ApiError(413, "Die Datei ist größer als 10 MB.")
    if not conn.execute("SELECT 1 FROM bank_accounts WHERE id = ?", (account_id,)).fetchone():
        raise ApiError(400, "Bitte zuerst ein Konto wählen.")

    def run() -> dict:
        with tx(conn):
            return banking.import_csv(conn, account_id, name, data)

    try:
        return await run_in_threadpool(run)
    except banking.BankImportError as exc:
        raise ApiError(400, str(exc)) from None


# --- Umsätze -----------------------------------------------------------------

@endpoint()
async def list_transactions(request: Request, conn, user):
    qp = request.query_params
    where, params = ["1=1"], []
    if qp.get("status") in ("open", "auto", "done"):
        where.append("t.status = ?")
        params.append(qp["status"])
    if qp.get("kind") in ("ausgabe", "einnahme", "ignoriert"):
        where.append("t.kind = ? AND t.status != 'open'")
        params.append(qp["kind"])
    if int_param(request, "property_id"):
        where.append("t.property_id = ?")
        params.append(int_param(request, "property_id"))
    if qp.get("q"):
        where.append("(t.counterparty LIKE ? OR t.purpose LIKE ?)")
        params += [f"%{qp['q'].strip()}%"] * 2
    year = int_param(request, "year")
    if year:
        where.append("substr(t.booking_date, 1, 4) = ?")
        params.append(str(year))
    if int_param(request, "account_id"):
        where.append("t.account_id = ?")
        params.append(int_param(request, "account_id"))
    limit = min(int_param(request, "limit") or 200, 1000)
    items = rows(conn.execute(TX_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY t.booking_date DESC, t.id DESC LIMIT ?",
                              [*params, limit]))
    return decorate(conn, items, with_candidates=True)


@endpoint()
async def review(request: Request, conn, user):
    return review_lists(conn)


class DecideIn(BaseModel):
    kind: str = Field(pattern=r"^(ausgabe|einnahme|ignoriert)$")
    property_id: int | None = None
    unit_id: int | None = None
    category_key: str | None = None
    expense_id: int | None = None
    amount_cents: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=1000)


def _one(conn, tx_id: int) -> dict:
    t = row(conn.execute(TX_SELECT + " WHERE t.id = ?", (tx_id,)))
    if not t:
        raise KeyError(tx_id)
    return decorate(conn, [t], True)[0]


@endpoint()
async def decide(request: Request, conn, user):
    tx_id = path_int(request)
    data = await body(request, DecideIn)
    try:
        with tx(conn):
            banking.decide(conn, tx_id, data.model_dump())
    except banking.BankImportError as exc:
        raise ApiError(400, str(exc)) from None
    return _one(conn, tx_id)


class ConfirmIn(BaseModel):
    ids: list[int] | None = None


@endpoint()
async def confirm_auto(request: Request, conn, user):
    data = await body(request, ConfirmIn)
    try:
        with tx(conn):
            n = banking.confirm_auto(conn, data.ids)
    except banking.BankImportError as exc:
        raise ApiError(400, str(exc)) from None
    return {"confirmed": n}


@endpoint()
async def undo(request: Request, conn, user):
    tx_id = path_int(request)
    with tx(conn):
        banking.undo(conn, tx_id)
        banking.refresh_suggestion(conn, tx_id)
    return _one(conn, tx_id)


routes = [
    Route("/api/bank/accounts", list_accounts, methods=["GET"]),
    Route("/api/bank/accounts", create_account, methods=["POST"]),
    Route("/api/bank/import", import_csv, methods=["POST"]),
    Route("/api/transactions", list_transactions, methods=["GET"]),
    Route("/api/transactions/review", review, methods=["GET"]),
    Route("/api/transactions/confirm-auto", confirm_auto, methods=["POST"]),
    Route("/api/transactions/{id:int}/decide", decide, methods=["POST"]),
    Route("/api/transactions/{id:int}/undo", undo, methods=["POST"]),
]
