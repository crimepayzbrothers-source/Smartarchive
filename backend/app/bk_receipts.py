"""Belegprüfung für die Betriebskostenabrechnung: Archivbelege des Objekts per Volltext auswerten.

Ein Abgabenbescheid (Grundbesitzabgaben) enthält mehrere Betriebskostenarten in einem Dokument – Grundsteuer,
Abfall, Schmutz- und Niederschlagswasser, Straßenreinigung, ggf. Wasser. Als eine Ausgabe übernommen, landete er
bisher mit dem Gesamtbetrag in einer einzigen Kostenart. Hier wird er zeilenweise in Positionen zerlegt.

Grundsätze:
- Nur Vorschläge: Jede Position zeigt die Zeile aus dem Beleg, aus der sie stammt (Fundstelle). Übernommen wird
  erst nach Auswahl; übernommene Positionen bleiben mit dem Originalbeleg verknüpft und gelten bis zur Prüfung
  als „ungeprüft“.
- Keine geschätzten Beträge: Ein Betrag stammt immer wörtlich aus einer Belegzeile. Ohne erkennbaren Betrag
  bleibt die Position leer und muss eingetragen werden.
- Sicherheit „hoch“ nur, wenn die Summe der Positionen dem im Bescheid genannten Gesamtbetrag entspricht.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import betriebskosten as bk
from . import invoices
from .db import row, rows
from .extract import _AMOUNT, parse_amount

# Reihenfolge zählt: spezifischere Begriffe zuerst (Abwasser vor Wasser)
TYPE_PATTERNS: list[tuple[str, str]] = [
    ("nicht_umlagefaehig", r"wiederkehrende?[rn]?\s+beitr|ausbaubeitr|erschlie(ß|ss)ungsbeitr|verkehrsanlage"),
    ("grundsteuer", r"grundsteuer"),
    ("entwaesserung", r"niederschlags|oberfl(ä|ae)chenwasser|regenwasser|schmutzwasser|abwasser|kanalbenutzung|entw(ä|ae)sserung"),
    ("strassenreinigung", r"stra(ß|ss)enreinigung|winterdienst"),
    ("muell", r"abfall|m(ü|ue)ll|biotonne|bioabfall|restm|wertstoff|altpapier"),
    ("wasser", r"trinkwasser|wasserversorgung|wasserbezug|wassergeld|wasserverbrauch|frischwasser|wasserz(ä|ae)hler|z(ä|ae)hlermiete"),
    ("schornstein", r"schornstein|kehr(gebühr|arbeiten|ung)|feuerst(ä|ae)ttenschau|abgaswegeüberprüfung"),
    ("versicherung", r"geb(ä|ae)udeversicherung|wohngeb(ä|ae)ude|haftpflichtversicherung"),
]
IGNORE_LINE = re.compile(
    r"f(ä|ae)llig|vorjahr|bereits\s+(gezahlt|entrichtet|geleistet)|abschl(a|ä)g|guthaben|erstattung|r(ü|ue)ckstand|s(ä|ae)umnis|mahn|"
    r"zinsen|iban|bic|kassenzeichen|bankverbindung|hundesteuer|zweitwohnung|abbuchung|lastschrift|ratenzahlung", re.I)
TOTAL_LINE = re.compile(r"gesamtbetrag|insgesamt|summe|festgesetzt|zu\s+zahlen|jahresbetrag\s+gesamt|gesamtabgaben", re.I)
BESCHEID = re.compile(r"bescheid", re.I)
PERMANENT = re.compile(r"folgejahre|bis\s+zur\s+bekanntgabe\s+eines\s+neuen|dauerbescheid", re.I)


def _amounts(line: str) -> list[int]:
    return [parse_amount(m.group(1), m.group(2)) for m in _AMOUNT.finditer(line)]


def _line_amount(line: str) -> tuple[int | None, str]:
    """Betrag einer Zeile: hinter dem letzten „=“, sonst der letzte Betrag. Vier gleiche Beträge = Vierteljahresraten."""
    if "=" in line:
        after = _amounts(line.rsplit("=", 1)[1])
        if after:
            return after[0], ""
    am = _amounts(line)
    if not am:
        return None, ""
    if len(am) >= 4 and len(set(am[-4:])) == 1:
        return sum(am[-4:]), "Summe aus vier gleichen Teilbeträgen (Vierteljahresraten) – bitte prüfen"
    return am[-1], ""


def cost_type_of(line: str) -> str | None:
    low = line.lower()
    for key, pat in TYPE_PATTERNS:
        if re.search(pat, low):
            return key
    return None


def parse_bescheid(text: str) -> dict[str, Any]:
    """Positionen eines Abgabenbescheids aus dem Volltext."""
    lines = [l.strip() for l in (text or "").splitlines()]
    positions: list[dict[str, Any]] = []
    totals: list[int] = []
    for i, line in enumerate(lines):
        if not line:
            continue
        if TOTAL_LINE.search(line) and not cost_type_of(line):
            am = _amounts(line) or (_amounts(lines[i + 1]) if i + 1 < len(lines) and not cost_type_of(lines[i + 1]) else [])
            if am:
                totals.append(max(am))
            continue
        ct = cost_type_of(line)
        if not ct or IGNORE_LINE.search(line):
            continue
        amount, note = _line_amount(line)
        evidence = line
        if amount is None and i + 1 < len(lines) and not cost_type_of(lines[i + 1]) and not IGNORE_LINE.search(lines[i + 1]):
            amount, note = _line_amount(lines[i + 1])
            evidence = f"{line} {lines[i + 1]}".strip()
        if amount is None or amount <= 0:
            continue
        if any(p["cost_type"] == ct and p["amount_cents"] == amount for p in positions):
            continue  # gleiche Angabe doppelt (z. B. Überschrift und Tabellenzeile)
        positions.append({"cost_type": ct, "amount_cents": amount, "evidence": evidence[:300], "line": i + 1, "note": note})
    s = sum(p["amount_cents"] for p in positions)
    match = next((t for t in totals if t == s), None)
    for p in positions:
        p["confidence"] = "hoch" if match and not p["note"] else "pruefen"
    return {"positions": positions, "sum": s, "total": match or (max(totals) if totals else None), "total_matches": bool(match)}


def _relevant(doc: dict[str, Any], start: str, end: str) -> tuple[bool, str]:
    text = doc.get("content") or ""
    y = start[:4]
    if doc["doc_date"] and start <= doc["doc_date"] <= end:
        return True, "Belegdatum im Abrechnungszeitraum"
    if re.search(rf"(kalender|veranlagungs|erhebungs|abrechnungs)?jahr\s*{y}|f(ü|ue)r\s+(das\s+jahr\s+)?{y}|ab\s+(dem\s+)?0?1\.0?1\.{y}|"
                 rf"01\.01\.{y}\s*(bis|-|–)", text, re.I):
        return True, f"Bescheid nennt das Jahr {y}"
    if doc["doc_date"] and doc["doc_date"][:4] < y and PERMANENT.search(text):
        return True, f"Dauerbescheid vom {doc['doc_date'][8:10]}.{doc['doc_date'][5:7]}.{doc['doc_date'][:4]} (gilt für Folgejahre) – prüfen, ob noch aktuell"
    return False, ""


def scan(conn: sqlite3.Connection, period_id: int) -> list[dict[str, Any]]:
    """Belege des Objekts für den Zeitraum, die noch nicht in der Abrechnung sind, mit Positionsvorschlägen."""
    p = bk.load_period(conn, period_id)
    linked = {r[0] for r in conn.execute("SELECT document_id FROM bk_costs WHERE period_id = ? AND document_id IS NOT NULL", (period_id,))}
    docs = rows(conn.execute(
        "SELECT d.id, d.title, d.doc_type, d.doc_date, d.created_at, c.name AS correspondent, t.content, "
        "(SELECT COUNT(*) FROM expenses e WHERE e.document_id = d.id) AS expenses "
        "FROM documents d JOIN areas a ON a.id = d.area_id LEFT JOIN correspondents c ON c.id = d.correspondent_id "
        "LEFT JOIN document_text t ON t.document_id = d.id "
        "WHERE a.key = 'vermietung' AND d.property_id = ? AND d.status = 'filed' AND d.deleted_at IS NULL AND d.tenancy_id IS NULL AND COALESCE(d.doc_type, '') != 'Betriebskostenabrechnung' "
        "ORDER BY COALESCE(d.doc_date, d.created_at)", (p["property_id"],)))
    out = []
    for doc in docs:
        if doc["id"] in linked:
            continue
        text = doc["content"] or ""
        is_bescheid = (doc["doc_type"] == "Bescheid") or bool(BESCHEID.search(f"{doc['title']} {text[:600]}"))
        ok, why = _relevant(doc, p["start"], p["end"])
        if not ok:
            continue
        if is_bescheid:
            parsed = parse_bescheid(text)
            if not parsed["positions"]:
                if not re.search("|".join(pat for _, pat in TYPE_PATTERNS), text, re.I):
                    continue
                parsed["positions"] = [{"cost_type": cost_type_of(text) or "sonstige", "amount_cents": None, "line": None, "note": "",
                                        "evidence": "Kein Betrag zeilenweise erkannt – bitte aus dem Beleg eintragen.", "confidence": "pruefen"}]
            kind = "bescheid"
        else:
            if doc["expenses"]:
                continue  # Rechnung ist als Ausgabe erfasst → „Aus Ausgaben übernehmen“
            prop = invoices._proposal(conn, {**doc, "content": text}) or {}
            ct, reason = bk.suggest_type(prop.get("category_key"), f"{doc['correspondent'] or ''} {doc['title']} {text[:2000]}")
            if ct == "nicht_umlagefaehig" and not prop.get("amount_cents"):
                continue  # weder Betrag noch Betriebskostenart erkennbar
            parsed = {"positions": [{"cost_type": ct, "amount_cents": prop.get("amount_cents"), "line": None, "note": reason,
                                     "evidence": prop.get("amount_evidence") or "Betrag nicht erkannt – bitte aus dem Beleg eintragen.",
                                     "confidence": "pruefen"}], "sum": prop.get("amount_cents") or 0, "total": None, "total_matches": False}
            kind = "rechnung"
        out.append({"document_id": doc["id"], "title": doc["title"], "doc_date": doc["doc_date"], "correspondent": doc["correspondent"],
                    "kind": kind, "why": why, **parsed})
    return out


def import_positions(conn: sqlite3.Connection, period_id: int, items: list[dict[str, Any]]) -> int:
    """Ausgewählte Positionen übernehmen – jede mit Verknüpfung zum Originalbeleg und Fundstelle, zunächst ungeprüft."""
    available = {d["document_id"]: d for d in scan(conn, period_id)}
    n = 0
    for it in items:
        doc = available.get(it.get("document_id"))
        if not doc:
            raise bk.BKError("Der Beleg ist nicht mehr verfügbar oder schon übernommen. Bitte die Belegprüfung neu laden.")
        amount = it.get("amount_cents")
        if not isinstance(amount, int) or amount == 0:
            raise bk.BKError(f"„{doc['title']}“: Bitte für jede ausgewählte Position einen Betrag angeben.")
        evidence = (it.get("evidence") or "").strip()[:300]
        label = bk.COST_TYPES.get(it.get("cost_type"), {}).get("label", "")
        cid = bk.add_cost(conn, period_id, {"cost_type": it.get("cost_type"), "amount_cents": amount, "document_id": doc["document_id"],
                                            "invoice_date": doc["doc_date"],
                                            "description": " – ".join(x for x in (doc["correspondent"] or doc["title"], label) if x)[:300]})
        conn.execute("UPDATE bk_costs SET origin = 'beleg', evidence = ?, verified_at = NULL WHERE id = ?", (evidence, cid))
        n += 1
    return n


def receipt_status(conn: sqlite3.Connection, period_id: int) -> dict[str, Any]:
    """Kurzübersicht für die Prüfung: Positionen ohne Beleg, ungeprüfte automatische Positionen, offene Belege."""
    cs = rows(conn.execute("SELECT id, document_id, origin, verified_at FROM bk_costs WHERE period_id = ?", (period_id,)))
    return {"without_document": sum(1 for c in cs if not c["document_id"]),
            "unverified": sum(1 for c in cs if c["origin"] != "manuell" and not c["verified_at"]),
            "positions": len(cs)}
