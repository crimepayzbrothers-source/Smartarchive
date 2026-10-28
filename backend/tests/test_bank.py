"""Kontoumsätze: CSV einlesen, Abgleich mit Rechnungen, Rückfrage, Lernen aus Zuordnungen."""
from __future__ import annotations

from conftest import HEADERS, make_pdf, run_worker

# Aufbau wie im Commerzbank-Umsatzexport: alles Wesentliche steht im „Buchungstext“.
CB_HEADER = "Buchungstag;Wertstellung;Umsatzart;Buchungstext;Betrag;Währung;IBAN Kontoinhaber;Kategorie\n"


def cb_csv(lines: list[str], enc: str = "utf-8-sig") -> bytes:
    return (CB_HEADER + "\n".join(lines) + "\n").encode(enc)


SPK = (
    '"Auftragskonto";"Buchungstag";"Valutadatum";"Buchungstext";"Verwendungszweck";"Glaeubiger ID";"Mandatsreferenz";'
    '"Kundenreferenz (End-to-End)";"Sammlerreferenz";"Lastschrift Ursprungsbetrag";"Auslagenersatz Ruecklastschrift";'
    '"Beguenstigter/Zahlungspflichtiger";"Kontonummer/IBAN";"BIC (SWIFT-Code)";"Betrag";"Waehrung";"Info"\n'
    '"DE001";"01.10.26";"01.10.26";"FOLGELASTSCHRIFT";"Grundsteuer B 4. Quartal";"DE98ZZZ09999999999";"M1";"";"";"";"";'
    '"Verbandsgemeinde Nahe-Glan";"DE89370400440532013000";"COBADEFFXXX";"-123,45";"EUR";"Umsatz gebucht"\n'
)


def _prop(client, name, address):
    return client.post("/api/properties", json={"name": name, "address": address}, headers=HEADERS).json()["id"]


def _account(client):
    return client.post("/api/bank/accounts", json={"name": "Mietkonto Commerzbank"}, headers=HEADERS).json()["id"]


def _import(client, acc, data, name="umsaetze.csv"):
    r = client.post("/api/bank/import", data={"account_id": str(acc)}, files=[("file", (name, data, "text/csv"))], headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


def test_parse_formats():
    from app.banking import parse_amount, parse_csv
    assert [parse_amount(x) for x in ("-1.234,56", "1234.56", "-145,00", "12", "1.000")] == [-123456, 123456, -14500, 1200, 100000]
    rows = parse_csv(cb_csv([
        "14.10.2026;14.10.2026;Lastschrift;Stadtwerke Bad Sobernheim Abschlag Allgemeinstrom End-to-End-Ref.: 123 "
        "Mandatsref: M9 Gläubiger-ID: DE11ZZZ00000123456;-38,00;EUR;DE00;Wohnen",
    ], enc="cp1252"))
    r = rows[0]
    assert r["booking_date"] == "2026-10-14" and r["amount_cents"] == -3800
    assert r["counterparty"] == "Stadtwerke Bad Sobernheim Abschlag Allgemeinstrom"
    assert r["creditor_id"] == "DE11ZZZ00000123456" and r["counterparty_iban"] == ""  # eigenes Konto ist kein Partner
    s = parse_csv(SPK.encode("cp1252"))[0]
    assert s["counterparty"] == "Verbandsgemeinde Nahe-Glan" and s["purpose"] == "Grundsteuer B 4. Quartal"
    assert s["counterparty_iban"] == "DE89370400440532013000" and s["booking_kind"] == "FOLGELASTSCHRIFT"
    assert s["booking_date"] == "2026-10-01" and s["amount_cents"] == -12345


def test_wrong_file_is_rejected(client):
    acc = _account(client)
    r = client.post("/api/bank/import", data={"account_id": str(acc)}, files=[("file", ("x.csv", b"a;b\n1;2\n", "text/csv"))], headers=HEADERS)
    assert r.status_code == 400 and "Buchungstag" in r.json()["error"]


def test_payment_matches_invoice_and_sets_payment_date(client):
    p1 = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    _prop(client, "Berliner Straße 23", "Berliner Straße 23, 55566 Bad Sobernheim")
    lines = ["Sanitaer Schmidt - Am Markt 2", "Rechnungsnummer: RE-2026/0815", "Datum: 14.10.2026", "Rechnungsbetrag 145,00 EUR"]
    doc = client.post("/api/upload", data={"area": "auto"}, files=[("files", ("r.pdf", make_pdf(lines), "application/pdf"))],
                      headers=HEADERS).json()["created"][0]
    run_worker()
    client.post(f"/api/documents/{doc}/invoice", json={"area": "vermietung", "property_id": p1, "amount_cents": 14500,
                                                       "expense_category": "erhaltung_sofort", "invoice_date": "2026-10-14",
                                                       "payee": "Sanitaer Schmidt", "note": "Rechnung Nr. RE-2026/0815"}, headers=HEADERS)
    acc = _account(client)
    res = _import(client, acc, cb_csv([
        "20.10.2026;20.10.2026;Überweisung;Sanitaer Schmidt RE-2026/0815 End-to-End-Ref.: NOTPROVIDED;-145,00;EUR;DE00;",
        "21.10.2026;21.10.2026;Lastschrift;Unbekannt GmbH Sonstwas;-99,00;EUR;DE00;",
    ]))
    assert (res["new"], res["auto"], res["open"]) == (2, 1, 1)
    rv = client.get("/api/transactions/review").json()
    t = rv["auto"][0]
    assert t["source"] == "beleg" and t["document_id"] == doc and t["property_id"] == p1
    e = client.get(f"/api/documents/{doc}").json()["expenses"][0]
    assert e["paid_on"] == "2026-10-20"

    # „Rückgängig“: Zahlungsdatum wieder leer, Umsatz offen (nicht sofort wieder automatisch)
    t = client.post(f"/api/transactions/{t['id']}/undo", headers=HEADERS).json()
    assert t["status"] == "open" and t["candidates"][0]["id"] == e["id"]
    e_undo = client.get(f"/api/documents/{doc}").json()["expenses"][0]
    assert (e_undo["paid_on"], e_undo["paid_assumed"]) == ("2026-10-14", 1)  # wieder die Annahme (= Rechnungsdatum)
    # Bestätigen mit Verknüpfung
    t = client.post(f"/api/transactions/{t['id']}/decide", json={"kind": "ausgabe", "expense_id": e["id"]}, headers=HEADERS).json()
    assert t["status"] == "done" and t["expense_id"] == e["id"]
    e_done = client.get(f"/api/documents/{doc}").json()["expenses"][0]
    assert (e_done["paid_on"], e_done["paid_assumed"]) == ("2026-10-20", 0)

    # erneuter Import derselben Datei: nichts doppelt
    res = _import(client, acc, cb_csv([
        "20.10.2026;20.10.2026;Überweisung;Sanitaer Schmidt RE-2026/0815 End-to-End-Ref.: NOTPROVIDED;-145,00;EUR;DE00;",
        "21.10.2026;21.10.2026;Lastschrift;Unbekannt GmbH Sonstwas;-99,00;EUR;DE00;",
    ]))
    assert (res["new"], res["duplicates"]) == (0, 2)


def test_question_then_learning_from_history(client):
    p1 = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    _prop(client, "Berliner Straße 23", "Berliner Straße 23, 55566 Bad Sobernheim")
    acc = _account(client)

    def month(m):
        return f"15.{m:02d}.2026;15.{m:02d}.2026;Lastschrift;Verbandsgemeindekasse Nahe-Glan Grundsteuer B Kassenzeichen 4711 " \
               f"Gläubiger-ID: DE98ZZZ09999999999;-123,45;EUR;DE00;"

    res = _import(client, acc, cb_csv([month(2)]))
    assert res["open"] == 1
    t = client.get("/api/transactions/review").json()["open"][0]
    assert t["suggestion"]["category_key"] == "grundsteuer" and t["suggestion"]["kind"] == "ausgabe"
    assert not t["suggestion"].get("property_id")  # zwei Objekte, keine Adresse -> Rückfrage

    r = client.post(f"/api/transactions/{t['id']}/decide", json={"kind": "ausgabe", "category_key": "grundsteuer"}, headers=HEADERS)
    assert r.status_code == 400 and "Mietobjekt" in r.json()["error"]
    t = client.post(f"/api/transactions/{t['id']}/decide", json={"kind": "ausgabe", "property_id": p1, "category_key": "grundsteuer"},
                    headers=HEADERS).json()
    assert t["status"] == "done" and t["expense_status"] == "bestaetigt" and t["expense_origin"] == "bank"

    _import(client, acc, cb_csv([month(5)]))
    t2 = client.get("/api/transactions/review").json()["open"][0]
    assert t2["suggestion"].get("property_id") is None  # erst eine Zuordnung -> noch fragen
    client.post(f"/api/transactions/{t2['id']}/decide", json={"kind": "ausgabe", "property_id": p1, "category_key": "grundsteuer"},
                headers=HEADERS)

    # dritte Rate: zweimal gleich zugeordnet -> automatisch, Ausgabe als Entwurf
    res = _import(client, acc, cb_csv([month(8)]))
    assert res["auto"] == 1
    t3 = client.get("/api/transactions/review").json()["auto"][0]
    assert (t3["source"], t3["property_id"], t3["category_key"], t3["expense_status"]) == ("history", p1, "grundsteuer", "entwurf")
    s = client.get("/api/expenses/summary", params={"year": 2026}).json()
    assert s["confirmed"] == 24690 and s["total"] == 37035
    # „Passt“ bestätigt die Ausgabe
    t3 = client.post(f"/api/transactions/{t3['id']}/decide", json={"kind": "ausgabe", "property_id": p1, "category_key": "grundsteuer"},
                     headers=HEADERS).json()
    assert t3["expense_status"] == "bestaetigt" and t3["source"] == "history"
    assert client.get("/api/dashboard").json()["counts"]["transactions"] == 0


def test_income_ignore_and_loan_partial(client):
    p1 = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    acc = _account(client)
    res = _import(client, acc, cb_csv([
        "01.10.2026;01.10.2026;Gutschrift;Max Mustermann Miete Oktober Hauptstr. 15 EG;650,00;EUR;DE00;",
        "30.10.2026;30.10.2026;Lastschrift;Commerzbank AG Darlehen 12345 Rate 10/2026;-900,00;EUR;DE00;",
    ]))
    assert res["open"] == 2
    items = {t["amount_cents"]: t for t in client.get("/api/transactions/review").json()["open"]}
    rent, loan = items[65000], items[-90000]
    assert rent["suggestion"]["kind"] == "einnahme" and rent["suggestion"]["property_id"] == p1
    assert "Tilgung" in loan["suggestion"]["hint"]
    client.post(f"/api/transactions/{rent['id']}/decide", json={"kind": "einnahme", "property_id": p1}, headers=HEADERS)

    # nur Zinsanteil erfassen
    t = client.post(f"/api/transactions/{loan['id']}/decide", json={"kind": "ausgabe", "property_id": p1, "category_key": "schuldzinsen",
                                                                    "amount_cents": 31000}, headers=HEADERS).json()
    assert t["expense_amount"] == 31000 and t["suggestion"]["partial"] is True
    r = client.post(f"/api/transactions/{loan['id']}/decide", json={"kind": "ausgabe", "property_id": p1, "category_key": "schuldzinsen",
                                                                    "amount_cents": 100000}, headers=HEADERS)
    assert r.status_code == 400

    # umentscheiden: ignorieren -> Ausgabe verschwindet
    t = client.post(f"/api/transactions/{loan['id']}/decide", json={"kind": "ignoriert"}, headers=HEADERS).json()
    assert t["expense_id"] is None
    assert client.get("/api/expenses", params={"year": 2026}).json() == []


def test_partial_history_is_never_auto(client):
    p1 = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    acc = _account(client)
    for m in (1, 2, 3):
        _import(client, acc, cb_csv([f"28.{m:02d}.2026;28.{m:02d}.2026;Lastschrift;Commerzbank AG Darlehen 12345;-900,00;EUR;DE00;"]))
        t = client.get("/api/transactions/review").json()["open"]
        assert len(t) == 1, f"Monat {m} sollte nachfragen"
        client.post(f"/api/transactions/{t[0]['id']}/decide", json={"kind": "ausgabe", "property_id": p1, "category_key": "schuldzinsen",
                                                                   "amount_cents": 30000 - m}, headers=HEADERS)
