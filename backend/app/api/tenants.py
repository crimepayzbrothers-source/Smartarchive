"""Mieterverwaltung: Mietverhältnisse anlegen, ändern, beenden; Belegung je Objekt."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from .. import tenancy
from ..db import tx
from .common import ApiError, body, endpoint, int_param, path_int

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


class PartyIn(BaseModel):
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)
    address: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=120)


class TermIn(BaseModel):
    valid_from: str = Field(pattern=DATE_RE)
    cold_rent_cents: int = Field(default=0, ge=0)
    bk_advance_cents: int = Field(default=0, ge=0)
    hk_advance_cents: int = Field(default=0, ge=0)
    persons: int | None = Field(default=None, ge=0, le=50)
    note: str = Field(default="", max_length=200)


class TenancyIn(BaseModel):
    property_id: int
    unit_id: int | None = None
    contract_start: str = Field(pattern=DATE_RE)
    move_in: str | None = Field(default=None, pattern=DATE_RE)
    contract_end: str | None = Field(default=None, pattern=DATE_RE)
    move_out: str | None = Field(default=None, pattern=DATE_RE)
    umlage_notes: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=4000)
    parties: list[PartyIn] = Field(default_factory=list, max_length=10)
    terms: list[TermIn] = Field(default_factory=list, max_length=120)


class EndIn(BaseModel):
    contract_end: str = Field(pattern=DATE_RE)
    move_out: str | None = Field(default=None, pattern=DATE_RE)


def _run(conn, fn):
    try:
        with tx(conn):
            return fn()
    except tenancy.TenancyError as exc:
        raise ApiError(400, str(exc)) from None


@endpoint()
async def list_tenancies(request: Request, conn, user):
    items = tenancy.list_all(conn, int_param(request, "property_id"))
    st = request.query_params.get("status")
    if st in ("aktiv", "beendet", "kuenftig"):
        items = [t for t in items if t["status"] == st]
    q = (request.query_params.get("q") or "").strip().lower()
    if q:
        items = [t for t in items if q in f"{t['names']} {t['property_name']} {t['unit_name'] or ''}".lower()]
    return items


@endpoint()
async def get_tenancy(request: Request, conn, user):
    return tenancy.load(conn, path_int(request))


@endpoint()
async def create_tenancy(request: Request, conn, user):
    data = (await body(request, TenancyIn)).model_dump()
    tid = _run(conn, lambda: tenancy.save(conn, data))
    return tenancy.load(conn, tid)


@endpoint()
async def update_tenancy(request: Request, conn, user):
    tid = path_int(request)
    data = (await body(request, TenancyIn)).model_dump()
    _run(conn, lambda: tenancy.save(conn, data, tid))
    return tenancy.load(conn, tid)


@endpoint()
async def end_tenancy(request: Request, conn, user):
    tid = path_int(request)
    data = await body(request, EndIn)
    _run(conn, lambda: tenancy.end(conn, tid, data.contract_end, data.move_out))
    return tenancy.load(conn, tid)


@endpoint()
async def delete_tenancy(request: Request, conn, user):
    tid = path_int(request)
    _run(conn, lambda: tenancy.delete(conn, tid))
    return {"ok": True}


@endpoint()
async def occupancy(request: Request, conn, user):
    pid = path_int(request)
    if not conn.execute("SELECT 1 FROM properties WHERE id = ?", (pid,)).fetchone():
        raise KeyError(pid)
    year = int_param(request, "year") or date.today().year
    try:
        start = date.fromisoformat(request.query_params.get("from") or f"{year}-01-01")
        end = date.fromisoformat(request.query_params.get("to") or f"{year}-12-31")
    except ValueError:
        raise ApiError(400, "Ungültiger Zeitraum.") from None
    try:
        units = tenancy.property_occupancy(conn, pid, start, end)
    except tenancy.TenancyError as exc:
        raise ApiError(400, str(exc)) from None
    return {"from": start.isoformat(), "to": end.isoformat(), "units": units}


routes = [
    Route("/api/tenancies", list_tenancies, methods=["GET"]),
    Route("/api/tenancies", create_tenancy, methods=["POST"]),
    Route("/api/tenancies/{id:int}", get_tenancy, methods=["GET"]),
    Route("/api/tenancies/{id:int}", update_tenancy, methods=["PUT"]),
    Route("/api/tenancies/{id:int}", delete_tenancy, methods=["DELETE"]),
    Route("/api/tenancies/{id:int}/end", end_tenancy, methods=["POST"]),
    Route("/api/properties/{id:int}/occupancy", occupancy, methods=["GET"]),
]
