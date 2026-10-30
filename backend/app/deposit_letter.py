"""Zahlungserinnerung und Mahnung zur Mietkaution.

- Barkaution/Kautionskonto: fällige, noch offene Raten (Beträge nur aus der erfassten Kaution und ihren Einzahlungen).
- Bürgschaft, Kautionsversicherung, verpfändetes Sparbuch: Aufforderung, die Urkunde bzw. Verpfändungserklärung vorzulegen.

Hinweis für die Oberfläche (nicht im Schreiben): § 569 Abs. 2a BGB nennt als Schwelle einen Verzug mit der Sicherheit
in Höhe von zwei Nettokaltmieten. Die App zeigt das nur rechnerisch an und bewertet nicht, ob eine Kündigung möglich ist.
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import date
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import deposit
from . import tenancy as tn
from .bk_pdf import LINE, MUTED, PETROL, SOFT, S, P, _FONT, dt, euro, letterhead
from .db import audit, get_setting, now, row, rows, tx
from .dunning import archive_letter, default_salutation

KINDS = {"erinnerung": "Zahlungserinnerung", "mahnung": "Mahnung"}
DOC_NAME = {"sparbuch": "die Verpfändungserklärung für das Sparbuch bzw. Kautionskonto",
            "buergschaft": "die Bürgschaftsurkunde Ihrer Bank",
            "versicherung": "die Bürgschaftsurkunde Ihrer Kautionsversicherung"}


FORM = {"sparbuch": "eines verpfändeten Sparbuchs", "buergschaft": "einer Bankbürgschaft", "versicherung": "einer Kautionsversicherung"}


class LetterError(ValueError):
    pass


def open_items(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Fällige, offene Raten einer Barkaution zum Stichtag (aus deposit.get mit today=Stichtag)."""
    if d.get("kind") != "bar" or d.get("returned_on"):
        return []
    return [{"no": r["no"], "due": r["due"], "amount": r["amount"], "paid": r["paid"], "open": r["amount"] - r["paid"]}
            for r in d["schedule"] if r["paid"] < r["amount"] and date.fromisoformat(r["due"]) <= date.fromisoformat(d["_asof"])]


def remindable(d: dict[str, Any]) -> dict[str, Any]:
    """Was sich anmahnen lässt – für die Oberfläche."""
    if d["status"] in ("fehlt", "keine", "zurueck", "abgerechnet"):
        return {"possible": False}
    if d["kind"] == "bar":
        items = open_items(d)
        total = sum(i["open"] for i in items)
        cold = d.get("cold_rent") or 0
        return {"possible": bool(items), "mode": "geld", "items": items, "total": total,
                "threshold": bool(cold) and total >= 2 * cold, "two_rents": 2 * cold}
    return {"possible": not d.get("document_received_on") and d["kind"] in DOC_NAME, "mode": "urkunde", "items": [], "total": d["amount_cents"]}


def state(conn: sqlite3.Connection, tenancy_id: int, asof: date | None = None) -> dict[str, Any]:
    asof = asof or date.today()
    d = deposit.get(conn, tenancy_id, asof)
    d["_asof"] = asof.isoformat()
    return {"remind": remindable(d), "letters": letters(conn, tenancy_id)}


def letters(conn: sqlite3.Connection, tenancy_id: int) -> list[dict[str, Any]]:
    out = rows(conn.execute("SELECT id, kind, letter_date, due_date, total_cents, document_id, snapshot FROM deposit_letters "
                            "WHERE tenancy_id = ? ORDER BY letter_date DESC, id DESC", (tenancy_id,)))
    for x in out:
        snap = json.loads(x.pop("snapshot"))
        x["label"] = KINDS[x["kind"]]
        x["mode"] = snap["mode"]
    return out


def build(conn: sqlite3.Connection, tenancy_id: int, data: dict[str, Any]) -> dict[str, Any]:
    if data["kind"] not in KINDS:
        raise LetterError("Unbekannte Art des Schreibens.")
    letter_d, due_d = date.fromisoformat(data["letter_date"]), date.fromisoformat(data["due_date"])
    if due_d <= letter_d:
        raise LetterError("Die Frist muss nach dem Briefdatum liegen.")
    t = tn.load(conn, tenancy_id)
    d = deposit.get(conn, tenancy_id, letter_d)
    d["_asof"] = letter_d.isoformat()
    r = remindable(d)
    if not r["possible"]:
        raise LetterError("Zum Briefdatum ist bei der Kaution nichts offen." if d["status"] not in ("fehlt", "keine")
                          else "Bitte zuerst die Kaution erfassen.")
    earlier = rows(conn.execute("SELECT kind, letter_date FROM deposit_letters WHERE tenancy_id = ? AND letter_date < ? ORDER BY letter_date",
                                (tenancy_id, data["letter_date"])))
    return {
        "kind": data["kind"], "mode": r["mode"], "letter_date": data["letter_date"], "due_date": data["due_date"],
        "deposit": {k: d.get(k) for k in ("kind", "kind_label", "amount_cents", "months", "installments", "account", "provider", "reference")},
        "items": r["items"], "total": r["total"], "earlier": [{"kind": e["kind"], "date": e["letter_date"]} for e in earlier],
        "salutation": (data.get("salutation") or "").strip() or default_salutation(t), "extra_text": (data.get("extra_text") or "").strip(),
        "landlord": get_setting(conn, "landlord", {}) or {},
        "tenancy": {"id": t["id"], "names": t["names"], "parties": t["parties"], "unit_name": t["unit_name"], "contract_start": t["contract_start"],
                    "property_name": t["property_name"], "property_address": t["property_address"], "property_id": t["property_id"],
                    "unit_id": t["unit_id"]},
    }


def render(snap: dict[str, Any]) -> bytes:
    ll, ten, dep = snap["landlord"], snap["tenancy"], snap["deposit"]
    kind, money_mode = snap["kind"], snap["mode"] == "geld"
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"{KINDS[kind]} Mietkaution", author=ll.get("name") or "SmartArchive")
    story: list = letterhead(ll, ten["parties"], ten["unit_name"], ten["property_address"], dt(snap["letter_date"]))
    addr = (ten.get("property_address") or ten.get("property_name") or "").replace("\n", ", ")
    obj = f"Wohnung {ten['unit_name']}, {addr}" if ten.get("unit_name") else addr
    story += [Paragraph(f"<b>{KINDS[kind]} – Mietkaution</b>", S["h2"]), P(f"Mietverhältnis: {obj}", "small"), Spacer(1, 6 * mm),
              P(snap["salutation"]), Spacer(1, 3 * mm)]
    due = dt(snap["due_date"])
    prev = "; ".join(f"{KINDS[e['kind']]} vom {dt(e['date'])}" for e in snap.get("earlier") or [])
    agreed = f"in Höhe von {euro(dep['amount_cents'])}"
    if money_mode:
        rates = " in drei monatlichen Teilzahlungen (§ 551 Abs. 2 BGB)" if dep["installments"] else ""
        intro = (f"nach unserem Mietvertrag ist eine Mietsicherheit (Kaution) {agreed} vereinbart, zahlbar{rates or ' zu Beginn des Mietverhältnisses'}. "
                 + ("Folgende Beträge sind bisher nicht eingegangen" + (f" – auch nicht nach meinem Schreiben ({prev})." if prev and kind == "mahnung" else ":")))
        story += [P(intro), Spacer(1, 4 * mm)]
        data = [[P("Teilbetrag", "head"), P("fällig am", "head"), P("Betrag", "head_r"), P("gezahlt", "head_r"), P("offen", "head_r")]]
        for i in snap["items"]:
            label = f"{i['no']}. Rate" if dep["installments"] else "Kaution"
            data.append([P(label, "cell"), P(dt(i["due"]), "cell"), P(euro(i["amount"]), "cell_r"), P(euro(i["paid"]), "cell_r"),
                         P(euro(i["open"]), "cell_br")])
        data.append([P("Offener Betrag", "cell_b"), "", "", "", P(euro(snap["total"]), "cell_br")])
        tbl = Table(data, colWidths=[46 * mm, 32 * mm, 32 * mm, 30 * mm, 30 * mm], repeatRows=1)
        tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PETROL), ("LINEBELOW", (0, 1), (-1, -2), 0.4, LINE),
                                 ("BACKGROUND", (0, -1), (-1, -1), SOFT), ("SPAN", (0, -1), (3, -1)),
                                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        account = dep.get("account") or " · ".join(x for x in (f"IBAN {ll['iban']}" if ll.get("iban") else "", ll.get("bank") or "") if x)
        target = (f"auf das Kautionskonto ({account})" if dep.get("account") else f"auf folgendes Konto ({account})" if account else "")
        ask = (f"Bitte überweisen Sie den offenen Betrag von {euro(snap['total'])} bis zum {due} {target}".rstrip() + "." if kind == "erinnerung"
               else f"Ich fordere Sie auf, den offenen Betrag von {euro(snap['total'])} bis spätestens {due} {target} zu überweisen."
               .replace("  ", " "))
        story += [tbl, Spacer(1, 5 * mm), P(ask),
                  P("Verwendungszweck: Kaution" + (f" {ten['unit_name']}" if ten.get("unit_name") else "")), Spacer(1, 4 * mm)]
    else:
        what = DOC_NAME[dep["kind"]]
        provider = f" ({dep['provider']}{', Nr. ' + dep['reference'] if dep.get('reference') else ''})" if dep.get("provider") else ""
        intro = (f"nach unserem Mietvertrag ist eine Mietsicherheit {agreed} vereinbart, die Sie in Form {FORM[dep['kind']]}{provider} "
                 f"stellen. Mir liegt dazu bisher {what} nicht vor"
                 + (f" – auch nicht nach meinem Schreiben ({prev})." if prev and kind == "mahnung" else "."))
        ask = (f"Bitte lassen Sie mir {what} im Original bis zum {due} zukommen." if kind == "erinnerung"
               else f"Ich fordere Sie auf, mir {what} im Original bis spätestens {due} vorzulegen.")
        story += [P(intro), Spacer(1, 3 * mm), P(ask), Spacer(1, 4 * mm)]
    if snap.get("extra_text"):
        story += [P(snap["extra_text"]), Spacer(1, 4 * mm)]
    if kind == "erinnerung":
        story.append(P("Sollte sich dieses Schreiben mit Ihrer Zahlung bzw. Zusendung überschnitten haben, betrachten Sie es bitte als gegenstandslos."))
    else:
        story.append(P("Sollte die Frist ungenutzt verstreichen, behalte ich mir weitere Schritte vor. Hat sich dieses Schreiben mit Ihrer "
                       "Zahlung bzw. Zusendung überschnitten, betrachten Sie es bitte als gegenstandslos."))
    story += [Spacer(1, 6 * mm), KeepTogether([P("Mit freundlichen Grüßen"), Spacer(1, 12 * mm), P(ll.get("name") or "")])]

    def on_page(canvas, d_):
        canvas.saveState()
        canvas.setFont(_FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 10 * mm, f"{KINDS[kind]} Mietkaution vom {dt(snap['letter_date'])}")
        canvas.drawRightString(190 * mm, 10 * mm, f"Seite {d_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def create(conn: sqlite3.Connection, tenancy_id: int, data: dict[str, Any], archive: bool = True) -> int:
    snap = build(conn, tenancy_id, data)
    pdf = render(snap)
    ten = snap["tenancy"]
    title = f"{KINDS[snap['kind']]} Kaution {dt(snap['letter_date'])} {ten['names']}"[:120]
    doc_id = archive_letter(conn, pdf, title, f"{KINDS[snap['kind']]} Kaution", ten, tenancy_id, snap["letter_date"], "kaution") \
        if archive else None
    with tx(conn):
        lid = conn.execute("INSERT INTO deposit_letters(tenancy_id, kind, letter_date, due_date, total_cents, snapshot, document_id, created_at) "
                           "VALUES(?,?,?,?,?,?,?,?)",
                           (tenancy_id, snap["kind"], snap["letter_date"], snap["due_date"], snap["total"],
                            json.dumps(snap, ensure_ascii=False, default=str), doc_id, now())).lastrowid
        audit(conn, "created", "deposit_letter", lid, {"art": snap["kind"], "betrag": snap["total"], "modus": snap["mode"]})
    return lid


def pdf_of(conn: sqlite3.Connection, letter_id: int) -> tuple[bytes, str]:
    x = row(conn.execute("SELECT * FROM deposit_letters WHERE id = ?", (letter_id,)))
    if not x:
        raise KeyError(letter_id)
    return render(json.loads(x["snapshot"])), f"{KINDS[x['kind']]} Kaution {dt(x['letter_date'])}.pdf"


def delete(conn: sqlite3.Connection, letter_id: int) -> int:
    x = row(conn.execute("SELECT id, tenancy_id, kind FROM deposit_letters WHERE id = ?", (letter_id,)))
    if not x:
        raise KeyError(letter_id)
    with tx(conn):
        conn.execute("DELETE FROM deposit_letters WHERE id = ?", (letter_id,))
        audit(conn, "deleted", "deposit_letter", letter_id, {"art": x["kind"]})
    return x["tenancy_id"]
