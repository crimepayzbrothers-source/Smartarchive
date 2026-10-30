"""Mietkaution je Mietverhältnis."""
from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from .. import deposit
from .common import ApiError, body, endpoint, path_int

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


@endpoint()
async def get_deposit(request: Request, conn, user):
    return deposit.get(conn, path_int(request))


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
    return deposit.get(conn, tid)


@endpoint()
async def add_payment(request: Request, conn, user):
    tid = path_int(request)
    data = await body(request, PaymentIn)
    try:
        deposit.add_payment(conn, tid, data.paid_on, data.amount_cents, data.note)
    except deposit.DepositError as exc:
        raise ApiError(400, str(exc)) from None
    return deposit.get(conn, tid)


@endpoint()
async def delete_payment(request: Request, conn, user):
    tid = deposit.delete_payment(conn, path_int(request))
    return deposit.get(conn, tid)


routes = [
    Route("/api/tenancies/{id:int}/deposit", get_deposit, methods=["GET"]),
    Route("/api/tenancies/{id:int}/deposit", save_deposit, methods=["PUT"]),
    Route("/api/tenancies/{id:int}/deposit/payments", add_payment, methods=["POST"]),
    Route("/api/deposit-payments/{id:int}", delete_payment, methods=["DELETE"]),
]
