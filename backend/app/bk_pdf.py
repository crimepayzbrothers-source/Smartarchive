"""PDF der Betriebskostenabrechnung (je Mietverhältnis), erzeugt aus einer gespeicherten Fassung.

Weil das PDF nur aus dem gespeicherten Stand (Snapshot) entsteht, lässt sich jede freigegebene Abrechnung
später unverändert reproduzieren – auch wenn sich Stammdaten inzwischen geändert haben.
"""
from __future__ import annotations

import io
import os
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

from . import storage

PETROL = colors.HexColor("#0d5c66")
INK = colors.HexColor("#15202a")
MUTED = colors.HexColor("#5b6670")
LINE = colors.HexColor("#d3dbd8")
SOFT = colors.HexColor("#eef4f4")

_FONT, _BOLD = "Helvetica", "Helvetica-Bold"
for regular, bold in (("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),):
    if os.path.exists(regular) and os.path.exists(bold):
        try:
            pdfmetrics.registerFont(TTFont("DejaVu", regular))
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold))
            _FONT, _BOLD = "DejaVu", "DejaVu-Bold"
        except Exception:  # noqa: BLE001
            pass


def _s(name: str, **kw) -> ParagraphStyle:
    base = {"fontName": _FONT, "fontSize": 9, "leading": 12.5, "textColor": INK}
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    "body": _s("body"),
    "small": _s("small", fontSize=7.8, leading=10, textColor=MUTED),
    "sender": _s("sender", fontSize=7, leading=9, textColor=MUTED),
    "addr": _s("addr", fontSize=10, leading=13.5),
    "h1": _s("h1", fontName=_BOLD, fontSize=15, leading=19, textColor=PETROL, spaceAfter=2),
    "h2": _s("h2", fontName=_BOLD, fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=4),
    "cell": _s("cell", fontSize=8, leading=10.2),
    "cell_r": _s("cell_r", fontSize=8, leading=10.2, alignment=TA_RIGHT),
    "cell_b": _s("cell_b", fontName=_BOLD, fontSize=8, leading=10.2),
    "cell_br": _s("cell_br", fontName=_BOLD, fontSize=8, leading=10.2, alignment=TA_RIGHT),
    "head": _s("head", fontName=_BOLD, fontSize=7.6, leading=9.5, textColor=colors.white),
    "head_r": _s("head_r", fontName=_BOLD, fontSize=7.6, leading=9.5, textColor=colors.white, alignment=TA_RIGHT),
    "right": _s("right", alignment=TA_RIGHT),
    "result": _s("result", fontName=_BOLD, fontSize=12, leading=16),
    "result_r": _s("result_r", fontName=_BOLD, fontSize=12, leading=16, alignment=TA_RIGHT),
}


def P(text: Any, style: str = "body") -> Paragraph:
    return Paragraph(escape(str(text if text is not None else "")).replace("\n", "<br/>"), S[style])


def euro(cents: int | None) -> str:
    if cents is None:
        return ""
    s = f"{abs(cents) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if cents < 0 else "") + s + " €"


def dt(iso: str | None) -> str:
    return date.fromisoformat(iso[:10]).strftime("%d.%m.%Y") if iso else ""


def render(snap: dict[str, Any], tenancy_id: int | None = None, draft: bool = False) -> bytes:
    """Abrechnung für ein Mietverhältnis; ohne tenancy_id alle Mietverhältnisse plus interne Übersicht."""
    res = snap["result"]
    results = [r for r in res["results"] if tenancy_id is None or r["tenancy_id"] == tenancy_id]
    if tenancy_id is not None and not results:
        raise KeyError(tenancy_id)
    buf = io.BytesIO()
    version = snap.get("version")
    stamp = "ENTWURF – nicht versenden" if draft else (f"Fassung {version} vom {dt(snap.get('released_at'))}" if version else "")

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont(_FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 10 * mm, stamp)
        canvas.drawRightString(190 * mm, 10 * mm, f"Seite {doc.page}")
        if draft:
            canvas.setFont(_BOLD, 60)
            canvas.setFillColor(colors.Color(0.8, 0.2, 0.15, alpha=0.08))
            canvas.translate(105 * mm, 150 * mm)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "ENTWURF")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title="Betriebskostenabrechnung", author=(snap.get("landlord") or {}).get("name") or "SmartArchive")
    story: list = []
    for i, r in enumerate(results):
        if i:
            story.append(PageBreak())
        story += _statement(snap, r)
    if tenancy_id is None:
        story.append(PageBreak())
        story += _internal(snap)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def _letterhead(snap: dict[str, Any], r: dict[str, Any]) -> list:
    period = snap["result"]["period"]
    released = dt(snap.get("released_at")) or date.today().strftime("%d.%m.%Y")
    return letterhead(snap.get("landlord") or {}, r["parties"], r["unit_name"], period["property_address"], released)


def letterhead(ll: dict[str, Any], parties: list[dict[str, Any]], unit_name: str | None, property_address: str | None,
               date_text: str) -> list:
    """Briefkopf mit Logo, Absenderzeile, Anschrift der Mieter (Fensterumschlag) und Ort/Datum."""
    logo = None
    if ll.get("logo"):
        try:
            path = storage.abs_path(ll["logo"])
            img = Image(str(path))
            ratio = img.imageWidth / img.imageHeight if img.imageHeight else 1
            h = 16 * mm
            img.drawHeight, img.drawWidth = h, min(h * ratio, 55 * mm)
            logo = img
        except Exception:  # noqa: BLE001 – ohne Logo weiter
            logo = None
    landlord_lines = [x for x in (ll.get("name"), ll.get("address"), ll.get("phone"), ll.get("email")) if x]
    head = Table([[logo or "", P("\n".join(landlord_lines) or "Vermieter: bitte in den Einstellungen eintragen", "small")]],
                 colWidths=[100 * mm, 70 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    sender = " · ".join(x.replace("\n", ", ") for x in (ll.get("name"), ll.get("address")) if x)
    addr_parties = [f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() for p in parties]
    own = next((p.get("address") for p in parties if p.get("address")), "")
    address = own or "\n".join(x for x in ((unit_name or "") and f"Wohnung {unit_name}", property_address) if x)
    place = ll.get("place") or ""
    block = Table([[[P(sender, "sender"), Spacer(1, 2), P("\n".join(addr_parties + [address.replace(", ", "\n", 1)]), "addr")],
                    P(f"{place + ', ' if place else ''}{date_text}", "right")]], colWidths=[105 * mm, 65 * mm])
    block.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (1, 0), (1, 0), "BOTTOM")]))
    return [head, Spacer(1, 10 * mm), block, Spacer(1, 9 * mm)]


def _statement(snap: dict[str, Any], r: dict[str, Any]) -> list:
    res = snap["result"]
    period = res["period"]
    story = _letterhead(snap, r)
    year = period["start"][:4] if period["start"][:4] == period["end"][:4] else f"{period['start'][:4]}/{period['end'][:4]}"
    story.append(P(f"Betriebskostenabrechnung {year}", "h1"))
    facts = [
        ("Mietobjekt", f"{period['property_address'] or period['property_name']}{', Wohnung ' + r['unit_name'] if r['unit_name'] else ''}"),
        ("Mieter", r["names"]),
        ("Abrechnungszeitraum", f"{dt(period['start'])} – {dt(period['end'])} ({res['period_days']} Tage)"),
        ("Ihr Nutzungszeitraum", f"{dt(r['from'])} – {dt(r['to'])} ({r['days']} Tage)"),
    ]
    t = Table([[P(a, "small"), P(b)] for a, b in facts], colWidths=[42 * mm, 128 * mm])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 4 * mm)]
    salutation = "Sehr geehrte Damen und Herren" if len(r["parties"]) != 1 else \
        f"Guten Tag {r['parties'][0].get('first_name', '')} {r['parties'][0].get('last_name', '')}".strip()
    story.append(P(f"{salutation},\n\nnachfolgend erhalten Sie die Abrechnung der Betriebskosten für den oben genannten Zeitraum. "
                   "Die Tabelle zeigt je Kostenart die Gesamtkosten des Hauses, den Umlageschlüssel, die Verteilungseinheiten und "
                   "Ihren Anteil. Erläuterungen zu den Schlüsseln finden Sie im Anschluss."))
    story.append(Spacer(1, 3 * mm))

    head = [P("Kostenart", "head"), P("Kosten gesamt", "head_r"), P("Schlüssel", "head"), P("Einheiten gesamt", "head"),
            P("Ihre Einheiten", "head"), P("Ihr Betrag", "head_r")]
    data = [head]
    for x in r["positions"]:
        data.append([P(x["label"], "cell"), P(euro(x["total"]), "cell_r"), P(x["key_label"], "cell"), P(x["basis"]["total"], "cell"),
                     P(x["basis"]["mine"], "cell"), P(euro(x["amount"]), "cell_r")])
    widths = [38 * mm, 21 * mm, 26 * mm, 30 * mm, 33 * mm, 22 * mm]
    tbl = Table(data, colWidths=widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PETROL), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story.append(tbl)
    story.append(Spacer(1, 4 * mm))

    adv = r["advances_ist"]
    rows_ = []
    if r["bk_total"]:
        rows_.append(("Ihre Betriebskosten (ohne Heizung/Warmwasser)", euro(r["bk_total"])))
    if r["hk_total"]:
        rows_.append(("Ihre Heiz- und Warmwasserkosten", euro(r["hk_total"])))
    rows_.append(("Summe Ihrer Kosten", euro(r["total"])))
    if adv["bk"]:
        rows_.append(("abzüglich geleistete Betriebskostenvorauszahlungen", "– " + euro(adv["bk"])))
    if adv["hk"]:
        rows_.append(("abzüglich geleistete Heizkostenvorauszahlungen", "– " + euro(adv["hk"])))
    if not adv["bk"] and not adv["hk"]:
        rows_.append(("abzüglich geleistete Vorauszahlungen", euro(0)))
    bal = r["balance"]
    label = "Nachzahlung" if bal > 0 else ("Guthaben" if bal < 0 else "Ergebnis")
    sum_tbl = Table([[P(a, "cell_b" if a.startswith("Summe") else "cell"), P(b, "cell_br" if a.startswith("Summe") else "cell_r")]
                     for a, b in rows_] + [[P(label, "result"), P(euro(abs(bal)), "result_r")]], colWidths=[130 * mm, 40 * mm])
    sum_tbl.setStyle(TableStyle([("LINEABOVE", (0, len(rows_)), (-1, len(rows_)), 1, PETROL),
                                 ("BACKGROUND", (0, len(rows_)), (-1, len(rows_)), SOFT),
                                 ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story.append(KeepTogether(sum_tbl))
    story.append(Spacer(1, 3 * mm))

    ll = snap.get("landlord") or {}
    if bal > 0:
        pay = f"Bitte überweisen Sie die Nachzahlung von {euro(bal)} innerhalb von 30 Tagen nach Zugang dieser Abrechnung"
        pay += f" auf das Konto {ll['iban']}" + (f" ({ll['bank']})" if ll.get("bank") else "") if ll.get("iban") else ""
        story.append(P(pay + "."))
    elif bal < 0:
        story.append(P(f"Das Guthaben von {euro(-bal)} erstatten wir Ihnen bzw. verrechnen es nach Absprache."))

    # Erläuterungen
    story.append(P("Erläuterungen zu Umlageschlüsseln und Berechnung", "h2"))
    notes = []
    used = {}
    for x in r["positions"]:
        used.setdefault(x["key_label"], x["key_reason"])
    for k, why in used.items():
        notes.append(f"{k}: {why}")
    if res.get("vacancy") and any(v["days"] for v in res["vacancy"]):
        notes.append("Leerstand: Kostenanteile, die nach dem Umlageschlüssel auf leerstehende Wohnungen entfallen, trägt der Vermieter; "
                     "sie werden nicht auf die Mieter verteilt.")
    if any(x["heat"] for x in r["positions"]):
        heat = "Heiz- und Warmwasserkosten: Die Verteilung nach der Heizkostenverordnung (Grund- und Verbrauchskosten, " \
               "bei Nutzerwechsel Zwischenablesung) hat der Messdienst vorgenommen; dessen Einzelabrechnung liegt bei."
        co2 = period.get("co2") or {}
        if co2.get("tenant_share_cents") is not None and co2.get("stage"):
            heat += (f" CO2-Kosten: Einstufung des Gebäudes {co2.get('stage')}"
                     f"{' (' + str(co2.get('kg_m2')).replace('.', ',') + ' kg CO2/m²/Jahr)' if co2.get('kg_m2') else ''}, "
                     f"Vermieteranteil {co2.get('landlord_percent', '–')} %. Die Berechnungsgrundlagen enthält die Heizkostenabrechnung.")
        notes.append(heat)
    if r["advances_ist"]["bk"] + r["advances_ist"]["hk"] != r["advances_soll"]["bk"] + r["advances_soll"]["hk"]:
        notes.append(f"Vorauszahlungen: Abgezogen sind die tatsächlich geleisteten Beträge "
                     f"({euro(r['advances_ist']['bk'] + r['advances_ist']['hk'])}); vereinbart waren "
                     f"{euro(r['advances_soll']['bk'] + r['advances_soll']['hk'])}.")
    if r["days"] < res["period_days"]:
        notes.append(f"Zeitanteil: Ihr Mietverhältnis bestand im Abrechnungszeitraum an {r['days']} von {res['period_days']} Tagen. "
                     "Zeitabhängige Kosten sind tagegenau anteilig berechnet.")
    notes.append("Rundung: Alle Anteile sind exakt berechnet und erst am Ende auf Cent gerundet; die Summe aller Anteile entspricht "
                 "genau den umlagefähigen Gesamtkosten.")
    notes.append("Belegeinsicht: Sie können die Rechnungen und Belege zu dieser Abrechnung nach Terminabsprache einsehen.")
    notes.append("Einwendungen gegen diese Abrechnung teilen Sie bitte spätestens bis zum Ablauf des zwölften Monats nach Zugang "
                 "mit (§ 556 Abs. 3 Satz 5 BGB).")
    for n in notes:
        story.append(P("• " + n, "small"))
        story.append(Spacer(1, 1.2 * mm))

    # Anlage: Kostenaufstellung
    story.append(P("Anlage: Zusammensetzung der Gesamtkosten", "h2"))
    lines = {l["cost_type"]: l for l in res["lines"]}
    data = [[P("Kostenart / Beleg", "head"), P("Datum", "head"), P("Betrag", "head_r")]]
    for x in r["positions"]:
        line = lines.get(x["cost_type"])
        data.append([P(x["label"], "cell_b"), P("", "cell"), P(euro(x["total"]), "cell_br")])
        for it in (line or {}).get("items", []):
            data.append([P("   " + (it.get("description") or "ohne Beschreibung") + (f" (nur {it['unit_name']})" if it.get("unit_name") else ""),
                           "cell"), P(dt(it.get("invoice_date")), "cell"), P(euro(it["amount_cents"]), "cell_r")])
    tbl = Table(data, colWidths=[120 * mm, 22 * mm, 28 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PETROL), ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
                             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story.append(tbl)
    story.append(Spacer(1, 6 * mm))
    story.append(P("Mit freundlichen Grüßen" + (f"\n\n{ll['name']}" if ll.get("name") else "")))
    return story


def _internal(snap: dict[str, Any]) -> list:
    """Interne Übersicht für den Vermieter (nicht zum Versand)."""
    res = snap["result"]
    story = [P("Interne Übersicht (nicht zum Versand)", "h1"),
             P(f"{res['period']['property_name']} · {dt(res['period']['start'])} – {dt(res['period']['end'])}", "small"), Spacer(1, 4 * mm)]
    data = [[P("Mieter", "head"), P("Zeitraum", "head"), P("Kosten", "head_r"), P("Vorauszahlungen", "head_r"), P("Ergebnis", "head_r")]]
    for r in res["results"]:
        data.append([P(r["names"], "cell"), P(f"{dt(r['from'])}–{dt(r['to'])}", "cell"), P(euro(r["total"]), "cell_r"),
                     P(euro(r["paid"]), "cell_r"), P(("Nachzahlung " if r["balance"] > 0 else "Guthaben " if r["balance"] < 0 else "") +
                                                     euro(abs(r["balance"])), "cell_r")])
    for v in res.get("vacancy", []):
        data.append([P(f"Leerstand {v['unit_name']}", "cell"), P(f"{v['days']} Tage", "cell"), P(euro(v["amount"]), "cell_r"),
                     P("", "cell"), P("trägt Vermieter", "cell_r")])
    tbl = Table(data, colWidths=[52 * mm, 38 * mm, 25 * mm, 28 * mm, 27 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PETROL), ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
                             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [tbl, Spacer(1, 4 * mm)]
    tot = res["totals"]
    story.append(P(f"Erfasste Kosten gesamt: {euro(tot['costs'])} · umlagefähig: {euro(tot['distributable'])} · verteilt: "
                   f"{euro(tot['distributed'])} · davon Leerstand (Vermieter): {euro(res['landlord']['vacancy'])} · nicht umlagefähig: "
                   f"{euro(res['landlord']['excluded'])}", "small"))
    if res.get("issues"):
        story.append(P("Prüfergebnis", "h2"))
        for i in res["issues"]:
            story.append(P(f"[{i['level']}] {i['text']}", "small"))
    return story
