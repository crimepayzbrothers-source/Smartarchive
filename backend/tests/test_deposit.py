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


def _text(pdf: bytes) -> str:
    import io
    from pypdf import PdfReader
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_deposit_reminder_cash_and_guarantee(client):
    pid, units, t, acc = _setup(client)
    client.put("/api/bk/landlord", json={"name": "Can Beispiel", "address": "Musterweg 1, 55566 Bad Sobernheim", "iban": "DE89 3704"},
               headers=HEADERS)
    tid = t["so"]
    # Ohne erfasste Kaution kein Schreiben
    r = client.post(f"/api/tenancies/{tid}/deposit/letters", json={"kind": "erinnerung", "letter_date": "2026-05-10",
                                                                    "due_date": "2026-05-24"}, headers=HEADERS)
    assert r.status_code == 400 and "erfassen" in r.json()["error"]

    _put(client, tid, {"kind": "bar", "months": 3, "installments": True, "account": "Sparkasse, Kautionskonto DE11 2222"})
    client.post(f"/api/tenancies/{tid}/deposit/payments", json={"paid_on": "2026-04-15", "amount_cents": 35000}, headers=HEADERS)
    d = client.get(f"/api/tenancies/{tid}/deposit").json()
    # Heute (30.10.2026) sind Rate 2 und 3 fällig und offen: 700 € = zwei Kaltmieten → rechnerische Schwelle § 569 Abs. 2a
    assert d["remind"]["possible"] and d["remind"]["total"] == 70000 and d["remind"]["threshold"] is True

    base = {"kind": "erinnerung", "letter_date": "2026-05-10", "due_date": "2026-05-24"}
    pv = client.post(f"/api/tenancies/{tid}/deposit/letters/preview", json=base, headers=HEADERS)
    txt = _text(pv.content)
    # Zum Briefdatum 10.05. ist nur Rate 2 (fällig 06.05.) offen
    assert "Zahlungserinnerung – Mietkaution" in txt and "2. Rate" in txt and "3. Rate" not in txt and "350,00 €" in txt
    assert "drei monatlichen Teilzahlungen" in txt and "Kautionskonto DE11 2222" in txt and "24.05.2026" in txt
    assert client.post(f"/api/tenancies/{tid}/deposit/letters", json={**base, "due_date": "2026-05-01"},
                       headers=HEADERS).status_code == 400
    # Vor Fälligkeit der 2. Rate ist nichts offen
    r = client.post(f"/api/tenancies/{tid}/deposit/letters", json={**base, "letter_date": "2026-04-20", "due_date": "2026-05-01"},
                    headers=HEADERS)
    assert r.status_code == 400 and "nichts offen" in r.json()["error"]

    d = client.post(f"/api/tenancies/{tid}/deposit/letters", json=base, headers=HEADERS).json()
    assert [(x["kind"], x["total_cents"]) for x in d["letters"]] == [("erinnerung", 35000)]
    d = client.post(f"/api/tenancies/{tid}/deposit/letters", json={"kind": "mahnung", "letter_date": "2026-06-20", "due_date": "2026-07-01"},
                    headers=HEADERS).json()
    m = d["letters"][0]
    txt = _text(client.get(f"/api/deposit-letters/{m['id']}/pdf").content)
    assert "Mahnung – Mietkaution" in txt and "Zahlungserinnerung vom 10.05.2026" in txt and "700,00 €" in txt
    docs = client.get(f"/api/tenancies/{tid}").json()["documents"]
    assert sorted(x["doc_type"] for x in docs) == ["Mahnung Kaution", "Zahlungserinnerung Kaution"]
    d = client.delete(f"/api/deposit-letters/{m['id']}", headers=HEADERS).json()
    assert len(d["letters"]) == 1

    # Kautionsversicherung ohne Urkunde → Aufforderung zur Vorlage; mit Urkunde → nichts zu mahnen
    tid2 = t["mm"]
    _put(client, tid2, {"kind": "versicherung", "amount_cents": 150000, "provider": "Beispiel AG", "reference": "KV-9"})
    pv = client.post(f"/api/tenancies/{tid2}/deposit/letters/preview", json={"kind": "erinnerung", "letter_date": "2026-10-30",
                                                                             "due_date": "2026-11-13"}, headers=HEADERS)
    txt = _text(pv.content)
    txt = " ".join(txt.split())
    assert "Form einer Kautionsversicherung (Beispiel AG, Nr. KV-9)" in txt and "Bürgschaftsurkunde" in txt and "13.11.2026" in txt
    _put(client, tid2, {"kind": "versicherung", "amount_cents": 150000, "document_received_on": "2021-05-01"})
    assert client.get(f"/api/tenancies/{tid2}/deposit").json()["remind"]["possible"] is False
