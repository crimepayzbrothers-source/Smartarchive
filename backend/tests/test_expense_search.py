"""Ausgaben-Suche: „alle Jahre“ lädt die Positionen ohne Jahresfilter, mit allen Feldern, die die Suche durchsucht."""
from __future__ import annotations

from conftest import HEADERS


def test_expense_list_all_years_for_search(client):
    pid = client.post("/api/properties", json={"name": "Berliner Straße 23"}, headers=HEADERS).json()["id"]
    cat = client.get("/api/meta").json()["expense_categories"][0]["key"]
    for y, payee in ((2025, "Schornsteinfeger Müller"), (2026, "Hausmeister Weber")):
        r = client.post("/api/expenses", json={"property_id": pid, "payee": payee, "category_key": cat, "amount_cents": 18600,
                                               "invoice_date": f"{y}-03-15", "note": "Treppenhaus"}, headers=HEADERS)
        assert r.status_code == 200, r.text
    assert [e["payee"] for e in client.get("/api/expenses", params={"year": 2026}).json()] == ["Hausmeister Weber"]
    every = client.get("/api/expenses").json()
    assert {e["payee"] for e in every} == {"Schornsteinfeger Müller", "Hausmeister Weber"}
    assert {e["booking_date"][:4] for e in every} == {"2025", "2026"}
    e = every[0]
    for field in ("payee", "document_title", "note", "category_key", "property_name", "unit_name", "amount_cents", "paid_on", "invoice_date", "status"):
        assert field in e
    assert len(client.get("/api/expenses", params={"property_id": pid}).json()) == 2
