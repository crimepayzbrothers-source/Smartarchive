"""Automatische Erfassung vorhandener Rechnungen und Liste der Rechnungen ohne Ausgabe."""
from __future__ import annotations

import pytest

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
    assert any(i["kind"] == "zahlung_angenommen" and i["count"] == 2 for i in s["issues"])
    csv_text = client.get("/api/export/expenses.csv", params={"year": 2026}).content.decode("utf-8-sig")
    assert "automatisch ausgelesen" in csv_text and ";Zahlung (angenommen);" in csv_text


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


def test_date_only_from_text_otherwise_question(client):
    """Kein Datum aus dem Dateinamen; ohne Datum im Text: Rückfrage im Eingang, Antwort setzt das Datum."""
    pid = _prop(client)
    no_date = ["Dach GmbH", "Rechnung Nr. 5", "Dachrinne gereinigt", "Gesamtbetrag 120,00 EUR"]
    named = _imported(client, "2023-05-12 Dachrinne.pdf", no_date, pid)
    texted = _imported(client, "IMG_20240302_101500.pdf", ["Maler GmbH", "Rechnungsdatum:", "02.03.2024",
                                                            "zahlbar bis 16.03.2024", "Gesamtbetrag 80,00 EUR"], pid)
    run_worker()
    e_named = client.get(f"/api/documents/{named}").json()["expenses"][0]
    e_text = client.get(f"/api/documents/{texted}").json()["expenses"][0]
    assert e_named["invoice_date"] is None            # Dateiname zählt nicht
    assert e_text["invoice_date"] == "2024-03-02"     # beschriftet (Zeile davor), nicht das Fälligkeitsdatum

    assert client.get("/api/expenses/summary", params={"year": 2026}).json()["total"] == 0
    inbox = client.get("/api/inbox").json()
    assert [d["id"] for d in inbox["undated"]] == [named] and inbox["undated"][0]["expense"]["amount_cents"] == 12000
    assert client.get("/api/dashboard").json()["counts"]["undated"] == 1

    d = client.post(f"/api/documents/{named}/date", json={"date": "2023-05-12"}, headers=HEADERS).json()
    assert d["doc_date"] == "2023-05-12" and d["expenses"][0]["invoice_date"] == "2023-05-12"
    assert client.get("/api/inbox").json()["undated"] == []
    assert client.get("/api/expenses/summary", params={"year": 2023}).json()["total"] == 12000


def test_redetect_removes_filename_dates(client):
    from app import invoices
    from app.db import connect
    pid = _prop(client)
    doc = _imported(client, "IMG_20220715_090000.pdf", ["Sanitaer GmbH", "Rechnung Nr. 1", "Gesamtbetrag 50,00 EUR"], pid)
    doc2 = _imported(client, "x.pdf", ["Elektro GmbH", "Rechnung Nr. 2", "Datum 3.4.2025", "Gesamtbetrag 70,00 EUR"], pid)
    run_worker()
    conn = connect()
    conn.execute("UPDATE expenses SET invoice_date = '2022-07-15' WHERE document_id = ?", (doc,))  # früher aus dem Namen
    conn.execute("UPDATE expenses SET invoice_date = NULL WHERE document_id = ?", (doc2,))
    conn.execute("UPDATE documents SET doc_date = NULL WHERE id = ?", (doc2,))
    res = invoices.redetect_dates(conn)
    conn.close()
    assert res["zurueckgesetzt"] == 1 and res["positionen"] == 1
    assert client.get(f"/api/documents/{doc}").json()["expenses"][0]["invoice_date"] is None
    assert client.get(f"/api/documents/{doc2}").json()["expenses"][0]["invoice_date"] == "2025-04-03"


@pytest.mark.parametrize("text,expected", [
    ("Rechnungsdatum: 27.10.2026", "2026-10-27"),
    ("Datum 2O.1O.2026", "2026-10-20"),                 # OCR: O statt 0
    ("Belegdatum 20,10,26", "2026-10-20"),              # Komma statt Punkt, kurzes Jahr
    ("vom 20/10/2026", "2026-10-20"),
    ("Bad Sobernheim, den 5. März 2025", "2025-03-05"),
    ("zahlbar bis 30.11.2026\nDatum: 15.11.2026", "2026-11-15"),
    ("Kassenbon 4711  20.10.2026 16:05", "2026-10-20"),
    ("Gesamtbetrag 1.234,56 EUR", None),               # Betrag ist kein Datum
    ("Rechnung", None),
])
def test_date_detection_variants(text, expected):
    from app.extract import detect_date
    assert detect_date(text)[0] == expected


def test_scanned_photo_date_found(client):
    from conftest import make_scan
    scan = make_scan(["HORNBACH Baumarkt", "Bon 4711", "Datum: 2O.1O.2026", "SUMME 19,99 EUR"])
    r = client.post("/api/upload", data={"area": "privat"}, files=[("files", ("IMG_20200101_000000.jpg", scan, "image/jpeg"))],
                    headers=HEADERS)
    doc = r.json()["created"][0]
    run_worker()
    assert client.get(f"/api/documents/{doc}").json()["doc_date"] == "2026-10-20"


def test_assumed_payment_date_and_correction(client):
    """Ohne Zahlungsdatum im Beleg gilt das Rechnungsdatum – als „angenommen“, jederzeit korrigierbar."""
    pid = _prop(client)
    doc = _imported(client, "r.pdf", ["Maler GmbH", "Rechnungsdatum: 12.11.2025", "Gesamtbetrag 300,00 EUR"], pid)
    cash = _imported(client, "bon.pdf", ["Baumarkt GmbH", "Kassenbon", "Datum 03.03.2026", "SUMME 19,99", "BAR 20,00"], pid)
    run_worker()
    e = client.get(f"/api/documents/{doc}").json()["expenses"][0]
    assert (e["invoice_date"], e["paid_on"], e["paid_assumed"]) == ("2025-11-12", "2025-11-12", 1)
    c = client.get(f"/api/documents/{cash}").json()["expenses"][0]
    assert (c["paid_on"], c["paid_assumed"]) == ("2026-03-03", 0)  # Barzahlung laut Beleg: nicht angenommen
    # tatsächlich im Januar bezahlt -> korrigieren; zählt dann zu 2026
    e = client.patch(f"/api/expenses/{e['id']}", json={"paid_on": "2026-01-08"}, headers=HEADERS).json()
    assert (e["paid_on"], e["paid_assumed"]) == ("2026-01-08", 0)
    assert client.get("/api/expenses/summary", params={"year": 2026}).json()["total"] == 30000 + 1999
    assert client.get("/api/expenses/summary", params={"year": 2025}).json()["total"] == 0


def test_assume_for_existing_positions(client):
    from app import invoices
    from app.db import connect
    pid = _prop(client)
    doc = _imported(client, "r.pdf", ["Maler GmbH", "Rechnungsdatum: 12.11.2025", "Gesamtbetrag 300,00 EUR"], pid)
    run_worker()
    conn = connect()
    conn.execute("UPDATE expenses SET paid_on = NULL, paid_assumed = 0")  # Stand vor dem Update
    assert invoices.assume_payment_dates(conn) == 1
    conn.close()
    e = client.get(f"/api/documents/{doc}").json()["expenses"][0]
    assert (e["paid_on"], e["paid_assumed"]) == ("2025-11-12", 1)
    # „Stimmt“ bestätigt auch das Zahlungsdatum
    e = client.patch(f"/api/expenses/{e['id']}", json={}, headers=HEADERS).json()
    assert e["paid_assumed"] == 0
