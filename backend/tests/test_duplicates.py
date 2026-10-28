"""Inhaltliche Dubletten beim Upload: Hinweis, überschreiben, verwerfen, behalten."""
from __future__ import annotations

from conftest import HEADERS, INVOICE, make_pdf, make_scan, run_worker


def _prop(client):
    return client.post("/api/properties", json={"name": "Hauptstraße 15", "address": "Hauptstraße 15, 55758 Schmidt-Hachenbach"},
                       headers=HEADERS).json()["id"]


def _up(client, name, data, mime, pid=None):
    form = {"area": "vermietung"}
    if pid:
        form["property_id"] = str(pid)
    r = client.post("/api/upload", data=form, files=[("files", (name, data, mime))], headers=HEADERS)
    assert r.status_code == 200, r.text
    run_worker()
    return r.json()["created"][0]


def _first_and_photo(client):
    pid = _prop(client)
    first = _up(client, "rechnung.pdf", make_pdf(INVOICE), "application/pdf", pid)
    assert client.get(f"/api/documents/{first}").json()["status"] == "filed"
    photo = _up(client, "IMG_2.jpg", make_scan(INVOICE), "image/jpeg", pid)  # dieselbe Rechnung als Foto
    return pid, first, photo


def test_same_invoice_as_photo_is_flagged(client):
    pid, first, photo = _first_and_photo(client)
    d = client.get(f"/api/documents/{photo}").json()
    assert d["status"] == "inbox" and d["expenses"] == []  # nichts doppelt gezählt
    assert d["duplicate"]["existing"]["id"] == first and "Rechnungsnummer 2026-1041" in d["duplicate"]["reason"]
    inbox = client.get("/api/inbox").json()
    assert [x["id"] for x in inbox["duplicates"]] == [photo] and photo not in [x["id"] for x in inbox["pending"]]
    assert client.get("/api/expenses/summary", params={"year": 2026}).json()["total"] == 24500


def test_replace_keeps_existing_record(client):
    pid, first, photo = _first_and_photo(client)
    before = client.get(f"/api/documents/{first}").json()
    r = client.post(f"/api/documents/{photo}/duplicate", json={"action": "replace"}, headers=HEADERS).json()
    assert r["document_id"] == first
    d = client.get(f"/api/documents/{first}").json()
    roles = sorted((f["role"], f["mime"]) for f in d["files"])
    assert ("original", "image/jpeg") in roles and ("replaced", "application/pdf") in roles  # altes Original bleibt erhalten
    assert d["category_id"] == before["category_id"] and d["property_id"] == pid
    assert [e["amount_cents"] for e in d["expenses"]] == [24500]
    assert client.get(f"/api/documents/{photo}").status_code == 404
    assert client.get(f"/api/documents/{first}/file").status_code == 200
    assert client.get("/api/inbox").json()["duplicates"] == []


def test_discard_moves_upload_to_trash(client):
    pid, first, photo = _first_and_photo(client)
    r = client.post(f"/api/documents/{photo}/duplicate", json={"action": "discard"}, headers=HEADERS).json()
    assert r["document_id"] == first
    assert client.get(f"/api/documents/{photo}").json()["deleted_at"]
    assert client.get("/api/expenses/summary", params={"year": 2026}).json()["total"] == 24500


def test_keep_files_normally(client):
    pid, first, photo = _first_and_photo(client)
    client.post(f"/api/documents/{photo}/duplicate", json={"action": "keep"}, headers=HEADERS)
    run_worker()
    d = client.get(f"/api/documents/{photo}").json()
    assert d["duplicate"] is None and d["status"] == "filed" and len(d["expenses"]) == 1


def test_different_invoices_same_amount_and_date_are_not_duplicates(client):
    pid = _prop(client)
    a = ["Maler GmbH", "Rechnung Nr. A-100", "Rechnungsdatum: 01.09.2026", "Treppenhaus streichen", "Gesamtbetrag 500,00 EUR"]
    b = ["Elektro Becker", "Rechnung Nr. EB-77", "Rechnungsdatum: 01.09.2026", "Zaehlerschrank erneuert, Sicherungen",
         "Gesamtbetrag 500,00 EUR"]
    _up(client, "a.pdf", make_pdf(a), "application/pdf", pid)
    second = _up(client, "b.pdf", make_pdf(b), "application/pdf", pid)
    d = client.get(f"/api/documents/{second}").json()
    assert d["duplicate"] is None and d["status"] == "filed"


def test_exact_same_file_is_rejected_at_upload(client):
    pid = _prop(client)
    data = make_pdf(INVOICE)
    first = _up(client, "a.pdf", data, "application/pdf", pid)
    r = client.post("/api/upload", data={"area": "vermietung"}, files=[("files", ("kopie.pdf", data, "application/pdf"))],
                    headers=HEADERS).json()
    assert r["created"] == [] and r["duplicates"][0]["document_id"] == first
