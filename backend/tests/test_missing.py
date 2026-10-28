"""Nacherfassung: Rechnungen im Bereich Vermietung ohne Ausgabe (z. B. aus dem Dropbox-Import)."""
from __future__ import annotations

from conftest import HEADERS, INVOICE, make_pdf, run_worker


def _imported(client, name, lines, pid, cat_name="Rechnungen und Belege"):
    from app import documents
    from app.db import connect
    meta = client.get("/api/meta").json()
    cat = next(c for c in meta["categories"] if c["area_key"] == "vermietung" and c["name"] == cat_name)
    conn = connect()
    area_id = conn.execute("SELECT id FROM areas WHERE key = 'vermietung'").fetchone()[0]
    _, doc_id, _ = documents.ingest_import(conn, name, make_pdf(lines), area="vermietung",
                                           preset={"area_id": area_id, "category_id": cat["id"], "property_id": pid},
                                           source_path=f"Can Finanzierung/{name}")
    conn.close()
    return doc_id


def test_missing_list_drafts_and_dismiss(client):
    pid = client.post("/api/properties", json={"name": "Hauptstraße 15", "address": "Hauptstraße 15, 55758 Schmidt-Hachenbach"},
                      headers=HEADERS).json()["id"]
    inv = _imported(client, "heizung.pdf", INVOICE, pid)
    old = _imported(client, "alt.pdf", INVOICE[:5] + ["Rechnung Nr. 77", "Rechnungsdatum: 03.02.2025", "Gesamtbetrag 50,00 EUR"], pid)
    tax = _imported(client, "grundsteuer.pdf", ["Verbandsgemeinde Nahe-Glan", "Grundsteuerbescheid", "Datum: 10.01.2026",
                                                "Grundsteuer B Jahresbetrag", "Gesamtbetrag 493,80 EUR"], pid, "Grundbesitzabgaben")
    _imported(client, "vertrag.pdf", ["Mietvertrag", "zwischen A und B", "Datum: 01.03.2026"], pid, "Mietverträge und Mieter")
    offer = _imported(client, "angebot.pdf", ["Dach GmbH", "Rechnung Nr. 5", "Datum: 05.05.2026", "Gesamtbetrag 900,00 EUR"], pid)
    run_worker()

    items = client.get("/api/expenses/missing", params={"year": 2026}).json()
    by_id = {i["id"]: i for i in items}
    assert set(by_id) == {inv, tax, offer}  # 2025er Rechnung und Vertrag nicht
    assert (by_id[inv]["amount_cents"], by_id[inv]["category_key"], by_id[inv]["date"]) == (24500, "heizung", "2026-10-27")
    assert by_id[inv]["note"] == "Rechnung Nr. 2026-1041" and by_id[inv]["property_name"] == "Hauptstraße 15"
    assert (by_id[tax]["amount_cents"], by_id[tax]["category_key"]) == (49380, "grundsteuer")
    assert [i["id"] for i in client.get("/api/expenses/missing", params={"year": 2025}).json()] == [old]

    # bewusst ohne Ausgabe
    assert client.post(f"/api/documents/{offer}/no-expense", headers=HEADERS).status_code == 200
    # einzeln bestätigt erfassen (normaler Ausgaben-Endpunkt)
    r = client.post("/api/expenses", json={"document_id": tax, "amount_cents": 49380, "category_key": "grundsteuer",
                                           "invoice_date": "2026-01-10", "paid_on": "2026-02-15", "status": "bestaetigt"}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["property_id"] == pid
    # Rest als Entwurf
    res = client.post("/api/expenses/missing/drafts", params={"year": 2026}, headers=HEADERS).json()
    assert res == {"created": 1, "skipped": 0}
    assert client.get("/api/expenses/missing", params={"year": 2026}).json() == []
    e = client.get(f"/api/documents/{inv}").json()["expenses"][0]
    assert (e["status"], e["origin"], e["paid_on"]) == ("entwurf", "auto", None)
    s = client.get("/api/expenses/summary", params={"year": 2026}).json()
    assert s["confirmed"] == 49380 and s["open_payment_count"] == 1
