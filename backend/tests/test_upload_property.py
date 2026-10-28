"""Upload mit gewähltem Mietobjekt und Datumssicherheit."""
from __future__ import annotations

import pytest

from conftest import HEADERS, make_pdf, run_worker

NO_ADDR = ["Sanitaer Schmidt GmbH", "Rechnungsnummer: RE-1", "Rechnungsdatum: 14.10.2026",
           "Reparatur Spuelkasten     120,00", "Rechnungsbetrag 145,00 EUR"]


def _props(client):
    a = client.post("/api/properties", json={"name": "Hauptstraße 15", "address": "Hauptstraße 15, 55758 Schmidt-Hachenbach"},
                    headers=HEADERS).json()["id"]
    b = client.post("/api/properties", json={"name": "Berliner Straße 23", "address": "Berliner Straße 23, 55566 Bad Sobernheim"},
                    headers=HEADERS).json()["id"]
    return a, b


def _upload(client, lines, area="vermietung", property_id=None, name="r.pdf"):
    data = {"area": area}
    if property_id:
        data["property_id"] = str(property_id)
    return client.post("/api/upload", data=data, files=[("files", (name, make_pdf(lines), "application/pdf"))], headers=HEADERS)


def test_upload_with_chosen_property_files_directly(client):
    a, b = _props(client)
    doc = _upload(client, NO_ADDR, property_id=b).json()["created"][0]
    run_worker()
    d = client.get(f"/api/documents/{doc}").json()
    assert d["status"] == "filed" and d["property_id"] == b and d["area_key"] == "vermietung"
    assert [(e["amount_cents"], e["status"], e["property_id"]) for e in d["expenses"]] == [(14500, "bestaetigt", b)]
    assert [i["id"] for i in client.get("/api/inbox").json()["auto_filed"]] == [doc]  # zur Kontrolle
    # auch ohne Rechnung (z. B. Mietvertrag) landet der Beleg beim Objekt
    mv = _upload(client, ["Mietvertrag", "zwischen A und B", "Datum: 01.03.2026"], property_id=a).json()["created"][0]
    run_worker()
    d = client.get(f"/api/documents/{mv}").json()
    assert d["property_id"] == a and d["category_path"] == "Vermietung › Mietverträge und Mieter" and d["expenses"] == []


def test_property_only_in_rental_area(client):
    a, _ = _props(client)
    r = _upload(client, NO_ADDR, area="privat", property_id=a)
    assert r.status_code == 400 and "Vermietung" in r.json()["error"]
    assert _upload(client, NO_ADDR, property_id=999).status_code == 400


@pytest.mark.parametrize("text,date,sure", [
    ("Rechnungsdatum: 14.10.2026\nLieferdatum 10.10.2026\nzahlbar bis 28.10.2026", "2026-10-14", True),
    ("Kassenbon 20.10.2026 16:05", "2026-10-20", True),                      # nur ein Datum
    ("Termin 03.09.2026\nAuftrag 01.08.2026\nBetrag 50,00", "2026-09-03", False),  # mehrere, unbeschriftet
    ("Datum: 01.02.2026\nDatum: 05.02.2026", "2026-02-01", False),           # zwei widersprüchliche Beschriftungen
    ("zahlbar bis 30.11.2026", "2026-11-30", False),                         # nur ein Fälligkeitsdatum
])
def test_date_certainty(text, date, sure):
    from app.extract import date_analysis
    a = date_analysis(text)
    assert (a["date"], a["sure"]) == (date, sure)


def test_unsure_date_asks_with_suggestion(client):
    a, _ = _props(client)
    lines = ["Maler GmbH", "Termin 03.09.2026", "Auftrag vom Kunden 01.08.2026", "Rechnungsbetrag 300,00 EUR"]
    doc = _upload(client, lines, property_id=a).json()["created"][0]
    run_worker()
    d = client.get(f"/api/documents/{doc}").json()
    assert d["date_status"] == "unsure" and d["doc_date"] == "2026-09-03"
    assert [c["date"] for c in d["date_candidates"]] == ["2026-09-03", "2026-08-01"]
    e = d["expenses"][0]
    assert (e["invoice_date"], e["paid_on"], e["paid_assumed"]) == ("2026-09-03", "2026-09-03", 1)
    inbox = client.get("/api/inbox").json()
    assert [x["id"] for x in inbox["undated"]] == [doc] and inbox["undated"][0]["date_status"] == "unsure"
    assert client.get("/api/dashboard").json()["counts"]["undated"] == 1
    # anderen Vorschlag bestätigen -> Datum, Rechnungs- und angenommenes Zahlungsdatum ziehen mit
    d = client.post(f"/api/documents/{doc}/date", json={"date": "2026-08-01"}, headers=HEADERS).json()
    assert d["date_status"] == "sure" and d["doc_date"] == "2026-08-01"
    e = d["expenses"][0]
    assert (e["invoice_date"], e["paid_on"], e["paid_assumed"]) == ("2026-08-01", "2026-08-01", 1)
    assert client.get("/api/inbox").json()["undated"] == []
