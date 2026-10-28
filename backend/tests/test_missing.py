"""Automatische Erfassung vorhandener Rechnungen und Liste der Rechnungen ohne Ausgabe."""
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


def _prop(client):
    return client.post("/api/properties", json={"name": "Hauptstraße 15", "address": "Hauptstraße 15, 55758 Schmidt-Hachenbach"},
                       headers=HEADERS).json()["id"]


def test_import_captures_and_lists_leftovers(client):
    pid = _prop(client)
    inv = _imported(client, "heizung.pdf", INVOICE, pid)
    tax = _imported(client, "grundsteuer.pdf", ["Verbandsgemeinde Nahe-Glan", "Grundsteuerbescheid", "Datum: 10.01.2026",
                                                "Grundsteuer B Jahresbetrag", "Gesamtbetrag 493,80 EUR"], pid, "Grundbesitzabgaben")
    _imported(client, "vertrag.pdf", ["Mietvertrag", "zwischen A und B", "Datum: 01.03.2026"], pid, "Mietverträge und Mieter")
    noamt = _imported(client, "unlesbar.pdf", ["Dach GmbH", "Rechnung Nr. 5", "Datum: 05.05.2026", "Dachrinne gereinigt"], pid)
    run_worker()

    e = client.get(f"/api/documents/{inv}").json()["expenses"]
    assert [(x["amount_cents"], x["category_key"], x["origin"]) for x in e] == [(24500, "heizung", "auto")]
    e = client.get(f"/api/documents/{tax}").json()["expenses"]
    assert [(x["amount_cents"], x["category_key"]) for x in e] == [(49380, "grundsteuer")]

    # ohne erkannten Betrag: bleibt in der Liste zum manuellen Erfassen
    items = client.get("/api/expenses/missing", params={"year": 2026}).json()
    assert [i["id"] for i in items] == [noamt] and items[0]["amount_cents"] is None
    assert client.post(f"/api/documents/{noamt}/no-expense", headers=HEADERS).status_code == 200
    assert client.get("/api/expenses/missing", params={"year": 2026}).json() == []

    s = client.get("/api/expenses/summary", params={"year": 2026}).json()
    assert s["confirmed"] == 24500 + 49380 and s["auto_count"] == 2
    assert any(i["kind"] == "zahlung_offen" and i["count"] == 2 for i in s["issues"])
    csv_text = client.get("/api/export/expenses.csv", params={"year": 2026}).content.decode("utf-8-sig")
    assert "automatisch ausgelesen" in csv_text and ";Rechnung;" in csv_text


def test_autofill_on_start_for_existing_documents(client):
    """Dokumente, die vor dem Update verarbeitet wurden, werden beim Start einmalig erfasst."""
    from app import main
    from app.db import connect, set_setting
    pid = _prop(client)
    doc = _imported(client, "alt.pdf", INVOICE, pid)
    run_worker()
    conn = connect()
    conn.execute("DELETE FROM expenses")  # Zustand wie vor dem Update
    set_setting(conn, "expense_autofill_v1", False)
    main._autofill_expenses(conn)
    main._autofill_expenses(conn)  # zweiter Start: nichts doppelt
    conn.close()
    assert len(client.get(f"/api/documents/{doc}").json()["expenses"]) == 1


def test_old_invoices_do_not_land_in_upload_year(client):
    """Belege ohne lesbares Datum dürfen nicht ins Jahr des Hochladens (Import 2026) fallen."""
    pid = _prop(client)
    no_date = ["Dach GmbH", "Rechnung Nr. 5", "Dachrinne gereinigt", "Gesamtbetrag 120,00 EUR"]
    named = _imported(client, "2023-05-12 Dachrinne.pdf", no_date, pid)          # Datum im Dateinamen
    cam = _imported(client, "IMG_20240302_101500.pdf", no_date[:1] + ["Rechnung Nr. 6", "Gesamtbetrag 80,00 EUR"], pid)
    none = _imported(client, "scan.pdf", ["Maler GmbH", "Rechnung Nr. 9", "Gesamtbetrag 300,00 EUR"], pid)  # gar kein Datum
    run_worker()
    exp = {d: client.get(f"/api/documents/{d}").json()["expenses"][0] for d in (named, cam, none)}
    assert exp[named]["invoice_date"] == "2023-05-12" and exp[cam]["invoice_date"] == "2024-03-02"
    assert exp[none]["invoice_date"] is None

    s26 = client.get("/api/expenses/summary", params={"year": 2026}).json()
    assert s26["total"] == 0  # nichts davon gehört zu 2026
    assert [u["id"] for u in s26["undated"]] == [exp[none]["id"]]
    assert client.get("/api/expenses/summary", params={"year": 2023}).json()["total"] == 12000
    assert client.get("/api/expenses/summary", params={"year": 2024}).json()["total"] == 8000


def test_date_fix_for_existing_positions(client):
    from app import invoices
    from app.db import connect
    pid = _prop(client)
    doc = _imported(client, "IMG_20220715_090000.pdf", ["Sanitaer GmbH", "Rechnung Nr. 1", "Gesamtbetrag 50,00 EUR"], pid)
    run_worker()
    conn = connect()
    conn.execute("UPDATE expenses SET invoice_date = NULL")  # Zustand vor dem Update
    assert invoices.fix_missing_dates(conn) == 1
    conn.close()
    assert client.get(f"/api/documents/{doc}").json()["expenses"][0]["invoice_date"] == "2022-07-15"


def test_date_from_name():
    from app.extract import date_from_name
    assert date_from_name("PXL_20231231_235959123.jpg") == "2023-12-31"
    assert date_from_name("Rechnung 05.03.2021.pdf") == "2021-03-05"
    assert date_from_name("Can Finanzierung/2024/Belege/scan.pdf") is None
    assert date_from_name("Rechnung 2023-13-45.pdf") is None
