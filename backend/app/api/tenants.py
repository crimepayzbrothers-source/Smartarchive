"""Mieterverwaltung: Mietverhältnisse anlegen, ändern, beenden; Belegung je Objekt."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from starlette.concurrency import run_in_threadpool

from .. import config, storage, tenancy, tenancy_extract
from ..db import get_setting, rows, tx
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


MAX_FILES, MAX_BYTES = 10, 25 * 1024 * 1024


@endpoint()
async def extract(request: Request, conn, user):
    """Mietvertrag (PDF/Foto) oder Screenshots auslesen. Speichert nichts – liefert nur Vorschläge fürs Formular."""
    form = await request.form(max_files=MAX_FILES, max_fields=5)
    try:
        property_id = int(str(form.get("property_id") or "0"))
    except ValueError:
        property_id = 0
    use_ai = str(form.get("use_ai") or "") in ("1", "true", "ja")
    uploads = [f for f in form.getlist("files") if hasattr(f, "read")]
    datas = [(await f.read(), f.filename or "datei") for f in uploads]
    await form.close()
    if not datas:
        raise ApiError(400, "Bitte eine Datei wählen.")
    if sum(len(d) for d, _ in datas) > MAX_BYTES:
        raise ApiError(413, "Zusammen höchstens 25 MB.")
    if not conn.execute("SELECT 1 FROM properties WHERE id = ?", (property_id,)).fetchone():
        raise ApiError(400, "Bitte das Mietobjekt wählen.")
    units = rows(conn.execute("SELECT id, name FROM units WHERE property_id = ? ORDER BY id", (property_id,)))
    landlord = (get_setting(conn, "landlord", {}) or {}).get("name") or ""

    def run() -> dict:
        texts = []
        for data, name in datas:
            mime = storage.sniff_mime(data, filename=name)
            try:
                texts.append(tenancy_extract.text_from_file(data, mime))
            except ValueError as exc:
                raise ApiError(400, f"{name}: {exc}") from None
        text = "\n".join(texts)
        local = tenancy_extract.parse(text, units, [landlord] if landlord else [])
        ai, ai_error = [], None
        if use_ai:
            if not (bool(get_setting(conn, "ai_enabled", True)) and config.settings.ai_available):
                ai_error = "KI ist nicht eingerichtet oder ausgeschaltet."
            else:
                try:
                    ai = tenancy_extract.ai_parse(text, units)
                except Exception as exc:  # noqa: BLE001 – lokale Auslese reicht als Ergebnis
                    ai_error = str(exc)
        return {"results": tenancy_extract.merge(local, ai), "chars": len(text.strip()), "ai_used": bool(ai), "ai_error": ai_error,
                "files": len(datas)}
    return await run_in_threadpool(run)


@endpoint()
async def preview(request: Request, conn, user):
    """Seitenbilder der gewählten Dateien zum Abtippen anzeigen (nichts wird gespeichert)."""
    import base64
    form = await request.form(max_files=MAX_FILES, max_fields=2)
    datas = [(await f.read(), f.filename or "datei") for f in form.getlist("files") if hasattr(f, "read")]
    await form.close()
    if not datas:
        raise ApiError(400, "Bitte eine Datei wählen.")
    if sum(len(d) for d, _ in datas) > MAX_BYTES:
        raise ApiError(413, "Zusammen höchstens 25 MB.")

    def run() -> list[dict]:
        pages = []
        for idx, (data, name) in enumerate(datas):
            try:
                imgs = tenancy_extract.preview_pages(data, storage.sniff_mime(data, filename=name))
            except ValueError as exc:
                raise ApiError(400, f"{name}: {exc}") from None
            for n, img in enumerate(imgs, 1):
                pages.append({"file": idx, "name": name, "page": n, "pages": len(imgs),
                              "src": "data:image/jpeg;base64," + base64.b64encode(img).decode()})
        return pages
    return {"pages": await run_in_threadpool(run)}


routes = [
    Route("/api/tenancies/extract", extract, methods=["POST"]),
    Route("/api/tenancies/preview", preview, methods=["POST"]),
    Route("/api/tenancies", list_tenancies, methods=["GET"]),
    Route("/api/tenancies", create_tenancy, methods=["POST"]),
    Route("/api/tenancies/{id:int}", get_tenancy, methods=["GET"]),
    Route("/api/tenancies/{id:int}", update_tenancy, methods=["PUT"]),
    Route("/api/tenancies/{id:int}", delete_tenancy, methods=["DELETE"]),
    Route("/api/tenancies/{id:int}/end", end_tenancy, methods=["POST"]),
    Route("/api/properties/{id:int}/occupancy", occupancy, methods=["GET"]),
]
