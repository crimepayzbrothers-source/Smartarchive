"""Mehrere Dokumente gemeinsam: ZIP als Ersatz für das Teilen mehrerer Dateien."""
from __future__ import annotations

import io
import zipfile

from conftest import HEADERS, make_pdf, run_worker


def test_bundle_zip(client):
    ids = []
    for name, lines in (("a.pdf", ["Rechnung Sanitaer", "Rechnungsdatum: 01.02.2026"]), ("b.pdf", ["Rechnung Sanitaer", "Datum: 01.02.2026", "Zweite"])):
        ids += client.post("/api/upload", data={"area": "privat"}, files=[("files", (name, make_pdf(lines), "application/pdf"))],
                           headers=HEADERS).json()["created"]
    run_worker()
    r = client.get("/api/documents/bundle.zip", params={"ids": ",".join(map(str, ids + [ids[0], 9999]))})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "SmartArchive_" in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == 2 and len(set(n.lower() for n in names)) == 2 and all(n.endswith(".pdf") for n in names)
    assert client.get("/api/documents/bundle.zip", params={"ids": ""}).status_code == 400
    assert client.get("/api/documents/bundle.zip", params={"ids": "x"}).status_code == 400
    assert client.get("/api/documents/bundle.zip", params={"ids": ",".join(str(i) for i in range(1, 60))}).status_code == 400
