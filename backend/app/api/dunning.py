"""Mahnwesen: Zahlungserinnerung und Mahnung je Mietverhältnis."""
from __future__ import annotations

from urllib.parse import quote

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import dunning
from .common import ApiError, body, endpoint, path_int

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


class DunningIn(BaseModel):
    kind: str = Field(pattern=r"^(erinnerung|mahnung)$")
    letter_date: str = Field(pattern=DATE_RE)
    due_date: str = Field(pattern=DATE_RE)
    months: list[str] = Field(default_factory=list, max_length=240)
    salutation: str = Field(default="", max_length=200)
    extra_text: str = Field(default="", max_length=2000)


def _pdf(data: bytes, name: str) -> Response:
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}", "Cache-Control": "no-store"})


@endpoint()
async def get_state(request: Request, conn, user):
    return dunning.state(conn, path_int(request))


@endpoint()
async def preview(request: Request, conn, user):
    tid = path_int(request)
    data = (await body(request, DunningIn)).model_dump()
    try:
        snap = dunning.build(conn, tid, data)
    except (dunning.DunningError, ValueError) as exc:
        raise ApiError(400, str(exc)) from None
    pdf = await run_in_threadpool(dunning.render, snap)
    return _pdf(pdf, f"{dunning.KINDS[snap['kind']]} Vorschau.pdf")


@endpoint()
async def create(request: Request, conn, user):
    tid = path_int(request)
    data = (await body(request, DunningIn)).model_dump()
    try:
        await run_in_threadpool(dunning.create, conn, tid, data)
    except (dunning.DunningError, ValueError) as exc:
        raise ApiError(400, str(exc)) from None
    return dunning.state(conn, tid)


@endpoint()
async def letter_pdf(request: Request, conn, user):
    pdf, name = await run_in_threadpool(dunning.pdf_of, conn, path_int(request))
    return _pdf(pdf, name)


@endpoint()
async def delete_letter(request: Request, conn, user):
    dunning.delete(conn, path_int(request))
    return {"ok": True}


routes = [
    Route("/api/tenancies/{id:int}/dunning", get_state, methods=["GET"]),
    Route("/api/tenancies/{id:int}/dunning", create, methods=["POST"]),
    Route("/api/tenancies/{id:int}/dunning/preview", preview, methods=["POST"]),
    Route("/api/dunning/{id:int}/pdf", letter_pdf, methods=["GET"]),
    Route("/api/dunning/{id:int}", delete_letter, methods=["DELETE"]),
]
