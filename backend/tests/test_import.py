from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import HEADERS, INVOICE, make_pdf, make_scan, run_worker


@pytest.fixture()
def tree(tmp_path):
    """Kleiner Nachbau der Dropbox-Struktur."""
    root = tmp_path / "dropbox"
    invoice = make_pdf(INVOICE)
    files = {
        "Can Finanzierung/Belege 2024/Rechnung Heizung.pdf": invoice,
        "Can Finanzierung/Ista Betriebskosten/Abrechnung 2023.pdf": make_pdf(["ista Deutschland", "Betriebskostenabrechnung 2023"]),
        "Can Finanzierung/Mietobjekt Schmidthachenbach/Grundbuchauszug.pdf": make_pdf(["Grundbuch von Schmidthachenbach", "Notar"]),
        "Can (Privat)/Einkommensteuerbescheide/ESt 2024.pdf": make_pdf(["Finanzamt Idar-Oberstein", "Einkommensteuerbescheid 2024"]),
        "Can (Privat)/Debeka/IMG_2041.jpg": make_scan(["Debeka Krankenversicherung", "Beitragsrechnung 2026"]),
        "Berliner Straße 21/Hausgeldabrechnung/HGA 2025.pdf": make_pdf(["Hausgeldabrechnung 2025", "WEG Berliner Straße 21"]),
        "Ugur/Lohn.pdf": make_pdf(["Lohnabrechnung Oktober"]),
        "Spareinlage Online.PDF": make_pdf(["Sparkasse", "Spareinlage"]),
        "Can (Privat)/Video.mp4": b"\x00\x00\x00\x18ftypmp42",
        "Cans Fitness App/notes.pdf": make_pdf(["App"]),
        "Can Finanzierung/Belege 2024/Kopie Rechnung Heizung.pdf": invoice,  # Dublette
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _imp(*args):
    from app import importer
    return importer.main(list(args))


def test_plan_changes_nothing(client, tree, capsys):
    assert _imp("plan", "--dir", str(tree)) == 0
    out = capsys.readouterr().out
    assert "Vermietung › Rechnungen und Belege · Hauptstraße 15" in out
    assert "Privat › Wohnen" in out
    assert "Dateityp .mp4" in out and "ausgenommen" in out
    assert client.get("/api/documents", params={"status": "processing"}).json()["total"] == 0
    assert client.get("/api/properties").json()["properties"] == []


def test_run_files_documents_by_folder(client, tree, capsys):
    assert _imp("run", "--dir", str(tree)) == 0
    out = capsys.readouterr().out
    assert "importiert 8" in out and "doppelt 1" in out and "übersprungen 2" in out
    run_worker()

    meta = client.get("/api/meta").json()
    props = {p["name"]: p["id"] for p in meta["properties"]}
    assert "Hauptstraße 15" in props
    assert any(c["name"] == "Wohnen" and c["area_key"] == "privat" for c in meta["categories"])

    docs = client.get("/api/documents", params={"limit": 50}).json()["results"]
    by_title = {d["title"]: d for d in docs}
    assert len(docs) == 8
    assert all(d["status"] == "filed" for d in docs)

    heizung = by_title.get("Rechnung Heizung") or by_title["Kopie Rechnung Heizung"]  # eine von beiden ist die Dublette
    assert heizung["area_key"] == "vermietung" and heizung["property_name"] == "Hauptstraße 15"
    assert heizung["category_path"] == "Vermietung › Rechnungen und Belege"
    assert "Belege 2024" in heizung["tags"]
    assert by_title["Abrechnung 2023"]["category_path"] == "Vermietung › Nebenkosten"
    assert by_title["Grundbuchauszug"]["category_path"] == "Vermietung › Kauf und Objektunterlagen"
    assert by_title["ESt 2024"]["category_path"] == "Privat › Steuern"
    assert by_title["HGA 2025"]["category_path"] == "Privat › Wohnen"
    assert by_title["Lohn"]["category_path"] == "Privat › Familie" and by_title["Lohn"]["tags"] == ["Ugur"]
    assert by_title["Spareinlage Online"]["category_path"] == "Privat › Finanzen und Banken"
    # Foto mit Allerweltsnamen bekommt einen sprechenden Titel
    debeka = next(d for d in docs if d["category_path"] == "Privat › Versicherungen")
    assert debeka["title"].startswith("Debeka") and debeka["tags"] == ["Debeka"]

    d = client.get(f"/api/documents/{heizung['id']}").json()
    assert d["confirmed"] == 1 and d["filed_by"] == "import"
    assert d["source_path"].startswith("Can Finanzierung/Belege 2024/")
    # Suche über den alten Ordnernamen
    assert client.get("/api/documents", params={"q": "Belege 2024"}).json()["total"] >= 1
    # importierte Dokumente fluten nicht den Eingang
    inbox = client.get("/api/inbox").json()
    assert inbox["pending"] == [] and inbox["auto_filed"] == []


def test_run_is_resumable(client, tree, capsys):
    _imp("run", "--dir", str(tree), "--limit", "3")
    capsys.readouterr()
    _imp("run", "--dir", str(tree))
    out = capsys.readouterr().out
    assert "8 Dateien zu verarbeiten (3 bereits erledigt)" in out
    run_worker()
    assert client.get("/api/documents", params={"limit": 50}).json()["total"] == 8
    _imp("status")
    assert "imported" in capsys.readouterr().out


def test_import_jobs_have_lower_priority(client, tree):
    from app.db import connect

    _imp("run", "--dir", str(tree), "--limit", "2")
    r = client.post("/api/upload", data={"area": "privat"}, headers=HEADERS,
                    files=[("files", ("neu.pdf", make_pdf(["Ganz neuer Beleg"]), "application/pdf"))])
    new_id = r.json()["created"][0]
    conn = connect()
    first = conn.execute("SELECT document_id FROM jobs WHERE status = 'queued' ORDER BY priority, id LIMIT 1").fetchone()[0]
    conn.close()
    assert first == new_id


def test_dropbox_source_pagination_and_download(monkeypatch):
    from app import importer

    calls = []

    def fake_call(self, url, body=None, arg=None, raw=False):
        calls.append((url.rsplit("/2/", 1)[1], body, arg))
        if url.endswith("files/list_folder"):
            return {"entries": [{".tag": "folder", "path_display": "/Can (Privat)"},
                                {".tag": "file", "id": "id:1", "path_display": "/Can (Privat)/a.pdf", "size": 10}],
                    "has_more": True, "cursor": "c1"}
        if url.endswith("list_folder/continue"):
            return {"entries": [{".tag": "file", "id": "id:2", "path_display": "/Berliner Straße 21/b.pdf", "size": 5}],
                    "has_more": False}
        return b"%PDF-1.4 test"

    monkeypatch.setattr(importer.DropboxSource, "_call", fake_call)
    src = importer.DropboxSource("token")
    items = list(src.list())
    assert [i.path for i in items] == ["Can (Privat)/a.pdf", "Berliner Straße 21/b.pdf"]
    assert items[1].folder == "Berliner Straße 21"
    assert src.fetch(items[0]) == b"%PDF-1.4 test"
    assert calls[-1][2] == {"path": "id:1"}


def test_dropbox_header_is_ascii():
    # Dropbox verlangt ASCII im Header – Umlaute müssen als \\u-Escapes gehen
    assert json.dumps({"path": "/Berliner Straße"}, ensure_ascii=True).isascii()


def test_office_upload_is_converted(client, monkeypatch, tmp_path):
    from app import ocr

    def fake_convert(src: Path, workdir: Path, timeout: int = 900) -> Path:
        out = workdir / "converted.pdf"
        out.write_bytes(make_pdf(["Mietvertrag Wohnung OG links", "Hauptstraße 15"]))
        return out

    monkeypatch.setattr(ocr, "office_to_pdf", fake_convert)
    docx = b"PK\x03\x04" + b"\x00" * 100
    r = client.post("/api/upload", data={"area": "vermietung"}, headers=HEADERS,
                    files=[("files", ("Mietvertrag.docx", docx, "application/octet-stream"))])
    assert r.status_code == 200 and len(r.json()["created"]) == 1
    run_worker()
    d = client.get(f"/api/documents/{r.json()['created'][0]}").json()
    assert d["text_source"] == "digital" and d["has_pdf"]
    roles = {f["role"]: f["mime"] for f in d["files"]}
    assert roles["original"].endswith("wordprocessingml.document") and roles["pdf"] == "application/pdf"
    orig = client.get(f"/api/documents/{d['id']}/file", params={"variant": "original"})
    assert orig.content == docx


def test_office_without_libreoffice_goes_to_rework(client, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    r = client.post("/api/upload", data={"area": "privat"}, headers=HEADERS,
                    files=[("files", ("Tabelle.xlsx", b"PK\x03\x04xx", "application/octet-stream"))])
    doc_id = r.json()["created"][0]
    for _ in range(3):  # drei Versuche, dann Nacharbeit
        from app.db import connect
        conn = connect()
        conn.execute("UPDATE jobs SET run_after = '2000-01-01'")
        conn.close()
        run_worker()
    d = client.get(f"/api/documents/{doc_id}").json()
    assert d["status"] == "error" and "LibreOffice" in d["error"]
