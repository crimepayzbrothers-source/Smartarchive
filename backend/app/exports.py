"""Ausgaben-Auswertung und Exporte (Excel, CSV, PDF-Paket, Gesamtarchiv)."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from datetime import date
from typing import Any

from . import storage
from .documents import best_pdf, display_filename
from .db import rows
from .seed import EXPENSE_CATEGORIES

LABEL = {c["key"]: c["label"] for c in EXPENSE_CATEGORIES}
DEDUCTIBLE = {c["key"]: c["deductible"] for c in EXPENSE_CATEGORIES}
ORDER = {c["key"]: i for i, c in enumerate(EXPENSE_CATEGORIES)}
STATUS_LABEL = {"entwurf": "Entwurf", "bestaetigt": "Bestätigt", "ungeklaert": "Ungeklärt"}


def euro(cents: int | None) -> str:
    if cents is None:
        return ""
    s = f"{abs(cents) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if cents < 0 else "") + s + " €"


def expense_rows(conn: sqlite3.Connection, year: int | None = None, property_id: int | None = None,
                 status: str | None = None, open_payment: bool = False) -> list[dict[str, Any]]:
    where, params = ["1=1"], []
    if open_payment:
        where.append("e.paid_on IS NULL")
        if year:
            where.append("substr(COALESCE(e.invoice_date, d.doc_date, e.created_at), 1, 4) = ?")
            params.append(str(year))
    elif year:
        where.append("substr(e.paid_on, 1, 4) = ?")
        params.append(str(year))
    if property_id:
        where.append("e.property_id = ?")
        params.append(property_id)
    if status:
        where.append("e.status = ?")
        params.append(status)
    return rows(conn.execute(
        "SELECT e.*, p.name AS property_name, u.name AS unit_name, d.title AS document_title "
        "FROM expenses e LEFT JOIN properties p ON p.id = e.property_id LEFT JOIN units u ON u.id = e.unit_id "
        "LEFT JOIN documents d ON d.id = e.document_id WHERE " + " AND ".join(where) +
        " ORDER BY p.name, e.paid_on, e.id", params))


def find_issues(conn: sqlite3.Connection, year: int | None = None) -> list[dict[str, Any]]:
    """Prüfhinweise – keine steuerliche Wertung, nur Auffälligkeiten."""
    issues: list[dict[str, Any]] = []
    cond = "1=1"
    params: list[Any] = []
    if year:
        cond = "substr(COALESCE(e.paid_on, e.invoice_date, e.created_at), 1, 4) = ?"
        params = [str(year)]
    for e in rows(conn.execute(f"SELECT e.*, d.title AS document_title FROM expenses e LEFT JOIN documents d ON d.id = e.document_id WHERE {cond}", params)):
        label = e["payee"] or e["document_title"] or f"Position {e['id']}"
        if not e["paid_on"]:
            issues.append({"kind": "zahlung_offen", "expense_id": e["id"], "document_id": e["document_id"],
                           "text": f"{label}: Zahlungsdatum fehlt – Steuerjahr unklar."})
        if not e["property_id"]:
            issues.append({"kind": "objekt_fehlt", "expense_id": e["id"], "document_id": e["document_id"],
                           "text": f"{label}: kein Mietobjekt zugeordnet."})
        if e["status"] == "ungeklaert":
            issues.append({"kind": "ungeklaert", "expense_id": e["id"], "document_id": e["document_id"],
                           "text": f"{label}: als ungeklärt markiert."})
    # mögliche Dubletten: gleicher Betrag + gleicher Aussteller, Datum höchstens 10 Tage auseinander
    dups = rows(conn.execute(
        "SELECT a.id AS a_id, b.id AS b_id, a.document_id AS a_doc, b.document_id AS b_doc, a.payee, a.amount_cents "
        "FROM expenses a JOIN expenses b ON a.id < b.id AND a.amount_cents = b.amount_cents "
        "AND lower(trim(a.payee)) = lower(trim(b.payee)) AND a.payee != '' "
        "AND abs(julianday(COALESCE(a.invoice_date, a.paid_on)) - julianday(COALESCE(b.invoice_date, b.paid_on))) <= 10 "
        "AND COALESCE(a.document_id, -1) != COALESCE(b.document_id, -2)"))
    for d in dups:
        issues.append({"kind": "dublette", "expense_id": d["b_id"], "document_id": d["b_doc"],
                       "text": f"Mögliche Doppelerfassung: {d['payee']} {euro(d['amount_cents'])} (Positionen {d['a_id']} und {d['b_id']})."})
    # anschaffungsnahe Herstellungskosten: Hinweis, keine Berechnung
    for p in rows(conn.execute("SELECT id, name, acquired_on FROM properties WHERE acquired_on IS NOT NULL")):
        try:
            acq = date.fromisoformat(p["acquired_on"])
        except ValueError:
            continue
        limit = date(acq.year + 3, acq.month, min(acq.day, 28)).isoformat()
        n = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE property_id = ? AND category_key IN ('erhaltung_sofort','erhaltung_verteilt') "
            "AND COALESCE(paid_on, invoice_date) BETWEEN ? AND ?", (p["id"], p["acquired_on"], limit)).fetchone()[0]
        if n:
            issues.append({"kind": "anschaffungsnah", "property_id": p["id"],
                           "text": f"{p['name']}: {n} Erhaltungsposition(en) innerhalb von drei Jahren nach Anschaffung – "
                                   "Prüfhinweis § 6 Abs. 1 Nr. 1a EStG (anschaffungsnahe Herstellungskosten)."})
    return issues


def summary(conn: sqlite3.Connection, year: int, property_id: int | None = None) -> dict[str, Any]:
    items = expense_rows(conn, year, property_id)
    by_prop: dict[str, dict[str, Any]] = {}
    for e in items:
        key = e["property_name"] or "Ohne Objekt"
        bucket = by_prop.setdefault(key, {"property_id": e["property_id"], "property": key, "total": 0,
                                          "confirmed": 0, "categories": {}})
        bucket["total"] += e["amount_cents"]
        if e["status"] == "bestaetigt":
            bucket["confirmed"] += e["amount_cents"]
        cat = bucket["categories"].setdefault(e["category_key"], {"key": e["category_key"], "label": LABEL.get(e["category_key"], e["category_key"]), "total": 0, "count": 0})
        cat["total"] += e["amount_cents"]
        cat["count"] += 1
    props = []
    for b in by_prop.values():
        b["categories"] = sorted(b["categories"].values(), key=lambda c: ORDER.get(c["key"], 99))
        props.append(b)
    prev = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM expenses WHERE substr(paid_on,1,4) = ?" +
                        (" AND property_id = ?" if property_id else ""),
                        [str(year - 1)] + ([property_id] if property_id else [])).fetchone()[0]
    open_items = expense_rows(conn, year, property_id, open_payment=True)
    return {
        "year": year,
        "total": sum(e["amount_cents"] for e in items),
        "confirmed": sum(e["amount_cents"] for e in items if e["status"] == "bestaetigt"),
        "unconfirmed": sum(e["amount_cents"] for e in items if e["status"] != "bestaetigt"),
        "non_deductible": sum(e["amount_cents"] for e in items if not DEDUCTIBLE.get(e["category_key"], True)),
        "previous_year_total": prev,
        "open_payment_count": len(open_items),
        "properties": props,
    }


# --- Exporte --------------------------------------------------------------

def to_csv(conn: sqlite3.Connection, year: int, property_id: int | None = None) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Objekt", "Einheit", "Zahlungsdatum", "Rechnungsdatum", "Aussteller", "Kategorie",
                "Sofort abziehbar", "Betrag", "Status", "Notiz", "Beleg-ID", "Beleg", "Seite"])
    for e in expense_rows(conn, year, property_id):
        w.writerow([e["property_name"] or "", e["unit_name"] or "", e["paid_on"] or "", e["invoice_date"] or "",
                    e["payee"], LABEL.get(e["category_key"], e["category_key"]),
                    "ja" if DEDUCTIBLE.get(e["category_key"], True) else "nein",
                    f"{e['amount_cents'] / 100:.2f}".replace(".", ","), STATUS_LABEL.get(e["status"], e["status"]),
                    e["note"], e["document_id"] or "", e["document_title"] or "", e["page"] or ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def to_xlsx(conn: sqlite3.Connection, year: int, property_id: int | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="2F3A4A")
    money = '#,##0.00 "€"'

    def header(ws, cols: list[str], widths: list[int]) -> None:
        ws.append(cols)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
            cell = ws.cell(row=ws.max_row, column=i)
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(vertical="center")
        ws.freeze_panes = ws.cell(row=ws.max_row + 1, column=1)

    items = expense_rows(conn, year, property_id)
    confirmed = [e for e in items if e["status"] == "bestaetigt"]

    ws = wb.active
    ws.title = "Übersicht"
    ws.append([f"Ausgaben Vermietung {year}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Nur bestätigte Positionen. Offene Positionen siehe Blatt „Offen“. Keine Steuerberatung."])
    ws.append([])
    header(ws, ["Objekt", "Kategorie", "Sofort abziehbar", "Anzahl", "Summe"], [28, 52, 16, 10, 16])
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in confirmed:
        groups.setdefault((e["property_name"] or "Ohne Objekt", e["category_key"]), []).append(e)
    for (prop, key), es in sorted(groups.items(), key=lambda x: (x[0][0], ORDER.get(x[0][1], 99))):
        ws.append([prop, LABEL.get(key, key), "ja" if DEDUCTIBLE.get(key, True) else "nein", len(es),
                   sum(x["amount_cents"] for x in es) / 100])
        ws.cell(row=ws.max_row, column=5).number_format = money
    ws.append(["Gesamt", "", "", len(confirmed), sum(x["amount_cents"] for x in confirmed) / 100])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    ws.cell(row=ws.max_row, column=5).font = Font(bold=True)
    ws.cell(row=ws.max_row, column=5).number_format = money

    cols = ["Zahlungsdatum", "Rechnungsdatum", "Aussteller", "Kategorie", "Betrag", "Einheit", "Notiz", "Beleg-ID", "Beleg", "Seite"]
    widths = [14, 14, 28, 44, 14, 14, 36, 10, 40, 8]
    by_prop: dict[str, list[dict]] = {}
    for e in confirmed:
        by_prop.setdefault(e["property_name"] or "Ohne Objekt", []).append(e)
    for prop, es in by_prop.items():
        sheet = wb.create_sheet(_sheet_title(prop))
        header(sheet, cols, widths)
        for e in es:
            sheet.append([e["paid_on"], e["invoice_date"], e["payee"], LABEL.get(e["category_key"], e["category_key"]),
                          e["amount_cents"] / 100, e["unit_name"] or "", e["note"], e["document_id"],
                          e["document_title"] or "", e["page"]])
            sheet.cell(row=sheet.max_row, column=5).number_format = money
        sheet.append(["", "", "", "Summe", sum(x["amount_cents"] for x in es) / 100])
        sheet.cell(row=sheet.max_row, column=5).number_format = money
        sheet.cell(row=sheet.max_row, column=4).font = Font(bold=True)

    open_rows = [e for e in items if e["status"] != "bestaetigt"] + expense_rows(conn, year, property_id, open_payment=True)
    sheet = wb.create_sheet("Offen")
    header(sheet, ["Objekt", "Status", "Zahlungsdatum", "Rechnungsdatum", "Aussteller", "Kategorie", "Betrag", "Beleg-ID"],
           [24, 14, 14, 14, 28, 44, 14, 10])
    for e in open_rows:
        sheet.append([e["property_name"] or "", "Zahlung offen" if not e["paid_on"] else STATUS_LABEL.get(e["status"], e["status"]),
                      e["paid_on"], e["invoice_date"], e["payee"], LABEL.get(e["category_key"], e["category_key"]),
                      e["amount_cents"] / 100, e["document_id"]])
        sheet.cell(row=sheet.max_row, column=7).number_format = money

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _sheet_title(name: str) -> str:
    for ch in '[]:*?/\\':
        name = name.replace(ch, " ")
    return (name.strip() or "Objekt")[:31]


def to_pdf_bundle(conn: sqlite3.Connection, year: int, property_id: int | None = None) -> bytes:
    """Übersicht plus alle zugehörigen Belege in Reihenfolge (für Rückfragen des Finanzamts)."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    items = [e for e in expense_rows(conn, year, property_id) if e["status"] == "bestaetigt"]
    doc_order: list[int] = []
    for e in items:
        if e["document_id"] and e["document_id"] not in doc_order:
            doc_order.append(e["document_id"])
    beleg_nr = {doc_id: i + 1 for i, doc_id in enumerate(doc_order)}

    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title=f"Belegübersicht Vermietung {year}")
    story = [Paragraph(f"Belegübersicht Vermietung {year}", styles["Title"]),
             Paragraph("Bestätigte Positionen mit Belegnummer. Die Belege folgen in dieser Reihenfolge. "
                       "Erstellt mit SmartArchive – keine Steuerberatung.", styles["Normal"]),
             Spacer(1, 6 * mm)]
    data = [["Nr.", "Objekt", "Zahlung", "Aussteller", "Kategorie", "Betrag"]]
    for e in items:
        data.append([str(beleg_nr.get(e["document_id"], "–")), (e["property_name"] or "")[:22], e["paid_on"] or "",
                     Paragraph((e["payee"] or "")[:40], styles["BodyText"]),
                     Paragraph(LABEL.get(e["category_key"], e["category_key"]), styles["BodyText"]), euro(e["amount_cents"])])
    data.append(["", "", "", "", "Summe", euro(sum(e["amount_cents"] for e in items))])
    table = Table(data, colWidths=[12 * mm, 32 * mm, 22 * mm, 45 * mm, 50 * mm, 24 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F3A4A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (5, 0), (5, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#CCCCCC")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    story.append(table)
    pdf.build(story)

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(buf.getvalue())).pages:
        writer.add_page(page)
    for doc_id in doc_order:
        f = best_pdf(conn, doc_id)
        if not f:
            continue
        try:
            reader = PdfReader(str(storage.abs_path(f["path"])))
        except Exception:  # noqa: BLE001
            continue
        start = len(writer.pages)
        for page in reader.pages:
            writer.add_page(page)
        writer.add_outline_item(f"Beleg {beleg_nr[doc_id]}", start)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def full_archive_zip(conn: sqlite3.Connection, fh: Any) -> None:
    """Gesamtexport: alle Originale, durchsuchbare PDFs und Metadaten als JSON."""
    docs = rows(conn.execute(
        "SELECT d.*, a.key AS area_key, cat.name AS category_name, c.name AS correspondent_name, p.name AS property_name "
        "FROM documents d LEFT JOIN areas a ON a.id = d.area_id LEFT JOIN categories cat ON cat.id = d.category_id "
        "LEFT JOIN correspondents c ON c.id = d.correspondent_id LEFT JOIN properties p ON p.id = d.property_id "
        "WHERE d.deleted_at IS NULL ORDER BY d.id"))
    with zipfile.ZipFile(fh, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for d in docs:
            folder = f"{d['area_key'] or 'eingang'}/{d['id']:05d}_{display_filename(conn, d['id'])[:-4]}"
            for f in rows(conn.execute("SELECT * FROM files WHERE document_id = ? AND role IN ('original','searchable','pdf')", (d["id"],))):
                try:
                    z.write(storage.abs_path(f["path"]), f"{folder}/{f['role']}_{f['position']}{_ext(f['mime'])}")
                except (FileNotFoundError, ValueError):
                    continue
        meta = {
            "documents": docs,
            "expenses": rows(conn.execute("SELECT * FROM expenses ORDER BY id")),
            "properties": rows(conn.execute("SELECT * FROM properties ORDER BY id")),
            "units": rows(conn.execute("SELECT * FROM units ORDER BY id")),
            "categories": rows(conn.execute("SELECT * FROM categories ORDER BY id")),
            "correspondents": rows(conn.execute("SELECT * FROM correspondents ORDER BY id")),
            "tags": rows(conn.execute("SELECT * FROM tags ORDER BY id")),
            "document_tags": rows(conn.execute("SELECT * FROM document_tags")),
            "rules": rows(conn.execute("SELECT * FROM rules ORDER BY id")),
        }
        z.writestr("metadaten.json", json.dumps(meta, ensure_ascii=False, indent=1, default=str))


def _ext(mime: str) -> str:
    return storage.MIME_EXT.get(mime, ".bin")
