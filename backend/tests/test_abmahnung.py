"""Abmahnungen: Vorfälle Pflicht, Frist je Anlass, verspätete Zahlungen als Vorschlag, PDF und Ablage."""
from __future__ import annotations

import io
from datetime import date

from conftest import HEADERS
from pypdf import PdfReader
from test_income import _import, _setup


def _text(pdf: bytes) -> str:
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_third_workday_rlp():
    from app.abmahnung import third_workday
    assert third_workday(2026, 7) == date(2026, 7, 3)    # Mi, Do, Fr
    assert third_workday(2026, 8) == date(2026, 8, 5)    # Samstag zählt nicht (BGH VIII ZR 129/09)
    assert third_workday(2026, 4) == date(2026, 4, 7)    # Karfreitag und Ostermontag
    assert third_workday(2026, 11) == date(2026, 11, 4)  # Allerheiligen (RLP)
    assert third_workday(2026, 1) == date(2026, 1, 6)    # Neujahr; Hl. Drei Könige ist in RLP kein Feiertag


def test_late_payments_suggested(client):
    pid, units, t, acc = _setup(client)
    _import(client, acc, ["06.07.2026;06.07.2026;Gutschrift;Otto Weber Miete;560,00;EUR;DE00;",    # 1 Werktag nach Fälligkeit: Laufzeit
                          "15.08.2026;15.08.2026;Gutschrift;Otto Weber Miete;560,00;EUR;DE00;",    # deutlich verspätet
                          "02.09.2026;02.09.2026;Gutschrift;Otto Weber Miete;560,00;EUR;DE00;"])   # pünktlich
    rv = client.get("/api/transactions/review").json()
    for x in rv["auto"] + rv["open"]:
        client.post(f"/api/transactions/{x['id']}/decide", json={"kind": "einnahme", "tenancy_id": t["eg"]}, headers=HEADERS)
    st = client.get(f"/api/tenancies/{t['eg']}/warnings").json()
    assert [(x["month"], x["date"], x["deadline"]) for x in st["late_payments"]] == [("2026-08", "2026-08-15", "2026-08-05")]
    assert "erst am 15.08.2026" in st["late_payments"][0]["text"]
    assert {r["key"] for r in st["reasons"]} >= {"unpuenktlich", "ruhestoerung", "untervermietung", "tierhaltung", "hausordnung"}
    assert st["defaults"]["delivery"] == "einwurf"


def test_create_warning_validation_pdf_and_archive(client):
    pid, units, t, acc = _setup(client)
    client.put("/api/bk/landlord", json={"name": "Can Beispiel", "address": "Musterweg 1, 55566 Bad Sobernheim"}, headers=HEADERS)
    url = f"/api/tenancies/{t['eg']}/warnings"
    base = {"reason": "ruhestoerung", "letter_date": "2026-10-20", "delivery": "einwurf",
            "incidents": [{"date": "2026-10-10", "time": "23:30–01:15", "text": "Laute Musik, Nachbarin OG hat geklingelt."},
                          {"date": "", "time": "", "text": ""}]}
    # Ohne konkreten Vorfall kein Schreiben; Vorfall nach Briefdatum abgelehnt
    r = client.post(url, json={**base, "incidents": []}, headers=HEADERS)
    assert r.status_code == 400 and "Vorfall" in r.json()["error"]
    r = client.post(url, json={**base, "incidents": [{"date": "2026-10-25", "text": "x"}]}, headers=HEADERS)
    assert r.status_code == 400 and "nach dem Briefdatum" in r.json()["error"]
    r = client.post(url, json={**base, "incidents": [{"date": "2026-10-10", "text": ""}]}, headers=HEADERS)
    assert r.status_code == 400 and "Beschreibung" in r.json()["error"]

    pv = client.post(f"{url}/preview", json=base, headers=HEADERS)
    assert pv.status_code == 200
    txt = _text(pv.content)
    assert "Abmahnung – Ruhestörung" in txt and "Per Einwurf-Einschreiben" in txt and "10.10.2026" in txt and "23:30" in txt
    assert "Laute Musik" in txt and "künftig zu unterlassen" in txt and "kündigen" in txt
    assert client.get(url).json()["letters"] == []

    # Anlass mit Frist: ohne Frist Fehler, mit Frist eingesetzt
    tb = {**base, "reason": "tierhaltung", "incidents": [{"date": "2026-10-12", "text": "Hund in der Wohnung gesehen."}]}
    r = client.post(url, json=tb, headers=HEADERS)
    assert r.status_code == 400 and "Frist" in r.json()["error"]
    r = client.post(url, json={**tb, "deadline": "2026-10-19"}, headers=HEADERS)
    assert r.status_code == 400 and "nach dem Briefdatum" in r.json()["error"]
    st = client.post(url, json={**tb, "deadline": "2026-11-20", "delivery": "bote"}, headers=HEADERS).json()
    w = st["letters"][0]
    assert (w["reason"], w["deadline"], w["delivery_label"], w["document_id"] is not None) == ("tierhaltung", "2026-11-20", "Bote", True)
    txt = _text(client.get(f"/api/warnings/{w['id']}/pdf").content)
    assert "bis zum 20.11.2026 zu beenden" in txt and "Per Boten" in txt and "{frist}" not in txt

    # Eigener Text ohne Frist-Verweis: Frist entfällt
    st = client.post(url, json={**base, "demand": "Bitte halten Sie die Nachtruhe ab 22 Uhr ein.", "delivery": "brief"},
                     headers=HEADERS).json()
    w2 = st["letters"][0]
    assert w2["deadline"] is None
    txt = _text(client.get(f"/api/warnings/{w2['id']}/pdf").content)
    assert "Nachtruhe ab 22 Uhr" in txt and "Per " not in txt

    docs = client.get(f"/api/tenancies/{t['eg']}").json()["documents"]
    assert [d["doc_type"] for d in docs] == ["Abmahnung", "Abmahnung"]
    assert client.delete(f"/api/warnings/{w2['id']}", headers=HEADERS).json() == {"ok": True}
    assert len(client.get(url).json()["letters"]) == 1
    assert len(client.get(f"/api/tenancies/{t['eg']}").json()["documents"]) == 2   # PDF bleibt im Archiv
    assert client.post(url, json={**base, "reason": "unbekannt"}, headers=HEADERS).status_code == 400
