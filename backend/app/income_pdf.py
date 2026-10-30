"""Einnahmenübersicht Vermietung als PDF (zur Weitergabe an das Finanzamt bzw. als Anlage zur Steuererklärung).

Enthält nur Zahlen aus den Einnahmen – keine steuerliche Berechnung oder Bewertung. Eingänge, die nur „als eingegangen
angenommen“ sind (kein Kontoumsatz, keine Barzahlung erfasst), werden getrennt ausgewiesen, damit nichts ohne Nachweis
unbemerkt in die Summe gerät.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .bk_pdf import LINE, MUTED, PETROL, SOFT, S, P, _FONT, dt, euro

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
PART_KEYS = ("cold", "bk", "hk", "excess")


def _table(data: list[list], widths: list[float], total_row: bool = True) -> Table:
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), PETROL), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    if total_row:
        style.append(("BACKGROUND", (0, -1), (-1, -1), SOFT))
    t.setStyle(TableStyle(style))
    return t


def _period(it: dict[str, Any], year: int) -> str:
    s = max(it["contract_start"], f"{year}-01-01")
    e = min(it["contract_end"] or f"{year}-12-31", f"{year}-12-31")
    return f"{dt(s)} – {dt(e)}"


def _sum(items: list[dict[str, Any]], key: str) -> int:
    return sum(i[key] for i in items)


def render(ov: dict[str, Any], landlord: dict[str, Any], scope: str) -> bytes:
    year = ov["year"]
    buf = io.BytesIO()
    created = date.today()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Einnahmenübersicht Vermietung {year}", author=landlord.get("name") or "SmartArchive")
    story: list = []
    who = " · ".join(x.replace("\n", ", ") for x in (landlord.get("name"), landlord.get("address")) if x)
    story += [Paragraph(f"Einnahmenübersicht Vermietung {year}", S["h1"]),
              P(f"{who}\n" if who else "", "small"), P(f"{scope} · Stand {dt(created.isoformat())}", "small"), Spacer(1, 5 * mm)]

    groups: dict[str, list[dict[str, Any]]] = {}
    for it in ov["items"]:
        groups.setdefault(it["property_name"], []).append(it)

    # Zusammenfassung über alle Objekte
    parts = ov["parts"]
    total = sum(parts[k] for k in PART_KEYS)
    assumed = ov["totals"]["assumed"]
    other = sum(u["amount_cents"] for u in ov["unassigned"])
    rows_ = [[P("Zusammenfassung", "head"), P("Betrag", "head_r")],
             [P("Mieteinnahmen (Kaltmiete)", "cell"), P(euro(parts["cold"]), "cell_r")],
             [P("Vorauszahlungen Betriebskosten (Umlagen)", "cell"), P(euro(parts["bk"]), "cell_r")],
             [P("Vorauszahlungen Heizkosten (Umlagen)", "cell"), P(euro(parts["hk"]), "cell_r")],
             [P("Mehrbeträge (Zahlungen über der Monatsmiete, z. B. Nachzahlungen)", "cell"), P(euro(parts["excess"]), "cell_r")]]
    if other:
        rows_.append([P("Weitere Einnahmen ohne Mietverhältnis", "cell"), P(euro(other), "cell_r")])
    rows_.append([P("Summe Einnahmen", "cell_b"), P(euro(total + other), "cell_br")])
    story += [_table(rows_, [140, 38]), Spacer(1, 2 * mm)]
    if assumed:
        story.append(P(f"Darin enthalten: {euro(assumed)} ohne Zahlungsnachweis – fällige Mieten, die in SmartArchive automatisch als "
                       f"eingegangen verbucht sind, für die aber kein Kontoumsatz und keine Barzahlung erfasst ist. Mit Nachweis: "
                       f"{euro(total + other - assumed)}.", "small"))
    if ov["totals"]["arrears"]:
        story.append(P(f"Offene Mietrückstände {year} (nicht in den Einnahmen enthalten): {euro(ov['totals']['arrears'])}.", "small"))
    story.append(Spacer(1, 6 * mm))

    for name, items in groups.items():
        ip = {k: sum(i["ist_parts"][k] for i in items) for k in PART_KEYS}
        head = [P("Mieter / Wohnung", "head"), P("Zeitraum", "head"), P("Kaltmiete", "head_r"), P("BK-VZ", "head_r"),
                P("HK-VZ", "head_r"), P("Mehrbetrag", "head_r"), P("Summe", "head_r")]
        data = [head]
        for it in items:
            pt = it["ist_parts"]
            label = it["names"] + (f"\n{it['unit_name']}" if it["unit_name"] else "")
            note = f"\nohne Nachweis {euro(it['assumed'])}" if it["assumed"] else ""
            data.append([P(label, "cell"), P(_period(it, year), "cell"), P(euro(pt["cold"]), "cell_r"), P(euro(pt["bk"]), "cell_r"),
                         P(euro(pt["hk"]), "cell_r"), P(euro(pt["excess"]), "cell_r"), P(euro(it["ist"]) + note, "cell_r")])
        data.append([P("Summe", "cell_b"), "", P(euro(ip["cold"]), "cell_br"), P(euro(ip["bk"]), "cell_br"), P(euro(ip["hk"]), "cell_br"),
                     P(euro(ip["excess"]), "cell_br"), P(euro(_sum(items, "ist")), "cell_br")])
        story += [Paragraph(name, S["h2"]), _table(data, [38, 28, 22, 21, 21, 22, 26]), Spacer(1, 4 * mm)]

        mdata = [[P("Monat", "head"), P("Kaltmiete", "head_r"), P("BK-VZ", "head_r"), P("HK-VZ", "head_r"), P("Mehrbetrag", "head_r"),
                  P("Summe", "head_r"), P("davon ohne Nachweis", "head_r")]]
        for m in range(12):
            mp = {k: sum(i["months"][m]["ist_parts"][k] for i in items) for k in PART_KEYS}
            ist = sum(i["months"][m]["ist"] for i in items)
            ass = sum(i["months"][m]["ist"] for i in items if i["months"][m]["assumed"])
            mdata.append([P(MONTHS[m], "cell"), P(euro(mp["cold"]), "cell_r"), P(euro(mp["bk"]), "cell_r"), P(euro(mp["hk"]), "cell_r"),
                          P(euro(mp["excess"]), "cell_r"), P(euro(ist), "cell_r"), P(euro(ass) if ass else "–", "cell_r")])
        mdata.append([P("Summe", "cell_b"), P(euro(ip["cold"]), "cell_br"), P(euro(ip["bk"]), "cell_br"), P(euro(ip["hk"]), "cell_br"),
                      P(euro(ip["excess"]), "cell_br"), P(euro(_sum(items, "ist")), "cell_br"),
                      P(euro(_sum(items, "assumed")) if _sum(items, "assumed") else "–", "cell_br")])
        story += [KeepTogether([P(f"Monatsübersicht {name}", "small"), Spacer(1, 1 * mm), _table(mdata, [34, 26, 24, 24, 24, 24, 22])]),
                  Spacer(1, 6 * mm)]

    if ov["unassigned"]:
        data = [[P("Datum", "head"), P("Zahler / Verwendungszweck", "head"), P("Betrag", "head_r")]]
        for u in ov["unassigned"]:
            data.append([P(dt(u["booking_date"]), "cell"), P(f"{u['counterparty']}\n{u['purpose'][:120]}", "cell"),
                         P(euro(u["amount_cents"]), "cell_r")])
        data.append([P("Summe", "cell_b"), "", P(euro(other), "cell_br")])
        story += [Paragraph("Weitere Einnahmen ohne Mietverhältnis", S["h2"]), _table(data, [24, 124, 30]), Spacer(1, 6 * mm)]

    story += [Paragraph("Erläuterungen", S["h2"]),
              P("Grundlage sind die in SmartArchive erfassten Mietverhältnisse (Soll) sowie die zugeordneten Eingänge aus dem Mietkonto "
                "und von Hand erfasste Barzahlungen (Ist). Zahlungen ab dem 25. eines Monats sind dem Folgemonat zugeordnet.", "small"),
              P("Die Aufteilung auf Kaltmiete und Vorauszahlungen folgt den vereinbarten Beträgen des jeweiligen Monats; Teilzahlungen sind "
                "im Verhältnis dieser Beträge aufgeteilt. Beträge über der Monatsmiete sind als Mehrbetrag ausgewiesen.", "small"),
              P("Diese Übersicht ist eine Zusammenstellung der erfassten Zahlen. Sie enthält keine steuerliche Berechnung oder Bewertung "
                "und ersetzt nicht die Anlage V.", "small")]

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setFont(_FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 10 * mm, f"Einnahmenübersicht Vermietung {year} · {scope}")
        canvas.drawRightString(194 * mm, 10 * mm, f"Seite {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
