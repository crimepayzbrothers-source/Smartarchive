"""Mietkaution je Mietverhältnis."""
from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from starlette.concurrency import run_in_threadpool

from .. import deposit, deposit_letter
from .common import ApiError, body, endpoint, path_int
from .dunning import _pdf

OPT_DATE = r"^(\d{4}-\d{2}-\d{2})?$"


class DepositIn(BaseModel):
    kind: str = Field(max_length=20)
    months: float | None = Field(default=None, ge=0.5, le=6)
    amount_cents: int = Field(default=0, ge=0, le=10_000_000)
    installments: bool = False
    account: str = Field(default="", max_length=200)
    provider: str = Field(default="", max_length=120)
    reference: str = Field(default="", max_length=80)
    document_received_on: str | None = Field(default=None, pattern=OPT_DATE)
    returned_on: str | None = Field(default=None, pattern=OPT_DATE)
    returned_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    retained_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    note: str = Field(default="", max_length=1000)


class PaymentIn(BaseModel):
    paid_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    amount_cents: int = Field(gt=0, le=10_000_000)
    note: str = Field(default="", max_length=200)


class LetterIn(BaseModel):
    kind: str = Field(pattern=r"^(erinnerung|mahnung)$")
    letter_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    salutation: str = Field(default="", max_length=200)
    extra_text: str = Field(default="", max_length=2000)


def _full(conn, tid: int) -> dict:
    out = deposit.get(conn, tid)
    out.update(deposit_letter.state(conn, tid))
    return out


@endpoint()
async def get_deposit(request: Request, conn, user):
    return _full(conn, path_int(request))


@endpoint()
async def save_deposit(request: Request, conn, user):
    tid = path_int(request)
    data = (await body(request, DepositIn)).model_dump()
    for k in ("document_received_on", "returned_on"):
        data[k] = data[k] or None
    try:
        deposit.save(conn, tid, data)
    except deposit.DepositError as exc:
        raise ApiError(400, str(exc)) from None
    return _full(conn, tid)


@endpoint()
async def add_payment(request: Request, conn, user):
    tid = path_int(request)
    data = await body(request, PaymentIn)
    try:
        deposit.add_payment(conn, tid, data.paid_on, data.amount_cents, data.note)
    except deposit.DepositError as exc:
        raise ApiError(400, str(exc)) from None
    return _full(conn, tid)


@endpoint()
async def delete_payment(request: Request, conn, user):
    tid = deposit.delete_payment(conn, path_int(request))
    return _full(conn, tid)


@endpoint()
async def letter_preview(request: Request, conn, user):
    tid = path_int(request)
    data = (await body(request, LetterIn)).model_dump()
    try:
        snap = deposit_letter.build(conn, tid, data)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    return _pdf(await run_in_threadpool(deposit_letter.render, snap), "Kaution Vorschau.pdf")


@endpoint()
async def letter_create(request: Request, conn, user):
    tid = path_int(request)
    data = (await body(request, LetterIn)).model_dump()
    try:
        await run_in_threadpool(deposit_letter.create, conn, tid, data)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    return _full(conn, tid)


@endpoint()
async def letter_pdf(request: Request, conn, user):
    pdf, name = await run_in_threadpool(deposit_letter.pdf_of, conn, path_int(request))
    return _pdf(pdf, name)


@endpoint()
async def letter_delete(request: Request, conn, user):
    return _full(conn, deposit_letter.delete(conn, path_int(request)))


routes = [
    Route("/api/tenancies/{id:int}/deposit/letters/preview", letter_preview, methods=["POST"]),
    Route("/api/tenancies/{id:int}/deposit/letters", letter_create, methods=["POST"]),
    Route("/api/deposit-letters/{id:int}/pdf", letter_pdf, methods=["GET"]),
    Route("/api/deposit-letters/{id:int}", letter_delete, methods=["DELETE"]),
    Route("/api/tenancies/{id:int}/deposit", get_deposit, methods=["GET"]),
    Route("/api/tenancies/{id:int}/deposit", save_deposit, methods=["PUT"]),
    Route("/api/tenancies/{id:int}/deposit/payments", add_payment, methods=["POST"]),
    Route("/api/deposit-payments/{id:int}", delete_payment, methods=["DELETE"]),
]
