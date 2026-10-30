"""Mietkaution je Mietverhältnis.

Arten:
- bar:          Barkaution bzw. Überweisung auf ein Kautionskonto – Einzahlungen werden erfasst, auf Wunsch in drei Raten
                (§ 551 Abs. 2 BGB: erste Rate zu Beginn des Mietverhältnisses, die weiteren mit den beiden folgenden Mieten).
- sparbuch:     verpfändetes Sparbuch/Kautionskonto des Mieters – maßgeblich ist die Verpfändungserklärung.
- buergschaft:  Bankbürgschaft – maßgeblich ist die Bürgschaftsurkunde.
- versicherung: Kautionsversicherung (Bürgschaft eines Versicherers) – maßgeblich ist die Urkunde/Police.
- keine:        keine Kaution vereinbart.

Höchstbetrag: drei Monatsmieten ohne Betriebskostenvorauszahlungen (§ 551 Abs. 1 BGB). Ein höherer Betrag wird
gespeichert, aber deutlich als Hinweis gezeigt – die App bewertet die Vereinbarung nicht.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from . import tenancy as tn
from .abmahnung import third_workday
from .db import audit, now, row, rows, tx

KINDS = {
    "bar": "Barkaution / Kautionskonto",
    "sparbuch": "Verpfändetes Sparbuch",
    "buergschaft": "Bankbürgschaft",
    "versicherung": "Kautionsversicherung",
    "keine": "Keine Kaution",
}
CASH = {"bar"}
DOCUMENT = {"sparbuch", "buergschaft", "versicherung"}
STATUS = {"offen": "offen", "teilweise": "teilweise gezahlt", "vollstaendig": "vollständig", "liegt_vor": "Urkunde liegt vor",
          "zurueck": "zurückgegeben", "abgerechnet": "abgerechnet", "keine": "keine Kaution", "fehlt": "nicht erfasst"}


class DepositError(ValueError):
    pass


def cold_rent_at_start(t: dict[str, Any]) -> int:
    term = tn.term_at(t["terms"], tn.span(t)[0])
    return term["cold_rent_cents"] if term else 0


def schedule(t: dict[str, Any], amount: int, installments: bool) -> list[dict[str, Any]]:
    start = tn.span(t)[0]
    if not installments or amount <= 0:
        return [{"no": 1, "due": start.isoformat(), "amount": amount}]
    parts = [amount // 3] * 3
    for i in range(amount - sum(parts)):
        parts[i] += 1
    dues = [start]
    y, m = start.year, start.month
    for _ in range(2):
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        dues.append(third_workday(y, m))
    return [{"no": i + 1, "due": d.isoformat(), "amount": a} for i, (d, a) in enumerate(zip(dues, parts))]


def _summary(t: dict[str, Any], d: dict[str, Any] | None, payments: list[dict[str, Any]], today: date) -> dict[str, Any]:
    if not d:
        return {"status": "fehlt", "status_label": STATUS["fehlt"]}
    paid = sum(p["amount_cents"] for p in payments)
    cap = 3 * cold_rent_at_start(t)
    out: dict[str, Any] = {**d, "kind_label": KINDS[d["kind"]], "paid": paid, "cap": cap,
                           "over_cap": d["kind"] != "keine" and cap > 0 and d["amount_cents"] > cap, "schedule": [], "due_now": 0}
    if d["kind"] == "keine":
        st = "keine"
    elif d["returned_on"]:
        st = "abgerechnet" if (d["retained_cents"] or 0) > 0 else "zurueck"
    elif d["kind"] in CASH:
        sched = schedule(t, d["amount_cents"], bool(d["installments"]))
        covered = paid
        for r in sched:
            r["paid"] = min(covered, r["amount"])
            covered -= r["paid"]
            r["overdue"] = r["paid"] < r["amount"] and date.fromisoformat(r["due"]) < today
        out["schedule"] = sched
        out["due_now"] = sum(r["amount"] - r["paid"] for r in sched if date.fromisoformat(r["due"]) <= today)
        st = "vollstaendig" if paid >= d["amount_cents"] > 0 else "teilweise" if paid > 0 else "offen"
    else:
        st = "liegt_vor" if d["document_received_on"] else "offen"
    out["status"], out["status_label"] = st, STATUS[st]
    out["overdue"] = any(r.get("overdue") for r in out["schedule"]) or (
        d["kind"] in DOCUMENT and st == "offen" and tn.span(t)[0] < today)
    return out


def get(conn: sqlite3.Connection, tenancy_id: int, today: date | None = None) -> dict[str, Any]:
    t = tn.load(conn, tenancy_id)
    d = row(conn.execute("SELECT * FROM deposits WHERE tenancy_id = ?", (tenancy_id,)))
    payments = rows(conn.execute("SELECT id, paid_on, amount_cents, note FROM deposit_payments WHERE tenancy_id = ? ORDER BY paid_on, id",
                                 (tenancy_id,)))
    out = _summary(t, d, payments, today or date.today())
    out["payments"] = payments
    out["kinds"] = [{"key": k, "label": v} for k, v in KINDS.items()]
    out["cold_rent"] = cold_rent_at_start(t)
    return out


def brief(conn: sqlite3.Connection, t: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Kurzstatus für Listen."""
    d = row(conn.execute("SELECT * FROM deposits WHERE tenancy_id = ?", (t["id"],)))
    payments = rows(conn.execute("SELECT amount_cents FROM deposit_payments WHERE tenancy_id = ?", (t["id"],))) if d else []
    s = _summary(t, d, payments, today or date.today())
    return {k: s.get(k) for k in ("status", "status_label", "kind", "kind_label", "amount_cents", "paid", "overdue")}


FIELDS = ("kind", "months", "amount_cents", "installments", "account", "provider", "reference", "document_received_on",
          "returned_on", "returned_cents", "retained_cents", "note")


def save(conn: sqlite3.Connection, tenancy_id: int, data: dict[str, Any]) -> None:
    t = tn.load(conn, tenancy_id)
    kind = data["kind"]
    if kind not in KINDS:
        raise DepositError("Unbekannte Art der Kaution.")
    if kind == "keine":
        data = {**{k: None for k in FIELDS}, "kind": "keine", "amount_cents": 0, "installments": 0, "account": "", "provider": "",
                "reference": "", "note": data.get("note") or ""}
    else:
        if data.get("months"):
            cold = cold_rent_at_start(t)
            if not cold:
                raise DepositError("Für die Berechnung fehlt die Kaltmiete zu Mietbeginn.")
            data["amount_cents"] = round(cold * data["months"])
        if not data.get("amount_cents") or data["amount_cents"] <= 0:
            raise DepositError("Bitte die Höhe der Kaution angeben.")
        if kind not in CASH:
            data["installments"] = 0
        if data.get("returned_on"):
            ret, kept = data.get("returned_cents") or 0, data.get("retained_cents") or 0
            if ret < 0 or kept < 0:
                raise DepositError("Beträge bei der Rückgabe dürfen nicht negativ sein.")
            if data["returned_on"] < t["contract_start"]:
                raise DepositError("Die Rückgabe kann nicht vor Mietbeginn liegen.")
        else:
            data["returned_cents"] = data["retained_cents"] = None
    values = {k: data.get(k) for k in FIELDS}
    values["installments"] = int(bool(values["installments"]))
    for k in ("account", "provider", "reference", "note"):
        values[k] = (values[k] or "").strip()
    with tx(conn):
        conn.execute(
            f"INSERT INTO deposits(tenancy_id, {', '.join(FIELDS)}, updated_at) VALUES(?, {', '.join('?' * len(FIELDS))}, ?) "
            f"ON CONFLICT(tenancy_id) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in FIELDS)}, updated_at = excluded.updated_at",
            (tenancy_id, *values.values(), now()))
        audit(conn, "saved", "deposit", tenancy_id, {"art": kind, "betrag": values["amount_cents"]})


def add_payment(conn: sqlite3.Connection, tenancy_id: int, paid_on: str, amount: int, note: str) -> int:
    d = row(conn.execute("SELECT kind FROM deposits WHERE tenancy_id = ?", (tenancy_id,)))
    if not d or d["kind"] not in CASH:
        raise DepositError("Einzahlungen gibt es nur bei einer Barkaution bzw. einem Kautionskonto.")
    if amount <= 0:
        raise DepositError("Bitte einen Betrag angeben.")
    with tx(conn):
        pid = conn.execute("INSERT INTO deposit_payments(tenancy_id, paid_on, amount_cents, note, created_at) VALUES(?,?,?,?,?)",
                           (tenancy_id, paid_on, amount, note.strip(), now())).lastrowid
        audit(conn, "created", "deposit_payment", pid, {"betrag": amount})
    return pid


def delete_payment(conn: sqlite3.Connection, payment_id: int) -> int:
    p = row(conn.execute("SELECT * FROM deposit_payments WHERE id = ?", (payment_id,)))
    if not p:
        raise KeyError(payment_id)
    with tx(conn):
        conn.execute("DELETE FROM deposit_payments WHERE id = ?", (payment_id,))
        audit(conn, "deleted", "deposit_payment", payment_id, {"betrag": p["amount_cents"]})
    return p["tenancy_id"]
