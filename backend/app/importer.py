"""Massenimport aus Dropbox oder einem Ordner, mit fester Ordner-Zuordnung.

Aufruf im Container (siehe README, Abschnitt „Import aus Dropbox“):

    python -m app.importer plan  [--mapping DATEI] [--dir ORDNER]   # Probelauf: zeigt nur, was wohin käme
    python -m app.importer run   [--mapping DATEI] [--dir ORDNER]   # lädt und legt ab (fortsetzbar)
    python -m app.importer status                                   # Fortschritt von Import und Texterkennung

Ohne --dir wird direkt aus Dropbox gelesen; dafür muss DROPBOX_TOKEN gesetzt sein
(kurzlebiger Zugriffsschlüssel, nur Leserechte). Bereits importierte Dateien werden
bei einem erneuten Lauf übersprungen.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import documents, storage
from .classify import category_paths
from .db import audit, connect, migrate, now, tx
from .seed import seed

DEFAULT_MAPPING = Path(__file__).parent / "import_mappings" / "dropbox.json"


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip("/").casefold()


@dataclass
class Item:
    source_id: str
    path: str            # relativ, mit „/“ getrennt, ohne führenden „/“
    size: int

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def folder(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""


# --- Quellen -----------------------------------------------------------------

class DropboxSource:
    name = "dropbox"
    api = "https://api.dropboxapi.com/2"
    content = "https://content.dropboxapi.com/2"

    def __init__(self, token: str) -> None:
        if not token:
            raise SystemExit("DROPBOX_TOKEN fehlt. Siehe README, Abschnitt „Import aus Dropbox“.")
        self.token = token

    def _call(self, url: str, body: dict | None = None, arg: dict | None = None, raw: bool = False) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if arg is not None:
            headers["Dropbox-API-Arg"] = json.dumps(arg, ensure_ascii=True)
        else:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode()
        last: Exception | None = None
        for attempt in range(4):
            req = urllib.request.Request(url, data=data if data is not None else b"", headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    payload = resp.read()
                return payload if raw else json.loads(payload)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                if exc.code == 401:
                    raise SystemExit("Dropbox-Zugriffsschlüssel ungültig oder abgelaufen (gilt 4 Stunden). "
                                     "Bitte neu erzeugen und den Lauf wiederholen – Erledigtes wird übersprungen.")
                if exc.code in (429, 500, 502, 503, 504):
                    last = RuntimeError(f"HTTP {exc.code}: {detail}")
                    time.sleep(int(exc.headers.get("Retry-After") or 2 ** attempt * 3))
                    continue
                raise RuntimeError(f"Dropbox HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
                time.sleep(2 ** attempt * 3)
        raise RuntimeError(f"Dropbox nicht erreichbar: {last}")

    def list(self) -> Iterator[Item]:
        res = self._call(f"{self.api}/files/list_folder", {"path": "", "recursive": True, "limit": 2000,
                                                           "include_non_downloadable_files": False})
        while True:
            for e in res.get("entries", []):
                if e.get(".tag") == "file":
                    yield Item(e["id"], unicodedata.normalize("NFC", e["path_display"].lstrip("/")), e.get("size", 0))
            if not res.get("has_more"):
                break
            res = self._call(f"{self.api}/files/list_folder/continue", {"cursor": res["cursor"]})

    def fetch(self, item: Item) -> bytes:
        return self._call(f"{self.content}/files/download", arg={"path": item.source_id}, raw=True)


class DirSource:
    name = "ordner"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise SystemExit(f"Ordner {root} existiert nicht.")

    def list(self) -> Iterator[Item]:
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                rel = unicodedata.normalize("NFC", p.relative_to(self.root).as_posix())
                yield Item(rel, rel, p.stat().st_size)

    def fetch(self, item: Item) -> bytes:
        return (self.root / item.source_id).read_bytes()


# --- Zuordnung -----------------------------------------------------------------

@dataclass
class Target:
    rule_index: int
    area: str
    category: str              # „auto“ oder Pfad wie „Verträge/Telefon und Internet“
    fallback: str
    property: str | None
    tags: list[str] = field(default_factory=list)

    def label(self) -> str:
        area = "Privat" if self.area == "privat" else "Vermietung"
        cat = f"automatisch, sonst {self.fallback}" if self.category == "auto" else self.category.replace("/", " › ")
        prop = f" · {self.property}" if self.property else ""
        return f"{area} › {cat}{prop}"


class Mapping:
    def __init__(self, path: Path) -> None:
        self.data = json.loads(Path(path).read_text("utf-8"))
        self.skip_ext = {e.lower() for e in self.data.get("skip", {}).get("extensions", [])}
        self.skip_folders = [_norm(f) for f in self.data.get("skip", {}).get("folders", [])]
        self.rules = self.data["rules"]

    @staticmethod
    def _match(folder: str, pattern: str) -> bool:
        f, p = _norm(folder), _norm(pattern)
        if p == "":
            return f == ""
        if p == "*":
            return True
        return f == p or f.startswith(p + "/") or fnmatch.fnmatchcase(f, p) or fnmatch.fnmatchcase(f, p + "/*")

    def skip_reason(self, item: Item) -> str | None:
        ext = Path(item.name).suffix.lower()
        if ext in self.skip_ext or not ext:
            return f"Dateityp {ext or '(ohne Endung)'} wird nicht übernommen"
        if any(self._match(item.folder, f) for f in self.skip_folders):
            return "Ordner ist vom Import ausgenommen"
        return None

    def target(self, item: Item) -> Target | None:
        segments = item.folder.split("/") if item.folder else []
        for idx, rule in enumerate(self.rules):
            patterns = rule["match"] if isinstance(rule["match"], list) else [rule["match"]]
            if any(self._match(item.folder, p) for p in patterns):
                tags = []
                for t in rule.get("tags", []):
                    if t.startswith("{") and t.endswith("}"):
                        n = int(t[1:-1])
                        if len(segments) >= n:
                            tags.append(segments[n - 1])
                    else:
                        tags.append(t)
                return Target(idx, rule["area"], rule.get("category", "auto"), rule.get("fallback", "Sonstiges"),
                              rule.get("property"), tags)
        return None


class Resolver:
    """Übersetzt Namen aus der Zuordnung in IDs und legt fehlende Ordner/Objekte an."""

    def __init__(self, conn, mapping: Mapping, create: bool) -> None:
        self.conn = conn
        self.create = create
        self.areas = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM areas")}
        if create:
            with tx(conn):
                for c in mapping.data.get("create_categories", []):
                    self.category_id(c["area"], c["path"], c.get("keywords", ""), c.get("ai_enabled", True))
                for name, p in mapping.data.get("properties", {}).items():
                    self.property_id(name, p.get("address", ""), p.get("keywords", ""))

    def category_id(self, area: str, path: str, keywords: str = "", ai: bool = True) -> int | None:
        parent = None
        cid = None
        for name in path.split("/"):
            r = self.conn.execute(
                "SELECT id FROM categories WHERE area_id = ? AND parent_id IS ? AND name = ? COLLATE NOCASE",
                (self.areas[area], parent, name)).fetchone()
            if r:
                cid = r[0]
            elif self.create:
                cid = self.conn.execute(
                    "INSERT INTO categories(area_id, parent_id, name, keywords, ai_enabled, sort) VALUES(?,?,?,?,?,?)",
                    (self.areas[area], parent, name, keywords, int(ai), 50)).lastrowid
                audit(self.conn, "created", "category", cid, {"name": name, "durch": "Import"}, actor="import")
            else:
                return None
            parent = cid
        return cid

    def property_id(self, name: str, address: str = "", keywords: str = "") -> int | None:
        r = self.conn.execute("SELECT id FROM properties WHERE name = ? COLLATE NOCASE AND archived = 0", (name,)).fetchone()
        if not r and address:
            street = address.split(",")[0].strip()
            r = self.conn.execute("SELECT id FROM properties WHERE archived = 0 AND (address LIKE ? OR name LIKE ?)",
                                  (street + "%", street + "%")).fetchone()
        if r:
            return r[0]
        if not self.create:
            return None
        pid = self.conn.execute(
            "INSERT INTO properties(name, address, keywords, created_at) VALUES(?,?,?,?)",
            (name, address, keywords, now())).lastrowid
        audit(self.conn, "created", "property", pid, {"name": name, "durch": "Import"}, actor="import")
        return pid

    def preset(self, t: Target) -> dict[str, Any]:
        cat = None if t.category == "auto" else self.category_id(t.area, t.category)
        if t.category != "auto" and cat is None:
            raise RuntimeError(f"Ordner „{t.category}“ fehlt im Bereich {t.area}")
        fallback = self.category_id(t.area, t.fallback)
        if fallback is None:
            raise RuntimeError(f"Ersatzordner „{t.fallback}“ fehlt im Bereich {t.area}")
        prop = self.property_id(t.property) if t.property else None
        if t.property and prop is None:
            raise RuntimeError(f"Mietobjekt „{t.property}“ fehlt")
        return {"area_id": self.areas[t.area], "category_id": cat, "fallback_category_id": fallback,
                "property_id": prop, "tags": t.tags}


# --- Befehle -------------------------------------------------------------------

def _source(args) -> DropboxSource | DirSource:
    if args.dir:
        return DirSource(args.dir)
    return DropboxSource(os.environ.get("DROPBOX_TOKEN", "").strip())


def _mb(n: int) -> str:
    return f"{n / 1e6:,.0f} MB".replace(",", ".")


def cmd_plan(args) -> int:
    mapping = Mapping(Path(args.mapping))
    src = _source(args)
    print("Lese Dateiliste …", flush=True)
    items = list(src.list())
    per_target: dict[str, list[Item]] = defaultdict(list)
    skipped: Counter = Counter()
    unmatched: Counter = Counter()
    for it in items:
        reason = mapping.skip_reason(it)
        if reason:
            skipped[reason] += 1
            continue
        t = mapping.target(it)
        if not t:
            unmatched[it.folder or "/"] += 1
            continue
        per_target[t.label()].append(it)
    total = sum(len(v) for v in per_target.values())
    print(f"\n{len(items)} Dateien gefunden, {total} würden importiert ({_mb(sum(i.size for v in per_target.values() for i in v))}).\n")
    print("Ziel in SmartArchive                                              Dateien")
    print("-" * 76)
    for label, its in sorted(per_target.items(), key=lambda kv: -len(kv[1])):
        print(f"{label[:66]:66} {len(its):6}")
    if skipped:
        print("\nNicht übernommen:")
        for reason, n in skipped.most_common():
            print(f"  {n:5}  {reason}")
    if unmatched:
        print("\nOhne Regel (würden NICHT importiert):")
        for folder, n in unmatched.most_common():
            print(f"  {n:5}  {folder}")
    print("\nProbelauf – es wurde nichts verändert.")
    return 0


def cmd_run(args) -> int:
    mapping = Mapping(Path(args.mapping))
    src = _source(args)
    conn = connect()
    migrate(conn)
    seed(conn)
    resolver = Resolver(conn, mapping, create=True)
    done = {r[0] for r in conn.execute(
        "SELECT source_id FROM import_items WHERE source = ? AND status IN ('imported','duplicate','skipped')", (src.name,))}
    print("Lese Dateiliste …", flush=True)
    items = [i for i in src.list() if i.source_id not in done]
    if args.limit:
        items = items[: args.limit]
    print(f"{len(items)} Dateien zu verarbeiten ({len(done)} bereits erledigt).", flush=True)
    stats: Counter = Counter()
    started = time.time()

    def record(item: Item, status: str, doc_id: int | None = None, sha: str | None = None, note: str = "") -> None:
        conn.execute(
            "INSERT INTO import_items(source, source_id, path, size, sha256, document_id, status, note, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source, source_id) DO UPDATE SET status = excluded.status, "
            "document_id = excluded.document_id, sha256 = excluded.sha256, note = excluded.note, created_at = excluded.created_at",
            (src.name, item.source_id, item.path, item.size, sha, doc_id, status, note[:500], now()))
        stats[status] += 1

    for n, item in enumerate(items, start=1):
        reason = mapping.skip_reason(item)
        target = None if reason else mapping.target(item)
        if reason or not target:
            record(item, "skipped", note=reason or "keine Regel")
        else:
            try:
                preset = resolver.preset(target)
                data = src.fetch(item)
                status, doc_id, sha = documents.ingest_import(
                    conn, item.name, data, area=target.area, preset=preset, source_path=item.path)
                record(item, status, doc_id, sha, "" if status != "skipped" else "Dateityp nicht unterstützt")
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 – einzelne Fehler dürfen den Import nicht stoppen
                record(item, "failed", note=str(exc))
                print(f"  Fehler bei {item.path}: {exc}", flush=True)
        if n % 25 == 0 or n == len(items):
            rate = n / max(1.0, time.time() - started)
            print(f"  {n}/{len(items)}  importiert {stats['imported']}, doppelt {stats['duplicate']}, "
                  f"übersprungen {stats['skipped']}, Fehler {stats['failed']}  ({rate:.1f}/s)", flush=True)
    print("\nFertig. Die Texterkennung läuft jetzt im Hintergrund – Fortschritt: python -m app.importer status")
    return 1 if stats["failed"] else 0


def cmd_status(args) -> int:
    conn = connect()
    migrate(conn)
    print("Import:")
    for status, n in conn.execute("SELECT status, COUNT(*) FROM import_items GROUP BY status ORDER BY 2 DESC"):
        print(f"  {status:10} {n:6}")
    queued = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'error' AND deleted_at IS NULL").fetchone()[0]
    filed = conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'filed' AND deleted_at IS NULL").fetchone()[0]
    print(f"\nTexterkennung: noch {queued} in der Warteschlange, {filed} Dokumente abgelegt, {failed} mit Fehler (Eingang › Nacharbeit).")
    rows = conn.execute("SELECT path, note FROM import_items WHERE status = 'failed' ORDER BY path LIMIT 20").fetchall()
    if rows:
        print("\nFehlgeschlagene Dateien (Lauf wiederholen versucht sie erneut):")
        for path, note in rows:
            print(f"  {path}: {note[:120]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.importer", description="Massenimport in SmartArchive")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--mapping", default=str(DEFAULT_MAPPING))
        p.add_argument("--dir", help="lokaler Ordner statt Dropbox")
        if name == "run":
            p.add_argument("--limit", type=int, default=0, help="nur die ersten N Dateien (zum Testen)")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    return {"plan": cmd_plan, "run": cmd_run, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
