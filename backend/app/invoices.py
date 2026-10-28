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
_POS_PAYMENT = re.compile(
    r"^\s*(?:bar|ec|girocard|maestro|visa|mastercard)\b[^\n]*\d+,\d{2}|\bgegeben\b|r(?:ü|ue)ckgeld|\bgirocard\b|"
    r"ec[- ]?karte|kartenzahlung|bezahlt mit|kontaktlos", re.I | re.M)
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
    if doc_date:
        m = _POS_PAYMENT.search(text)  # Zahlung an der Kasse, auch ohne das Wort „Kassenbon“
        if m:
            return doc_date, f"An der Kasse bezahlt („{m.group(0).strip()[:30]}“)"
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


# --- Automatische Erfassung als Aufwand ---------------------------------------
#
# Jede abgelegte Rechnung im Bereich Vermietung mit erkanntem Betrag und Mietobjekt wird sofort als
# Aufwand erfasst (Status „bestätigt“, Herkunft „auto“ = automatisch ausgelesen). Eine Änderung durch
# Sie macht daraus „geprüft“ (Herkunft „user“). Es findet keine steuerliche Bewertung statt.

_YEARLY_TAX = {"grundsteuer", "grundbesitzabgaben"}

_DOC_SELECT = (
    "SELECT d.id, d.title, d.doc_type, d.doc_date, d.created_at, d.property_id, d.unit_id, d.source_path, d.original_name, d.status, "
    "d.deleted_at, d.no_expense, a.key AS area_key, p.name AS property_name, c.name AS correspondent_name, t.content "
    "FROM documents d LEFT JOIN areas a ON a.id = d.area_id LEFT JOIN properties p ON p.id = d.property_id "
    "LEFT JOIN correspondents c ON c.id = d.correspondent_id LEFT JOIN document_text t ON t.document_id = d.id"
)


def _proposal(conn: sqlite3.Connection, d: dict[str, Any]) -> dict[str, Any] | None:
    """Ausgabe-Vorschlag für ein Dokument (Rechnung/Beleg oder Grundsteuer-/Abgabenbescheid), sonst None."""
    text = d.get("content") or ""
    payload = _latest_payload(conn, d["id"])
    inv = payload.get("invoice") or {}
    doc_type = d["doc_type"] or payload.get("doc_type") or extract.detect_doc_type(text)
    if doc_type not in INVOICE_TYPES and doc_type != "Bescheid":
        return None
    # Datum nur aus dem erkannten Text – nie aus Titel oder Dateiname; fehlt es, fragt die App nach.
    doc_date = d["doc_date"] or payload.get("doc_date") or extract.detect_date(text)[0]
    f = fields(text, doc_type, doc_date)
    f.update({k: v for k, v in inv.items() if v})  # Vorschlag aus der Einordnung (ggf. KI) hat Vorrang
    if doc_type == "Bescheid" and f.get("expense_category") not in _YEARLY_TAX:
        return None
    amount = f.get("amount_cents") or payload.get("amount_cents")
    return {
        "doc_type": doc_type, "date": doc_date, "amount_cents": int(amount) if amount else None,
        "category_key": f.get("expense_category"), "paid_on": f.get("paid_on"),
        "payee": (d.get("correspondent_name") or payload.get("correspondent_name") or f.get("payee") or "")[:120],
        "note": f"Rechnung Nr. {f['number']}" if f.get("number") else "",
        "reason": f.get("expense_reason"), "amount_evidence": f.get("amount_evidence"),
    }


def _insert(conn: sqlite3.Connection, d: dict[str, Any], prop: dict[str, Any], actor: str, status: str = "bestaetigt",
            property_id: int | None | str = "doc") -> int:
    ts = now()
    # Zahlungsdatum: aus dem Beleg („bezahlt am …“, Barzahlung), sonst Rechnungsdatum als Annahme
    paid_on, assumed = prop["paid_on"], 0
    if not paid_on and prop["date"]:
        paid_on, assumed = prop["date"], 1
    eid = conn.execute(
        "INSERT INTO expenses(document_id, property_id, unit_id, invoice_date, paid_on, paid_assumed, amount_cents, payee, "
        "category_key, note, status, origin, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (d["id"], d["property_id"] if property_id == "doc" else property_id, d["unit_id"] if property_id == "doc" else None,
         prop["date"], paid_on, assumed, prop["amount_cents"], prop["payee"],
         prop["category_key"] or "sonstige", prop["note"], status, "auto", ts, ts)).lastrowid
    audit(conn, "expense_added", "document", d["id"],
          {"expense_id": eid, "betrag": f"{prop['amount_cents'] / 100:.2f}".replace(".", ",") + " €",
           "automatisch_ausgelesen": True, "beleg": prop.get("amount_evidence")}, actor=actor)
    return eid


def ensure_expense(conn: sqlite3.Connection, document_id: int, actor: str = "system") -> int | None:
    """Erfasst eine abgelegte Rechnung im Bereich Vermietung automatisch als Aufwand.

    Voraussetzungen: Mietobjekt steht fest, Betrag erkannt, noch keine Ausgabe am Dokument,
    nicht als „keine Ausgabe“ markiert.
    """
    d = row(conn.execute(_DOC_SELECT + " WHERE d.id = ?", (document_id,)))
    if not d or d["deleted_at"] or d["status"] != "filed":
        return None
    if d["area_key"] != "vermietung" or d["no_expense"]:
        # privat abgelegt oder „keine Ausgabe“: beim Upload vorläufig erfasster Betrag entfällt
        conn.execute("DELETE FROM expenses WHERE document_id = ? AND origin = 'auto'", (document_id,))
        return None
    pending = row(conn.execute("SELECT id FROM expenses WHERE document_id = ? AND origin = 'auto' AND status = 'entwurf' "
                               "ORDER BY id LIMIT 1", (document_id,)))
    if pending and d["property_id"]:
        # vorläufig erfasster Betrag: jetzt Objekt übernehmen und zählen
        conn.execute("UPDATE expenses SET property_id = ?, unit_id = COALESCE(?, unit_id), status = 'bestaetigt', updated_at = ? "
                     "WHERE id = ?", (d["property_id"], d["unit_id"], now(), pending["id"]))
        audit(conn, "expense_assigned", "document", document_id, {"expense_id": pending["id"]}, actor=actor)
        return pending["id"]
    if not d["property_id"]:
        return None
    if conn.execute("SELECT 1 FROM expenses WHERE document_id = ? LIMIT 1", (document_id,)).fetchone():
        return None
    prop = _proposal(conn, d)
    if not prop or not prop["amount_cents"]:
        return None
    return _insert(conn, d, prop, actor)


def capture_on_upload(conn: sqlite3.Connection, document_id: int, payload: dict[str, Any]) -> int | None:
    """Sofort beim Upload: erkannter Rechnungsbetrag wird als Ausgabe erfasst – auch wenn der Beleg noch im
    Eingang liegt (z. B. Objekt unklar). Bis zur Zuordnung ist die Position ein Entwurf; mit der Ablage im
    Bereich Vermietung zählt sie, bei „Privat“ entfällt sie.
    """
    d = row(conn.execute(_DOC_SELECT + " WHERE d.id = ?", (document_id,)))
    if not d or d["deleted_at"] or d["no_expense"]:
        return None
    if conn.execute("SELECT 1 FROM expenses WHERE document_id = ? LIMIT 1", (document_id,)).fetchone():
        return None
    area = d["area_key"]
    if not area and payload.get("area_id"):
        r = conn.execute("SELECT key FROM areas WHERE id = ?", (payload["area_id"],)).fetchone()
        area = r[0] if r else None
    upload_area = conn.execute("SELECT upload_area FROM documents WHERE id = ?", (document_id,)).fetchone()[0]
    if area == "privat" or (area is None and upload_area == "privat"):
        return None
    if area != "vermietung" and upload_area != "vermietung" and not payload.get("property_id"):
        return None  # weder Bereich noch Objekt deuten auf Vermietung
    prop = _proposal(conn, d)
    if not prop or not prop["amount_cents"]:
        return None
    return _insert(conn, d, prop, "system", status="entwurf", property_id=payload.get("property_id"))


# frühere Bezeichnung (Aufrufer aus älteren Teilen)
ensure_draft_expense = ensure_expense


def missing_expenses(conn: sqlite3.Connection, year: int | None = None) -> list[dict[str, Any]]:
    """Rechnungen im Bereich Vermietung ohne Ausgabe – z. B. kein Betrag erkannt oder kein Objekt zugeordnet."""
    docs = rows(conn.execute(
        _DOC_SELECT + " WHERE a.key = 'vermietung' AND d.deleted_at IS NULL AND d.status = 'filed' AND d.no_expense = 0 "
        "AND NOT EXISTS (SELECT 1 FROM expenses e WHERE e.document_id = d.id) ORDER BY COALESCE(d.doc_date, d.created_at) DESC"))
    out = []
    for d in docs:
        prop = _proposal(conn, d)
        d.pop("content", None)
        if not prop:
            continue
        # ohne Datum: in jedem Jahr anzeigen (muss ergänzt werden), nie stillschweigend dem Hochladejahr zuordnen
        if year and prop["date"] and prop["date"][:4] != str(year):
            continue
        out.append(d | prop)
    return out


def capture_all(conn: sqlite3.Connection, year: int | None = None, actor: str = "system") -> dict[str, int]:
    """Erfasst alle passenden Rechnungen ohne Ausgabe (mit Betrag und Objekt) automatisch als Aufwand."""
    made = skipped = 0
    for m in missing_expenses(conn, year):
        if not m["amount_cents"] or not m["property_id"]:
            skipped += 1
            continue
        _insert(conn, m, m, actor)
        made += 1
    return {"created": made, "skipped": skipped}


create_drafts = capture_all


def redetect_dates(conn: sqlite3.Connection) -> dict[str, int]:
    """Datum der Belege neu aus dem Text bestimmen (verbesserte Erkennung) und Datumsangaben, die nur aus dem
    Dateinamen stammten, wieder entfernen. Betrifft nur automatisch erfasste Positionen und leere Dokumentdaten."""
    set_doc = set_exp = cleared = 0
    for d in rows(conn.execute(_DOC_SELECT + " WHERE d.deleted_at IS NULL")):
        text_date = extract.detect_date(d.get("content") or "")[0]
        name_date = extract.date_from_name(d.get("original_name"), d.get("source_path"))
        if text_date and not d["doc_date"]:
            conn.execute("UPDATE documents SET doc_date = ? WHERE id = ?", (text_date, d["id"]))
            set_doc += 1
        for e in rows(conn.execute("SELECT id, invoice_date FROM expenses WHERE document_id = ? AND origin = 'auto'", (d["id"],))):
            if text_date and e["invoice_date"] != text_date and (e["invoice_date"] is None or e["invoice_date"] == name_date):
                conn.execute("UPDATE expenses SET invoice_date = ?, updated_at = ? WHERE id = ?", (text_date, now(), e["id"]))
                set_exp += 1
            elif not text_date and name_date and e["invoice_date"] == name_date:
                conn.execute("UPDATE expenses SET invoice_date = NULL, updated_at = ? WHERE id = ?", (now(), e["id"]))
                cleared += 1
    return {"dokumente": set_doc, "positionen": set_exp, "zurueckgesetzt": cleared}


def undated(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Positionen ohne Datum (für die Rückfrage im Eingang)."""
    return rows(conn.execute(
        "SELECT e.id AS expense_id, e.amount_cents, e.payee, e.document_id, p.name AS property_name FROM expenses e "
        "LEFT JOIN documents d ON d.id = e.document_id LEFT JOIN properties p ON p.id = e.property_id "
        "WHERE e.paid_on IS NULL AND e.invoice_date IS NULL AND d.doc_date IS NULL AND d.deleted_at IS NULL ORDER BY e.id"))


def assume_payment_dates(conn: sqlite3.Connection) -> int:
    """Einmalig für vorhandene Positionen: fehlendes Zahlungsdatum = Rechnungsdatum (als „angenommen“ markiert)."""
    cur = conn.execute(
        "UPDATE expenses SET paid_on = COALESCE(invoice_date, (SELECT doc_date FROM documents d WHERE d.id = expenses.document_id)), "
        "paid_assumed = 1, updated_at = ? WHERE paid_on IS NULL AND origin != 'bank' "
        "AND COALESCE(invoice_date, (SELECT doc_date FROM documents d WHERE d.id = expenses.document_id)) IS NOT NULL", (now(),))
    return cur.rowcount


def extract_for_document(conn: sqlite3.Connection, document_id: int, use_ai: bool = True) -> dict[str, Any]:
    """„Aus Beleg auslesen“: Positionsdaten aus dem erkannten Text, mit Herkunft je Feld.

    Betrag: nur ein beschrifteter Endbetrag wird übernommen. Ist keiner eindeutig erkennbar, liefert die Antwort
    alle Beträge des Belegs („candidates“) zur Auswahl; ein KI-Vorschlag (falls eingerichtet, nur wenn er im Text
    steht) wird lediglich hervorgehoben („suggested_cents“), nie automatisch eingesetzt.
    """
    d = row(conn.execute(_DOC_SELECT + " WHERE d.id = ?", (document_id,)))
    if not d:
        raise KeyError(document_id)
    text = d.get("content") or ""
    if len(text.strip()) < 10:
        return {"text_available": False}
    doc_type = d["doc_type"] or extract.detect_doc_type(text) or "Rechnung"
    date_, date_ev = extract.detect_date(text)
    f = fields(text, doc_type, date_)
    out: dict[str, Any] = {
        "text_available": True, "doc_type": doc_type,
        "amount_cents": f.get("amount_cents"), "amount_source": "label" if f.get("amount_cents") else None,
        "amount_evidence": f.get("amount_evidence"),
        "invoice_date": date_, "date_evidence": date_ev or None,
        "paid_on": f.get("paid_on") or date_, "paid_assumed": not f.get("paid_on") and bool(date_),
        "payee": d.get("correspondent_name") or f.get("payee") or "",
        "category_key": f.get("expense_category"), "category_reason": f.get("expense_reason"),
        "note": f"Rechnung Nr. {f['number']}" if f.get("number") else "",
    }
    out["candidates"] = extract.amount_candidates(text)
    # Nur ein beschrifteter Endbetrag gilt als eindeutig. Sonst: Auswahl durch Sie (KI-Vorschlag nur hervorgehoben).
    out["needs_choice"] = not out["amount_cents"] and bool(out["candidates"])
    if (not out["amount_cents"] or not out["invoice_date"]) and use_ai:
        ai = _ai_extract(conn, text)
        if ai:
            if not out["amount_cents"] and ai.get("amount_cents"):
                out["suggested_cents"] = ai["amount_cents"]
            if not out["invoice_date"] and ai.get("doc_date"):
                out.update(invoice_date=ai["doc_date"], date_evidence="Vorschlag der KI")
                if not f.get("paid_on"):
                    out.update(paid_on=ai["doc_date"], paid_assumed=True)
            out["payee"] = out["payee"] or ai.get("correspondent") or ""
            out["category_key"] = out["category_key"] or ai.get("expense_category")
    return out


def _ai_extract(conn: sqlite3.Connection, text: str) -> dict[str, Any] | None:
    """Optional: KI (Mistral) liest Betrag/Datum, wenn lokal nichts gefunden wurde. Der Betrag zählt nur, wenn er
    so im Text vorkommt – erfundene Zahlen werden verworfen."""
    from . import config, mistral
    from .db import get_setting
    from .seed import EXPENSE_KEYS
    if not (bool(get_setting(conn, "ai_enabled", True)) and config.settings.ai_available):
        return None
    try:
        res = mistral.extract_invoice(text)
    except mistral.AIError:
        return None
    out: dict[str, Any] = {}
    amt = res.get("amount_cents")
    if isinstance(amt, (int, float)) and 0 < amt < 10_000_000_00:
        amt = int(amt)
        de = f"{amt // 100:,}".replace(",", ".") + f",{amt % 100:02d}"
        plain = f"{amt // 100},{amt % 100:02d}"
        if de in text or plain in text or f"{amt // 100}.{amt % 100:02d}" in text:
            out["amount_cents"] = amt
            out["amount_evidence"] = "KI, Betrag im Beleg gefunden"
    if isinstance(res.get("doc_date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", res["doc_date"]):
        out["doc_date"] = res["doc_date"]
    if isinstance(res.get("correspondent"), str):
        out["correspondent"] = res["correspondent"].strip()[:120]
    if res.get("expense_category") in EXPENSE_KEYS:
        out["expense_category"] = res["expense_category"]
    return out
