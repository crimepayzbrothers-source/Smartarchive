"""Mahnwesen: offene Monate aus den Einnahmen, Zahlungserinnerung/Mahnung als PDF, Ablage beim Mieter."""
from __future__ import annotations

import datetime as dt
import io

import pytest
from conftest import HEADERS
from pypdf import PdfReader
from test_income import _import, _setup


@pytest.fixture
def today(monkeypatch):
    from app import dunning, income

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 30)
    monkeypatch.setattr(income, "date", FakeDate)
    monkeypatch.setattr(dunning, "date", FakeDate)


def _text(pdf: bytes) -> str:
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def _miss(client, tid, month, paid=0):
    r = client.put("/api/income/exceptions", json={"tenancy_id": tid, "month": month, "paid_cents": paid}, headers=HEADERS)
    assert r.status_code == 200, r.text


def test_open_months_threshold_and_credit(client, today):
    pid, units, t, acc = _setup(client)
    st = client.get(f"/api/tenancies/{t['eg']}/dunning").json()
    assert st["open"] == [] and st["total"] == 0 and st["threshold"]["reached"] is False  # alles als eingegangen verbucht
    assert st["defaults"]["due_date"] == "2026-11-13" and st["defaults"]["salutation"] == "Guten Tag Otto Weber,"

    _miss(client, t["eg"], "2026-07")
    _miss(client, t["eg"], "2026-08", 30000)
    st = client.get(f"/api/tenancies/{t['eg']}/dunning").json()
    assert [(i["month"], i["open"]) for i in st["open"]] == [("2026-07", 56000), ("2026-08", 26000)]
    th = st["threshold"]
    # Zwei aufeinanderfolgende Monate mit zusammen mehr als einer Monatsmiete Rückstand → rechnerisch erreicht
    assert (th["monthly_rent"], th["consecutive"], th["two_rents"], th["reached"]) == (56000, True, False, True)

    # Überzahlung im September (Juli nachgezahlt) wird auf den ältesten Rückstand angerechnet
    _import(client, acc, ["02.09.2026;02.09.2026;Gutschrift;Otto Weber Miete Sept + Juli;1120,00;EUR;DE00;"])
    for x in client.get("/api/transactions/review").json()["auto"] + client.get("/api/transactions/review").json()["open"]:
        client.post(f"/api/transactions/{x['id']}/decide", json={"kind": "einnahme", "tenancy_id": t["eg"]}, headers=HEADERS)
    st = client.get(f"/api/tenancies/{t['eg']}/dunning").json()
    assert [(i["month"], i["open"]) for i in st["open"]] == [("2026-08", 26000)]
    assert st["threshold"]["reached"] is False


def test_create_letters_archive_and_income_marker(client, today):
    pid, units, t, acc = _setup(client)
    client.put("/api/bk/landlord", json={"name": "Can Beispiel", "address": "Musterweg 1, 55566 Bad Sobernheim",
                                         "iban": "DE89 3704 0044 0532 0130 00", "bank": "Commerzbank", "place": "Bad Sobernheim"},
               headers=HEADERS)
    _miss(client, t["eg"], "2026-07")
    _miss(client, t["eg"], "2026-08", 30000)
    base = {"kind": "erinnerung", "letter_date": "2026-09-05", "due_date": "2026-09-19", "months": ["2026-07", "2026-08"]}

    # Vorschau speichert nichts
    r = client.post(f"/api/tenancies/{t['eg']}/dunning/preview", json=base, headers=HEADERS)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    txt = _text(r.content)
    assert "Zahlungserinnerung" in txt and "820,00 €" in txt and "19.09.2026" in txt and "DE89 3704" in txt
    assert "Guten Tag Otto Weber" in txt and "Wohnung EG" in txt
    assert client.get(f"/api/tenancies/{t['eg']}/dunning").json()["letters"] == []

    # Fehler: Frist vor Briefdatum, Monat ohne Rückstand, keine Monate
    bad = client.post(f"/api/tenancies/{t['eg']}/dunning", json={**base, "due_date": "2026-09-01"}, headers=HEADERS)
    assert bad.status_code == 400 and "Zahlungsfrist" in bad.json()["error"]
    bad = client.post(f"/api/tenancies/{t['eg']}/dunning", json={**base, "months": ["2026-06"]}, headers=HEADERS)
    assert bad.status_code == 400 and "Juni 2026" in bad.json()["error"]
    assert client.post(f"/api/tenancies/{t['eg']}/dunning", json={**base, "months": []}, headers=HEADERS).status_code == 400

    st = client.post(f"/api/tenancies/{t['eg']}/dunning", json=base, headers=HEADERS).json()
    first = st["letters"][0]
    assert (first["kind"], first["total_cents"], first["document_id"] is not None) == ("erinnerung", 82000, True)
    # Im Archiv beim Mieter abgelegt
    docs = client.get(f"/api/tenancies/{t['eg']}").json()["documents"]
    assert [d["doc_type"] for d in docs] == ["Zahlungserinnerung"]
    d = client.get(f"/api/documents/{docs[0]['id']}").json()
    assert d["property_id"] == pid and d["unit_id"] == units["EG"]

    # Mahnung nimmt Bezug auf die Erinnerung; nur Juli
    st = client.post(f"/api/tenancies/{t['eg']}/dunning", json={**base, "kind": "mahnung", "letter_date": "2026-09-25",
                                                                 "due_date": "2026-10-05", "months": ["2026-07"],
                                                                 "extra_text": "Bei Fragen erreichen Sie mich unter 0170 000000."},
                     headers=HEADERS).json()
    m = st["letters"][0]
    txt = _text(client.get(f"/api/dunning/{m['id']}/pdf").content)
    assert "Mahnung" in txt and "Zahlungserinnerung vom 05.09.2026" in txt and "560,00 €" in txt and "§ 556b" in txt
    assert "0170 000000" in txt

    # Einnahmen zeigen, wann ein Monat angemahnt wurde
    eg = next(i for i in client.get("/api/income", params={"year": 2026}).json()["items"] if i["tenancy_id"] == t["eg"])
    assert [x["kind"] for x in eg["months"][6]["dunned"]] == ["erinnerung", "mahnung"]
    assert [x["kind"] for x in eg["months"][7]["dunned"]] == ["erinnerung"]

    # Löschen entfernt nur den Eintrag im Mahnwesen; das abgelegte PDF bleibt im Archiv
    assert client.delete(f"/api/dunning/{m['id']}", headers=HEADERS).json() == {"ok": True}
    assert len(client.get(f"/api/tenancies/{t['eg']}/dunning").json()["letters"]) == 1
    assert len(client.get(f"/api/tenancies/{t['eg']}").json()["documents"]) == 2
    assert client.get(f"/api/dunning/{m['id']}/pdf").status_code == 404
