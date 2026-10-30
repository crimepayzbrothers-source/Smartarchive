"""Abmahnungen je Mietverhältnis: Anlass, konkrete Vorfälle, Aufforderung, Hinweis auf Folgen, Zustellweg.

Die App liefert nur Textbausteine und die Dokumentation. Den Vorfall beschreibt immer der Vermieter selbst
(Datum, ggf. Uhrzeit, was geschehen ist) – ohne konkrete Vorfälle wird kein Schreiben erstellt.
Ob eine Abmahnung berechtigt ist oder eine Kündigung trägt, bewertet die App nicht.

Verspätete Mietzahlungen werden aus dem Mietkonto vorgeschlagen: fällig bis zum dritten Werktag (§ 556b Abs. 1 BGB);
Samstage zählen dabei nicht (BGH, Urteil vom 13.07.2010 – VIII ZR 129/09), ebenso wenig gesetzliche Feiertage in Rheinland-Pfalz.
Bei Überweisung genügt es, wenn der Mieter den Auftrag bis zum dritten Werktag erteilt (BGH, Urteil vom 05.10.2016 –
VIII ZR 222/15). Der Gutschriftstag beim Vermieter liegt oft ein bis zwei Bankarbeitstage später – deshalb werden erst
Eingänge vorgeschlagen, die mehr als zwei Werktage nach Fälligkeit gebucht wurden.
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

from . import income
from . import tenancy as tn
from .bk_pdf import LINE, MUTED, PETROL, S, P, _FONT, dt, euro, letterhead
from .db import audit, get_setting, now, row, rows, tx
from .dunning import archive_letter, default_salutation, month_label

CONSEQUENCE = ("Sollte es erneut zu einem solchen Verstoß kommen, behalte ich mir vor, das Mietverhältnis zu kündigen – "
               "gegebenenfalls fristlos.")

REASONS: dict[str, dict[str, Any]] = {
    "unpuenktlich": {
        "label": "Unpünktliche Mietzahlung",
        "intro": "hiermit mahne ich Sie wegen wiederholt unpünktlicher Mietzahlung ab. Die Miete ist nach dem Mietvertrag und "
                 "§ 556b Abs. 1 BGB jeweils im Voraus, spätestens bis zum dritten Werktag eines Monats zu zahlen. "
                 "Sie ist jedoch verspätet eingegangen:",
        "demand": "Ich fordere Sie auf, die Miete künftig pünktlich zu zahlen.",
        "consequence": "Sollten Sie die Miete erneut verspätet zahlen, behalte ich mir vor, das Mietverhältnis zu kündigen – "
                       "gegebenenfalls fristlos.",
        "deadline": False,
    },
    "ruhestoerung": {
        "label": "Ruhestörung",
        "intro": "hiermit mahne ich Sie wegen Störung des Hausfriedens ab. Es kam zu folgenden Ruhestörungen:",
        "demand": "Ich fordere Sie auf, derartige Störungen künftig zu unterlassen und insbesondere die Ruhezeiten der Hausordnung einzuhalten.",
        "consequence": CONSEQUENCE,
        "deadline": False,
    },
    "untervermietung": {
        "label": "Unerlaubte Überlassung an Dritte",
        "intro": "hiermit mahne ich Sie ab, weil Sie die Wohnung ohne meine Erlaubnis Dritten überlassen haben (§ 540 Abs. 1 BGB). "
                 "Festgestellt habe ich Folgendes:",
        "demand": "Ich fordere Sie auf, die Überlassung an Dritte bis zum {frist} zu beenden.",
        "consequence": "Sollte die Überlassung nicht bis zu diesem Tag beendet sein, behalte ich mir vor, das Mietverhältnis zu kündigen – "
                       "gegebenenfalls fristlos.",
        "deadline": True,
    },
    "tierhaltung": {
        "label": "Tierhaltung ohne Erlaubnis",
        "intro": "hiermit mahne ich Sie wegen Tierhaltung ohne meine Erlaubnis ab. Festgestellt habe ich Folgendes:",
        "demand": "Ich fordere Sie auf, die Tierhaltung bis zum {frist} zu beenden.",
        "consequence": "Sollte die Tierhaltung nicht bis zu diesem Tag beendet sein, behalte ich mir vor, das Mietverhältnis zu kündigen.",
        "deadline": True,
    },
    "hausordnung": {
        "label": "Verstoß gegen die Hausordnung",
        "intro": "hiermit mahne ich Sie wegen Verstößen gegen die Hausordnung ab, die Bestandteil des Mietvertrags ist:",
        "demand": "Ich fordere Sie auf, die Hausordnung künftig einzuhalten.",
        "consequence": CONSEQUENCE,
        "deadline": False,
    },
    "sonstiges": {
        "label": "Vertragswidriges Verhalten",
        "intro": "hiermit mahne ich Sie wegen vertragswidrigen Verhaltens ab:",
        "demand": "Ich fordere Sie auf, dieses Verhalten künftig zu unterlassen.",
        "consequence": CONSEQUENCE,
        "deadline": False,
    },
}

DELIVERY = {"einwurf": "Per Einwurf-Einschreiben", "bote": "Per Boten", "persoenlich": "Persönlich übergeben",
            "brief": "", "email": ""}
DELIVERY_LABEL = {"einwurf": "Einwurf-Einschreiben", "bote": "Bote", "persoenlich": "persönlich übergeben", "brief": "einfacher Brief",
                  "email": "E-Mail"}


class WarningError(ValueError):
    pass


# --- Fälligkeit: dritter Werktag ---------------------------------------------------------

def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(y, month, day + 1)


def holidays_rlp(y: int) -> set[date]:
    e = _easter(y)
    fixed = {date(y, 1, 1), date(y, 5, 1), date(y, 10, 3), date(y, 11, 1), date(y, 12, 25), date(y, 12, 26)}
    return fixed | {e - timedelta(days=2), e + timedelta(days=1), e + timedelta(days=39), e + timedelta(days=50), e + timedelta(days=60)}


def _is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays_rlp(d.year)


def add_workdays(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if _is_workday(d):
            n -= 1
    return d


def third_workday(y: int, m: int) -> date:
    first = date(y, m, 1)
    return add_workdays(first, 3 - _is_workday(first))  # ist der 1. ein Werktag, zählt er als erster


GRACE_WORKDAYS = 2  # Laufzeit der Überweisung


def late_payments(conn: sqlite3.Connection, t: dict[str, Any], today: date | None = None, months: int = 24) -> list[dict[str, Any]]:
    """Tatsächlich gebuchte Zahlungen, die nach dem dritten Werktag ihres Monats eingingen (nur Vorschläge)."""
    today = today or date.today()
    since = date(today.year - months // 12, today.month, 1)
    out = []
    for p in income.payments(conn, t["id"]):
        d = date.fromisoformat(p["date"])
        if d < since or (d.year, d.month) != (p["year"], p["month"]):
            continue
        due = third_workday(p["year"], p["month"])
        if d > add_workdays(due, GRACE_WORKDAYS):
            key = f"{p['year']}-{p['month']:02d}"
            out.append({"month": key, "date": p["date"], "amount": p["amount"], "deadline": due.isoformat(),
                        "text": f"Miete {month_label(key)} erst am {dt(p['date'])} eingegangen ({euro(p['amount'])}, fällig bis {dt(due.isoformat())})"})
    return sorted(out, key=lambda x: x["date"])


# --- Lesen ------------------------------------------------------------------------------

def letters(conn: sqlite3.Connection, tenancy_id: int) -> list[dict[str, Any]]:
    out = rows(conn.execute("SELECT id, reason, letter_date, deadline, incidents, delivery, document_id, created_at FROM warning_letters "
                            "WHERE tenancy_id = ? ORDER BY letter_date DESC, id DESC", (tenancy_id,)))
    for x in out:
        x["incidents"] = json.loads(x["incidents"])
        x["label"] = REASONS.get(x["reason"], {}).get("label", x["reason"])
        x["delivery_label"] = DELIVERY_LABEL.get(x["delivery"], x["delivery"])
    return out


def state(conn: sqlite3.Connection, tenancy_id: int) -> dict[str, Any]:
    t = tn.load(conn, tenancy_id)
    today = date.today()
    ll = get_setting(conn, "landlord", {}) or {}
    return {
        "letters": letters(conn, tenancy_id),
        "reasons": [{"key": k, **v} for k, v in REASONS.items()],
        "delivery": [{"key": k, "label": v} for k, v in DELIVERY_LABEL.items()],
        "late_payments": late_payments(conn, t, today),
        "defaults": {"letter_date": today.isoformat(), "deadline": (today + timedelta(days=14)).isoformat(),
                     "salutation": default_salutation(t), "delivery": "einwurf"},
        "landlord_missing": [label for k, label in (("name", "Name"), ("address", "Anschrift")) if not ll.get(k)],
    }


# --- Schreiben --------------------------------------------------------------------------

def build(conn: sqlite3.Connection, tenancy_id: int, data: dict[str, Any]) -> dict[str, Any]:
    t = tn.load(conn, tenancy_id)
    reason = REASONS.get(data["reason"])
    if not reason:
        raise WarningError("Bitte einen Anlass wählen.")
    if data.get("delivery", "") not in DELIVERY:
        raise WarningError("Unbekannter Zustellweg.")
    letter_d = date.fromisoformat(data["letter_date"])
    incidents = []
    for i in data.get("incidents") or []:
        text = (i.get("text") or "").strip()
        if not text and not i.get("date"):
            continue
        if not text or not i.get("date"):
            raise WarningError("Jeder Vorfall braucht ein Datum und eine Beschreibung.")
        if date.fromisoformat(i["date"]) > letter_d:
            raise WarningError(f"Der Vorfall vom {dt(i['date'])} liegt nach dem Briefdatum.")
        incidents.append({"date": i["date"], "time": (i.get("time") or "").strip(), "text": text})
    if not incidents:
        raise WarningError("Bitte mindestens einen konkreten Vorfall mit Datum beschreiben – eine Abmahnung braucht ihn.")
    incidents.sort(key=lambda x: (x["date"], x["time"]))
    demand = (data.get("demand") or "").strip() or reason["demand"]
    consequence = (data.get("consequence") or "").strip() or reason["consequence"]
    deadline = data.get("deadline") or None
    if "{frist}" in demand + consequence:
        if not deadline:
            raise WarningError("Bitte eine Frist angeben (der Text verweist darauf).")
        if date.fromisoformat(deadline) <= letter_d:
            raise WarningError("Die Frist muss nach dem Briefdatum liegen.")
    else:
        deadline = None
    return {
        "reason": data["reason"], "label": reason["label"], "intro": reason["intro"], "letter_date": data["letter_date"],
        "deadline": deadline, "incidents": incidents, "demand": demand, "consequence": consequence,
        "extra_text": (data.get("extra_text") or "").strip(), "salutation": (data.get("salutation") or "").strip() or default_salutation(t),
        "delivery": data.get("delivery", ""),
        "landlord": get_setting(conn, "landlord", {}) or {},
        "tenancy": {"id": t["id"], "names": t["names"], "parties": t["parties"], "unit_name": t["unit_name"],
                    "property_name": t["property_name"], "property_address": t["property_address"], "property_id": t["property_id"],
                    "unit_id": t["unit_id"]},
    }


def _fill(text: str, snap: dict[str, Any]) -> str:
    return text.replace("{frist}", dt(snap["deadline"])) if snap.get("deadline") else text


def render(snap: dict[str, Any]) -> bytes:
    ll, ten = snap["landlord"], snap["tenancy"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title="Abmahnung", author=ll.get("name") or "SmartArchive")
    story: list = letterhead(ll, ten["parties"], ten["unit_name"], ten["property_address"], dt(snap["letter_date"]))
    if DELIVERY.get(snap["delivery"]):
        story += [P(DELIVERY[snap["delivery"]], "small"), Spacer(1, 1 * mm)]
    addr = (ten.get("property_address") or ten.get("property_name") or "").replace("\n", ", ")
    obj = f"Wohnung {ten['unit_name']}, {addr}" if ten.get("unit_name") else addr
    story += [Paragraph(f"<b>Abmahnung – {snap['label']}</b>", S["h2"]), P(f"Mietverhältnis: {obj}", "small"), Spacer(1, 6 * mm),
              P(snap["salutation"]), Spacer(1, 3 * mm), P(snap["intro"]), Spacer(1, 3 * mm)]
    rows_ = [[P("Datum", "head"), P("Uhrzeit", "head"), P("Vorfall", "head")]]
    for i in snap["incidents"]:
        rows_.append([P(dt(i["date"]), "cell"), P(i["time"] or "–", "cell"), P(i["text"], "cell")])
    tbl = Table(rows_, colWidths=[24 * mm, 18 * mm, 128 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PETROL), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [tbl, Spacer(1, 5 * mm), P(_fill(snap["demand"], snap)), Spacer(1, 3 * mm), P(_fill(snap["consequence"], snap)),
              Spacer(1, 3 * mm)]
    if snap.get("extra_text"):
        story += [P(_fill(snap["extra_text"], snap)), Spacer(1, 3 * mm)]
    story += [Spacer(1, 4 * mm), KeepTogether([P("Mit freundlichen Grüßen"), Spacer(1, 12 * mm), P(ll.get("name") or "")])]

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setFont(_FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 10 * mm, f"Abmahnung vom {dt(snap['letter_date'])}")
        canvas.drawRightString(190 * mm, 10 * mm, f"Seite {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def create(conn: sqlite3.Connection, tenancy_id: int, data: dict[str, Any], archive: bool = True) -> int:
    snap = build(conn, tenancy_id, data)
    pdf = render(snap)
    ten = snap["tenancy"]
    title = f"Abmahnung {snap['label']} {dt(snap['letter_date'])} {ten['names']}"[:120]
    doc_id = archive_letter(conn, pdf, title, "Abmahnung", ten, tenancy_id, snap["letter_date"], "abmahnung") if archive else None
    with tx(conn):
        lid = conn.execute(
            "INSERT INTO warning_letters(tenancy_id, reason, letter_date, deadline, incidents, delivery, snapshot, document_id, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (tenancy_id, snap["reason"], snap["letter_date"], snap["deadline"], json.dumps(snap["incidents"], ensure_ascii=False),
             snap["delivery"], json.dumps(snap, ensure_ascii=False, default=str), doc_id, now())).lastrowid
        # Nur Anlass und Anzahl ins Protokoll – keine Vorfallsbeschreibungen oder Namen (Datenschutz)
        audit(conn, "created", "warning_letter", lid, {"anlass": snap["reason"], "vorfaelle": len(snap["incidents"])})
    return lid


def pdf_of(conn: sqlite3.Connection, letter_id: int) -> tuple[bytes, str]:
    x = row(conn.execute("SELECT * FROM warning_letters WHERE id = ?", (letter_id,)))
    if not x:
        raise KeyError(letter_id)
    return render(json.loads(x["snapshot"])), f"Abmahnung {dt(x['letter_date'])}.pdf"


def delete(conn: sqlite3.Connection, letter_id: int) -> None:
    x = row(conn.execute("SELECT id, reason FROM warning_letters WHERE id = ?", (letter_id,)))
    if not x:
        raise KeyError(letter_id)
    with tx(conn):
        conn.execute("DELETE FROM warning_letters WHERE id = ?", (letter_id,))
        audit(conn, "deleted", "warning_letter", letter_id, {"anlass": x["reason"]})
