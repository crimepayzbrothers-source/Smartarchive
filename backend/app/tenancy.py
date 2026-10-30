"""Mieterverwaltung: Mietverhältnisse, Vertragspartner, Konditionen, Belegung.

Grundsätze
- Ein Mietverhältnis gehört zu einer Mietsache: einer Einheit (wenn das Objekt Einheiten hat) oder dem ganzen Objekt.
- Mietverhältnisse werden nie überschrieben. Ein Mieterwechsel beendet das alte und legt ein neues an.
- Für die Abrechnung maßgeblich ist die Vertragslaufzeit (Mietbeginn bis Mietende). Ist kein Mietende eingetragen,
  aber ein Auszug, gilt das Auszugsdatum. Die tatsächlichen Ein-/Auszugsdaten werden zusätzlich gespeichert
  (z. B. für Zwischenablesungen). Ob im Einzelfall etwas anderes vereinbart ist, prüft die App nicht.
- Konditionen (Kaltmiete, Vorauszahlungen, Personenzahl) gelten ab einem Datum. Eine Änderung legt einen neuen Stand an,
  der alte bleibt erhalten.
- Alle Tage werden kalendergenau gezählt (Monatslängen, Schaltjahre), Beginn und Ende jeweils einschließlich.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from .db import audit, now, row, rows


class TenancyError(ValueError):
    pass


def d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def days(start: date, end: date) -> int:
    """Kalendertage von start bis end, beide einschließlich."""
    return (end - start).days + 1


def span(t: dict[str, Any]) -> tuple[date, date | None]:
    """Abrechnungsrelevanter Zeitraum eines Mietverhältnisses (Ende None = unbefristet/laufend)."""
    return d(t["contract_start"]), d(t.get("contract_end")) or d(t.get("move_out"))


def status(t: dict[str, Any], today: date | None = None) -> str:
    today = today or date.today()
    start, end = span(t)
    if start > today:
        return "kuenftig"
    if end and end < today:
        return "beendet"
    return "aktiv"


# --- Prüfungen -----------------------------------------------------------------

def _check_dates(t: dict[str, Any]) -> None:
    start = d(t["contract_start"])
    for key, label in (("contract_end", "Mietende"), ("move_out", "Auszug")):
        if t.get(key) and d(t[key]) < start:
            raise TenancyError(f"{label} liegt vor dem Mietbeginn.")
    if t.get("move_in") and t.get("move_out") and d(t["move_out"]) < d(t["move_in"]):
        raise TenancyError("Der Auszug liegt vor dem Einzug.")


def _check_object(conn: sqlite3.Connection, property_id: int, unit_id: int | None) -> None:
    if not conn.execute("SELECT 1 FROM properties WHERE id = ?", (property_id,)).fetchone():
        raise TenancyError("Mietobjekt existiert nicht.")
    has_units = conn.execute("SELECT COUNT(*) FROM units WHERE property_id = ?", (property_id,)).fetchone()[0] > 0
    if unit_id:
        if not conn.execute("SELECT 1 FROM units WHERE id = ? AND property_id = ?", (unit_id, property_id)).fetchone():
            raise TenancyError("Die Einheit gehört nicht zu diesem Objekt.")
    elif has_units:
        raise TenancyError("Bitte die Wohnung (Einheit) wählen – das Objekt ist in Einheiten aufgeteilt.")


def _check_overlap(conn: sqlite3.Connection, t: dict[str, Any], exclude_id: int | None = None) -> None:
    start, end = span(t)
    others = rows(conn.execute(
        "SELECT * FROM tenancies WHERE property_id = ? AND COALESCE(unit_id, 0) = COALESCE(?, 0) AND id != ?",
        (t["property_id"], t.get("unit_id"), exclude_id or 0)))
    for o in others:
        o_start, o_end = span(o)
        if start <= (o_end or date.max) and o_start <= (end or date.max):
            names = party_names(conn, o["id"]) or "ein anderes Mietverhältnis"
            raise TenancyError(f"Überschneidet sich mit {names} ({o_start:%d.%m.%Y} – "
                               f"{o_end.strftime('%d.%m.%Y') if o_end else 'unbefristet'}). "
                               "Beim Mieterwechsel zuerst das bisherige Mietverhältnis beenden.")


def _check_parties(parties: list[dict[str, Any]]) -> None:
    if not parties:
        raise TenancyError("Mindestens einen Vertragspartner angeben.")
    for p in parties:
        if not (p.get("last_name") or "").strip():
            raise TenancyError("Bei jedem Vertragspartner den Nachnamen angeben.")


def _check_terms(t: dict[str, Any], terms: list[dict[str, Any]]) -> None:
    if not terms:
        raise TenancyError("Bitte Miete und Vorauszahlungen angeben.")
    start, end = span(t)
    seen = set()
    for term in terms:
        vf = d(term["valid_from"])
        if vf < start:
            raise TenancyError("Eine Änderung der Konditionen liegt vor dem Mietbeginn.")
        if end and vf > end:
            raise TenancyError("Eine Änderung der Konditionen liegt nach dem Mietende.")
        if vf in seen:
            raise TenancyError("Zwei Konditionen mit demselben Gültigkeitsdatum.")
        seen.add(vf)
        for k in ("cold_rent_cents", "bk_advance_cents", "hk_advance_cents"):
            if (term.get(k) or 0) < 0:
                raise TenancyError("Beträge dürfen nicht negativ sein.")
        if term.get("persons") is not None and not 0 <= term["persons"] <= 50:
            raise TenancyError("Personenzahl ist unplausibel.")
    if min(seen) != start:
        raise TenancyError("Die ersten Konditionen müssen ab Mietbeginn gelten.")


# --- Lesen --------------------------------------------------------------------------

def party_names(conn: sqlite3.Connection, tenancy_id: int) -> str:
    ps = rows(conn.execute("SELECT first_name, last_name FROM tenancy_parties WHERE tenancy_id = ? ORDER BY sort, id", (tenancy_id,)))
    return ", ".join(f"{p['first_name']} {p['last_name']}".strip() for p in ps)


def load(conn: sqlite3.Connection, tenancy_id: int) -> dict[str, Any]:
    t = row(conn.execute(
        "SELECT t.*, p.name AS property_name, p.address AS property_address, u.name AS unit_name, u.living_area AS unit_area "
        "FROM tenancies t JOIN properties p ON p.id = t.property_id LEFT JOIN units u ON u.id = t.unit_id WHERE t.id = ?",
        (tenancy_id,)))
    if not t:
        raise KeyError(tenancy_id)
    t["parties"] = rows(conn.execute("SELECT * FROM tenancy_parties WHERE tenancy_id = ? ORDER BY sort, id", (tenancy_id,)))
    t["terms"] = rows(conn.execute("SELECT * FROM tenancy_terms WHERE tenancy_id = ? ORDER BY valid_from", (tenancy_id,)))
    t["names"] = ", ".join(f"{p['first_name']} {p['last_name']}".strip() for p in t["parties"])
    t["status"] = status(t)
    t["current_terms"] = term_at(t["terms"], min(max(date.today(), span(t)[0]), span(t)[1] or date.max))
    t.update(neighbours(conn, t))
    return t


def neighbours(conn: sqlite3.Connection, t: dict[str, Any]) -> dict[str, Any]:
    """Vor- und Nachmieter derselben Wohnung und die Leerstandszeiten dazwischen (automatisch, nichts zu erfassen)."""
    start, end = span(t)
    others = rows(conn.execute("SELECT * FROM tenancies WHERE property_id = ? AND COALESCE(unit_id, 0) = COALESCE(?, 0) AND id != ?",
                               (t["property_id"], t.get("unit_id"), t["id"])))
    before = [o for o in others if span(o)[1] and span(o)[1] < start]
    after = [o for o in others if span(o)[0] > (end or date.max)]
    prev = max(before, key=lambda o: span(o)[1], default=None)
    nxt = min(after, key=lambda o: span(o)[0], default=None)

    def gap(a: date, b: date) -> dict[str, Any] | None:
        return {"from": a.isoformat(), "to": b.isoformat(), "days": days(a, b)} if a <= b else None

    return {
        "previous": {"id": prev["id"], "names": party_names(conn, prev["id"]), "end": span(prev)[1].isoformat()} if prev else None,
        "next": {"id": nxt["id"], "names": party_names(conn, nxt["id"]), "start": nxt["contract_start"]} if nxt else None,
        "vacancy_before": gap(span(prev)[1] + timedelta(days=1), start - timedelta(days=1)) if prev else None,
        "vacancy_after": gap(end + timedelta(days=1), span(nxt)[0] - timedelta(days=1)) if nxt and end else None,
    }


def term_at(terms: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """Konditionen, die an einem Tag gelten."""
    current = None
    for term in sorted(terms, key=lambda x: x["valid_from"]):
        if d(term["valid_from"]) <= day:
            current = term
    return current


def list_all(conn: sqlite3.Connection, property_id: int | None = None) -> list[dict[str, Any]]:
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM tenancies WHERE (? IS NULL OR property_id = ?) ORDER BY contract_start DESC, id DESC",
        (property_id, property_id))]
    return [load(conn, i) for i in ids]


# --- Schreiben ----------------------------------------------------------------------

TENANCY_FIELDS = ("property_id", "unit_id", "contract_start", "move_in", "contract_end", "move_out", "umlage_notes", "notes")
PARTY_FIELDS = ("first_name", "last_name", "address", "phone", "email")
TERM_FIELDS = ("valid_from", "cold_rent_cents", "bk_advance_cents", "hk_advance_cents", "persons", "note")


def _write_children(conn: sqlite3.Connection, tid: int, parties: list[dict], terms: list[dict]) -> None:
    conn.execute("DELETE FROM tenancy_parties WHERE tenancy_id = ?", (tid,))
    for i, p in enumerate(parties):
        conn.execute("INSERT INTO tenancy_parties(tenancy_id, first_name, last_name, address, phone, email, sort) VALUES(?,?,?,?,?,?,?)",
                     (tid, *((p.get(k) or "").strip() for k in PARTY_FIELDS), i))
    conn.execute("DELETE FROM tenancy_terms WHERE tenancy_id = ?", (tid,))
    for term in sorted(terms, key=lambda x: x["valid_from"]):
        conn.execute("INSERT INTO tenancy_terms(tenancy_id, valid_from, cold_rent_cents, bk_advance_cents, hk_advance_cents, persons, note) "
                     "VALUES(?,?,?,?,?,?,?)", (tid, term["valid_from"], term.get("cold_rent_cents") or 0, term.get("bk_advance_cents") or 0,
                                               term.get("hk_advance_cents") or 0, term.get("persons"), (term.get("note") or "").strip()))


def _audit_details(t: dict[str, Any], parties: list[dict], terms: list[dict]) -> dict[str, Any]:
    # Keine Namen oder Kontaktdaten ins Protokoll: Nach einer Löschung sollen keine personenbezogenen Daten zurückbleiben.
    return {**{k: t.get(k) for k in TENANCY_FIELDS if k not in ("notes", "umlage_notes")},
            "vertragspartner": len(parties), "konditionen": [{k: x.get(k) for k in TERM_FIELDS if k != "note"} for x in terms]}


def save(conn: sqlite3.Connection, data: dict[str, Any], tenancy_id: int | None = None) -> int:
    t = {k: data.get(k) for k in TENANCY_FIELDS}
    t["umlage_notes"] = (t["umlage_notes"] or "").strip()
    t["notes"] = (t["notes"] or "").strip()
    parties, terms = data.get("parties") or [], data.get("terms") or []
    if not t["contract_start"]:
        raise TenancyError("Mietbeginn fehlt.")
    _check_object(conn, t["property_id"], t["unit_id"])
    _check_dates(t)
    _check_parties(parties)
    _check_terms(t, terms)
    _check_overlap(conn, t, tenancy_id)
    ts = now()
    if tenancy_id:
        if not conn.execute("SELECT 1 FROM tenancies WHERE id = ?", (tenancy_id,)).fetchone():
            raise KeyError(tenancy_id)
        conn.execute(f"UPDATE tenancies SET {', '.join(k + ' = ?' for k in t)}, updated_at = ? WHERE id = ?", (*t.values(), ts, tenancy_id))
        action = "updated"
    else:
        tenancy_id = conn.execute(f"INSERT INTO tenancies({', '.join(t)}, created_at, updated_at) VALUES({', '.join('?' * len(t))}, ?, ?)",
                                  (*t.values(), ts, ts)).lastrowid
        action = "created"
    _write_children(conn, tenancy_id, parties, terms)
    audit(conn, action, "tenancy", tenancy_id, _audit_details(t, parties, terms))
    return tenancy_id


def end(conn: sqlite3.Connection, tenancy_id: int, contract_end: str, move_out: str | None) -> None:
    """Mietverhältnis beenden (z. B. Mieterwechsel). Spätere Konditionen wären ungültig und werden abgelehnt."""
    t = load(conn, tenancy_id)
    data = {**t, "contract_end": contract_end, "move_out": move_out or contract_end}
    save(conn, data, tenancy_id)


def delete(conn: sqlite3.Connection, tenancy_id: int) -> None:
    if not conn.execute("SELECT 1 FROM tenancies WHERE id = ?", (tenancy_id,)).fetchone():
        raise KeyError(tenancy_id)
    conn.execute("DELETE FROM tenancies WHERE id = ?", (tenancy_id,))
    audit(conn, "deleted", "tenancy", tenancy_id)


# --- Belegung (Grundlage für die zeitanteilige Verteilung) ------------------------

def occupancy(conn: sqlite3.Connection, property_id: int, unit_id: int | None, start: date, end: date) -> list[dict[str, Any]]:
    """Lückenlose Abschnitte eines Zeitraums: je Mietverhältnis oder Leerstand, mit Tagen (einschließlich)."""
    if end < start:
        raise TenancyError("Das Ende des Zeitraums liegt vor dem Beginn.")
    ts = rows(conn.execute("SELECT * FROM tenancies WHERE property_id = ? AND COALESCE(unit_id, 0) = COALESCE(?, 0)",
                           (property_id, unit_id)))
    parts = []
    for t in ts:
        s, e = span(t)
        s, e = max(s, start), min(e or date.max, end)
        if s <= e:
            parts.append({"tenancy_id": t["id"], "from": s, "to": e})
    parts.sort(key=lambda x: x["from"])
    out, cursor = [], start
    for p in parts:
        if p["from"] > cursor:
            out.append({"tenancy_id": None, "from": cursor, "to": p["from"] - timedelta(days=1)})
        out.append(p)
        cursor = p["to"] + timedelta(days=1)
    if cursor <= end:
        out.append({"tenancy_id": None, "from": cursor, "to": end})
    for o in out:
        o["days"] = days(o["from"], o["to"])
        o["names"] = party_names(conn, o["tenancy_id"]) if o["tenancy_id"] else ""
        o["from"], o["to"] = o["from"].isoformat(), o["to"].isoformat()
    return out


def property_occupancy(conn: sqlite3.Connection, property_id: int, start: date, end: date) -> list[dict[str, Any]]:
    units = rows(conn.execute("SELECT id, name, living_area FROM units WHERE property_id = ? ORDER BY id", (property_id,)))
    targets = units or [{"id": None, "name": "Ganzes Objekt", "living_area": None}]
    total = days(start, end)
    result = []
    for u in targets:
        segs = occupancy(conn, property_id, u["id"], start, end)
        vacant = sum(s["days"] for s in segs if not s["tenancy_id"])
        result.append({"unit_id": u["id"], "unit_name": u["name"], "living_area": u["living_area"], "segments": segs,
                       "days_total": total, "days_vacant": vacant})
    return result
