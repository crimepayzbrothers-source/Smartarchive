"""Zugeschnittene Scans: bearbeitetes Bild wird zur Seite, das Kamerafoto bleibt unverändert erhalten."""
from __future__ import annotations

from conftest import HEADERS, make_scan, run_worker


def test_cropped_scan_keeps_raw_capture(client):
    raw1 = make_scan(["ROHFOTO SEITE 1", "mit Tisch drumherum"])
    page1 = make_scan(["Kassenbon 4711", "SUMME 19,99 EUR"])
    raw2 = make_scan(["ROHFOTO SEITE 2"])
    page2 = make_scan(["Vielen Dank fuer Ihren Einkauf"])
    files = [
        ("files", ("Scan S1.jpg", page1, "image/jpeg")),
        ("files", ("Scan S2.jpg", page2, "image/jpeg")),
        ("captures", ("Foto S1.jpg", raw1, "image/jpeg")),
        ("captures", ("Foto S2.jpg", raw2, "image/jpeg")),
    ]
    r = client.post("/api/upload", data={"area": "privat", "combine": "1", "capture_index": ["0", "1"]}, files=files, headers=HEADERS)
    assert r.status_code == 200, r.text
    doc_id = r.json()["created"][0]
    run_worker()
    d = client.get(f"/api/documents/{doc_id}").json()
    assert d["page_count"] == 2  # nur die zugeschnittenen Bilder sind Seiten
    caps = sorted((f["position"], f["original_name"]) for f in d["files"] if f["role"] == "capture")
    assert caps == [(0, "Foto S1.jpg"), (1, "Foto S2.jpg")]
    got = client.get(f"/api/documents/{doc_id}/file", params={"variant": "capture", "position": 1})
    assert got.status_code == 200 and got.content == raw2
    text = client.get(f"/api/documents/{doc_id}/text").json()["content"].upper()
    assert "KASSENBON" in text and "ROHFOTO" not in text


def test_capture_without_page_is_ignored(client):
    r = client.post("/api/upload", data={"area": "privat", "capture_index": ["5"]},
                    files=[("files", ("a.jpg", make_scan(["Hallo Welt"]), "image/jpeg")),
                           ("captures", ("roh.jpg", make_scan(["roh"]), "image/jpeg"))], headers=HEADERS)
    assert r.status_code == 200
    doc_id = r.json()["created"][0]
    run_worker()
    assert all(f["role"] != "capture" for f in client.get(f"/api/documents/{doc_id}").json()["files"])
