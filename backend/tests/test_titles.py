"""Automatische Titel."""
from __future__ import annotations

import pytest

from conftest import HEADERS, INVOICE, make_pdf, make_scan, run_worker


@pytest.mark.parametrize("name,generic", [
    ("IMG_20261028_160503.jpg", True), ("PXL_20261028_150503123.jpg", True), ("Scan 2026-10-28 16.05.03.jpg", True),
    ("Scan 2026-10-28 (2 Seiten)", True), ("document(3).pdf", True), ("WhatsApp Image 2026-10-28 at 16.05.03.jpeg", True),
    ("CamScanner 10-28-2026 16.05.pdf", True), ("rechnung.pdf", True), ("Foto 2026-10-28 16.05.03.jpg", True),
    ("Mietvertrag Müller EG.pdf", False), ("Heizung Wartung 2026.pdf", False), ("Teilungserklärung", False),
])
def test_generic_names(name, generic):
    from app.titles import is_generic
    assert is_generic(name) is generic


def test_make_title_variants():
    from app.titles import make_title
    t = make_title("\n".join(INVOICE), doc_type="Rechnung", correspondent=None, doc_date="2026-10-27",
                   payee_guess="Heizungsbau Mueller GmbH")
    assert t == "Rechnung Heizungsbau Mueller GmbH – Heizungswartung Gasbrennwertkessel 10/2026"
    t = make_title("Verbandsgemeinde Nahe-Glan\nGrundsteuerbescheid 2026\nBetreff: Grundbesitzabgaben Hauptstr. 15",
                   doc_type="Bescheid", correspondent="Verbandsgemeinde Nahe-Glan", doc_date="2026-01-15")
    assert t == "Grundsteuerbescheid Verbandsgemeinde Nahe-Glan – Grundbesitzabgaben 2026"  # gekürzt, Jahr bleibt
    t = make_title("BAUMARKT HORNBACH\nKassenbon 4711\nSUMME 19,99", doc_type="Kassenbon", correspondent=None,
                   doc_date="2026-10-20", payee_guess="BAUMARKT HORNBACH")
    assert t == "Kassenbon Baumarkt Hornbach 10/2026"
    # nichts erkannt: erste aussagekräftige Zeile, sonst Datum
    assert make_title("Herrn Can\nEinladung zur Eigentümerversammlung\n", doc_type=None, correspondent=None,
                      doc_date=None, created="2026-10-28T10:00:00") == "Einladung zur Eigentümerversammlung 10/2026"
    assert make_title("", doc_type=None, correspondent=None, doc_date=None, created="2026-10-28T10:00:00") == "Dokument 10/2026"


def test_camera_photo_gets_title_but_own_filename_stays(client):
    scan = make_scan(["BAUMARKT HORNBACH", "Kassenbon 4711", "Datum 20.10.2026", "Silikon weiss 2x  9,99", "SUMME 19,99 EUR"])
    r = client.post("/api/upload", data={"area": "privat"}, headers=HEADERS,
                    files=[("files", ("IMG_20261028_160503.jpg", scan, "image/jpeg")),
                           ("files", ("Mietvertrag Müller EG.pdf", make_pdf(["Mietvertrag", "zwischen A und B"]), "application/pdf"))])
    a, b = r.json()["created"]
    run_worker()
    ta = client.get(f"/api/documents/{a}").json()["title"]
    assert ta.startswith("Kassenbon Baumarkt Hornbach") and ta.endswith("10/2026"), ta
    assert client.get(f"/api/documents/{b}").json()["title"] == "Mietvertrag Müller EG"
