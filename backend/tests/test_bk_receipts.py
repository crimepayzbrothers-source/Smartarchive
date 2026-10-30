"""Belegprüfung Betriebskosten: Abgabenbescheid aus dem Archiv zerlegen, mit Beleg verknüpfen, Prüfung je Position."""
from __future__ import annotations

from conftest import HEADERS, make_pdf, run_worker
from test_betriebskosten import _setup

BESCHEID = ["Verbandsgemeinde Nahe-Glan", "Bescheid ueber Grundbesitzabgaben 2026", "Datum: 15.01.2026",
            "Fuer das Kalenderjahr 2026 werden festgesetzt:",
            "Grundsteuer B   Messbetrag 52,30   Hebesatz 465 %   Jahresbetrag 243,20 EUR",
            "Abfallentsorgung Restmüll 120 l, 4 Personen = 186,00 EUR",
            "Schmutzwasser 140 m3 x 2,95 = 413,00 EUR",
            "Niederschlagswasser 180 m2 x 0,48 = 86,40 EUR",
            "Straßenreinigung 12 m Frontlänge = 36,00 EUR",
            "Wiederkehrender Beitrag Verkehrsanlagen 95,50 EUR",
            "Gesamtbetrag 2026   1.060,10 EUR",
            "Fälligkeiten: 15.02.2026 265,03 EUR  15.05.2026 265,03 EUR",
            "Rückstand Vorjahr 12,00 EUR"]


def test_parse_bescheid():
    from app.bk_receipts import parse_bescheid
    r = parse_bescheid("\n".join(BESCHEID))
    assert [(p["cost_type"], p["amount_cents"]) for p in r["positions"]] == [
        ("grundsteuer", 24320), ("muell", 18600), ("entwaesserung", 41300), ("entwaesserung", 8640), ("strassenreinigung", 3600),
        ("nicht_umlagefaehig", 9550)]
    assert r["total_matches"] and r["total"] == 106010 and all(p["confidence"] == "hoch" for p in r["positions"])
    assert "Jahresbetrag 243,20" in r["positions"][0]["evidence"]
    # Vierteljahresraten in einer Zeile → Summe, aber nur „prüfen“
    q = parse_bescheid("Grundsteuer B 60,80 60,80 60,80 60,80\nSumme 243,20")
    assert q["positions"][0]["amount_cents"] == 24320 and q["positions"][0]["confidence"] == "pruefen"
    # Betrag in der Folgezeile (Tabellenspalten)
    n = parse_bescheid("Niederschlagswasser\n86,40 EUR")
    assert n["positions"][0]["amount_cents"] == 8640 and "Niederschlagswasser 86,40" in n["positions"][0]["evidence"]


def test_scan_import_link_and_verify(client):
    pid, units, t, period = _setup(client)
    r = client.post("/api/upload", data={"area": "vermietung", "property_id": str(pid)},
                    files=[("files", ("abgaben.pdf", make_pdf(BESCHEID), "application/pdf"))], headers=HEADERS)
    doc = r.json()["created"][0]
    # nicht relevant: Bescheid eines anderen Jahres ohne Dauerwirkung
    client.post("/api/upload", data={"area": "vermietung", "property_id": str(pid)},
                files=[("files", ("alt.pdf", make_pdf(["Bescheid Grundbesitzabgaben", "Datum: 10.01.2024", "Kalenderjahr 2024",
                                                       "Grundsteuer B Jahresbetrag 230,00 EUR"]), "application/pdf"))], headers=HEADERS)
    run_worker()
    scan = client.get(f"/api/bk/periods/{period}/receipt-scan").json()
    assert [s["document_id"] for s in scan] == [doc]
    s = scan[0]
    assert s["kind"] == "bescheid" and s["total_matches"] and len(s["positions"]) == 6

    # Ohne Betrag nicht übernehmbar
    bad = client.post(f"/api/bk/periods/{period}/receipt-scan/import",
                      json={"items": [{"document_id": doc, "cost_type": "grundsteuer", "amount_cents": None}]}, headers=HEADERS)
    assert bad.status_code == 400

    items = [{"document_id": doc, "cost_type": p["cost_type"], "amount_cents": p["amount_cents"], "evidence": p["evidence"]}
             for p in s["positions"]]
    out = client.post(f"/api/bk/periods/{period}/receipt-scan/import", json={"items": items}, headers=HEADERS).json()
    assert out["imported"] == 6
    lines = {l["cost_type"]: l for l in out["lines"]}
    assert lines["entwaesserung"]["total"] == 49940 and lines["grundsteuer"]["total"] == 24320
    assert lines["nicht_umlagefaehig"]["total"] == 9550
    gs = lines["grundsteuer"]["items"][0]
    assert gs["document_id"] == doc and gs["origin"] == "beleg" and "Jahresbetrag" in gs["evidence"] and gs["verified_at"] is None
    codes = [i["code"] for i in out["issues"]]
    assert "ungeprueft" in codes and not any(c.startswith("doppelt") for c in codes)
    assert out["receipts"]["unverified"] == 6
    # Beleg ist jetzt verbraucht: weder in der Belegprüfung noch als Ausgabe (Gesamtbetrag) erneut übernehmbar
    assert client.get(f"/api/bk/periods/{period}/receipt-scan").json() == []
    assert all(e["document_id"] != doc for e in client.get(f"/api/bk/periods/{period}/importable").json())

    # Prüfen: einzeln als geprüft markieren; Änderung gilt ebenfalls als geprüft
    for l in out["lines"]:
        for c in l["items"]:
            out = client.patch(f"/api/bk/costs/{c['id']}", json={"verified": True}, headers=HEADERS).json()
    assert "ungeprueft" not in [i["code"] for i in out["issues"]] and out["receipts"]["unverified"] == 0
    out = client.patch(f"/api/bk/costs/{gs['id']}", json={"verified": False}, headers=HEADERS).json()
    assert out["receipts"]["unverified"] == 1
    out = client.patch(f"/api/bk/costs/{gs['id']}", json={"amount_cents": 24321}, headers=HEADERS).json()
    assert out["receipts"]["unverified"] == 0


def test_search_limited_to_billing_period(client):
    pid, units, t, period = _setup(client)

    def up(name, lines):
        return client.post("/api/upload", data={"area": "vermietung", "property_id": str(pid)},
                           files=[("files", (name, make_pdf(lines), "application/pdf"))], headers=HEADERS).json()["created"][0]
    d26 = up("s26.pdf", ["Bezirksschornsteinfeger Klein", "Rechnungsdatum: 12.03.2026", "Kehrarbeiten Feuerstaettenschau",
                         "Rechnungsbetrag 96,40 EUR"])
    up("s25.pdf", ["Bezirksschornsteinfeger Klein", "Rechnungsdatum: 11.03.2025", "Kehrarbeiten", "Rechnungsbetrag 91,00 EUR"])
    run_worker()
    r = client.get(f"/api/bk/periods/{period}/receipt-search", params={"q": "Schornsteinfeger"}).json()
    assert [x["document_id"] for x in r["results"]] == [d26] and (r["from"], r["to"]) == ("2026-01-01", "2026-12-31")
    hit = r["results"][0]
    assert hit["linked"] is False and hit["positions"][0]["cost_type"] == "schornstein" and hit["positions"][0]["amount_cents"] == 9640
    assert client.get(f"/api/bk/periods/{period}/receipt-search", params={"q": ""}).status_code == 400

    # Treffer übernehmen (auch wenn als Ausgabe erfasst), danach als „bereits übernommen“ markiert
    out = client.post(f"/api/bk/periods/{period}/receipt-scan/import",
                      json={"items": [{"document_id": d26, "cost_type": "schornstein", "amount_cents": 9640, "evidence": hit["why"]}]},
                      headers=HEADERS).json()
    assert out["imported"] == 1
    assert client.get(f"/api/bk/periods/{period}/receipt-search", params={"q": "Schornsteinfeger"}).json()["results"][0]["linked"] is True
    # Beleg außerhalb des Zeitraums lässt sich nicht unterschieben
    other = [d["id"] for d in client.get("/api/documents", params={"q": "Schornsteinfeger"}).json()["results"] if d["id"] != d26][0]
    bad = client.post(f"/api/bk/periods/{period}/receipt-scan/import",
                      json={"items": [{"document_id": other, "cost_type": "schornstein", "amount_cents": 9100}]}, headers=HEADERS)
    assert bad.status_code == 400
