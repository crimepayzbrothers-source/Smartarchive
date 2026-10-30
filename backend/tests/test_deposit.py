"""Mietkaution: Arten, Höhe als Vielfaches der Kaltmiete, Raten nach § 551 BGB, Urkunden, Rückgabe."""
from __future__ import annotations

from conftest import HEADERS
from test_income import _setup


def _put(client, tid, body, code=200):
    r = client.put(f"/api/tenancies/{tid}/deposit", json=body, headers=HEADERS)
    assert r.status_code == code, r.text
    return r.json()


def test_cash_deposit_installments_and_payments(client):
    pid, units, t, acc = _setup(client)
    tid = t["so"]  # Julia Sommer, Beginn 15.04.2026, Kaltmiete 350 €
    d = client.get(f"/api/tenancies/{tid}/deposit").json()
    assert d["status"] == "fehlt" and d["cold_rent"] == 35000 and {k["key"] for k in d["kinds"]} >= {"bar", "versicherung", "keine"}

    d = _put(client, tid, {"kind": "bar", "months": 3, "installments": True, "account": "Kautionskonto Sparkasse DE00 1234"})
    assert d["amount_cents"] == 105000 and d["cap"] == 105000 and d["over_cap"] is False
    # Drei Raten: Mietbeginn, dann mit den beiden folgenden Mieten (3. Werktag Mai und Juni 2026)
    assert [(r["due"], r["amount"]) for r in d["schedule"]] == [("2026-04-15", 35000), ("2026-05-06", 35000), ("2026-06-03", 35000)]
    assert d["status"] == "offen" and d["overdue"] is True

    r = client.post(f"/api/tenancies/{tid}/deposit/payments", json={"paid_on": "2026-04-15", "amount_cents": 35000}, headers=HEADERS)
    d = r.json()
    assert d["status"] == "teilweise" and d["paid"] == 35000 and [x["paid"] for x in d["schedule"]] == [35000, 0, 0]
    client.post(f"/api/tenancies/{tid}/deposit/payments", json={"paid_on": "2026-05-04", "amount_cents": 70000}, headers=HEADERS)
    d = client.get(f"/api/tenancies/{tid}/deposit").json()
    assert d["status"] == "vollstaendig" and d["overdue"] is False and d["due_now"] == 0

    # Liste der Mietverhältnisse zeigt den Kurzstatus
    lst = {x["id"]: x for x in client.get("/api/tenancies").json()}
    assert lst[tid]["deposit"]["status"] == "vollstaendig" and lst[t["eg"]]["deposit"]["status"] == "fehlt"

    # Zwei Kaltmieten statt drei; Zahlung löschen
    d = _put(client, tid, {"kind": "bar", "months": 2})
    assert d["amount_cents"] == 70000 and len(d["schedule"]) == 1 and d["status"] == "vollstaendig"
    d = client.delete(f"/api/deposit-payments/{d['payments'][1]['id']}", headers=HEADERS).json()
    assert d["status"] == "teilweise" and d["paid"] == 35000

    # Rückgabe mit Einbehalt
    d = _put(client, tid, {"kind": "bar", "months": 2, "returned_on": "2026-10-01", "returned_cents": 30000, "retained_cents": 5000,
                           "note": "Einbehalt Reinigung"})
    assert d["status"] == "abgerechnet" and d["retained_cents"] == 5000


def test_guarantee_insurance_cap_and_none(client):
    pid, units, t, acc = _setup(client)
    tid = t["mm"]  # Kaltmiete 500 €
    d = _put(client, tid, {"kind": "versicherung", "amount_cents": 150000, "provider": "Beispiel Kautionsversicherung AG",
                           "reference": "KV-123", "installments": True})
    assert d["status"] == "offen" and d["installments"] == 0 and d["schedule"] == [] and d["overdue"] is True
    d = _put(client, tid, {"kind": "versicherung", "amount_cents": 150000, "provider": "Beispiel AG", "document_received_on": "2021-04-20"})
    assert d["status"] == "liegt_vor" and d["status_label"] == "Urkunde liegt vor" and d["overdue"] is False
    # Keine Einzahlungen bei Bürgschaft/Versicherung
    r = client.post(f"/api/tenancies/{tid}/deposit/payments", json={"paid_on": "2021-05-01", "amount_cents": 1000}, headers=HEADERS)
    assert r.status_code == 400

    # Über drei Nettokaltmieten: gespeichert, aber als Hinweis markiert
    d = _put(client, tid, {"kind": "buergschaft", "amount_cents": 160000})
    assert d["over_cap"] is True and d["cap"] == 150000

    d = _put(client, tid, {"kind": "keine", "amount_cents": 5000})
    assert d["status"] == "keine" and d["amount_cents"] == 0
    _put(client, tid, {"kind": "bar"}, 400)                    # Höhe fehlt
    _put(client, tid, {"kind": "gold", "amount_cents": 1}, 400)
    _put(client, tid, {"kind": "bar", "amount_cents": 1000, "returned_on": "2020-01-01"}, 400)  # vor Mietbeginn
