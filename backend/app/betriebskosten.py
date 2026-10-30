"""Betriebskostenabrechnung (Wohnraum): Kosten erfassen, verteilen, prüfen, freigeben.

Rechtsgrundlagen, an denen sich die Logik orientiert (Stand der Prüfung: 30.10.2026, siehe docs/BETRIEBSKOSTEN.md):
- § 556, § 556a, § 259 BGB; Betriebskostenverordnung (BetrKV) § 2 Nr. 1–17; Heizkostenverordnung (HeizkostenV);
  Kohlendioxidkostenaufteilungsgesetz (CO2KostAufG).
- Formelle Mindestangaben (BGH VIII ZR 244/18): Gesamtkosten je Kostenart, Umlageschlüssel (ggf. erläutert),
  Berechnung des Mieteranteils, Abzug der geleisteten Vorauszahlungen.
- Leerstand (BGH VIII ZR 159/05): Anteile, die nach dem Schlüssel auf leere Wohnungen entfallen, trägt der Vermieter.

Die App rechnet und prüft. Sie ersetzt keine rechtliche Prüfung im Einzelfall; Unsicherheiten werden als Hinweis ausgegeben.

Rechenweg
- Gerechnet wird mit exakten Brüchen. Erst am Ende wird je Kostenart auf Cent gerundet, und zwar so, dass die Summe
  der Anteile genau den Gesamtkosten entspricht (Methode des größten Rests).
- Zeitanteile werden taggenau gezählt (Monatslängen, Schaltjahre), Beginn und Ende einschließlich.
- Heiz- und Warmwasserkosten werden nicht nach Tagen verteilt, sondern aus der Abrechnung der Ablesefirma übernommen
  (Verteilung nach §§ 7–9b HeizkostenV einschließlich Zwischenablesung beim Mieterwechsel liegt dort).
"""
from __future__ import annotations

import json
import re
import sqlite3
from calendar import monthrange
from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from . import tenancy as tn
from .db import audit, get_setting, now, row, rows

# --- Stammdaten ----------------------------------------------------------------------

COST_TYPES: dict[str, dict[str, Any]] = {
    "grundsteuer": {"label": "Grundsteuer (laufende öffentliche Lasten)", "ref": "§ 2 Nr. 1 BetrKV", "key": "flaeche"},
    "wasser": {"label": "Wasserversorgung", "ref": "§ 2 Nr. 2 BetrKV", "key": "flaeche"},
    "entwaesserung": {"label": "Entwässerung (Schmutz- und Niederschlagswasser)", "ref": "§ 2 Nr. 3 BetrKV", "key": "flaeche"},
    "heizung": {"label": "Heizung", "ref": "§ 2 Nr. 4 BetrKV, HeizkostenV", "key": "extern", "heat": True},
    "warmwasser": {"label": "Warmwasserversorgung", "ref": "§ 2 Nr. 5 BetrKV, HeizkostenV", "key": "extern", "heat": True},
    "heizung_warmwasser": {"label": "Verbundene Heizungs- und Warmwasseranlage", "ref": "§ 2 Nr. 6 BetrKV, HeizkostenV",
                           "key": "extern", "heat": True},
    "aufzug": {"label": "Aufzug", "ref": "§ 2 Nr. 7 BetrKV", "key": "flaeche"},
    "strassenreinigung": {"label": "Straßenreinigung", "ref": "§ 2 Nr. 8 BetrKV", "key": "flaeche"},
    "muell": {"label": "Müllbeseitigung", "ref": "§ 2 Nr. 8 BetrKV", "key": "flaeche"},
    "gebaeudereinigung": {"label": "Gebäudereinigung und Ungezieferbekämpfung", "ref": "§ 2 Nr. 9 BetrKV", "key": "flaeche"},
    "gartenpflege": {"label": "Gartenpflege", "ref": "§ 2 Nr. 10 BetrKV", "key": "flaeche"},
    "beleuchtung": {"label": "Beleuchtung (Allgemeinstrom)", "ref": "§ 2 Nr. 11 BetrKV", "key": "flaeche"},
    "schornstein": {"label": "Schornsteinreinigung", "ref": "§ 2 Nr. 12 BetrKV", "key": "flaeche"},
    "versicherung": {"label": "Sach- und Haftpflichtversicherung", "ref": "§ 2 Nr. 13 BetrKV", "key": "flaeche"},
    "hauswart": {"label": "Hauswart", "ref": "§ 2 Nr. 14 BetrKV", "key": "flaeche"},
    "antenne_glasfaser": {"label": "Gemeinschaftsantenne, Breitbandnetz, Glasfaser", "ref": "§ 2 Nr. 15 BetrKV", "key": "einheiten"},
    "waeschepflege": {"label": "Einrichtungen für die Wäschepflege", "ref": "§ 2 Nr. 16 BetrKV", "key": "flaeche"},
    "sonstige": {"label": "Sonstige Betriebskosten", "ref": "§ 2 Nr. 17 BetrKV", "key": "flaeche"},
    "nicht_umlagefaehig": {"label": "Nicht umlagefähig (trägt der Vermieter)", "ref": "", "key": None, "excluded": True},
}
HEAT_TYPES = {k for k, v in COST_TYPES.items() if v.get("heat")}

KEYS: dict[str, dict[str, str]] = {
    "flaeche": {"label": "Wohnfläche", "why": "Gesetzlicher Regelmaßstab, wenn nichts anderes vereinbart ist (§ 556a Abs. 1 S. 1 BGB)."},
    "personen": {"label": "Personentage", "why": "Verteilung nach Personenzahl und Nutzungsdauer (muss vereinbart sein)."},
    "einheiten": {"label": "Wohneinheiten", "why": "Gleicher Anteil je Wohnung, z. B. bei Kosten, die je Anschluss anfallen."},
    "verbrauch": {"label": "Erfasster Verbrauch", "why": "Kosten, die von einem erfassten Verbrauch abhängen, nach Verbrauch (§ 556a Abs. 1 S. 2 BGB)."},
    "direkt": {"label": "Direkt zugeordnet", "why": "Die Kosten betreffen nur eine Wohnung (z. B. eigener Zähler, eigene Wartung)."},
    "extern": {"label": "Abrechnung der Ablesefirma", "why": "Verteilung nach HeizkostenV (Grund- und Verbrauchskosten, Zwischenablesung) durch den Messdienst."},
    "individuell": {"label": "Vereinbarter Schlüssel", "why": "Individuell vereinbarter Verteilungsmaßstab (z. B. Miteigentumsanteile)."},
}

# Ausgabenkategorien der Steuer-Erfassung, die keine umlagefähigen Betriebskosten sind
NOT_ALLOCABLE_CATEGORIES = {"schuldzinsen", "geldbeschaffung", "erhaltung_sofort", "erhaltung_verteilt", "verwaltung",
                            "fahrtkosten", "nicht_sofort", "hausgeld_laufend"}


class BKError(ValueError):
    pass


def d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def de_num(x: float | Fraction, digits: int = 2) -> str:
    return f"{float(x):,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def euro(cents: int) -> str:
    return de_num(Fraction(cents, 100)) + " €"


# --- Vorschlag der Kostenart für übernommene Ausgaben ------------------------------

_TYPE_WORDS = [
    ("grundsteuer", r"grundsteuer"),
    ("strassenreinigung", r"stra(ß|ss)enreinigung|winterdienst"),
    ("muell", r"m(ü|ue)ll|abfall|wertstoff|biotonne|restm"),
    ("entwaesserung", r"abwasser|schmutzwasser|niederschlag|entw(ä|ae)sserung|kanal"),
    ("wasser", r"wasser"),
    ("schornstein", r"schornstein|kaminkehr|feuerst(ä|ae)ttenschau"),
    ("beleuchtung", r"allgemeinstrom|hausstrom|treppenhaus.*strom|beleuchtung"),
    ("hauswart", r"hausmeister|hauswart"),
    ("gebaeudereinigung", r"treppenhausreinigung|geb(ä|ae)udereinigung|reinigung|ungeziefer|sch(ä|ae)dlings"),
    ("gartenpflege", r"garten|gr(ü|ue)npflege|baumschnitt|rasen"),
    ("versicherung", r"versicherung|haftpflicht|geb(ä|ae)udeversicherung|wohngeb(ä|ae)ude"),
    ("heizung", r"heizkosten|heizung|brennstoff|heiz(ö|oe)l|erdgas|fernw(ä|ae)rme|ista|techem|brunata|minol|kalo"),
    ("aufzug", r"aufzug|fahrstuhl"),
    ("antenne_glasfaser", r"glasfaser|antenne|breitband"),
]


def suggest_type(category_key: str | None, text: str) -> tuple[str, str]:
    """Kostenart für eine übernommene Ausgabe. Nicht umlagefähige Kategorien bleiben nicht umlagefähig."""
    if category_key in NOT_ALLOCABLE_CATEGORIES:
        return "nicht_umlagefaehig", "Ausgabenkategorie ist keine umlagefähige Betriebskostenart."
    low = (text or "").lower()
    if category_key == "grundsteuer":
        return "grundsteuer", "aus Ausgabenkategorie Grundsteuer"
    if category_key == "versicherung":
        return "versicherung", "aus Ausgabenkategorie Versicherung"
    if category_key == "heizung":
        return "heizung", "aus Ausgabenkategorie Heizung"
    for key, pattern in _TYPE_WORDS:
        if re.search(pattern, low):
            return key, f"Stichwort im Beleg ({COST_TYPES[key]['label']})"
    return "nicht_umlagefaehig", "Keine Betriebskostenart erkannt – bitte zuordnen oder als nicht umlagefähig lassen."


# --- Zeitraum ------------------------------------------------------------------------

def load_period(conn: sqlite3.Connection, period_id: int) -> dict[str, Any]:
    p = row(conn.execute("SELECT b.*, pr.name AS property_name, pr.address AS property_address, pr.living_area AS property_area "
                         "FROM bk_periods b JOIN properties pr ON pr.id = b.property_id WHERE b.id = ?", (period_id,)))
    if not p:
        raise KeyError(period_id)
    p["co2"] = json.loads(p["co2"] or "{}")
    p["acknowledged"] = json.loads(p["acknowledged"] or "[]")
    return p


def create_period(conn: sqlite3.Connection, property_id: int, start: str, end: str) -> int:
    s, e = d(start), d(end)
    if e < s:
        raise BKError("Das Ende liegt vor dem Beginn.")
    if conn.execute("SELECT 1 FROM bk_periods WHERE property_id = ? AND start <= ? AND end >= ?", (property_id, end, start)).fetchone():
        raise BKError("Für dieses Objekt gibt es schon einen Abrechnungszeitraum, der sich damit überschneidet.")
    ts = now()
    pid = conn.execute("INSERT INTO bk_periods(property_id, start, end, created_at, updated_at) VALUES(?,?,?,?,?)",
                       (property_id, start, end, ts, ts)).lastrowid
    # Schlüssel vom Vorjahr übernehmen, sonst Standard je Kostenart
    prev = row(conn.execute("SELECT id FROM bk_periods WHERE property_id = ? AND end < ? ORDER BY end DESC LIMIT 1", (property_id, start)))
    if prev:
        conn.execute("INSERT INTO bk_keys(period_id, cost_type, key, reason) SELECT ?, cost_type, key, reason FROM bk_keys WHERE period_id = ?",
                     (pid, prev["id"]))
        conn.execute("UPDATE bk_periods SET vacancy_persons = (SELECT vacancy_persons FROM bk_periods WHERE id = ?) WHERE id = ?", (prev["id"], pid))
    audit(conn, "created", "bk_period", pid, {"property_id": property_id, "start": start, "end": end})
    return pid


def touch(conn: sqlite3.Connection, period_id: int) -> None:
    """Jede Änderung setzt eine freigegebene Abrechnung zurück auf Entwurf (die freigegebene Fassung bleibt gespeichert)."""
    conn.execute("UPDATE bk_periods SET status = 'entwurf', acknowledged = '[]', updated_at = ? WHERE id = ?", (now(), period_id))


def key_for(conn: sqlite3.Connection, period_id: int, cost_type: str) -> tuple[str | None, str]:
    """Schlüssel und eigene Begründung. Ohne eigene Begründung ist sie leer; die Erläuterung des Schlüssels
    (KEYS[...]["why"]) wird erst bei der Berechnung ergänzt, damit sie beim Wechsel des Schlüssels nicht mitwandert."""
    r = row(conn.execute("SELECT key, reason FROM bk_keys WHERE period_id = ? AND cost_type = ?", (period_id, cost_type)))
    if r:
        return r["key"], r["reason"]
    return COST_TYPES[cost_type]["key"], ""


# --- Kosten übernehmen ---------------------------------------------------------------

def importable_expenses(conn: sqlite3.Connection, period_id: int) -> list[dict[str, Any]]:
    """Erfasste Ausgaben des Objekts, im Zeitraum bezahlt (Abflussprinzip), noch nicht übernommen."""
    p = load_period(conn, period_id)
    items = rows(conn.execute(
        "SELECT e.*, d.title AS document_title FROM expenses e LEFT JOIN documents d ON d.id = e.document_id "
        "WHERE e.property_id = ? AND e.status != 'ungeklaert' AND COALESCE(e.paid_on, e.invoice_date) BETWEEN ? AND ? "
        "AND e.id NOT IN (SELECT expense_id FROM bk_costs WHERE period_id = ? AND expense_id IS NOT NULL) "
        "ORDER BY COALESCE(e.paid_on, e.invoice_date)", (p["property_id"], p["start"], p["end"], period_id)))
    for e in items:
        e["suggested_type"], e["suggested_reason"] = suggest_type(e["category_key"], f"{e['payee']} {e['note']} {e['document_title'] or ''}")
    return items


def add_cost(conn: sqlite3.Connection, period_id: int, data: dict[str, Any]) -> int:
    ct = data.get("cost_type")
    if ct not in COST_TYPES:
        raise BKError("Unbekannte Kostenart.")
    amount = data.get("amount_cents")
    if not isinstance(amount, int) or amount == 0:
        raise BKError("Betrag fehlt.")
    p = load_period(conn, period_id)
    if data.get("unit_id") and not conn.execute("SELECT 1 FROM units WHERE id = ? AND property_id = ?",
                                                (data["unit_id"], p["property_id"])).fetchone():
        raise BKError("Die Einheit gehört nicht zu diesem Objekt.")
    source_category, document_id = None, data.get("document_id")
    if data.get("expense_id"):
        e = row(conn.execute("SELECT * FROM expenses WHERE id = ?", (data["expense_id"],)))
        if not e:
            raise BKError("Die Ausgabe existiert nicht.")
        if conn.execute("SELECT 1 FROM bk_costs WHERE period_id = ? AND expense_id = ?", (period_id, e["id"])).fetchone():
            raise BKError("Diese Ausgabe ist schon in der Abrechnung enthalten.")
        source_category, document_id = e["category_key"], document_id or e["document_id"]
    cid = conn.execute(
        "INSERT INTO bk_costs(period_id, cost_type, description, amount_cents, invoice_date, unit_id, document_id, expense_id, "
        "source_category, origin, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (period_id, ct, (data.get("description") or "").strip()[:300], amount, data.get("invoice_date"), data.get("unit_id"),
         document_id, data.get("expense_id"), source_category, "ausgabe" if data.get("expense_id") else "manuell", now())).lastrowid
    touch(conn, period_id)
    audit(conn, "created", "bk_cost", cid, {k: data.get(k) for k in ("cost_type", "amount_cents", "expense_id", "unit_id")})
    return cid


# --- Belegung und Gewichte -----------------------------------------------------------

def _units(conn: sqlite3.Connection, p: dict[str, Any]) -> list[dict[str, Any]]:
    us = rows(conn.execute("SELECT id, name, living_area FROM units WHERE property_id = ? ORDER BY id", (p["property_id"],)))
    return us or [{"id": None, "name": p["property_name"], "living_area": p["property_area"]}]


def _segments(conn: sqlite3.Connection, p: dict[str, Any]) -> list[dict[str, Any]]:
    start, end = d(p["start"]), d(p["end"])
    out = []
    for u in _units(conn, p):
        for s in tn.occupancy(conn, p["property_id"], u["id"], start, end):
            out.append({**s, "unit_id": u["id"], "unit_name": u["name"], "area": u["living_area"]})
    return out


def _person_days(terms: list[dict[str, Any]], s: date, e: date) -> Fraction | None:
    total = Fraction(0)
    day = s
    while day <= e:
        t = tn.term_at(terms, day)
        if not t or t.get("persons") is None:
            return None
        nxt = [d(x["valid_from"]) for x in terms if d(x["valid_from"]) > day]
        stop = min([e] + [n - timedelta(days=1) for n in nxt])
        total += Fraction(t["persons"]) * ((stop - day).days + 1)
        day = stop + timedelta(days=1)
    return total


def target_key(seg: dict[str, Any]) -> str:
    return f"{seg['unit_id'] or 0}:{seg['tenancy_id'] or 0}"


def _allocate(total: int, weights: dict[str, Fraction]) -> dict[str, int]:
    """Verteilt total Cent nach Gewichten. Summe der Ergebnisse == total (größter Rest)."""
    w_sum = sum(weights.values())
    if w_sum <= 0:
        raise BKError("Summe der Verteilungseinheiten ist 0.")
    exact = {k: Fraction(total) * w / w_sum for k, w in weights.items()}
    floor = {k: (v.numerator // v.denominator) for k, v in exact.items()}
    rest = total - sum(floor.values())
    order = sorted(exact, key=lambda k: (-(exact[k] - floor[k]), k))
    for k in order[:rest]:
        floor[k] += 1
    return floor


# --- Berechnung ----------------------------------------------------------------------

def compute(conn: sqlite3.Connection, period_id: int) -> dict[str, Any]:
    """Vollständige Berechnung mit Zwischenschritten. Wirft keine Fehler wegen fehlender Daten,
    sondern sammelt sie als Prüfergebnisse (kritisch/Warnung/Hinweis)."""
    p = load_period(conn, period_id)
    start, end = d(p["start"]), d(p["end"])
    period_days = (end - start).days + 1
    segs = _segments(conn, p)
    tenancy_ids = sorted({s["tenancy_id"] for s in segs if s["tenancy_id"]})
    tenancies = {tid: tn.load(conn, tid) for tid in tenancy_ids}
    costs = rows(conn.execute("SELECT c.*, u.name AS unit_name FROM bk_costs c LEFT JOIN units u ON u.id = c.unit_id "
                              "WHERE c.period_id = ? ORDER BY c.cost_type, c.invoice_date, c.id", (period_id,)))
    values = rows(conn.execute("SELECT * FROM bk_values WHERE period_id = ?", (period_id,)))
    issues: list[dict[str, str]] = []

    def issue(level: str, code: str, text: str) -> None:
        issues.append({"level": level, "code": code, "text": text})

    # Zielgrößen je Segment
    seg_by_target: dict[str, list[dict]] = {}
    for s in segs:
        seg_by_target.setdefault(target_key(s), []).append(s)

    lines: list[dict[str, Any]] = []
    shares: dict[str, dict[str, int]] = {}  # cost_type -> target -> cents
    landlord_excluded = 0
    order = list(COST_TYPES)
    types_present = sorted({c["cost_type"] for c in costs}, key=order.index)  # Reihenfolge wie § 2 BetrKV
    for ct in types_present:
        items = [c for c in costs if c["cost_type"] == ct]
        total = sum(c["amount_cents"] for c in items)
        meta = COST_TYPES[ct]
        if meta.get("excluded"):
            landlord_excluded += total
            lines.append({"cost_type": ct, "label": meta["label"], "total": total, "key": None, "items": items, "excluded": True})
            continue
        key, reason = key_for(conn, period_id, ct)
        line = {"cost_type": ct, "label": meta["label"], "ref": meta["ref"], "total": total, "key": key,
                "key_label": KEYS[key]["label"], "key_reason": reason or KEYS[key]["why"], "items": items, "units": {}}
        weights: dict[str, Fraction] = {}
        unit_label = ""
        try:
            if key == "flaeche":
                unit_label = "m² × Tage"
                missing = sorted({s["unit_name"] for s in segs if not s["area"]})
                if missing:
                    issue("kritisch", f"flaeche:{ct}", f"{meta['label']}: Wohnfläche fehlt für {', '.join(missing)}.")
                    raise BKError
                for s in segs:
                    weights[target_key(s)] = weights.get(target_key(s), 0) + Fraction(str(s["area"])) * s["days"]
            elif key == "einheiten":
                unit_label = "Wohnungen × Tage"
                for s in segs:
                    weights[target_key(s)] = weights.get(target_key(s), 0) + Fraction(s["days"])
            elif key == "personen":
                unit_label = "Personentage"
                for s in segs:
                    if s["tenancy_id"]:
                        pd = _person_days(tenancies[s["tenancy_id"]]["terms"], d(s["from"]), d(s["to"]))
                        if pd is None:
                            issue("kritisch", f"personen:{s['tenancy_id']}",
                                  f"{meta['label']}: Personenzahl fehlt bei {tenancies[s['tenancy_id']]['names']}.")
                            raise BKError
                    else:
                        pd = Fraction(str(p["vacancy_persons"])) * s["days"]
                    weights[target_key(s)] = weights.get(target_key(s), 0) + pd
            elif key == "individuell":
                unit_label = "Anteile × Tage"
                for s in segs:
                    v = next((x for x in values if x["cost_type"] == ct and (x["unit_id"] or None) == s["unit_id"] and not x["tenancy_id"]), None)
                    if not v or v["value"] is None:
                        issue("kritisch", f"individuell:{ct}", f"{meta['label']}: Anteil für {s['unit_name']} fehlt.")
                        raise BKError
                    weights[target_key(s)] = weights.get(target_key(s), 0) + Fraction(str(v["value"])) * s["days"]
            elif key == "verbrauch":
                unit_label = "Verbrauch"
                for tk, ss in seg_by_target.items():
                    s0 = ss[0]
                    v = next((x for x in values if x["cost_type"] == ct and (x["unit_id"] or None) == s0["unit_id"]
                              and (x["tenancy_id"] or None) == s0["tenancy_id"]), None)
                    who = tenancies[s0["tenancy_id"]]["names"] if s0["tenancy_id"] else f"Leerstand {s0['unit_name']}"
                    if not v or v["value"] is None:
                        issue("kritisch", f"verbrauch:{ct}:{tk}", f"{meta['label']}: Verbrauchswert fehlt für {who}. "
                              "Bitte ablesen oder ein zulässiges Ersatzverfahren (Schätzung) anwenden und kennzeichnen.")
                        raise BKError
                    if v["estimated"]:
                        issue("warnung", f"geschaetzt:{ct}:{tk}", f"{meta['label']}: Verbrauch für {who} ist geschätzt "
                              f"({v['note'] or 'ohne Begründung'}). Die Schätzung muss in der Abrechnung nachvollziehbar sein.")
                    weights[tk] = Fraction(str(v["value"]))
                vals = [float(w) for w in weights.values()]
                if vals and any(x < 0 for x in vals):
                    issue("kritisch", f"verbrauch_neg:{ct}", f"{meta['label']}: negativer Verbrauchswert.")
                    raise BKError
                occupied = [float(weights[tk]) for tk, ss in seg_by_target.items() if ss[0]["tenancy_id"]]
                if occupied and len(occupied) > 1:
                    mean = sum(occupied) / len(occupied)
                    for tk, ss in seg_by_target.items():
                        if ss[0]["tenancy_id"] and mean > 0 and (float(weights[tk]) > 3 * mean or float(weights[tk]) == 0):
                            issue("warnung", f"verbrauch_plaus:{ct}:{tk}", f"{meta['label']}: Verbrauch von "
                                  f"{tenancies[ss[0]['tenancy_id']]['names']} ({de_num(weights[tk], 2)}) weicht stark vom Durchschnitt "
                                  f"({de_num(mean, 2)}) ab – Ablesung prüfen.")
            elif key == "direkt":
                unit_label = "Tage"
                alloc: dict[str, int] = {}
                for c in items:
                    if not c["unit_id"]:
                        issue("kritisch", f"direkt:{c['id']}", f"{meta['label']}: Position „{c['description'] or euro(c['amount_cents'])}“ "
                              "ist keiner Wohnung zugeordnet.")
                        raise BKError
                    w = {target_key(s): Fraction(s["days"]) for s in segs if s["unit_id"] == c["unit_id"]}
                    for k, v in _allocate(c["amount_cents"], w).items():
                        alloc[k] = alloc.get(k, 0) + v
                shares[ct] = alloc
            elif key == "extern":
                unit_label = "laut Messdienst"
                alloc = {}
                for tk, ss in seg_by_target.items():
                    s0 = ss[0]
                    v = next((x for x in values if x["cost_type"] == ct and (x["unit_id"] or None) == s0["unit_id"]
                              and (x["tenancy_id"] or None) == s0["tenancy_id"]), None)
                    who = tenancies[s0["tenancy_id"]]["names"] if s0["tenancy_id"] else f"Leerstand {s0['unit_name']}"
                    if not v or v["amount_cents"] is None:
                        issue("kritisch", f"extern:{ct}:{tk}", f"{meta['label']}: Betrag laut Abrechnung der Ablesefirma fehlt für {who}.")
                        raise BKError
                    alloc[tk] = v["amount_cents"]
                diff = sum(alloc.values()) - total
                if diff:
                    issue("kritisch" if abs(diff) > 100 else "warnung", f"extern_summe:{ct}",
                          f"{meta['label']}: Summe laut Ablesefirma ({euro(sum(alloc.values()))}) weicht von den erfassten Kosten "
                          f"({euro(total)}) um {euro(diff)} ab.")
                    if abs(diff) > 100:
                        raise BKError
                    # Kleine Differenz: auf die Einzelbeträge im Verhältnis verteilen, damit die Summe stimmt
                    alloc = _allocate(total, {k: Fraction(v) for k, v in alloc.items()}) if sum(alloc.values()) else alloc
                shares[ct] = alloc
            else:
                issue("kritisch", f"schluessel:{ct}", f"{meta['label']}: kein Umlageschlüssel festgelegt.")
                raise BKError
            if key not in ("direkt", "extern"):
                if sum(weights.values()) == 0:
                    issue("kritisch", f"null:{ct}", f"{meta['label']}: Summe der Verteilungseinheiten ist 0.")
                    raise BKError
                shares[ct] = _allocate(total, weights)
                line["weights"] = {k: v for k, v in weights.items()}
                line["weight_total"] = sum(weights.values())
            line["unit_label"] = unit_label
            line["ok"] = True
        except BKError:
            line["ok"] = False
        lines.append(line)

    # Ergebnisse je Mietverhältnis und Vermieteranteil
    results = []
    landlord_vacancy = sum(v for ct in shares for k, v in shares[ct].items() if k.endswith(":0"))
    for tid in tenancy_ids:
        t = tenancies[tid]
        my_segs = [s for s in segs if s["tenancy_id"] == tid]
        tks = {target_key(s) for s in my_segs}
        days_in = sum(s["days"] for s in my_segs)
        positions = []
        for line in lines:
            if line.get("excluded") or not line.get("ok"):
                continue
            ct = line["cost_type"]
            amount = sum(shares.get(ct, {}).get(k, 0) for k in tks)
            pos = {"cost_type": ct, "label": line["label"], "ref": line["ref"], "total": line["total"], "key": line["key"],
                   "key_label": line["key_label"], "key_reason": line["key_reason"], "amount": amount, "heat": ct in HEAT_TYPES}
            pos["basis"] = _basis_text(line, my_segs, tks, t, p, period_days)
            positions.append(pos)
        bk = sum(x["amount"] for x in positions if not x["heat"])
        hk = sum(x["amount"] for x in positions if x["heat"])
        soll = advances_soll(t, start, end)
        adv = row(conn.execute("SELECT * FROM bk_advances WHERE period_id = ? AND tenancy_id = ?", (period_id, tid)))
        if not adv or not adv["confirmed"]:
            issue("kritisch", f"vz:{tid}", f"Vorauszahlungen von {t['names']}: tatsächlich geleistete Beträge noch nicht bestätigt "
                  f"(vereinbart: BK {euro(soll['bk'])}, HK {euro(soll['hk'])}).")
        ist_bk, ist_hk = (adv["bk_cents"], adv["hk_cents"]) if adv else (0, 0)
        if adv and adv["confirmed"] and (ist_bk, ist_hk) != (soll["bk"], soll["hk"]):
            issue("info", f"vz_abw:{tid}", f"{t['names']}: geleistete Vorauszahlungen ({euro(ist_bk + ist_hk)}) weichen vom Soll "
                  f"({euro(soll['bk'] + soll['hk'])}) ab. Abgerechnet werden die tatsächlich geleisteten Beträge.")
        total = bk + hk
        paid = ist_bk + ist_hk
        results.append({
            "tenancy_id": tid, "names": t["names"], "parties": t["parties"], "unit_id": t["unit_id"], "unit_name": t["unit_name"],
            "from": min(s["from"] for s in my_segs), "to": max(s["to"] for s in my_segs), "days": days_in,
            "contract_start": t["contract_start"], "contract_end": t["contract_end"] or t["move_out"],
            "positions": positions, "bk_total": bk, "hk_total": hk, "total": total,
            "advances_soll": soll, "advances_ist": {"bk": ist_bk, "hk": ist_hk, "confirmed": bool(adv and adv["confirmed"])},
            "paid": paid, "balance": total - paid,  # > 0 Nachzahlung, < 0 Guthaben
            "umlage_notes": t["umlage_notes"],
        })

    distributable = sum(l["total"] for l in lines if not l.get("excluded"))
    distributed = sum(sum(v.values()) for v in shares.values())
    vacancy = []
    for u in _units(conn, p):
        tk = f"{u['id'] or 0}:0"
        amount = sum(shares[ct].get(tk, 0) for ct in shares)
        dd = sum(s["days"] for s in segs if target_key(s) == tk)
        if dd:
            vacancy.append({"unit_id": u["id"], "unit_name": u["name"], "days": dd, "amount": amount})

    _checks(conn, p, lines, results, segs, costs, issues, shares, distributable, distributed)
    return {
        "period": {k: p[k] for k in ("id", "property_id", "property_name", "property_address", "start", "end", "status",
                                      "vacancy_persons", "co2", "notes")},
        "period_days": period_days, "units": _units(conn, p), "segments": segs, "lines": [_line_public(l) for l in lines],
        "results": results, "vacancy": vacancy, "landlord": {"vacancy": landlord_vacancy, "excluded": landlord_excluded},
        "totals": {"costs": sum(l["total"] for l in lines), "distributable": distributable, "distributed": distributed},
        "issues": issues, "acknowledged": p["acknowledged"],
        "can_release": not any(i["level"] == "kritisch" for i in issues)
        and all(i["code"] in p["acknowledged"] for i in issues if i["level"] == "warnung"),
    }


def _line_public(line: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in line.items() if k not in ("weights", "weight_total")}
    if "weight_total" in line:
        out["weight_total"] = float(line["weight_total"])
    out["items"] = [{k: c[k] for k in ("id", "description", "amount_cents", "invoice_date", "unit_id", "unit_name", "document_id",
                                       "expense_id", "origin", "source_category")} for c in line["items"]]
    return out


def _basis_text(line: dict[str, Any], my_segs: list[dict], tks: set[str], t: dict, p: dict, period_days: int) -> dict[str, str]:
    """Nachvollziehbare Angabe: Ihre Einheiten / Gesamteinheiten."""
    key = line["key"]
    if key == "flaeche":
        area = sum(Fraction(str(s["area"])) * s["days"] for s in my_segs)
        total = line.get("weight_total") or 0
        total_area = Fraction(total) / period_days if period_days else 0
        mine = " + ".join(f"{de_num(Fraction(str(s['area'])))} m² × {s['days']} Tage" for s in my_segs)
        return {"mine": mine, "total": f"{de_num(total_area)} m² × {period_days} Tage",
                "mine_units": de_num(area), "total_units": de_num(total)}
    if key == "einheiten":
        days_ = sum(s["days"] for s in my_segs)
        return {"mine": f"1 Wohnung × {days_} Tage", "total": f"{de_num(line['weight_total'], 0)} Wohnungstage",
                "mine_units": str(days_), "total_units": de_num(line["weight_total"], 0)}
    if key == "personen":
        mine = sum(line["weights"].get(k, 0) for k in tks)
        return {"mine": f"{de_num(mine, 0)} Personentage", "total": f"{de_num(line['weight_total'], 0)} Personentage",
                "mine_units": de_num(mine, 0), "total_units": de_num(line["weight_total"], 0)}
    if key == "individuell":
        mine = sum(line["weights"].get(k, 0) for k in tks)
        return {"mine": f"{de_num(mine)} Anteilstage", "total": f"{de_num(line['weight_total'])} Anteilstage",
                "mine_units": de_num(mine), "total_units": de_num(line["weight_total"])}
    if key == "verbrauch":
        mine = sum(line["weights"].get(k, 0) for k in tks)
        return {"mine": f"Verbrauch {de_num(mine, 2)}", "total": f"Gesamtverbrauch {de_num(line['weight_total'], 2)}",
                "mine_units": de_num(mine, 2), "total_units": de_num(line["weight_total"], 2)}
    if key == "direkt":
        days_ = sum(s["days"] for s in my_segs)
        return {"mine": f"nur Ihre Wohnung, {days_} Tage", "total": "Kosten der Wohnung", "mine_units": str(days_), "total_units": ""}
    if key == "extern":
        return {"mine": "laut Ablesefirma (Anlage)", "total": "laut Ablesefirma",
                "mine_units": "", "total_units": ""}
    return {"mine": "", "total": "", "mine_units": "", "total_units": ""}


def advances_soll(t: dict[str, Any], start: date, end: date) -> dict[str, int]:
    """Vereinbarte Vorauszahlungen im Abrechnungszeitraum (Soll), taggenau anteilig je Monat."""
    s, e = tn.span(t)
    s, e = max(s, start), min(e or date.max, end)
    bk = hk = Fraction(0)
    day = s
    while day <= e:
        term = tn.term_at(t["terms"], day)
        md = monthrange(day.year, day.month)[1]
        if term:
            bk += Fraction(term["bk_advance_cents"], md)
            hk += Fraction(term["hk_advance_cents"], md)
        day += timedelta(days=1)
    return {"bk": round(bk), "hk": round(hk)}


def advances_suggestion(conn: sqlite3.Connection, period_id: int, tenancy_id: int) -> dict[str, Any]:
    """Vorschlag für die Ist-Vorauszahlungen aus dem Mietkonto. Wird nie automatisch übernommen."""
    p = load_period(conn, period_id)
    t = tn.load(conn, tenancy_id)
    start, end = d(p["start"]), d(p["end"])
    s, e = tn.span(t)
    s, e = max(s, start), min(e or date.max, end)
    soll = advances_soll(t, start, end)
    cold = Fraction(0)
    day = s
    while day <= e:
        term = tn.term_at(t["terms"], day)
        if term:
            cold += Fraction(term["cold_rent_cents"], monthrange(day.year, day.month)[1])
        day += timedelta(days=1)
    soll_total = round(cold) + soll["bk"] + soll["hk"]
    received = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0), COUNT(*) FROM transactions WHERE kind = 'einnahme' AND status IN ('done', 'auto') "
        "AND property_id = ? AND COALESCE(unit_id, 0) = COALESCE(?, 0) AND booking_date BETWEEN ? AND ?",
        (t["property_id"], t["unit_id"], s.isoformat(), e.isoformat())).fetchone()
    amount, count = received[0], received[1]
    if not count:
        text = "Im Mietkonto sind für diesen Zeitraum keine zugeordneten Mieteingänge. Bitte die geleisteten Beträge selbst eintragen."
        sug = None
    elif amount >= soll_total:
        text = f"Mieteingänge {euro(amount)} decken die vereinbarte Gesamtmiete ({euro(soll_total)}). Vorschlag: Vorauszahlungen voll geleistet."
        sug = {"bk": soll["bk"], "hk": soll["hk"]}
    else:
        ratio = Fraction(amount, soll_total) if soll_total else Fraction(0)
        text = (f"Mieteingänge {euro(amount)} liegen unter der vereinbarten Gesamtmiete ({euro(soll_total)}). "
                "Bitte prüfen, welche Vorauszahlungen tatsächlich geleistet wurden (§ 366 BGB: Tilgungsbestimmung).")
        sug = {"bk": round(soll["bk"] * ratio), "hk": round(soll["hk"] * ratio)}
    return {"soll": soll, "soll_total": soll_total, "received": amount, "count": count, "suggestion": sug, "text": text,
            "from": s.isoformat(), "to": e.isoformat()}


# --- Plausibilitätsprüfung -----------------------------------------------------------

def _checks(conn, p, lines, results, segs, costs, issues, shares, distributable, distributed) -> None:
    def issue(level, code, text):
        issues.append({"level": level, "code": code, "text": text})

    start, end = d(p["start"]), d(p["end"])
    days_ = (end - start).days + 1
    if (end.year - start.year) * 12 + end.month - start.month >= 12 and days_ > 366:
        issue("kritisch", "zeitraum", "Der Abrechnungszeitraum ist länger als zwölf Monate (§ 556 Abs. 3 S. 1 BGB).")
    deadline = date(end.year + 1, end.month, min(end.day, monthrange(end.year + 1, end.month)[1]))
    today = date.today()
    if today > deadline:
        issue("warnung", "frist", f"Die Abrechnungsfrist ist am {deadline:%d.%m.%Y} abgelaufen (§ 556 Abs. 3 S. 2, 3 BGB). "
              "Nachforderungen sind in der Regel ausgeschlossen, Guthaben müssen trotzdem ausgezahlt werden.")
    elif (deadline - today).days <= 60:
        issue("info", "frist_bald", f"Abrechnungsfrist endet am {deadline:%d.%m.%Y} (Zugang beim Mieter maßgeblich).")
    if not costs:
        issue("kritisch", "keine_kosten", "Es sind noch keine Kosten erfasst.")
    if not results:
        issue("kritisch", "keine_mieter", "Im Zeitraum gibt es kein Mietverhältnis.")

    # Nicht umlagefähige Herkunft
    for c in costs:
        if c["cost_type"] != "nicht_umlagefaehig" and c["source_category"] in NOT_ALLOCABLE_CATEGORIES:
            issue("kritisch", f"herkunft:{c['id']}", f"„{c['description'] or euro(c['amount_cents'])}“ stammt aus der Ausgabenkategorie "
                  f"„{c['source_category']}“ (z. B. Verwaltung, Instandhaltung, Zinsen) und ist keine umlagefähige Betriebskostenart. "
                  "Bitte als „nicht umlagefähig“ einordnen.")
        if c["cost_type"] == "antenne_glasfaser" and re.search(r"kabel|tv|fernseh|urheber|gema|vodafone kabel", (c["description"] or "").lower()) \
                and end >= date(2024, 7, 1):
            issue("kritisch", f"kabel:{c['id']}", f"„{c['description']}“: Kabel-TV-Entgelte sind seit dem 1.7.2024 nicht mehr umlagefähig "
                  "(§ 2 Nr. 15 BetrKV n. F.). Umlagefähig bleiben nur Betriebsstrom/Prüfung bzw. ein Glasfaserbereitstellungsentgelt.")
        if c["cost_type"] == "sonstige":
            issue("warnung", f"sonstige:{c['id']}", f"„{c['description'] or 'Sonstige Betriebskosten'}“: Sonstige Betriebskosten sind nur "
                  "umlagefähig, wenn sie im Mietvertrag einzeln benannt sind (§ 2 Nr. 17 BetrKV).")
        if c["amount_cents"] < 0:
            issue("info", f"gutschrift:{c['id']}", f"„{c['description']}“ ist eine Gutschrift ({euro(c['amount_cents'])}) und mindert die Kosten.")
        if c["invoice_date"] and not (p["start"] <= c["invoice_date"] <= p["end"]):
            issue("info", f"datum:{c['id']}", f"„{c['description'] or euro(c['amount_cents'])}“ hat ein Rechnungsdatum außerhalb des "
                  "Abrechnungszeitraums. Bei Abrechnung nach Abfluss ist das zulässig; bitte Zeitraum der Leistung prüfen.")

    # Doppelte Rechnungen
    seen: dict[tuple, dict] = {}
    for c in costs:
        if c["document_id"]:
            k = ("doc", c["document_id"])
            if k in seen:
                issue("warnung", f"doppelt_beleg:{c['id']}", f"Derselbe Beleg ist zweimal erfasst: „{c['description']}“ ({euro(c['amount_cents'])}).")
            seen[k] = c
        k2 = ("sum", c["cost_type"], c["amount_cents"], c["invoice_date"])
        if c["invoice_date"] and k2 in seen:
            issue("warnung", f"doppelt:{c['id']}", f"Mögliche Doppelerfassung: zwei Positionen „{COST_TYPES[c['cost_type']]['label']}“ über "
                  f"{euro(c['amount_cents'])} vom {d(c['invoice_date']):%d.%m.%Y}.")
        seen[k2] = c

    # Rundung / Vollständigkeit der Verteilung
    if distributed != sum(l["total"] for l in lines if not l.get("excluded") and l.get("ok")):
        issue("kritisch", "summe", "Die verteilten Beträge stimmen nicht mit den umlagefähigen Gesamtkosten überein.")

    # Flächen
    us = _units(conn, p)
    if any(l.get("key") == "flaeche" for l in lines) and p.get("property_area"):
        s_area = sum((u["living_area"] or 0) for u in us)
        if s_area and abs(s_area - p["property_area"]) > 0.5 and len(us) > 1:
            issue("warnung", "flaechen", f"Summe der Wohnungsflächen ({de_num(s_area)} m²) weicht von der Gesamtfläche des Objekts "
                  f"({de_num(p['property_area'])} m²) ab. Verteilt wird nach der Summe der Wohnungsflächen.")

    # Leerstand
    vac_days = sum(s["days"] for s in segs if not s["tenancy_id"])
    if vac_days:
        issue("info", "leerstand", f"Leerstand im Zeitraum: {vac_days} Wohnungstage. Der darauf entfallende Kostenanteil wird nicht auf "
              "die Mieter verteilt, sondern vom Vermieter getragen (BGH VIII ZR 159/05).")
        if any(l.get("key") == "personen" for l in lines):
            issue("info", "leerstand_personen", f"Personenschlüssel: leere Wohnungen werden mit {de_num(p['vacancy_persons'], 1)} Person(en) "
                  "angesetzt. Die Rechtslage dazu ist nicht abschließend geklärt; im Zweifel nicht zu Lasten der Mieter.")

    # Mieterwechsel / Nutzungszeiten
    for r in results:
        t_from, t_to = r["from"], r["to"]
        if t_from != p["start"] or t_to != p["end"]:
            issue("info", f"zeit:{r['tenancy_id']}", f"{r['names']}: Abrechnung nur für {d(t_from):%d.%m.%Y}–{d(t_to):%d.%m.%Y} "
                  f"({r['days']} Tage).")
        if not (r.get("umlage_notes") or "").strip():
            issue("info", f"vereinbarung:{r['tenancy_id']}", f"{r['names']}: Keine Umlagevereinbarung hinterlegt. Umgelegt werden dürfen nur "
                  "Kostenarten, die im Mietvertrag vereinbart sind (Verweis auf die BetrKV genügt in der Regel).")

    # Heizkosten
    heat_lines = [l for l in lines if l["cost_type"] in HEAT_TYPES]
    if heat_lines:
        if not (p.get("co2") or {}).get("tenant_share_cents") and not (p.get("co2") or {}).get("not_applicable"):
            issue("warnung", "co2", "CO₂-Kostenaufteilung fehlt: Mieteranteil, Einstufung und Berechnungsgrundlagen müssen in der "
                  "Heizkostenabrechnung stehen (§ 7 Abs. 3 CO2KostAufG). Fehlen sie, darf der Mieter seinen Heizkostenanteil um 3 % "
                  "kürzen (§ 7 Abs. 4). Meist liefert die Ablesefirma diese Angaben.")
        changes = [r for r in results if r["from"] != p["start"] or r["to"] != p["end"]]
        if changes:
            issue("info", "zwischenablesung", "Mieterwechsel im Zeitraum: Für Heiz- und Warmwasserkosten ist eine Zwischenablesung "
                  "erforderlich (§ 9b HeizkostenV). Bitte prüfen, dass die Ablesefirma sie berücksichtigt hat.")
        if end >= date(2027, 1, 1):
            issue("info", "fernablesung", "Ab 1.1.2027: Sind die Erfassungsgeräte nicht fernablesbar, darf der Mieter die Heizkosten "
                  "um 3 % kürzen (§ 12 Abs. 1 HeizkostenV).")

    # Vollständigkeit gegenüber dem Vorjahr
    prev = row(conn.execute("SELECT id FROM bk_periods WHERE property_id = ? AND end < ? ORDER BY end DESC LIMIT 1",
                            (p["property_id"], p["start"])))
    present = {c["cost_type"] for c in costs}
    if prev:
        prev_costs = rows(conn.execute("SELECT cost_type, SUM(amount_cents) AS total FROM bk_costs WHERE period_id = ? GROUP BY cost_type",
                                       (prev["id"],)))
        for pc in prev_costs:
            if pc["cost_type"] == "nicht_umlagefaehig":
                continue
            if pc["cost_type"] not in present:
                issue("warnung", f"fehlt:{pc['cost_type']}", f"{COST_TYPES[pc['cost_type']]['label']} war im Vorjahr enthalten "
                      f"({euro(pc['total'])}), fehlt aber jetzt.")
            else:
                now_total = sum(c["amount_cents"] for c in costs if c["cost_type"] == pc["cost_type"])
                if pc["total"] > 0 and now_total > pc["total"] * 1.5:
                    issue("warnung", f"anstieg:{pc['cost_type']}", f"{COST_TYPES[pc['cost_type']]['label']}: {euro(now_total)} gegenüber "
                          f"{euro(pc['total'])} im Vorjahr (+{round((now_total / pc['total'] - 1) * 100)} %). Bitte prüfen "
                          "(Wirtschaftlichkeitsgebot, § 556 Abs. 3 S. 1 Hs. 2 BGB).")
    else:
        usual = [k for k in ("grundsteuer", "versicherung", "wasser", "entwaesserung", "muell") if k not in present]
        if usual and costs:
            issue("info", "ueblich", "Häufig enthaltene Kostenarten fehlen: " + ", ".join(COST_TYPES[k]["label"] for k in usual) + ".")


# --- Freigabe und Fassungen ----------------------------------------------------------

def release(conn: sqlite3.Connection, period_id: int, note: str = "") -> int:
    result = compute(conn, period_id)
    if not result["can_release"]:
        raise BKError("Freigabe nicht möglich: kritische Fehler beheben und alle Warnungen bestätigen.")
    landlord = get_setting(conn, "landlord", {}) or {}
    version = (conn.execute("SELECT MAX(version) FROM bk_statements WHERE period_id = ?", (period_id,)).fetchone()[0] or 0) + 1
    snapshot = {"result": result, "landlord": landlord, "released_at": now(), "version": version,
                "documents": receipts(conn, period_id)}
    sid = conn.execute("INSERT INTO bk_statements(period_id, version, created_at, note, snapshot) VALUES(?,?,?,?,?)",
                       (period_id, version, now(), note.strip(), json.dumps(snapshot, ensure_ascii=False, default=str))).lastrowid
    conn.execute("UPDATE bk_periods SET status = 'freigegeben', updated_at = ? WHERE id = ?", (now(), period_id))
    audit(conn, "released", "bk_period", period_id, {"version": version, "statement_id": sid})
    return sid


def receipts(conn: sqlite3.Connection, period_id: int) -> list[dict[str, Any]]:
    """Belege der Abrechnung (für die Belegeinsicht)."""
    return rows(conn.execute(
        "SELECT c.id AS cost_id, c.cost_type, c.description, c.amount_cents, c.invoice_date, d.id AS document_id, d.title "
        "FROM bk_costs c LEFT JOIN documents d ON d.id = c.document_id AND d.deleted_at IS NULL WHERE c.period_id = ? "
        "ORDER BY c.cost_type, c.invoice_date", (period_id,)))
