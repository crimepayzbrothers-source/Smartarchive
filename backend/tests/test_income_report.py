"""Einnahmen: Aufteilung auf Kaltmiete/Vorauszahlungen, Monatssummen und PDF-Übersicht."""
from __future__ import annotations

import datetime as dt
import io

import pytest
from conftest import HEADERS
from pypdf import PdfReader
from test_income import _import, _setup


@pytest.fixture
def today(monkeypatch):
    from app import income

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 30)
    monkeypatch.setattr(income, "date", FakeDate)


def test_split_parts():
    from app.income import split_parts
    soll = {"cold": 40000, "bk": 10000, "hk": 6000, "total": 56000}
    assert split_parts(56000, soll) == {"cold": 40000, "bk": 10000, "hk": 6000, "excess": 0}
    assert split_parts(60000, soll) == {"cold": 40000, "bk": 10000, "hk": 6000, "excess": 4000}
    p = split_parts(30001, soll)                                   # Teilzahlung anteilig, Cent-genau
    assert sum(p.values()) == 30001 and p["excess"] == 0 and p["cold"] == 21429
    assert split_parts(5000, {"cold": 0, "bk": 0, "hk": 0, "total": 0})["excess"] == 5000
    assert split_parts(0, soll) == {"cold": 0, "bk": 0, "hk": 0, "excess": 0}


def test_overview_parts_monthly_and_pdf(client, today):
    pid, units, t, acc = _setup(client)
    client.put("/api/bk/landlord", json={"name": "Can Beispiel", "address": "Musterweg 1, 55566 Bad Sobernheim"}, headers=HEADERS)
    _import(client, acc, [f"02.{m:02d}.2026;02.{m:02d}.2026;Gutschrift;Otto Weber Miete;560,00;EUR;DE00;" for m in (1, 2, 3)])
    rv = client.get("/api/transactions/review").json()
    for x in rv["auto"] + rv["open"]:
        client.post(f"/api/transactions/{x['id']}/decide", json={"kind": "einnahme", "tenancy_id": t["eg"]}, headers=HEADERS)
    client.put("/api/income/exceptions", json={"tenancy_id": t["eg"], "month": "2026-07", "paid_cents": 0}, headers=HEADERS)
    ov = client.get("/api/income", params={"year": 2026, "property_id": pid}).json()
    eg = next(i for i in ov["items"] if i["tenancy_id"] == t["eg"])
    # Jan–Okt fällig, Juli ausgeblieben → 9 Monate je 400/100/60
    assert eg["ist_parts"] == {"cold": 360000, "bk": 90000, "hk": 54000, "excess": 0}
    assert sum(eg["ist_parts"].values()) == eg["ist"]
    assert sum(ov["parts"].values()) == ov["totals"]["ist"]
    assert [m["month"] for m in ov["monthly"]] == list(range(1, 13))
    assert ov["monthly"][0]["assumed"] < ov["monthly"][0]["ist"]             # Januar: Otto per Bank, andere angenommen
    assert ov["monthly"][10]["ist"] == 0 and ov["monthly"][10]["soll"] > 0  # November noch nicht fällig

    r = client.get("/api/export/income.pdf", params={"year": 2026, "property_id": pid})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "Einnahmen_Vermietung_2026" in r.headers["content-disposition"]
    txt = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    assert "Einnahmenübersicht Vermietung 2026" in txt and "Hauptstraße 15" in txt and "Can Beispiel" in txt
    assert "Mieteinnahmen (Kaltmiete)" in txt and "Otto Weber" in txt and "3.600,00 €" in txt
    assert "ohne Zahlungsnachweis" in txt and "Offene Mietrückstände" in txt and "ersetzt nicht die Anlage V" in txt
    assert client.get("/api/export/income.pdf", params={"year": 2026, "property_id": 999}).status_code == 404
