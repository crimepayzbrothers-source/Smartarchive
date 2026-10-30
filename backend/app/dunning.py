"""Mahnwesen: Zahlungserinnerung und Mahnung je Mietverhältnis.

Grundlage sind ausschließlich die Einnahmen (Soll/Ist je Monat). Beträge werden nie geschätzt:
offen ist, was laut Mietvertrag fällig war und weder gebucht noch als „angenommen“ verbucht ist –
also im Wesentlichen die Monate, die Sie als „ausgeblieben“ hinterlegt haben.

Überzahlungen einzelner Monate werden auf die ältesten offenen Monate angerechnet (§ 366 Abs. 2 BGB).
Die Kündigungsschwellen (§ 543 Abs. 2 Satz 1 Nr. 3, § 569 Abs. 3 Nr. 1 BGB) werden nur rechnerisch angezeigt –
eine rechtliche Bewertung nimmt die App nicht vor.
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import documents, income, storage
from . import tenancy as tn
from .bk_pdf import LINE, MUTED, PETROL, SOFT, S, P, _BOLD, _FONT, dt, euro, letterhead
from .db import audit, get_setting, now, row, rows, tx

KINDS = {"erinnerung": "Zahlungserinnerung", "mahnung": "Mahnung"}
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


class DunningError(ValueError):
    pass


def month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def _today() -> date:
    return date.today()


def open_months(conn: sqlite3.Connection, t: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """Fällige Monate mit Rückstand, Überzahlungen auf die ältesten Rückstände angerechnet."""
    today = today or _today()
    assume = bool(get_setting(conn, "rent_assume_paid", True))
    start, end = tn.span(t)
    last = min(end or today, today)
    debts: list[dict[str, Any]] = []
    credit = 0
    for year in range(start.year, last.year + 1):
        months, _ = income.month_rows(conn, t, year, today, assume, None)
        for m in months:
            if m["status"] in ("frei", "kuenftig"):
                continue
            diff = m["soll"]["total"] - m["ist"]
            if diff > 0:
                debts.append({"month": f"{year}-{m['month']:02d}", "soll": m["soll"]["total"], "ist": m["ist"], "open": diff})
            elif diff < 0:
                credit += -diff
    for d in debts:  # Tilgung: älteste Schuld zuerst
        use = min(credit, d["open"])
        d["open"] -= use
        d["credited"] = use
        credit -= use
    return [d for d in debts if d["open"] > 0]


def monthly_rent(t: dict[str, Any], today: date | None = None) -> int:
    today = today or _today()
    start, end = tn.span(t)
    term = tn.term_at(t["terms"], min(max(today, start), end or date.max))
    return (term["cold_rent_cents"] + term["bk_advance_cents"] + term["hk_advance_cents"]) if term else 0


def _next_month(key: str) -> str:
    y, m = map(int, key.split("-"))
    return f"{y + (m == 12)}-{(m % 12) + 1:02d}"


def threshold(items: list[dict[str, Any]], rent: int) -> dict[str, Any]:
    """Rein rechnerischer Abgleich mit § 543 Abs. 2 Satz 1 Nr. 3 BGB (Wohnraum: § 569 Abs. 3 Nr. 1 BGB)."""
    total = sum(i["open"] for i in items)
    by_month = {i["month"]: i["open"] for i in items}
    consecutive = any(_next_month(k) in by_month and v + by_month[_next_month(k)] > rent for k, v in by_month.items()) if rent else False
    two_rents = bool(rent) and total >= 2 * rent
    return {"monthly_rent": rent, "total": total, "consecutive": consecutive, "two_rents": two_rents, "reached": consecutive or two_rents}


def letters(conn: sqlite3.Connection, tenancy_id: int) -> list[dict[str, Any]]:
    out = rows(conn.execute("SELECT id, kind, letter_date, due_date, months, total_cents, document_id, created_at FROM dunning_letters "
                            "WHERE tenancy_id = ? ORDER BY letter_date DESC, id DESC", (tenancy_id,)))
    for x in out:
        x["months"] = json.loads(x["months"])
        x["label"] = KINDS[x["kind"]]
    return out


def by_month(conn: sqlite3.Connection, tenancy_id: int) -> dict[str, list[dict[str, str]]]:
    """Für die Einnahmen: welcher Monat wurde wann angemahnt."""
    out: dict[str, list[dict[str, str]]] = {}
    for x in letters(conn, tenancy_id):
        for m in x["months"]:
            out.setdefault(m["month"], []).append({"date": x["letter_date"], "kind": x["kind"]})
    for v in out.values():
        v.sort(key=lambda e: e["date"])
    return out


def default_salutation(t: dict[str, Any]) -> str:
    names = [f"{p['first_name']} {p['last_name']}".strip() for p in t["parties"]]
    return f"Guten Tag {' und '.join(n for n in names if n)}," if any(names) else "Guten Tag,"


def state(conn: sqlite3.Connection, tenancy_id: int) -> dict[str, Any]:
    t = tn.load(conn, tenancy_id)
    today = _today()
    items = open_months(conn, t, today)
    ll = get_setting(conn, "landlord", {}) or {}
    return {
        "open": items, "total": sum(i["open"] for i in items), "threshold": threshold(items, monthly_rent(t, today)),
        "letters": letters(conn, tenancy_id), "assume_paid": bool(get_setting(conn, "rent_assume_paid", True)),
        "defaults": {"letter_date": today.isoformat(), "due_date": (today + timedelta(days=14)).isoformat(),
                     "salutation": default_salutation(t)},
        "landlord_missing": [k for k, label in (("name", "Name"), ("address", "Anschrift"), ("iban", "IBAN")) if not ll.get(k)],
    }


def build(conn: sqlite3.Connection, tenancy_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Alles, was der Brief braucht – Beträge nur aus den offenen Monaten, nie aus Eingaben."""
    t = tn.load(conn, tenancy_id)
    if data["kind"] not in KINDS:
        raise DunningError("Unbekannte Art des Schreibens.")
    letter_d, due_d = date.fromisoformat(data["letter_date"]), date.fromisoformat(data["due_date"])
    if due_d <= letter_d:
        raise DunningError("Die Zahlungsfrist muss nach dem Briefdatum liegen.")
    available = {i["month"]: i for i in open_months(conn, t)}
    chosen = sorted(set(data.get("months") or []))
    if not chosen:
        raise DunningError("Bitte mindestens einen offenen Monat auswählen.")
    missing = [m for m in chosen if m not in available]
    if missing:
        raise DunningError(f"Für {', '.join(month_label(m) for m in missing)} ist laut Einnahmen nichts offen.")
    items = [available[m] for m in chosen]
    earlier = [x for x in letters(conn, tenancy_id) if x["letter_date"] < data["letter_date"]
               and {m["month"] for m in x["months"]} & set(chosen)]
    return {
        "kind": data["kind"], "letter_date": data["letter_date"], "due_date": data["due_date"],
        "salutation": (data.get("salutation") or "").strip() or default_salutation(t), "extra_text": (data.get("extra_text") or "").strip(),
        "items": items, "total": sum(i["open"] for i in items),
        "earlier": [{"kind": x["kind"], "date": x["letter_date"]} for x in sorted(earlier, key=lambda x: x["letter_date"])],
        "landlord": get_setting(conn, "landlord", {}) or {},
        "tenancy": {"id": t["id"], "names": t["names"], "parties": t["parties"], "unit_name": t["unit_name"],
                    "property_name": t["property_name"], "property_address": t["property_address"], "property_id": t["property_id"],
                    "unit_id": t["unit_id"]},
    }


def _object_line(ten: dict[str, Any]) -> str:
    addr = (ten.get("property_address") or ten.get("property_name") or "").replace("\n", ", ")
    return f"Wohnung {ten['unit_name']}, {addr}" if ten.get("unit_name") else addr


def render(snap: dict[str, Any]) -> bytes:
    ll, ten = snap["landlord"], snap["tenancy"]
    kind = snap["kind"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=KINDS[kind], author=ll.get("name") or "SmartArchive")
    story: list = letterhead(ll, ten["parties"], ten["unit_name"], ten["property_address"], dt(snap["letter_date"]))
    subject = f"{KINDS[kind]} – Mietrückstand" if kind == "mahnung" else KINDS[kind]
    story += [Paragraph(f"<b>{subject}</b>", S["h2"]), P(f"Mietverhältnis: {_object_line(ten)}", "small"), Spacer(1, 6 * mm),
              P(snap["salutation"]), Spacer(1, 3 * mm)]
    total, due = euro(snap["total"]), dt(snap["due_date"])
    earlier = snap.get("earlier") or []
    if kind == "erinnerung":
        intro = ("bei der Durchsicht meines Mietkontos ist mir aufgefallen, dass folgende Zahlungen noch nicht vollständig "
                 "eingegangen sind. Sicher handelt es sich um ein Versehen.")
    else:
        prev = "; ".join(f"{KINDS[e['kind']]} vom {dt(e['date'])}" for e in earlier)
        intro = ("trotz Fälligkeit sind folgende Mietzahlungen bisher nicht oder nicht vollständig eingegangen"
                 + (f" – auch nicht nach meinem Schreiben ({prev})." if prev else ".")
                 + " Die Miete ist jeweils im Voraus, spätestens bis zum dritten Werktag des Monats zu zahlen (§ 556b Abs. 1 BGB).")
    story += [P(intro), Spacer(1, 4 * mm)]
    rows_ = [[P("Monat", "head"), P("Miete (Soll)", "head_r"), P("gezahlt / angerechnet", "head_r"), P("offen", "head_r")]]
    for i in snap["items"]:
        rows_.append([P(month_label(i["month"]), "cell"), P(euro(i["soll"]), "cell_r"), P(euro(i["ist"] + i.get("credited", 0)), "cell_r"),
                      P(euro(i["open"]), "cell_br")])
    rows_.append([P("Offener Betrag", "cell_b"), "", "", P(total, "cell_br")])
    tbl = Table(rows_, colWidths=[62 * mm, 36 * mm, 40 * mm, 32 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PETROL), ("LINEBELOW", (0, 1), (-1, -2), 0.4, LINE),
                             ("BACKGROUND", (0, -1), (-1, -1), SOFT), ("SPAN", (0, -1), (2, -1)),
                             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [tbl, Spacer(1, 5 * mm)]
    ask = (f"Bitte überweisen Sie den offenen Betrag von {total} bis zum {due}" if kind == "erinnerung"
           else f"Ich fordere Sie auf, den offenen Betrag von {total} bis spätestens {due}")
    ask += " auf folgendes Konto" + (" zu überweisen:" if kind == "mahnung" else ":")
    bank = [x for x in (f"Kontoinhaber: {ll['name']}" if ll.get("name") else "", f"IBAN: {ll['iban']}" if ll.get("iban") else "",
                        f"Bank: {ll['bank']}" if ll.get("bank") else "",
                        "Verwendungszweck: Miete " + ", ".join(month_label(i["month"]) for i in snap["items"])
                        + (f" {ten['unit_name']}" if ten.get("unit_name") else "")) if x]
    story += [P(ask), Spacer(1, 2 * mm), P("\n".join(bank)), Spacer(1, 4 * mm)]
    if snap.get("extra_text"):
        story += [P(snap["extra_text"]), Spacer(1, 4 * mm)]
    if kind == "erinnerung":
        story.append(P("Sollten Sie die Zahlung inzwischen geleistet haben, betrachten Sie dieses Schreiben bitte als gegenstandslos."))
    else:
        story.append(P("Sollte der Betrag nicht fristgerecht eingehen, behalte ich mir weitere Schritte vor. "
                       "Haben Sie die Zahlung inzwischen geleistet, betrachten Sie dieses Schreiben bitte als gegenstandslos."))
    story += [Spacer(1, 6 * mm), KeepTogether([P("Mit freundlichen Grüßen"), Spacer(1, 12 * mm), P(ll.get("name") or "")])]

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setFont(_FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 10 * mm, f"{KINDS[kind]} vom {dt(snap['letter_date'])}")
        canvas.drawRightString(190 * mm, 10 * mm, f"Seite {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def create(conn: sqlite3.Connection, tenancy_id: int, data: dict[str, Any], archive: bool = True) -> int:
    snap = build(conn, tenancy_id, data)
    pdf = render(snap)
    ten = snap["tenancy"]
    title = f"{KINDS[snap['kind']]} {dt(snap['letter_date'])} {ten['names']}"[:120]
    doc_id = None
    ts = now()
    if archive:
        area_id = conn.execute("SELECT id FROM areas WHERE key = 'vermietung'").fetchone()[0]
        cat = conn.execute("SELECT id FROM categories WHERE area_id = ? AND name = 'Mietverträge und Mieter'", (area_id,)).fetchone()
        sha, rel = storage.store_original(pdf, "application/pdf")
        with tx(conn):
            doc_id = conn.execute(
                "INSERT INTO documents(area_id, category_id, property_id, unit_id, tenancy_id, title, original_name, doc_type, doc_date, "
                "date_status, upload_area, status, filed_by, filed_reason, confidence, confirmed, filed_at, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (area_id, cat[0] if cat else None, ten["property_id"], ten["unit_id"], tenancy_id, title, f"{title}.pdf"[:200],
                 KINDS[snap["kind"]], snap["letter_date"], "sure", "vermietung", "filed", "user", "Vom Mahnwesen erstellt.", 1.0, 1,
                 ts, ts, ts)).lastrowid
            conn.execute("INSERT INTO files(document_id, role, sha256, mime, size, path, original_name, position, created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (doc_id, "original", sha, "application/pdf", len(pdf), rel, f"{title}.pdf", 0, ts))
            conn.execute("INSERT INTO document_text(document_id, content) VALUES(?, '')", (doc_id,))
            audit(conn, "created", "document", doc_id, {"quelle": "mahnwesen"})
        try:
            documents.extract_text(conn, doc_id)
        except Exception:  # noqa: BLE001 – Ablage darf das Schreiben nicht verhindern
            pass
        with tx(conn):
            documents.index_document(conn, doc_id)
    with tx(conn):
        lid = conn.execute(
            "INSERT INTO dunning_letters(tenancy_id, kind, letter_date, due_date, months, total_cents, snapshot, document_id, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (tenancy_id, snap["kind"], snap["letter_date"], snap["due_date"], json.dumps(snap["items"]), snap["total"],
             json.dumps(snap, ensure_ascii=False, default=str), doc_id, ts)).lastrowid
        # Betrag und Monate ins Protokoll, keine Namen (Datenschutz)
        audit(conn, "created", "dunning_letter", lid, {"art": snap["kind"], "betrag": snap["total"],
                                                       "monate": [i["month"] for i in snap["items"]]})
    return lid


def pdf_of(conn: sqlite3.Connection, letter_id: int) -> tuple[bytes, str]:
    x = row(conn.execute("SELECT * FROM dunning_letters WHERE id = ?", (letter_id,)))
    if not x:
        raise KeyError(letter_id)
    snap = json.loads(x["snapshot"])
    return render(snap), f"{KINDS[x['kind']]} {dt(x['letter_date'])}.pdf"


def delete(conn: sqlite3.Connection, letter_id: int) -> None:
    x = row(conn.execute("SELECT id, kind, total_cents FROM dunning_letters WHERE id = ?", (letter_id,)))
    if not x:
        raise KeyError(letter_id)
    with tx(conn):
        conn.execute("DELETE FROM dunning_letters WHERE id = ?", (letter_id,))
        audit(conn, "deleted", "dunning_letter", letter_id, {"art": x["kind"], "betrag": x["total_cents"]})
