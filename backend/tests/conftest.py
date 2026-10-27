from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

HEADERS = {"X-Requested-With": "SmartArchive"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SETUP_TOKEN", "test-setup-token")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "static"))
    config.reload_settings()
    yield tmp_path
    config.reload_settings()


@pytest.fixture()
def client(env):
    from starlette.testclient import TestClient

    from app.main import app

    with TestClient(app, base_url="http://testserver") as c:
        r = c.post("/api/auth/setup", json={"setup_token": "test-setup-token", "username": "can",
                                             "password": "ein-sicheres-passwort"}, headers=HEADERS)
        assert r.status_code == 200, r.text
        yield c


def run_worker():
    from app import worker
    from app.db import connect

    conn = connect()
    try:
        return worker.run_pending(conn)
    finally:
        conn.close()


def make_pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(60, y, line)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def make_scan(lines: list[str]) -> bytes:
    """Simuliert ein Handyfoto eines Belegs (Bild ohne Textebene)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if Path(cand).exists():
            font = ImageFont.truetype(cand, 40)
            break
    font = font or ImageFont.load_default()
    y = 120
    for line in lines:
        draw.text((100, y), line, fill="black", font=font)
        y += 70
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


INVOICE = [
    "Heizungsbau Mueller GmbH - Industriestr. 4 - 55566 Bad Sobernheim",
    "",
    "Herrn Can",
    "Objekt: Hauptstr. 15, 55758 Schmidt-Hachenbach",
    "",
    "Rechnung Nr. 2026-1041",
    "Rechnungsdatum: 27.10.2026",
    "",
    "Pos. 1  Heizungswartung Gasbrennwertkessel        205,88",
    "Pos. 2  Dichtungssatz                               39,12",
    "",
    "Gesamtbetrag                                      245,00 EUR",
    "Zahlbar innerhalb von 14 Tagen ohne Abzug.",
]
