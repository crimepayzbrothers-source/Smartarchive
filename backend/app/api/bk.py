"""Betriebskostenabrechnung: Zeiträume, Kosten, Schlüssel, Werte, Vorauszahlungen, Prüfung, Freigabe, PDF."""
from __future__ import annotations

import io
import json
import zipfile

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import betriebskosten as bk
from .. import bk_pdf, bk_receipts, documents, storage
from ..db import audit, get_setting, now, row, rows, set_setting, tx
from .common import ApiError, body, endpoint, int_param, path_int

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


def _run(conn, fn):
    try:
        with tx(conn):
            return fn()
    except bk.BKError as exc:
        raise ApiError(400, str(exc)) from None


def _period_or_404(conn, period_id: int) -> dict:
    return bk.load_period(conn, period_id)


# --- Stammdaten ---------------------------------------------------------------------

@endpoint()
async def meta(request: Request, conn, user):
    return {"cost_types": [{**{a: b for a, b in v.items() if a != "key"}, "key": k, "default_key": v["key"]} for k, v in bk.COST_TYPES.items()],
            "keys": [{"key": k, **v} for k, v in bk.KEYS.items()],
            "landlord": get_setting(conn, "landlord", {}) or {}}


class LandlordIn(BaseModel):
    name: str = Field(default="", max_length=120)
    address: str = Field(default="", max_length=300)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=120)
    iban: str = Field(default="", max_length=40)
    bank: str = Field(default="", max_length=80)
    place: str = Field(default="", max_length=80)


@endpoint()
async def save_landlord(request: Request, conn, user):
    data = (await body(request, LandlordIn)).model_dump()
    current = get_setting(conn, "landlord", {}) or {}
    current.update({k: v.strip() for k, v in data.items()})
    set_setting(conn, "landlord", current)
    return current


@endpoint()
async def upload_logo(request: Request, conn, user):
    form = await request.form(max_files=1, max_fields=2)
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        raise ApiError(400, "Keine Datei erhalten.")
    data = await f.read()
    await form.close()
    if len(data) > 2 * 1024 * 1024:
        raise ApiError(413, "Das Logo darf höchstens 2 MB groß sein.")
    mime = storage.sniff_mime(data)
    if mime not in ("image/png", "image/jpeg"):
        raise ApiError(400, "Bitte ein PNG- oder JPEG-Bild verwenden.")
    _, rel = storage.store_original(data, mime)
    current = get_setting(conn, "landlord", {}) or {}
    current["logo"] = rel
    set_setting(conn, "landlord", current)
    return current


@endpoint()
async def delete_logo(request: Request, conn, user):
    current = get_setting(conn, "landlord", {}) or {}
    current.pop("logo", None)
    set_setting(conn, "landlord", current)
    return current


# --- Zeiträume ----------------------------------------------------------------------

class PeriodIn(BaseModel):
    property_id: int
    start: str = Field(pattern=DATE_RE)
    end: str = Field(pattern=DATE_RE)


class PeriodPatch(BaseModel):
    vacancy_persons: float | None = Field(default=None, ge=0, le=10)
    co2: dict | None = None
    notes: str | None = Field(default=None, max_length=4000)


@endpoint()
async def list_periods(request: Request, conn, user):
    pid = int_param(request, "property_id")
    items = rows(conn.execute(
        "SELECT b.*, p.name AS property_name, (SELECT MAX(version) FROM bk_statements s WHERE s.period_id = b.id) AS version, "
        "(SELECT COALESCE(SUM(amount_cents), 0) FROM bk_costs c WHERE c.period_id = b.id AND c.cost_type != 'nicht_umlagefaehig') AS costs "
        "FROM bk_periods b JOIN properties p ON p.id = b.property_id WHERE (? IS NULL OR b.property_id = ?) ORDER BY b.start DESC, p.name",
        (pid, pid)))
    for it in items:
        it.pop("co2", None)
        it.pop("acknowledged", None)
    return items


@endpoint()
async def create_period(request: Request, conn, user):
    data = await body(request, PeriodIn)
    if not conn.execute("SELECT 1 FROM properties WHERE id = ?", (data.property_id,)).fetchone():
        raise ApiError(400, "Mietobjekt existiert nicht.")
    pid = _run(conn, lambda: bk.create_period(conn, data.property_id, data.start, data.end))
    return {"id": pid}


def _full(conn, period_id: int) -> dict:
    result = bk.compute(conn, period_id)
    result["keys"] = {ct: dict(zip(("key", "reason"), bk.key_for(conn, period_id, ct))) for ct in bk.COST_TYPES if ct != "nicht_umlagefaehig"}
    result["values"] = rows(conn.execute("SELECT * FROM bk_values WHERE period_id = ?", (period_id,)))
    result["advances"] = rows(conn.execute("SELECT * FROM bk_advances WHERE period_id = ?", (period_id,)))
    result["importable"] = len(bk.importable_expenses(conn, period_id))
    result["receipts"] = bk_receipts.receipt_status(conn, period_id)
    result["statements"] = rows(conn.execute("SELECT id, version, created_at, note FROM bk_statements WHERE period_id = ? ORDER BY version DESC",
                                             (period_id,)))
    return result


@endpoint()
async def get_period(request: Request, conn, user):
    return _full(conn, path_int(request))


@endpoint()
async def patch_period(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    data = (await body(request, PeriodPatch)).model_dump(exclude_unset=True)

    def go():
        if "co2" in data and data["co2"] is not None:
            data["co2"] = json.dumps(data["co2"], ensure_ascii=False)
        if data:
            conn.execute(f"UPDATE bk_periods SET {', '.join(k + ' = ?' for k in data)} WHERE id = ?", (*data.values(), period_id))
            bk.touch(conn, period_id)
            audit(conn, "updated", "bk_period", period_id, {k: (v if k != "notes" else "…") for k, v in data.items()})
    _run(conn, go)
    return _full(conn, period_id)


@endpoint()
async def delete_period(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    if conn.execute("SELECT 1 FROM bk_statements WHERE period_id = ?", (period_id,)).fetchone():
        raise ApiError(400, "Für diesen Zeitraum gibt es freigegebene Abrechnungen. Er bleibt zur Nachvollziehbarkeit erhalten.")
    with tx(conn):
        conn.execute("DELETE FROM bk_periods WHERE id = ?", (period_id,))
        audit(conn, "deleted", "bk_period", period_id)
    return {"ok": True}


class KeyIn(BaseModel):
    key: str
    reason: str = Field(default="", max_length=500)


@endpoint()
async def set_key(request: Request, conn, user):
    period_id = path_int(request)
    ct = request.path_params["ct"]
    data = await body(request, KeyIn)
    if ct not in bk.COST_TYPES or ct == "nicht_umlagefaehig" or data.key not in bk.KEYS:
        raise ApiError(400, "Unbekannte Kostenart oder unbekannter Schlüssel.")
    _period_or_404(conn, period_id)

    def go():
        reason = data.reason.strip()
        if any(reason == k["why"] for k in bk.KEYS.values()):
            reason = ""  # Standarderläuterung nicht als eigene Begründung speichern
        conn.execute("INSERT INTO bk_keys(period_id, cost_type, key, reason) VALUES(?,?,?,?) ON CONFLICT(period_id, cost_type) "
                     "DO UPDATE SET key = excluded.key, reason = excluded.reason", (period_id, ct, data.key, reason))
        bk.touch(conn, period_id)
        audit(conn, "key_set", "bk_period", period_id, {"cost_type": ct, "key": data.key})
    _run(conn, go)
    return _full(conn, period_id)


# --- Kosten -------------------------------------------------------------------------

class CostIn(BaseModel):
    cost_type: str
    description: str = Field(default="", max_length=300)
    amount_cents: int
    invoice_date: str | None = Field(default=None, pattern=DATE_RE)
    unit_id: int | None = None
    document_id: int | None = None
    expense_id: int | None = None


class CostPatch(BaseModel):
    verified: bool | None = None
    cost_type: str | None = None
    description: str | None = Field(default=None, max_length=300)
    amount_cents: int | None = None
    invoice_date: str | None = Field(default=None, pattern=DATE_RE)
    unit_id: int | None = None


class ImportIn(BaseModel):
    items: list[dict] = Field(default_factory=list, max_length=500)


@endpoint()
async def importable(request: Request, conn, user):
    return bk.importable_expenses(conn, path_int(request))


@endpoint()
async def add_cost(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    data = (await body(request, CostIn)).model_dump()
    _run(conn, lambda: bk.add_cost(conn, period_id, data))
    return _full(conn, period_id)


@endpoint()
async def import_costs(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    data = await body(request, ImportIn)
    avail = {e["id"]: e for e in bk.importable_expenses(conn, period_id)}

    def go():
        n = 0
        for it in data.items:
            e = avail.get(it.get("expense_id"))
            if not e:
                continue
            bk.add_cost(conn, period_id, {"cost_type": it.get("cost_type") or e["suggested_type"], "expense_id": e["id"],
                                          "amount_cents": e["amount_cents"], "invoice_date": e["invoice_date"] or e["paid_on"],
                                          "description": " – ".join(x for x in (e["payee"], e["document_title"]) if x)[:300],
                                          "unit_id": e["unit_id"]})
            n += 1
        return n
    n = _run(conn, go)
    result = _full(conn, period_id)
    result["imported"] = n
    return result


@endpoint()
async def receipt_scan(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    return await run_in_threadpool(bk_receipts.scan, conn, period_id)


@endpoint()
async def receipt_search(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    q = (request.query_params.get("q") or "").strip()
    if not q:
        raise ApiError(400, "Bitte einen Suchbegriff eingeben.")
    return await run_in_threadpool(bk_receipts.search_period, conn, period_id, q)


class HiddenIn(BaseModel):
    document_id: int
    hidden: bool = True


@endpoint()
async def receipt_hidden_list(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    return bk_receipts.hidden_list(conn, period_id)


@endpoint()
async def receipt_hidden_set(request: Request, conn, user):
    period_id = path_int(request)
    data = await body(request, HiddenIn)
    _run(conn, lambda: bk_receipts.set_hidden(conn, period_id, data.document_id, data.hidden))
    return bk_receipts.hidden_list(conn, period_id)


class ReceiptImportIn(BaseModel):
    items: list[dict] = Field(default_factory=list, max_length=200)


@endpoint()
async def receipt_import(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    data = await body(request, ReceiptImportIn)
    n = _run(conn, lambda: bk_receipts.import_positions(conn, period_id, data.items))
    result = _full(conn, period_id)
    result["imported"] = n
    return result


@endpoint()
async def patch_cost(request: Request, conn, user):
    cid = path_int(request)
    c = row(conn.execute("SELECT * FROM bk_costs WHERE id = ?", (cid,)))
    if not c:
        raise KeyError(cid)
    data = (await body(request, CostPatch)).model_dump(exclude_unset=True)
    if "cost_type" in data and data["cost_type"] not in bk.COST_TYPES:
        raise ApiError(400, "Unbekannte Kostenart.")
    if "amount_cents" in data and not data["amount_cents"]:
        raise ApiError(400, "Betrag fehlt.")

    if "verified" in data:
        v = data.pop("verified")
        data["verified_at"] = now() if v else None
    elif data and c["origin"] != "manuell":
        data["verified_at"] = now()  # geändert = mit dem Beleg abgeglichen

    def go():
        if data:
            conn.execute(f"UPDATE bk_costs SET {', '.join(k + ' = ?' for k in data)} WHERE id = ?", (*data.values(), cid))
            bk.touch(conn, c["period_id"])
            audit(conn, "updated", "bk_cost", cid, data)
    _run(conn, go)
    return _full(conn, c["period_id"])


@endpoint()
async def delete_cost(request: Request, conn, user):
    cid = path_int(request)
    c = row(conn.execute("SELECT * FROM bk_costs WHERE id = ?", (cid,)))
    if not c:
        raise KeyError(cid)
    with tx(conn):
        conn.execute("DELETE FROM bk_costs WHERE id = ?", (cid,))
        bk.touch(conn, c["period_id"])
        audit(conn, "deleted", "bk_cost", cid, {"amount_cents": c["amount_cents"], "cost_type": c["cost_type"]})
    return _full(conn, c["period_id"])


# --- Werte (Verbrauch, Ablesefirma, individuelle Anteile) ---------------------------

class ValueIn(BaseModel):
    cost_type: str
    unit_id: int | None = None
    tenancy_id: int | None = None
    value: float | None = None
    amount_cents: int | None = None
    estimated: bool = False
    note: str = Field(default="", max_length=300)


@endpoint()
async def set_value(request: Request, conn, user):
    period_id = path_int(request)
    p = _period_or_404(conn, period_id)
    v = await body(request, ValueIn)
    if v.cost_type not in bk.COST_TYPES:
        raise ApiError(400, "Unbekannte Kostenart.")
    if v.unit_id and not conn.execute("SELECT 1 FROM units WHERE id = ? AND property_id = ?", (v.unit_id, p["property_id"])).fetchone():
        raise ApiError(400, "Die Einheit gehört nicht zu diesem Objekt.")
    if v.estimated and not v.note.strip():
        raise ApiError(400, "Bei einem geschätzten Wert bitte Grund und Verfahren angeben (z. B. „Vorjahresverbrauch, Zähler defekt“).")

    def go():
        conn.execute("DELETE FROM bk_values WHERE period_id = ? AND cost_type = ? AND COALESCE(unit_id, 0) = COALESCE(?, 0) "
                     "AND COALESCE(tenancy_id, 0) = COALESCE(?, 0)", (period_id, v.cost_type, v.unit_id, v.tenancy_id))
        if v.value is not None or v.amount_cents is not None:
            conn.execute("INSERT INTO bk_values(period_id, cost_type, unit_id, tenancy_id, value, amount_cents, estimated, note) "
                         "VALUES(?,?,?,?,?,?,?,?)", (period_id, v.cost_type, v.unit_id, v.tenancy_id, v.value, v.amount_cents,
                                                     int(v.estimated), v.note.strip()))
        bk.touch(conn, period_id)
    _run(conn, go)
    return _full(conn, period_id)


# --- Vorauszahlungen (Ist) ----------------------------------------------------------

class AdvanceIn(BaseModel):
    bk_cents: int = Field(ge=0)
    hk_cents: int = Field(ge=0)
    note: str = Field(default="", max_length=300)


@endpoint()
async def advance_suggestion(request: Request, conn, user):
    return bk.advances_suggestion(conn, path_int(request), path_int(request, "tid"))


@endpoint()
async def set_advance(request: Request, conn, user):
    period_id, tid = path_int(request), path_int(request, "tid")
    _period_or_404(conn, period_id)
    data = await body(request, AdvanceIn)

    def go():
        conn.execute("INSERT INTO bk_advances(period_id, tenancy_id, bk_cents, hk_cents, confirmed, note) VALUES(?,?,?,?,1,?) "
                     "ON CONFLICT(period_id, tenancy_id) DO UPDATE SET bk_cents = excluded.bk_cents, hk_cents = excluded.hk_cents, "
                     "confirmed = 1, note = excluded.note", (period_id, tid, data.bk_cents, data.hk_cents, data.note.strip()))
        bk.touch(conn, period_id)
        audit(conn, "advances_confirmed", "bk_period", period_id, {"tenancy_id": tid, "bk": data.bk_cents, "hk": data.hk_cents})
    _run(conn, go)
    return _full(conn, period_id)


# --- Prüfung, Freigabe, Fassungen ------------------------------------------------------

class AckIn(BaseModel):
    codes: list[str] = Field(default_factory=list, max_length=500)


@endpoint()
async def acknowledge(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    data = await body(request, AckIn)
    with tx(conn):
        conn.execute("UPDATE bk_periods SET acknowledged = ? WHERE id = ?", (json.dumps(sorted(set(data.codes))), period_id))
        audit(conn, "warnings_acknowledged", "bk_period", period_id, {"codes": data.codes})
    return _full(conn, period_id)


class ReleaseIn(BaseModel):
    note: str = Field(default="", max_length=500)
    archive: bool = True


def _archive(conn, period_id: int, statement_id: int) -> list[int]:
    """Abrechnungen als PDF im Archiv ablegen (Vermietung › Nebenkosten, Objekt), durchsuchbar."""
    st = row(conn.execute("SELECT * FROM bk_statements WHERE id = ?", (statement_id,)))
    snap = json.loads(st["snapshot"])
    res = snap["result"]
    area_id = conn.execute("SELECT id FROM areas WHERE key = 'vermietung'").fetchone()[0]
    cat = conn.execute("SELECT id FROM categories WHERE area_id = ? AND name = 'Nebenkosten'", (area_id,)).fetchone()
    ids = []
    year = res["period"]["start"][:4] if res["period"]["start"][:4] == res["period"]["end"][:4] else \
        f"{res['period']['start'][:4]}-{res['period']['end'][2:4]}"
    for r in res["results"]:
        pdf = bk_pdf.render(snap, r["tenancy_id"])
        title = f"Betriebskostenabrechnung {year} {r['names']}" + (f" (Fassung {st['version']})" if st["version"] > 1 else "")
        sha, rel = storage.store_original(pdf, "application/pdf")
        ts = now()
        with tx(conn):
            doc_id = conn.execute(
                "INSERT INTO documents(area_id, category_id, property_id, unit_id, title, original_name, doc_type, doc_date, date_status, "
                "upload_area, status, filed_by, filed_reason, confidence, confirmed, filed_at, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (area_id, cat[0] if cat else None, res["period"]["property_id"], r["unit_id"], title[:120], f"{title}.pdf"[:200],
                 "Betriebskostenabrechnung", st["created_at"][:10], "sure", "vermietung", "filed", "user",
                 "Von der Betriebskostenabrechnung erstellt.", 1.0, 1, ts, ts, ts)).lastrowid
            conn.execute("INSERT INTO files(document_id, role, sha256, mime, size, path, original_name, position, created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (doc_id, "original", sha, "application/pdf", len(pdf), rel, f"{title}.pdf", 0, ts))
            conn.execute("INSERT INTO document_text(document_id, content) VALUES(?, '')", (doc_id,))
            audit(conn, "created", "document", doc_id, {"quelle": "betriebskostenabrechnung", "statement_id": statement_id})
        try:
            documents.extract_text(conn, doc_id)
        except Exception:  # noqa: BLE001 – Archivierung darf die Freigabe nicht verhindern
            pass
        with tx(conn):
            documents.index_document(conn, doc_id)
        ids.append(doc_id)
    with tx(conn):
        snap["archived_documents"] = ids
        conn.execute("UPDATE bk_statements SET snapshot = ? WHERE id = ?", (json.dumps(snap, ensure_ascii=False, default=str), statement_id))
    return ids


@endpoint()
async def release(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    data = await body(request, ReleaseIn)
    sid = _run(conn, lambda: bk.release(conn, period_id, data.note))
    if data.archive:
        await run_in_threadpool(_archive, conn, period_id, sid)
    out = _full(conn, period_id)
    out["statement_id"] = sid
    return out


@endpoint()
async def get_statement(request: Request, conn, user):
    st = row(conn.execute("SELECT * FROM bk_statements WHERE id = ?", (path_int(request),)))
    if not st:
        raise KeyError(path_int(request))
    st["snapshot"] = json.loads(st["snapshot"])
    return st


def _disposition(name: str, inline: bool) -> str:
    """Dateiname nach RFC 6266/5987: ASCII-Ersatz plus UTF-8-Fassung (Umlaute, ß)."""
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
    return f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _pdf_response(data: bytes, name: str, inline: bool = True) -> Response:
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": _disposition(name, inline), "Cache-Control": "no-store"})


@endpoint()
async def statement_pdf(request: Request, conn, user):
    st = row(conn.execute("SELECT * FROM bk_statements WHERE id = ?", (path_int(request),)))
    if not st:
        raise KeyError(path_int(request))
    snap = json.loads(st["snapshot"])
    tid = int_param(request, "tenancy_id")
    data = await run_in_threadpool(bk_pdf.render, snap, tid)
    return _pdf_response(data, f"Betriebskostenabrechnung-{st['period_id']}-v{st['version']}.pdf")


@endpoint()
async def preview_pdf(request: Request, conn, user):
    period_id = path_int(request)
    _period_or_404(conn, period_id)
    snap = {"result": bk.compute(conn, period_id), "landlord": get_setting(conn, "landlord", {}) or {}, "released_at": now(),
            "version": None, "documents": bk.receipts(conn, period_id)}
    data = await run_in_threadpool(bk_pdf.render, snap, int_param(request, "tenancy_id"), True)
    return _pdf_response(data, f"Entwurf-Betriebskostenabrechnung-{period_id}.pdf")


@endpoint()
async def receipts(request: Request, conn, user):
    return bk.receipts(conn, path_int(request))


@endpoint()
async def receipts_zip(request: Request, conn, user):
    period_id = path_int(request)
    p = _period_or_404(conn, period_id)

    def build() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            listing = ["Nr;Kostenart;Beschreibung;Betrag;Datum;Datei"]
            for i, r in enumerate(bk.receipts(conn, period_id), 1):
                name = ""
                if r["document_id"]:
                    f = documents.best_pdf(conn, r["document_id"])
                    if f:
                        name = f"{i:03d} {bk.COST_TYPES[r['cost_type']]['label'][:30]}.pdf".replace("/", "-")
                        z.write(storage.abs_path(f["path"]), name)
                listing.append(f"{i};{bk.COST_TYPES[r['cost_type']]['label']};{(r['description'] or '').replace(';', ',')};"
                               f"{r['amount_cents'] / 100:.2f}".replace(".", ",") + f";{r['invoice_date'] or ''};{name or 'kein Beleg'}")
            z.writestr("Belegliste.csv", "﻿" + "\n".join(listing))
        return buf.getvalue()
    data = await run_in_threadpool(build)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": _disposition(f"Belege-{p['property_name']}-{p['start'][:4]}.zip", False)})


routes = [
    Route("/api/bk/meta", meta, methods=["GET"]),
    Route("/api/bk/landlord", save_landlord, methods=["PUT"]),
    Route("/api/bk/landlord/logo", upload_logo, methods=["POST"]),
    Route("/api/bk/landlord/logo", delete_logo, methods=["DELETE"]),
    Route("/api/bk/periods", list_periods, methods=["GET"]),
    Route("/api/bk/periods", create_period, methods=["POST"]),
    Route("/api/bk/periods/{id:int}", get_period, methods=["GET"]),
    Route("/api/bk/periods/{id:int}", patch_period, methods=["PATCH"]),
    Route("/api/bk/periods/{id:int}", delete_period, methods=["DELETE"]),
    Route("/api/bk/periods/{id:int}/keys/{ct}", set_key, methods=["PUT"]),
    Route("/api/bk/periods/{id:int}/importable", importable, methods=["GET"]),
    Route("/api/bk/periods/{id:int}/costs", add_cost, methods=["POST"]),
    Route("/api/bk/periods/{id:int}/costs/import", import_costs, methods=["POST"]),
    Route("/api/bk/periods/{id:int}/receipt-scan", receipt_scan, methods=["GET"]),
    Route("/api/bk/periods/{id:int}/receipt-scan/import", receipt_import, methods=["POST"]),
    Route("/api/bk/periods/{id:int}/receipt-search", receipt_search, methods=["GET"]),
    Route("/api/bk/periods/{id:int}/receipt-hidden", receipt_hidden_list, methods=["GET"]),
    Route("/api/bk/periods/{id:int}/receipt-hidden", receipt_hidden_set, methods=["PUT"]),
    Route("/api/bk/costs/{id:int}", patch_cost, methods=["PATCH"]),
    Route("/api/bk/costs/{id:int}", delete_cost, methods=["DELETE"]),
    Route("/api/bk/periods/{id:int}/values", set_value, methods=["PUT"]),
    Route("/api/bk/periods/{id:int}/advances/{tid:int}/suggestion", advance_suggestion, methods=["GET"]),
    Route("/api/bk/periods/{id:int}/advances/{tid:int}", set_advance, methods=["PUT"]),
    Route("/api/bk/periods/{id:int}/acknowledge", acknowledge, methods=["POST"]),
    Route("/api/bk/periods/{id:int}/release", release, methods=["POST"]),
    Route("/api/bk/periods/{id:int}/preview.pdf", preview_pdf, methods=["GET"]),
    Route("/api/bk/periods/{id:int}/receipts", receipts, methods=["GET"]),
    Route("/api/bk/periods/{id:int}/receipts.zip", receipts_zip, methods=["GET"]),
    Route("/api/bk/statements/{id:int}", get_statement, methods=["GET"]),
    Route("/api/bk/statements/{id:int}/pdf", statement_pdf, methods=["GET"]),
]
