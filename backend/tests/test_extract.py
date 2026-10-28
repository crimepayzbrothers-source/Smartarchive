"""„Aus Beleg auslesen“: Positionsdaten aus dem Dokumentinhalt."""
from __future__ import annotations

from conftest import HEADERS, INVOICE, make_pdf, make_scan, run_worker


def _up(client, name, data, mime="application/pdf", area="privat"):
    doc = client.post("/api/upload", data={"area": area}, files=[("files", (name, data, mime))], headers=HEADERS).json()["created"][0]
    run_worker()
    return doc


def test_extract_labeled_invoice(client):
    doc = _up(client, "r.pdf", make_pdf(INVOICE))
    x = client.get(f"/api/documents/{doc}/extract").json()
    assert x["text_available"] is True
    assert (x["amount_cents"], x["amount_source"]) == (24500, "label")
    assert (x["invoice_date"], x["paid_on"], x["paid_assumed"]) == ("2026-10-27", "2026-10-27", True)
    assert x["payee"].startswith("Heizungsbau Mueller") and x["category_key"] == "heizung"
    assert x["note"] == "Rechnung Nr. 2026-1041"


def test_extract_without_total_label_offers_choice(client):
    """Ohne beschrifteten Endbetrag kein Raten: alle Beträge zur Auswahl, auch Beispielwerte."""
    lines = ["Dach GmbH", "Datum 12.09.2026", "Dachrinne reinigen    180,00", "Material    45,50",
             "MwSt 19%    42,85", "  268,35 EUR", "Beispiel: Wartungsvertrag ab 1.999,00 jährlich"]
    doc = _up(client, "x.pdf", make_pdf(lines))
    x = client.get(f"/api/documents/{doc}/extract").json()
    assert x["amount_cents"] is None and x["needs_choice"] is True
    assert [c["amount_cents"] for c in x["candidates"]] == [18000, 4550, 4285, 26835, 199900]
    tags = {c["amount_cents"]: c["tag"] for c in x["candidates"]}
    assert tags[4285] == "Steuer"
    assert x["candidates"][-1]["line"].startswith("Beispiel: Wartungsvertrag")
    assert x["invoice_date"] == "2026-09-12"


def test_extract_from_photo(client):
    scan = make_scan(["BAUMARKT HORNBACH", "Datum 2O.1O.2026", "Silikon 2x  9,99", "SUMME 19,99 EUR", "BAR 20,00"])
    doc = _up(client, "IMG_1.jpg", scan, "image/jpeg")
    x = client.get(f"/api/documents/{doc}/extract").json()
    assert (x["amount_cents"], x["invoice_date"], x["paid_on"], x["paid_assumed"]) == (1999, "2026-10-20", "2026-10-20", False)


def test_extract_no_text(client):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(buf, "JPEG")
    doc = _up(client, "leer.jpg", buf.getvalue(), "image/jpeg")
    assert client.get(f"/api/documents/{doc}/extract").json() == {"text_available": False}


def test_extract_ai_amount_must_be_in_text(client, monkeypatch):
    from app import config, mistral
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    config.reload_settings()
    monkeypatch.setattr(mistral, "classify_with_ai", lambda prompt, timeout=45: {"category_id": None})
    lines = ["Schreiben der Hausverwaltung", "Bitte überweisen Sie 312,40 Euro bis Monatsende."]
    doc = _up(client, "brief.pdf", make_pdf(lines))
    monkeypatch.setattr(mistral, "extract_invoice", lambda text, timeout=30: {"amount_cents": 31240, "doc_date": "2026-08-01"})
    x = client.get(f"/api/documents/{doc}/extract").json()
    # KI-Vorschlag wird nur hervorgehoben, nicht eingesetzt
    assert x["amount_cents"] is None and x["suggested_cents"] == 31240 and x["needs_choice"] is True
    assert x["invoice_date"] == "2026-08-01"
    monkeypatch.setattr(mistral, "extract_invoice", lambda text, timeout=30: {"amount_cents": 99999})  # erfunden
    x = client.get(f"/api/documents/{doc}/extract").json()
    assert "suggested_cents" not in x
