"""Rechnungs-Erkennung, Entwürfe und die Abfrage zur Objektzuordnung."""
from __future__ import annotations

import pytest

from conftest import HEADERS, INVOICE, make_pdf, make_scan, run_worker


def _prop(client, name, address):
    r = client.post("/api/properties", json={"name": name, "address": address}, headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload(client, name, data, area="auto", mime="application/pdf"):
    r = client.post("/api/upload", data={"area": area, "combine": "0"}, files=[("files", (name, data, mime))], headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["created"][0]


def _cat(client, area, name):
    meta = client.get("/api/meta").json()
    return next(c for c in meta["categories"] if c["area_key"] == area and c["name"] == name)


# Rechnung ohne Objektadresse: Die App kann das Objekt nicht wissen und muss fragen.
INVOICE_NO_ADDRESS = [
    "Sanitaer Schmidt - Am Markt 2 - 55566 Bad Sobernheim",
    "Herrn Can",
    "Rechnungsnummer: RE-2026/0815",
    "Datum: 14.10.2026",
    "Reparatur Spuelkasten, Austausch Dichtung     120,00",
    "Anfahrt                                        25,00",
    "Rechnungsbetrag                               145,00 EUR",
]


@pytest.mark.parametrize("text,expected", [
    ("Rechnung Nr. 2026-1041", "2026-1041"),
    ("Rechnungsnummer: RE-2026/0815", "RE-2026/0815"),
    ("Re.-Nr. 4711", "4711"),
    ("Bon-Nr 0042", "0042"),
    ("Rechnungsdatum: 27.10.2026", None),
    ("Rechnung Nr. ABC", None),
])
def test_invoice_number(text, expected):
    from app.invoices import detect_invoice_number
    assert detect_invoice_number(text) == expected


def test_payee_guess():
    from app.invoices import guess_payee
    assert guess_payee("Heizungsbau Mueller GmbH - Industriestr. 4 - 55566 Bad Sobernheim") == "Heizungsbau Mueller GmbH"
    assert guess_payee("BAUMARKT HORNBACH\nKassenbon") == "BAUMARKT HORNBACH"
    assert guess_payee("Herrn Can\nMusterweg 1") is None


def test_paid_on_and_category():
    from app.invoices import detect_paid_on, suggest_expense_category
    assert detect_paid_on("Betrag bezahlt am 03.11.2026", "Rechnung", "2026-10-27")[0] == "2026-11-03"
    assert detect_paid_on("Betrag dankend erhalten", "Rechnung", "2026-10-27")[0] == "2026-10-27"
    assert detect_paid_on("Zahlbar innerhalb von 14 Tagen", "Rechnung", "2026-10-27")[0] is None
    assert detect_paid_on("SUMME 19,99\nBAR 20,00", "Kassenbon", "2026-10-20")[0] == "2026-10-20"
    assert detect_paid_on("Druck 1,5 bar", "Rechnung", "2026-10-20")[0] is None  # „bar“ nur bei Kassenbons
    assert suggest_expense_category("Schornsteinfeger Kehrung 2026")[0] == "hausnebenkosten"
    assert suggest_expense_category("Grundsteuer B Jahresbetrag")[0] == "grundsteuer"
    assert suggest_expense_category("Reparatur Spuelkasten, Austausch Dichtung")[0] == "erhaltung_sofort"
    assert suggest_expense_category("Sehr geehrter Herr")[0] is None


def test_invoice_without_property_asks_and_files_with_expense(client):
    p1 = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    _prop(client, "Berliner Straße 23", "Berliner Straße 23, 55566 Bad Sobernheim")
    doc_id = _upload(client, "scan.pdf", make_pdf(INVOICE_NO_ADDRESS))
    run_worker()
    d = client.get(f"/api/documents/{doc_id}").json()
    # Betrag sofort erfasst, obwohl das Objekt noch unklar ist (Entwurf, zählt noch nicht)
    assert d["status"] == "inbox"
    assert [(e["amount_cents"], e["status"], e["property_id"], e["origin"]) for e in d["expenses"]] == [(14500, "entwurf", None, "auto")]
    draft_id = d["expenses"][0]["id"]
    inv = d["suggestion"]["payload"]["invoice"]
    assert inv["payee"] == "Sanitaer Schmidt"
    assert inv["number"] == "RE-2026/0815" and inv["amount_cents"] == 14500 and inv["expense_category"] == "erhaltung_sofort"
    assert d["suggestion"]["payload"]["doc_type"] == "Rechnung"
    assert not d["suggestion"]["payload"].get("property_id")

    # Vermietung ohne Objekt wird abgelehnt
    r = client.post(f"/api/documents/{doc_id}/invoice", json={"area": "vermietung"}, headers=HEADERS)
    assert r.status_code == 400 and "Mietobjekt" in r.json()["error"]

    body = {"area": "vermietung", "property_id": p1, "amount_cents": 14500, "expense_category": "erhaltung_sofort",
            "invoice_date": "2026-10-14", "paid_on": "2026-10-20", "payee": "Sanitaer Schmidt", "note": "Rechnung Nr. RE-2026/0815"}
    d = client.post(f"/api/documents/{doc_id}/invoice", json=body, headers=HEADERS).json()
    assert d["status"] == "filed" and d["confirmed"] == 1 and d["property_id"] == p1
    assert d["category_path"] == "Vermietung › Rechnungen und Belege"
    assert d["correspondent_name"] == "Sanitaer Schmidt"
    assert len(d["expenses"]) == 1
    e = d["expenses"][0]
    assert (e["status"], e["amount_cents"], e["paid_on"], e["origin"]) == ("bestaetigt", 14500, "2026-10-20", "user")
    assert e["id"] == draft_id and e["property_id"] == p1  # derselbe Datensatz, jetzt mit Objekt

    # zweimal absenden legt keine zweite Position an
    d = client.post(f"/api/documents/{doc_id}/invoice", json=body, headers=HEADERS).json()
    assert len(d["expenses"]) == 1

    s = client.get("/api/expenses/summary", params={"year": 2026}).json()
    assert s["confirmed"] == 14500


def test_auto_filed_invoice_gets_draft_and_confirm_reuses_it(client):
    pid = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    cat = _cat(client, "vermietung", "Rechnungen und Belege")
    client.post("/api/rules", json={"name": "Heizungsbauer", "match_field": "text", "pattern": "Heizungsbau Mueller",
                                    "category_id": cat["id"]}, headers=HEADERS)
    doc_id = _upload(client, "rechnung.pdf", make_pdf(INVOICE), area="vermietung")
    run_worker()
    d = client.get(f"/api/documents/{doc_id}").json()
    assert d["status"] == "filed" and d["confirmed"] == 0 and d["property_id"] == pid
    assert [(e["status"], e["origin"], e["amount_cents"]) for e in d["expenses"]] == [("bestaetigt", "auto", 24500)]
    inbox = client.get("/api/inbox").json()
    assert inbox["auto_filed"][0]["expenses"][0]["origin"] == "auto"

    # automatisch ausgelesen zählt sofort (ohne Zahlungsdatum nach Rechnungsdatum)
    s = client.get("/api/expenses/summary", params={"year": 2026}).json()
    assert s["confirmed"] == 24500 and s["auto_count"] == 1

    d = client.post(f"/api/documents/{doc_id}/invoice", json={
        "area": "vermietung", "property_id": pid, "amount_cents": 24500, "expense_category": "heizung",
        "invoice_date": "2026-10-27", "payee": "Heizungsbau Mueller GmbH"}, headers=HEADERS).json()
    assert d["confirmed"] == 1
    assert [(e["status"], e["category_key"]) for e in d["expenses"]] == [("bestaetigt", "heizung")]


def test_invoice_moved_to_private_drops_system_draft(client):
    _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    cat = _cat(client, "vermietung", "Rechnungen und Belege")
    client.post("/api/rules", json={"name": "Heizungsbauer", "match_field": "text", "pattern": "Heizungsbau Mueller",
                                    "category_id": cat["id"]}, headers=HEADERS)
    doc_id = _upload(client, "rechnung.pdf", make_pdf(INVOICE), area="vermietung")
    run_worker()
    assert len(client.get(f"/api/documents/{doc_id}").json()["expenses"]) == 1
    d = client.post(f"/api/documents/{doc_id}/invoice", json={"area": "privat"}, headers=HEADERS).json()
    assert d["area_key"] == "privat" and d["property_id"] is None and d["expenses"] == []
    assert d["category_path"] == "Privat › Sonstiges" and d["confirmed"] == 1
    assert any(h["action"] == "area_changed" for h in d["history"])


def test_scanned_receipt_ocr_detects_invoice(client):
    pid = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    _prop(client, "Berliner Straße 23", "Berliner Straße 23, 55566 Bad Sobernheim")
    scan = make_scan(["BAUMARKT HORNBACH", "Kassenbon 4711", "Datum 20.10.2026", "Silikon weiss 2x", "SUMME 19,99 EUR", "BAR 20,00"])
    doc_id = _upload(client, "foto.jpg", scan, area="vermietung", mime="image/jpeg")
    run_worker()
    d = client.get(f"/api/documents/{doc_id}").json()
    assert d["text_source"] == "ocr" and d["status"] == "inbox"
    p = d["suggestion"]["payload"]
    assert p["doc_type"] == "Kassenbon"
    inv = p["invoice"]
    assert inv["amount_cents"] == 1999
    assert inv["paid_on"] == "2026-10-20"
    assert inv["expense_category"] == "erhaltung_sofort"
    d = client.post(f"/api/documents/{doc_id}/invoice", json={"area": "vermietung", "property_id": pid, "amount_cents": 1999,
                                                              "expense_category": "erhaltung_sofort", "paid_on": "2026-10-20"},
                    headers=HEADERS).json()
    assert d["expenses"][0]["paid_on"] == "2026-10-20"


def test_import_captures_expense_automatically(client):
    from app import documents
    from app.db import connect

    pid = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    cat = _cat(client, "vermietung", "Rechnungen und Belege")
    conn = connect()
    area_id = conn.execute("SELECT id FROM areas WHERE key = 'vermietung'").fetchone()[0]
    _, doc_id, _ = documents.ingest_import(conn, "r.pdf", make_pdf(INVOICE), area="vermietung",
                                           preset={"area_id": area_id, "category_id": cat["id"], "property_id": pid},
                                           source_path="Can Finanzierung/r.pdf")
    conn.close()
    run_worker()
    d = client.get(f"/api/documents/{doc_id}").json()
    assert d["status"] == "filed"
    assert [(e["amount_cents"], e["origin"], e["status"]) for e in d["expenses"]] == [(24500, "auto", "bestaetigt")]
    # „Stimmt“ ohne Änderung -> geprüft
    eid = d["expenses"][0]["id"]
    assert client.patch(f"/api/expenses/{eid}", json={}, headers=HEADERS).json()["origin"] == "user"



def test_upload_captures_amount_immediately_then_private_or_undo(client):
    p1 = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    _prop(client, "Berliner Straße 23", "Berliner Straße 23, 55566 Bad Sobernheim")
    # 1. Privat gewählt: vorläufiger Betrag entfällt
    a = _upload(client, "a.pdf", make_pdf(INVOICE_NO_ADDRESS), area="vermietung")
    run_worker()
    assert client.get(f"/api/documents/{a}").json()["expenses"][0]["status"] == "entwurf"
    d = client.post(f"/api/documents/{a}/invoice", json={"area": "privat"}, headers=HEADERS).json()
    assert d["expenses"] == []
    # 2. Objekt über das normale Ablegen gewählt: Entwurf wird gezählt
    b = _upload(client, "b.pdf", make_pdf(INVOICE_NO_ADDRESS[:2] + ["Rechnung Nr. 77", "Datum: 01.09.2026",
                                                                     "Rechnungsbetrag 99,00 EUR"]), area="vermietung")
    run_worker()
    cat = _cat(client, "vermietung", "Rechnungen und Belege")
    d = client.patch(f"/api/documents/{b}", json={"category_id": cat["id"], "property_id": p1, "file": True}, headers=HEADERS).json()
    assert [(e["amount_cents"], e["status"], e["property_id"]) for e in d["expenses"]] == [(9900, "bestaetigt", p1)]
    # 3. Privat-Upload: keine Ausgabe
    c = _upload(client, "c.pdf", make_pdf(INVOICE_NO_ADDRESS), area="privat")
    run_worker()
    assert client.get(f"/api/documents/{c}").json()["expenses"] == []


def test_undo_auto_filing_turns_expense_back_to_draft(client):
    pid = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    cat = _cat(client, "vermietung", "Rechnungen und Belege")
    client.post("/api/rules", json={"name": "Heizungsbauer", "match_field": "text", "pattern": "Heizungsbau Mueller",
                                    "category_id": cat["id"]}, headers=HEADERS)
    doc = _upload(client, "r.pdf", make_pdf(INVOICE), area="vermietung")
    run_worker()
    assert client.get(f"/api/documents/{doc}").json()["expenses"][0]["status"] == "bestaetigt"
    d = client.post(f"/api/documents/{doc}/undo", headers=HEADERS).json()
    assert d["status"] == "inbox" and d["expenses"][0]["status"] == "entwurf"
    d = client.post(f"/api/documents/{doc}/accept", headers=HEADERS).json()
    assert [(e["status"], e["property_id"]) for e in d["expenses"]] == [("bestaetigt", pid)]
