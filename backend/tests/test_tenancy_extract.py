"""Mietverhältnis aus Mietvertrag oder Screenshot auslesen."""
from __future__ import annotations

import io

from conftest import HEADERS

UNITS = [{"id": i + 1, "name": n} for i, n in enumerate(["EG", "OG links", "OG Mitte", "OG rechts", "Galerie"])]

CONTRACT = """Wohnraummietvertrag
zwischen Can Beispiel, Musterweg 1, 55566 Bad Sobernheim – nachfolgend Vermieter –
und Herrn Max Mustermann und Frau Erika Mustermann, bisher wohnhaft Feldweg 3, 55758 Sien – nachfolgend Mieter genannt –
§ 1 Mietsache
Vermietet wird die Wohnung im Obergeschoss Mitte des Hauses Hauptstraße 15, 55758 Schmidt-Hachenbach.
Die Wohnfläche beträgt ca. 48,5 m².
§ 2 Mietzeit
Das Mietverhältnis beginnt am 1. Mai 2021 und läuft auf unbestimmte Zeit.
§ 3 Miete
Die Grundmiete beträgt monatlich 420,00 EUR.
Vorauszahlung auf die Betriebskosten: 110,00 EUR
Vorauszahlung auf die Heizkosten: 65,00 EUR
Gesamtmiete: 595,00 EUR
Die Wohnung wird von 2 Personen bewohnt. E-Mail: max.mustermann@example.org Tel. 0170 1234567"""

SCREENSHOT = """Mietverhältnis
Wohneinheit
Galerie
Mieter
Vorname
Julia
Nachname
Sommer
Mietbeginn
15.08.2023
Kaltmiete
350,00 €
Betriebskosten
90,00 €
Heizkosten
55,00 €
Gesamt 495,00 €"""


def test_parse_contract_text():
    from app.tenancy_extract import parse
    r = parse(CONTRACT, UNITS, ["Can Beispiel"])
    assert [(p["first_name"], p["last_name"]) for p in r["parties"]] == [("Max", "Mustermann"), ("Erika", "Mustermann")]
    assert r["parties"][0]["email"] == "max.mustermann@example.org" and r["parties"][0]["phone"] == "0170 1234567"
    assert (r["contract_start"], r["cold_rent_cents"], r["bk_advance_cents"], r["hk_advance_cents"], r["persons"]) == \
        ("2021-05-01", 42000, 11000, 6500, 2)
    assert r["unit_id"] == 3 and r["living_area"] == 48.5 and r["missing"] == []
    assert "Grundmiete" in r["evidence"]["cold_rent_cents"] and "Gesamtmiete" not in r["evidence"]["cold_rent_cents"]


def test_parse_screenshot_and_rules():
    from app.tenancy_extract import match_unit, parse
    r = parse(SCREENSHOT, UNITS, [])
    assert r["parties"][0]["first_name"] == "Julia" and r["parties"][0]["last_name"] == "Sommer"
    assert (r["unit_id"], r["contract_start"], r["cold_rent_cents"], r["bk_advance_cents"], r["hk_advance_cents"]) == \
        (5, "2023-08-15", 35000, 9000, 5500)
    # Mehrdeutige Wohnung: nicht raten
    assert match_unit("Wohnung im 1. OG", UNITS)[0] is None
    assert match_unit("Erdgeschoss rechts", [{"id": 1, "name": "EG"}, {"id": 2, "name": "OG rechts"}])[0] == 1
    # Vermieter ist kein Mieter; fehlende Angaben werden gemeldet
    r = parse("Mieter: Can Beispiel\nMietbeginn: 01.02.2019", UNITS, ["Can Beispiel"])
    assert "parties" in r["missing"] and "unit_id" in r["missing"] and r["contract_start"] == "2019-02-01"
    # Nur Einzug genannt → als Mietbeginn vorgeschlagen, mit Hinweis
    r = parse("Mieter: Peter Schmidt\nEinzug: 01.03.2020", UNITS, [])
    assert r["contract_start"] == "2020-03-01" and "bitte prüfen" in r["evidence"]["contract_start"]


def _pid(client):
    pid = client.post("/api/properties", json={"name": "Hauptstraße 15", "address": "Hauptstraße 15, 55758 Schmidt-Hachenbach"},
                      headers=HEADERS).json()["id"]
    for n in ("EG", "OG links", "OG Mitte", "OG rechts", "Galerie"):
        client.post(f"/api/properties/{pid}/units", json={"name": n}, headers=HEADERS)
    return pid


def test_extract_endpoint_pdf_and_screenshot(client):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.pdfgen import canvas
    pid = _pid(client)
    # Digitales PDF (Textebene)
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setFont("Helvetica", 7.5)
    y = 800
    for line in CONTRACT.replace("–", "-").splitlines():
        c.drawString(30, y, line)
        y -= 16
    c.save()
    r = client.post("/api/tenancies/extract", data={"property_id": str(pid)}, files=[("files", ("vertrag.pdf", buf.getvalue(), "application/pdf"))],
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["contract_start"] == "2021-05-01" and res["cold_rent_cents"] == 42000 and res["bk_advance_cents"] == 11000
    assert len(res["parties"]) == 2

    # Screenshot (Bild) über die Texterkennung
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
    im = Image.new("RGB", (900, 1100), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(["Wohneinheit: Galerie", "Vorname: Julia", "Nachname: Sommer", "Mietbeginn: 15.08.2023",
                              "Kaltmiete: 350,00 EUR", "Betriebskosten: 90,00 EUR", "Heizkosten: 55,00 EUR"]):
        d.text((40, 40 + i * 70), line, fill="black", font=font)
    png = io.BytesIO()
    im.save(png, format="PNG")
    r = client.post("/api/tenancies/extract", data={"property_id": str(pid), "use_ai": "1"},
                    files=[("files", ("screenshot.png", png.getvalue(), "image/png"))], headers=HEADERS)
    assert r.status_code == 200, r.text
    out = r.json()
    res = out["results"][0]
    assert out["ai_used"] is False and "nicht eingerichtet" in out["ai_error"]  # ohne Schlüssel: nur lokal
    assert (res["contract_start"], res["cold_rent_cents"], res["bk_advance_cents"], res["hk_advance_cents"]) == \
        ("2023-08-15", 35000, 9000, 5500)
    assert res["parties"][0]["last_name"] == "Sommer"
    # Es wurde nichts gespeichert
    assert client.get("/api/tenancies").json() == []
    assert client.post("/api/tenancies/extract", data={"property_id": str(pid)}, files=[("files", ("x.txt", b"hallo", "text/plain"))],
                       headers=HEADERS).status_code == 400


def test_upload_documents_to_tenancy(client):
    from conftest import make_pdf, run_worker
    pid = _pid(client)
    uid = client.get(f"/api/properties/{pid}").json()["units"][2]["id"]
    t = client.post("/api/tenancies", json={"property_id": pid, "unit_id": uid, "contract_start": "2021-05-01",
                                            "parties": [{"first_name": "Max", "last_name": "Mustermann"}],
                                            "terms": [{"valid_from": "2021-05-01", "cold_rent_cents": 42000}]}, headers=HEADERS).json()
    files = [("files", ("vertrag.pdf", make_pdf(["Wohnraummietvertrag Hauptstrasse 15", "Mieter Max Mustermann"]), "application/pdf")),
             ("files", ("uebergabe.pdf", make_pdf(["Uebergabeprotokoll", "Zaehlerstand Wasser 123"]), "application/pdf"))]
    r = client.post("/api/upload", data={"tenancy_id": str(t["id"]), "area": "auto"}, files=files, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert len(r.json()["created"]) == 2 and r.json()["tenancy_id"] == t["id"]
    run_worker()
    docs = client.get(f"/api/tenancies/{t['id']}").json()["documents"]
    assert len(docs) == 2
    d = client.get(f"/api/documents/{docs[0]['id']}").json()
    assert d["property_id"] == pid and d["unit_id"] == uid
    assert client.post("/api/upload", data={"tenancy_id": "999"}, files=files[:1], headers=HEADERS).status_code == 400



def test_preview_pages_for_manual_entry(client):
    from conftest import make_pdf
    from PIL import Image
    png = io.BytesIO()
    Image.new("RGB", (400, 600), "white").save(png, format="PNG")
    files = [("files", ("vertrag.pdf", make_pdf(["Seite 1"]), "application/pdf")), ("files", ("foto.png", png.getvalue(), "image/png"))]
    r = client.post("/api/tenancies/preview", files=files, headers=HEADERS)
    assert r.status_code == 200, r.text
    pages = r.json()["pages"]
    assert [(p["name"], p["page"]) for p in pages] == [("vertrag.pdf", 1), ("foto.png", 1)]
    assert all(p["src"].startswith("data:image/jpeg;base64,") for p in pages)
    assert client.get("/api/tenancies").json() == []  # nichts gespeichert
