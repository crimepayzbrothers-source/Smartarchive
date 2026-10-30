"""Abmahnungen je Mietverhältnis."""
from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from .. import abmahnung
from .common import ApiError, body, endpoint, path_int
from .dunning import DATE_RE, _pdf


class IncidentIn(BaseModel):
    date: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    time: str = Field(default="", max_length=20)
    text: str = Field(default="", max_length=1500)


class WarningIn(BaseModel):
    reason: str = Field(max_length=40)
    letter_date: str = Field(pattern=DATE_RE)
    deadline: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    incidents: list[IncidentIn] = Field(default_factory=list, max_length=50)
    demand: str = Field(default="", max_length=2000)
    consequence: str = Field(default="", max_length=2000)
    extra_text: str = Field(default="", max_length=2000)
    salutation: str = Field(default="", max_length=200)
    delivery: str = Field(default="einwurf", max_length=20)


def _data(m: WarningIn) -> dict:
    d = m.model_dump()
    d["deadline"] = d["deadline"] or None
    return d


@endpoint()
async def get_state(request: Request, conn, user):
    return abmahnung.state(conn, path_int(request))


@endpoint()
async def preview(request: Request, conn, user):
    tid = path_int(request)
    data = _data(await body(request, WarningIn))
    try:
        snap = abmahnung.build(conn, tid, data)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    return _pdf(await run_in_threadpool(abmahnung.render, snap), "Abmahnung Vorschau.pdf")


@endpoint()
async def create(request: Request, conn, user):
    tid = path_int(request)
    data = _data(await body(request, WarningIn))
    try:
        await run_in_threadpool(abmahnung.create, conn, tid, data)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    return abmahnung.state(conn, tid)


@endpoint()
async def letter_pdf(request: Request, conn, user):
    pdf, name = await run_in_threadpool(abmahnung.pdf_of, conn, path_int(request))
    return _pdf(pdf, name)


@endpoint()
async def delete_letter(request: Request, conn, user):
    abmahnung.delete(conn, path_int(request))
    return {"ok": True}


routes = [
    Route("/api/tenancies/{id:int}/warnings", get_state, methods=["GET"]),
    Route("/api/tenancies/{id:int}/warnings", create, methods=["POST"]),
    Route("/api/tenancies/{id:int}/warnings/preview", preview, methods=["POST"]),
    Route("/api/warnings/{id:int}/pdf", letter_pdf, methods=["GET"]),
    Route("/api/warnings/{id:int}", delete_letter, methods=["DELETE"]),
]
