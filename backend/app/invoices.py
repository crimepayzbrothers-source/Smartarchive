"""Rechnungen und Belege: Felder lokal erkennen, Ausgabe als Entwurf anlegen.

Alles hier sind Vorschläge. Eine Ausgabe, die das System selbst anlegt, bleibt
„Entwurf“ und geht erst nach Ihrer Bestätigung in den Export. Es wird nichts
steuerlich bewertet; die Ausgabenkategorie ist nur eine Vorbelegung.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import extract
from .db import audit, now, row, rows

INVOICE_TYPES = {"Rechnung", "Kassenbon", "Quittung"}

_INVOICE_NO = re.compile(
    r"(?:rechnungs?[\s-]*(?:nr|nummer|no)\.?|\bre[\s.-]*nr\.?|beleg[\s-]*(?:nr|nummer)\.?|\bbon[\s-]*(?:nr|nummer)\.?|"
    r"invoice\s*(?:no|number)\.?)\s*[:.#]?\s*([A-Z0-9][A-Z0-9\-/._]{1,30})",
    re.I,
)
_PAID = re.compile(
    r"(?:bezahlt|beglichen|zahlung\s+erhalten|betrag\s+(?:dankend\s+)?erhalten|bar\s+erhalten|dankend\s+erhalten)"
    r"(?:\s+am)?\s*[:.]?\s*(\d{1,2}\.\s?\d{1,2}\.\s?(?:\d{4}|\d{2}))?",
    re.I,
)
_RECEIPT_PAYMENT = re.compile(
    r"\b(barzahlung|bar|ec[- ]?karte|girocard|kartenzahlung|gegeben|rückgeld|rueckgeld|kreditkarte|visa|mastercard)\b", re.I)

# Vorbelegung der Ausgabenkategorie (Anlage V). Reihenfolge = Vorrang bei Gleichstand.
EXPENSE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("grundsteuer", ["grundsteuer"]),
    ("grundbesitzabgaben", ["müllabfuhr", "abfallgebühr", "abfallentsorgung", "abwasser", "niederschlagswasser",
                            "straßenreinigung", "frischwasser", "wasserversorgung", "grundbesitzabgaben"]),
    ("versicherung", ["gebäudeversicherung", "wohngebäudeversicherung", "grundbesitzerhaftpflicht", "elementarschaden"]),
    ("heizung", ["heizöl", "fernwärme", "pellets", "brennstoff", "heizkosten", "wärmelieferung", "flüssiggas",
                 "heizungswartung", "wartung heizung", "brennerwartung"]),
    ("hausnebenkosten", ["schornsteinfeger", "kaminkehrer", "hausmeister", "hauswart", "treppenhausreinigung",
                         "gartenpflege", "winterdienst", "hausbeleuchtung", "allgemeinstrom"]),
    ("verwaltung", ["hausverwaltung", "verwaltergebühr", "verwaltervergütung", "verwaltungsgebühr"]),
    ("hausgeld_laufend", ["hausgeld", "wohngeldabrechnung", "hausgeldabrechnung"]),
    ("schuldzinsen", ["sollzinsen", "darlehenszinsen", "zinsbescheinigung", "schuldzinsen"]),
    ("geldbeschaffung", ["schätzgebühr", "bearbeitungsgebühr darlehen", "grundschuldbestellung", "bereitstellungsprovision"]),
    ("erhaltung_sofort", ["reparatur", "instandsetzung", "instandhaltung", "austausch", "erneuerung", "handwerker",
                          "sanitär", "elektroinstallation", "malerarbeiten", "maler", "dachdecker", "fliesen",
                          "baumarkt", "material", "montage", "arbeitszeit", "anfahrt", "monteur", "stundenlohn",
                          "silikon", "dichtung", "ersatzteil"]),
]


def _kw_re(word: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\wäöüß])" + re.escape(word).replace(r"\ ", r"\s+"), re.I)


def suggest_expense_category(text: str) -> tuple[str | None, list[str]]:
    low = text.lower()
    best: tuple[int, int, str, list[str]] | None = None  # (Treffer, -Rang, Schlüssel, Wörter)
    for rank, (key, words) in enumerate(EXPENSE_KEYWORDS):
        hits = [w for w in words if _kw_re(w).search(low)]
        if hits and (best is None or (len(hits), -rank) > (best[0], best[1])):
            best = (len(hits), -rank, key, hits)
    return (best[2], best[3]) if best else (None, [])


def detect_invoice_number(text: str) -> str | None:
    for m in _INVOICE_NO.finditer(text[:5000]):
        value = m.group(1).strip(".-/_")
        if re.search(r"\d", value) and not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", value):
            return value[:30]
    return None


def detect_paid_on(text: str, doc_type: str | None, doc_date: str | None) -> tuple[str | None, str]:
    """Zahlungsdatum nur, wenn der Beleg es hergibt: „bezahlt am …“ oder ein Kassenbon mit Bar-/Kartenzahlung."""
    for m in _PAID.finditer(text):
        if m.group(1):
            dates = extract.find_dates(m.group(1))
            if dates:
                return dates[0][0].isoformat(), m.group(0).strip()[:120]
        if doc_date:
            return doc_date, m.group(0).strip()[:120]
    if doc_type in ("Kassenbon", "Quittung") and doc_date:
        m = _RECEIPT_PAYMENT.search(text)
        if m:
            return doc_date, f"Beleg mit Zahlart „{m.group(1)}“"
    return None, ""


_COMPANY = re.compile(r"\b(gmbh|ag|kg|ohg|gbr|ug|e\.\s?k\.|inh\.|baumarkt|stadtwerke|verbandsgemeinde|kreisverwaltung|"
                      r"versicherung|sparkasse|volksbank|bank|meisterbetrieb|handwerk|sanit(?:ä|ae|a)r|elektro|heizung)", re.I)
_SKIP_LINE = re.compile(r"^(herrn?|frau|familie|firma\s*$|rechnung|datum|seite|tel|fax|e-?mail|www\.)", re.I)


def guess_payee(text: str) -> str | None:
    """Aussteller aus dem Briefkopf: erste Kopfzeile mit Firmenmerkmal, nur bis zum ersten Trenner."""
    lines = [ln.strip() for ln in text.splitlines()[:8] if ln.strip()]
    for line in lines:
        if _SKIP_LINE.match(line):
            continue
        name = re.split(r"\s+[-–·|•]\s+|,\s*|\s{3,}", line)[0].strip()
        if 3 <= len(name) <= 60 and re.search(r"[A-Za-zÄÖÜäöü]{3}", name) and _COMPANY.search(name):
            return name
    return None


def fields(text: str, doc_type: str | None, doc_date: str | None) -> dict[str, Any]:
    """Rechnungsfelder für den Vorschlag. Leere Werte werden weggelassen."""
    amount, amount_ev = extract.detect_amount(text)
    paid_on, paid_ev = detect_paid_on(text, doc_type, doc_date)
    cat, words = suggest_expense_category(text)
    out: dict[str, Any] = {
        "number": detect_invoice_number(text),
        "payee": guess_payee(text),
        "amount_cents": amount,
        "amount_evidence": amount_ev or None,
        "paid_on": paid_on,
        "paid_evidence": paid_ev or None,
        "expense_category": cat,
        "expense_reason": ("Stichwörter: " + ", ".join(words[:4])) if words else None,
    }
    return {k: v for k, v in out.items() if v}


def is_invoice(payload: dict[str, Any]) -> bool:
    return payload.get("doc_type") in INVOICE_TYPES or bool(payload.get("invoice"))


def _latest_payload(conn: sqlite3.Connection, document_id: int) -> dict[str, Any]:
    r = conn.execute("SELECT payload FROM suggestions WHERE document_id = ? AND status != 'superseded' ORDER BY id DESC LIMIT 1",
                     (document_id,)).fetchone()
    return json.loads(r[0]) if r else {}


def expense_values(conn: sqlite3.Connection, document_id: int) -> dict[str, Any] | None:
    """Vorbelegung einer Ausgabe aus Dokument und letztem Vorschlag (oder None, wenn kein Betrag erkannt)."""
    d = row(conn.execute("SELECT d.*, c.name AS corr FROM documents d LEFT JOIN correspondents c ON c.id = d.correspondent_id "
                         "WHERE d.id = ?", (document_id,)))
    if not d:
        return None
    p = _latest_payload(conn, document_id)
    inv = p.get("invoice") or {}
    amount = inv.get("amount_cents") or p.get("amount_cents")
    if not amount:
        return None
    return {
        "amount_cents": int(amount),
        "category_key": inv.get("expense_category") or "sonstige",
        "invoice_date": d["doc_date"] or p.get("doc_date"),
        "paid_on": inv.get("paid_on"),
        "payee": (d["corr"] or p.get("correspondent_name") or inv.get("payee") or "")[:120],
        "note": f"Rechnung Nr. {inv['number']}" if inv.get("number") else "",
    }


def ensure_draft_expense(conn: sqlite3.Connection, document_id: int, actor: str = "system") -> int | None:
    """Legt für eine abgelegte Rechnung im Bereich Vermietung eine Ausgabe als Entwurf an.

    Nur wenn ein Mietobjekt feststeht, ein Betrag erkannt wurde und am Dokument noch keine Ausgabe hängt.
    """
    d = row(conn.execute("SELECT d.*, a.key AS area_key FROM documents d LEFT JOIN areas a ON a.id = d.area_id WHERE d.id = ?",
                         (document_id,)))
    if not d or d["area_key"] != "vermietung" or not d["property_id"] or d["preset"]:
        return None
    if conn.execute("SELECT 1 FROM expenses WHERE document_id = ? LIMIT 1", (document_id,)).fetchone():
        return None
    payload = _latest_payload(conn, document_id)
    if not (is_invoice(payload) or d["doc_type"] in INVOICE_TYPES):
        return None
    values = expense_values(conn, document_id)
    if not values:
        return None
    ts = now()
    eid = conn.execute(
        "INSERT INTO expenses(document_id, property_id, unit_id, invoice_date, paid_on, amount_cents, payee, category_key, note, "
        "status, origin, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (document_id, d["property_id"], d["unit_id"], values["invoice_date"], values["paid_on"], values["amount_cents"],
         values["payee"], values["category_key"], values["note"], "entwurf", "auto", ts, ts),
    ).lastrowid
    audit(conn, "expense_added", "document", document_id,
          {"expense_id": eid, "betrag": f"{values['amount_cents'] / 100:.2f}".replace(".", ",") + " €", "entwurf": True}, actor=actor)
    return eid


# --- Nacherfassung -------------------------------------------------------------

_YEARLY_TAX = {"grundsteuer", "grundbesitzabgaben"}


def missing_expenses(conn: sqlite3.Connection, year: int | None = None) -> list[dict[str, Any]]:
    """Abgelegte Rechnungen im Bereich Vermietung ohne Ausgabe, mit Vorschlag aus dem erkannten Text.

    Erfasst auch importierte Dokumente (dort legt der Import bewusst keine Ausgaben an) und Bescheide
    zu Grundsteuer/Abgaben. Jahr: Rechnungsdatum, sonst Dokumentdatum, sonst Hochladedatum.
    """
    docs = rows(conn.execute(
        "SELECT d.id, d.title, d.doc_type, d.doc_date, d.created_at, d.property_id, d.unit_id, d.source_path, "
        "p.name AS property_name, c.name AS correspondent_name, t.content FROM documents d "
        "JOIN areas a ON a.id = d.area_id AND a.key = 'vermietung' "
        "LEFT JOIN properties p ON p.id = d.property_id LEFT JOIN correspondents c ON c.id = d.correspondent_id "
        "LEFT JOIN document_text t ON t.document_id = d.id "
        "WHERE d.deleted_at IS NULL AND d.status = 'filed' AND d.no_expense = 0 "
        "AND NOT EXISTS (SELECT 1 FROM expenses e WHERE e.document_id = d.id) ORDER BY COALESCE(d.doc_date, d.created_at) DESC"))
    out = []
    for d in docs:
        text = d.pop("content") or ""
        doc_type = d["doc_type"] or extract.detect_doc_type(text)
        if doc_type not in INVOICE_TYPES and doc_type != "Bescheid":
            continue
        doc_date = d["doc_date"] or extract.detect_date(text)[0]
        f = fields(text, doc_type, doc_date)
        if doc_type == "Bescheid" and f.get("expense_category") not in _YEARLY_TAX:
            continue
        when = doc_date or d["created_at"][:10]
        if year and when[:4] != str(year):
            continue
        payee = d["correspondent_name"] or f.get("payee") or ""
        out.append(d | {
            "doc_type": doc_type, "date": doc_date, "amount_cents": f.get("amount_cents"),
            "category_key": f.get("expense_category"), "paid_on": f.get("paid_on"), "payee": payee[:120],
            "note": f"Rechnung Nr. {f['number']}" if f.get("number") else "",
            "reason": f.get("expense_reason"), "amount_evidence": f.get("amount_evidence"),
        })
    return out


def create_drafts(conn: sqlite3.Connection, year: int | None) -> dict[str, int]:
    """Legt für alle gefundenen Rechnungen mit Betrag und Objekt eine Ausgabe als Entwurf an."""
    made = skipped = 0
    ts = now()
    for m in missing_expenses(conn, year):
        if not m["amount_cents"] or not m["property_id"]:
            skipped += 1
            continue
        eid = conn.execute(
            "INSERT INTO expenses(document_id, property_id, unit_id, invoice_date, paid_on, amount_cents, payee, category_key, note, "
            "status, origin, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["id"], m["property_id"], m["unit_id"], m["date"], m["paid_on"], m["amount_cents"], m["payee"],
             m["category_key"] or "sonstige", m["note"], "entwurf", "auto", ts, ts)).lastrowid
        audit(conn, "expense_added", "document", m["id"], {"expense_id": eid, "nacherfasst": True, "entwurf": True})
        made += 1
    return {"created": made, "skipped": skipped}
