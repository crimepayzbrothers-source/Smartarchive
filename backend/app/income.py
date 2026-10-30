"""Einnahmen aus Mietverhältnissen: Soll (laut Mietvertrag) gegen Ist (Mietkonto und Barzahlungen) je Monat.

- Soll je Monat: Kaltmiete + Betriebs- + Heizkostenvorauszahlung laut gültigen Konditionen, taggenau anteilig bei
  Ein-/Auszug im laufenden Monat (Monatsbetrag / Tage des Monats × Miettage). Leerstand erzeugt kein Soll.
- Ist: zugeordnete Mieteingänge aus dem Mietkonto und von Hand erfasste Zahlungen (z. B. bar).
- Monatszuordnung einer Zahlung: Monat des Zahlungseingangs; Eingänge ab dem 25. zählen für den Folgemonat
  (übliche Vorauszahlung der Miete). Bei Hand-Erfassung kann der Monat ausdrücklich gewählt werden.
- Der Saldo läuft innerhalb des Jahres auf (negativ = Rückstand). Ein Vortrag aus Vorjahren wird nicht gebildet.
Es wird nichts steuerlich bewertet; die Übersicht dokumentiert Zahlungen.
"""
from __future__ import annotations

import re
import sqlite3
from calendar import monthrange
from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from . import tenancy as tn
from .db import rows

EARLY_DAY = 25


def tenancy_for(conn: sqlite3.Connection, property_id: int | None, unit_id: int | None, day: str) -> int | None:
    """Mietverhältnis, das an einem Tag in dieser Wohnung (bzw. im ganzen Objekt) läuft."""
    if not property_id:
        return None
    if unit_id is None and conn.execute("SELECT COUNT(*) FROM units WHERE property_id = ?", (property_id,)).fetchone()[0]:
        return None
    d = date.fromisoformat(day)
    for t in rows(conn.execute("SELECT * FROM tenancies WHERE property_id = ? AND COALESCE(unit_id, 0) = COALESCE(?, 0)",
                               (property_id, unit_id))):
        s, e = tn.span(t)
        # Vorauszahlung kurz vor Mietbeginn oder Nachzahlung kurz nach Ende noch diesem Mietverhältnis zuordnen
        if s - timedelta(days=10) <= d <= (e + timedelta(days=40) if e else date.max):
            return t["id"]
    return None


def match_by_name(conn: sqlite3.Connection, text: str, day: str) -> dict[str, Any] | None:
    """Mietverhältnis, dessen Vertragspartner (Nachname) eindeutig im Buchungstext steht."""
    d = date.fromisoformat(day)
    low = text.lower()
    hits = {}
    for p in rows(conn.execute("SELECT tp.last_name, t.* FROM tenancy_parties tp JOIN tenancies t ON t.id = tp.tenancy_id")):
        name = (p["last_name"] or "").strip().lower()
        if len(name) < 3 or not re.search(r"(?<![a-zäöüß])" + re.escape(name) + r"(?![a-zäöüß])", low):
            continue
        s, e = tn.span(p)
        if s - timedelta(days=10) <= d <= (e + timedelta(days=40) if e else date.max):
            hits[p["id"]] = p
    return next(iter(hits.values())) if len(hits) == 1 else None


def link_unassigned(conn: sqlite3.Connection, property_id: int | None = None) -> int:
    """Zugeordnete Mieteingänge ohne Mietverhältnis nachträglich verknüpfen (z. B. Mietverhältnis später angelegt)."""
    n = 0
    for tx in rows(conn.execute("SELECT id, property_id, unit_id, booking_date, counterparty, purpose FROM transactions "
                                "WHERE kind = 'einnahme' AND status IN ('done', 'auto') AND tenancy_id IS NULL AND property_id IS NOT NULL "
                                "AND (? IS NULL OR property_id = ?)", (property_id, property_id))):
        tid = tenancy_for(conn, tx["property_id"], tx["unit_id"], tx["booking_date"])
        if not tid:
            m = match_by_name(conn, f"{tx['counterparty']} {tx['purpose']}", tx["booking_date"])
            tid = m["id"] if m and m["property_id"] == tx["property_id"] else None
        if tid:
            conn.execute("UPDATE transactions SET tenancy_id = ? WHERE id = ?", (tid, tx["id"]))
            n += 1
    return n


def monthly_soll(t: dict[str, Any], year: int, month: int) -> dict[str, int]:
    """Soll eines Monats (taggenau anteilig bei Beginn/Ende im Monat)."""
    s, e = tn.span(t)
    md = monthrange(year, month)[1]
    first, last = date(year, month, 1), date(year, month, md)
    a, b = max(s, first), min(e or date.max, last)
    out = {"cold": Fraction(0), "bk": Fraction(0), "hk": Fraction(0)}
    day = a
    while day <= b:
        term = tn.term_at(t["terms"], day)
        if term:
            out["cold"] += Fraction(term["cold_rent_cents"], md)
            out["bk"] += Fraction(term["bk_advance_cents"], md)
            out["hk"] += Fraction(term["hk_advance_cents"], md)
        day += timedelta(days=1)
    res = {k: round(v) for k, v in out.items()}
    res["total"] = res["cold"] + res["bk"] + res["hk"]
    res["days"] = max(0, (b - a).days + 1)
    return res


def payment_month(day: str) -> tuple[int, int]:
    d = date.fromisoformat(day)
    if d.day >= EARLY_DAY:
        nxt = d.replace(day=1) + timedelta(days=32)
        return nxt.year, nxt.month
    return d.year, d.month


def payments(conn: sqlite3.Connection, tenancy_id: int) -> list[dict[str, Any]]:
    out = []
    for tx in rows(conn.execute("SELECT id, booking_date, amount_cents, counterparty, purpose, status FROM transactions "
                                "WHERE tenancy_id = ? AND kind = 'einnahme' AND status IN ('done', 'auto') ORDER BY booking_date",
                                (tenancy_id,))):
        y, m = payment_month(tx["booking_date"])
        out.append({"source": "konto", "id": tx["id"], "date": tx["booking_date"], "amount": tx["amount_cents"], "year": y, "month": m,
                    "text": f"{tx['counterparty']} {tx['purpose']}".strip()[:120], "unconfirmed": tx["status"] == "auto"})
    for r in rows(conn.execute("SELECT * FROM rent_receipts WHERE tenancy_id = ? ORDER BY paid_on", (tenancy_id,))):
        if r["for_month"]:
            y, m = int(r["for_month"][:4]), int(r["for_month"][5:7])
        else:
            y, m = payment_month(r["paid_on"])
        out.append({"source": "hand", "id": r["id"], "date": r["paid_on"], "amount": r["amount_cents"], "year": y, "month": m,
                    "text": r["note"] or "von Hand erfasst", "unconfirmed": False})
    return sorted(out, key=lambda p: p["date"])


def overview(conn: sqlite3.Connection, year: int, property_id: int | None = None) -> dict[str, Any]:
    link_unassigned(conn, property_id)
    start, end = date(year, 1, 1), date(year, 12, 31)
    today = date.today()
    items = []
    for t in tn.list_all(conn, property_id):
        s, e = tn.span(t)
        if s > end or (e and e < start):
            continue
        pays = [p for p in payments(conn, t["id"]) if p["year"] == year]
        months, cum_soll, cum_ist = [], 0, 0
        for m in range(1, 13):
            soll = monthly_soll(t, year, m)
            ist = sum(p["amount"] for p in pays if p["month"] == m)
            cum_soll += soll["total"]
            cum_ist += ist
            saldo = cum_ist - cum_soll
            future = date(year, m, 1) > today
            if soll["total"] == 0 and ist == 0:
                st = "frei"
            elif future and ist == 0:
                st = "kuenftig"
            elif ist >= soll["total"]:
                st = "bezahlt"
            elif ist > 0:
                st = "teilweise"
            else:
                st = "offen"
            months.append({"month": m, "soll": soll, "ist": ist, "saldo": saldo, "status": st})
        due_months = [x for x in months if date(year, x["month"], 1) <= today]
        soll_due = sum(x["soll"]["total"] for x in due_months)
        ist_total = sum(p["amount"] for p in pays)
        balance = ist_total - soll_due
        items.append({
            "tenancy_id": t["id"], "names": t["names"], "property_id": t["property_id"], "property_name": t["property_name"],
            "unit_id": t["unit_id"], "unit_name": t["unit_name"], "status": t["status"], "contract_start": t["contract_start"],
            "contract_end": t["contract_end"] or t["move_out"], "months": months, "payments": pays,
            "soll_year": sum(x["soll"]["total"] for x in months), "soll_due": soll_due,
            "soll_parts": {k: sum(x["soll"][k] for x in months) for k in ("cold", "bk", "hk")},
            "ist": ist_total, "balance": balance, "arrears": max(0, -balance), "credit": max(0, balance),
        })
    items.sort(key=lambda x: (x["property_name"], x["unit_id"] or 0, x["contract_start"]))
    unassigned = rows(conn.execute(
        "SELECT id, booking_date, amount_cents, counterparty, purpose, property_id, unit_id FROM transactions "
        "WHERE kind = 'einnahme' AND status IN ('done', 'auto') AND tenancy_id IS NULL AND substr(booking_date, 1, 4) = ? "
        "AND (? IS NULL OR property_id = ? OR property_id IS NULL) ORDER BY booking_date", (str(year), property_id, property_id)))
    open_in = conn.execute("SELECT COUNT(*) FROM transactions WHERE amount_cents > 0 AND status = 'open' AND substr(booking_date, 1, 4) = ?",
                           (str(year),)).fetchone()[0]
    return {
        "year": year, "items": items, "unassigned": unassigned, "open_incoming": open_in,
        "totals": {k: sum(i[k] for i in items) for k in ("soll_year", "soll_due", "ist", "arrears", "credit")},
    }
