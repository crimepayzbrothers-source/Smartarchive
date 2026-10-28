"""Automatische Ablage beim Upload.

Reihenfolge: eigene Regeln -> bestätigter Verlauf je Absender -> KI (Mistral)
-> lokale Stichwörter. Ergänzt wird immer um lokal erkannte Felder (Datum,
Betrag, Objekt, Absender). Entscheidet anschließend nach Sicherheit:
sofort ablegen, Vorschlag im Eingang oder Eingang ohne Vorschlag.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, extract, invoices, mistral, titles
from .db import audit, get_setting, now, rows
from .seed import EXPENSE_KEYS

log = logging.getLogger("smartarchive.classify")

SOURCE_LABEL = {
    "rule": "Regel",
    "history": "Bisherige Ablage",
    "ai": "KI",
    "keyword": "Stichwörter",
}


@dataclass
class Candidate:
    source: str
    confidence: float
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    evidence: str = ""


def category_paths(conn: sqlite3.Connection, ai_only: bool = False) -> list[dict[str, Any]]:
    cats = rows(conn.execute(
        "SELECT c.*, a.key AS area_key, a.name AS area_name, a.ai_enabled AS area_ai "
        "FROM categories c JOIN areas a ON a.id = c.area_id ORDER BY a.id, c.sort, c.name"))
    by_id = {c["id"]: c for c in cats}
    for c in cats:
        parts, depth, cur = [c["name"]], 0, c
        while cur["parent_id"]:
            cur = by_id[cur["parent_id"]]
            parts.insert(0, cur["name"])
            depth += 1
        c["path"] = f"{c['area_name']} › " + " › ".join(parts)
        c["depth"] = depth
        # gesperrt, wenn die Kategorie oder eine übergeordnete gesperrt ist
        blocked, cur = not c["ai_enabled"], c
        while cur["parent_id"] and not blocked:
            cur = by_id[cur["parent_id"]]
            blocked = not cur["ai_enabled"]
        c["ai_blocked"] = blocked or not c["area_ai"]
    if ai_only:
        return [c for c in cats if not c["ai_blocked"]]
    return cats


def _rule_matches(rule: dict[str, Any], text: str, filename: str, correspondent: str) -> str | None:
    hay = {"text": text, "filename": filename, "correspondent": correspondent}.get(rule["match_field"], text)
    pattern = rule["pattern"].strip()
    if not pattern or not hay:
        return None
    if len(pattern) > 2 and pattern.startswith("/") and pattern.endswith("/"):
        try:
            m = re.search(pattern[1:-1], hay, re.I)
        except re.error:
            return None
        return m.group(0) if m else None
    idx = hay.lower().find(pattern.lower())
    return hay[idx:idx + len(pattern)] if idx >= 0 else None


def _history_candidate(conn: sqlite3.Connection, correspondent_id: int) -> Candidate | None:
    hist = rows(conn.execute(
        "SELECT category_id, property_id, area_id, COUNT(*) AS n FROM documents "
        "WHERE correspondent_id = ? AND confirmed = 1 AND status = 'filed' AND deleted_at IS NULL "
        "GROUP BY category_id, property_id, area_id ORDER BY n DESC",
        (correspondent_id,)))
    if not hist:
        return None
    total = sum(h["n"] for h in hist)
    top = hist[0]
    if top["n"] < 2 or top["n"] / total < 0.8:
        return None
    conf = 0.95 if top["n"] >= 3 else 0.92
    name = conn.execute("SELECT name FROM correspondents WHERE id = ?", (correspondent_id,)).fetchone()[0]
    return Candidate(
        "history", conf,
        {"area_id": top["area_id"], "category_id": top["category_id"], "property_id": top["property_id"],
         "correspondent_id": correspondent_id},
        f"{top['n']} bestätigte Dokumente von „{name}“ liegen in dieser Ablage.",
    )


def _ai_candidate(conn: sqlite3.Connection, text: str, upload_area: str, cats: list[dict], props: list[dict],
                  correspondents: list[dict]) -> Candidate | None:
    allowed = [c for c in cats if not c["ai_blocked"]]
    if upload_area != "auto":
        allowed = [c for c in allowed if c["area_key"] == upload_area]
    if not allowed:
        return None
    prompt = mistral.build_prompt(text, allowed, props, [c["name"] for c in correspondents], upload_area)
    try:
        res = mistral.classify_with_ai(prompt)
    except mistral.AIError as exc:
        log.warning("KI-Einordnung nicht möglich: %s", exc)
        return None
    by_id = {c["id"]: c for c in allowed}
    try:
        cat_id = int(res.get("category_id"))
    except (TypeError, ValueError):
        return None
    if cat_id not in by_id:
        return None
    conf = max(0.0, min(1.0, float(res.get("confidence") or 0)))
    evidence = (res.get("evidence") or "").strip()
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()  # noqa: E731
    if evidence and norm(evidence) not in norm(text):
        conf = min(conf, 0.7)  # Zitat nicht im Dokument -> nicht vertrauenswürdig genug
        evidence = ""
    prop_ids = {p["id"] for p in props}
    prop_id = res.get("property_id")
    prop_id = int(prop_id) if isinstance(prop_id, (int, float, str)) and str(prop_id).isdigit() and int(prop_id) in prop_ids else None
    payload: dict[str, Any] = {
        "area_id": by_id[cat_id]["area_id"],
        "category_id": cat_id,
        "property_id": prop_id,
        "correspondent_name": (res.get("correspondent") or "").strip()[:80] or None,
        "doc_type": (res.get("doc_type") or "").strip()[:40] or None,
        "title": (res.get("title") or "").strip()[:80] or None,
        "doc_date": res.get("doc_date") if isinstance(res.get("doc_date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", res.get("doc_date") or "") else None,
    }
    amount = res.get("amount_cents")
    if isinstance(amount, (int, float)) and 0 < amount < 10_000_000_00:
        payload["amount_cents"] = int(amount)
    ai_invoice = {}
    if isinstance(res.get("invoice_number"), str) and res["invoice_number"].strip():
        ai_invoice["number"] = res["invoice_number"].strip()[:30]
    if res.get("expense_category") in EXPENSE_KEYS:
        ai_invoice["expense_category"] = res["expense_category"]
        ai_invoice["expense_reason"] = "Vorschlag der KI"
    if ai_invoice:
        payload["ai_invoice"] = ai_invoice
    return Candidate("ai", conf, payload, (res.get("reason") or "").strip()[:240], evidence[:160])


def _keyword_candidate(text: str, cats: list[dict], upload_area: str,
                       rental_hint: bool = False) -> tuple[Candidate | None, dict | None]:
    area = upload_area
    if area == "auto" and rental_hint:
        area = "vermietung"  # Adresse eines Mietobjekts im Text -> Bereich Vermietung
    pool = cats if area == "auto" else [c for c in cats if c["area_key"] == area]
    scored = extract.keyword_scores(text, pool)
    if not scored:
        return None, None
    top, hits, words = scored[0]
    second = scored[1][1] if len(scored) > 1 else 0
    conf = min(0.8, 0.45 + 0.1 * hits) - (0.1 if second == hits else 0)
    cand = Candidate("keyword", round(conf, 2), {"area_id": top["area_id"], "category_id": top["id"]},
                     "Gefundene Stichwörter: " + ", ".join(words[:5]), "")
    return cand, top


def analyze(conn: sqlite3.Connection, document_id: int) -> Candidate | None:
    doc = dict(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
    text_row = conn.execute("SELECT content FROM document_text WHERE document_id = ?", (document_id,)).fetchone()
    text = text_row[0] if text_row else ""
    upload_area = doc["upload_area"]
    cats = category_paths(conn)
    props = rows(conn.execute("SELECT id, name, address, keywords FROM properties WHERE archived = 0 ORDER BY id"))
    correspondents = rows(conn.execute("SELECT id, name, aliases FROM correspondents ORDER BY name"))

    corr_id, corr_ev = extract.detect_correspondent(text, correspondents)
    corr_name = ""
    if corr_id:
        corr_name = next(c["name"] for c in correspondents if c["id"] == corr_id)

    best: Candidate | None = None

    # 1. Eigene Regeln
    for rule in rows(conn.execute("SELECT * FROM rules WHERE enabled = 1 ORDER BY sort, id")):
        hit = _rule_matches(rule, text, doc["original_name"], corr_name)
        if hit is None:
            continue
        payload = {k: rule[k] for k in ("area_id", "category_id", "property_id", "correspondent_id") if rule[k]}
        if rule["doc_type"]:
            payload["doc_type"] = rule["doc_type"]
        if not payload.get("area_id") and payload.get("category_id"):
            payload["area_id"] = next(c["area_id"] for c in cats if c["id"] == payload["category_id"])
        best = Candidate("rule", 0.99, payload, f"Regel „{rule['name']}“ hat gegriffen.", hit[:160])
        conn.execute("UPDATE rules SET hits = hits + 1 WHERE id = ?", (rule["id"],))
        break

    # 2. Bestätigter Verlauf je Absender
    if best is None and corr_id:
        best = _history_candidate(conn, corr_id)

    # 3. KI – mit Datenschutz-Vorprüfung
    rental_hint = extract.detect_property(text, props)[0] is not None
    kw_cand, kw_top = _keyword_candidate(text, cats, upload_area, rental_hint)
    ai_allowed = bool(get_setting(conn, "ai_enabled", True)) and config.settings.ai_available and len(text.strip()) >= 20
    blocked_reason = ""
    if kw_top and kw_top["ai_blocked"] and kw_cand and kw_cand.confidence >= 0.55:
        ai_allowed = False
        blocked_reason = f"Dokument wirkt wie „{kw_top['path']}“ – dafür ist die KI gesperrt, nur lokal ausgewertet."
    if upload_area != "auto":
        area_ai = conn.execute("SELECT ai_enabled FROM areas WHERE key = ?", (upload_area,)).fetchone()
        if area_ai and not area_ai[0]:
            ai_allowed = False
    if best is None and ai_allowed:
        best = _ai_candidate(conn, text, upload_area, cats, props, correspondents)

    # 4. Lokale Stichwörter
    if best is None or (kw_cand and best.source == "ai" and best.confidence < kw_cand.confidence):
        if kw_cand:
            best = kw_cand
            if blocked_reason:
                best.reason = blocked_reason + " " + best.reason
    if best is None:
        best = Candidate("keyword", 0.0, {}, blocked_reason or "Keine passende Ablage erkannt.")

    # Beim Upload gewähltes Mietobjekt hat Vorrang vor jeder Erkennung
    if doc["property_id"]:
        vm = conn.execute("SELECT id FROM areas WHERE key = 'vermietung'").fetchone()[0]
        if best.payload.get("area_id") not in (None, vm) or not best.payload.get("category_id"):
            vm_cats = [c for c in cats if c["area_key"] == "vermietung"]
            kw = extract.keyword_scores(text, vm_cats)
            cat = kw[0][0]["id"] if kw else next(c["id"] for c in vm_cats if c["name"] == "Rechnungen und Belege" or c["name"] == "Sonstiges")
            best = Candidate(best.source if best.payload.get("area_id") == vm else "keyword", max(best.confidence, 0.6),
                             {"area_id": vm, "category_id": cat}, best.reason, best.evidence)
        best.payload["property_id"] = doc["property_id"]
        best.payload["property_chosen"] = True

    # Ergänzen um lokal erkannte Felder
    p = best.payload
    if corr_id and not p.get("correspondent_id"):
        p["correspondent_id"] = corr_id
    area_key = next((c["area_key"] for c in cats if c["id"] == p.get("category_id")), None)
    if area_key is None and p.get("area_id"):
        r = conn.execute("SELECT key FROM areas WHERE id = ?", (p["area_id"],)).fetchone()
        area_key = r[0] if r else None
    if area_key == "vermietung" or (area_key is None and upload_area == "vermietung"):
        if not p.get("property_id"):
            prop_id, prop_ev = extract.detect_property(text, props)
            if prop_id:
                p["property_id"] = prop_id
                best.evidence = best.evidence or prop_ev
            elif len(props) == 1:
                p["property_id"] = props[0]["id"]
                p["property_assumed"] = True
        if "amount_cents" not in p:
            amount, _amount_ev = extract.detect_amount(text)
            if amount:
                p["amount_cents"] = amount
    da = extract.date_analysis(text)
    p["date_candidates"] = da["candidates"]
    if not p.get("doc_date"):
        if da["date"]:
            p["doc_date"] = da["date"]
            p["date_sure"] = da["sure"]
    else:  # Datum der KI: sicher nur, wenn es mit einem eindeutig erkannten Datum übereinstimmt
        p["date_sure"] = bool(da["sure"] and da["date"] == p["doc_date"])
    if not p.get("doc_type"):
        dt = extract.detect_doc_type(text)
        if dt:
            p["doc_type"] = dt
    if p.get("doc_type") in invoices.INVOICE_TYPES:
        inv = invoices.fields(text, p.get("doc_type"), p.get("doc_date"))
        inv.update(p.pop("ai_invoice", {}))
        if p.get("amount_cents"):
            inv["amount_cents"] = p["amount_cents"]
        elif inv.get("amount_cents"):
            p["amount_cents"] = inv["amount_cents"]
        p["invoice"] = inv
    p.pop("ai_invoice", None)
    if not p.get("title"):
        corr = corr_name or p.get("correspondent_name") or ""
        if not corr and p.get("correspondent_id"):
            r = conn.execute("SELECT name FROM correspondents WHERE id = ?", (p["correspondent_id"],)).fetchone()
            corr = r[0] if r else ""
        p["title"] = titles.make_title(text, doc_type=p.get("doc_type"), correspondent=corr or None,
                                       doc_date=p.get("doc_date"), payee_guess=invoices.guess_payee(text),
                                       created=doc["created_at"])
    return best


def get_or_create_correspondent(conn: sqlite3.Connection, name: str) -> int:
    name = re.sub(r"\s+", " ", name).strip()[:80]
    r = conn.execute("SELECT id FROM correspondents WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if r:
        return r[0]
    return conn.execute("INSERT INTO correspondents(name) VALUES(?)", (name,)).lastrowid


def decide(conn: sqlite3.Connection, document_id: int, cand: Candidate) -> str:
    """Speichert den Vorschlag und legt bei hoher Sicherheit direkt ab. Liefert den neuen Status."""
    doc = dict(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
    auto_file = bool(get_setting(conn, "auto_file", True))
    auto_threshold = float(get_setting(conn, "auto_threshold", 0.9))
    suggest_threshold = float(get_setting(conn, "suggest_threshold", 0.5))
    p = cand.payload

    area_key = None
    if p.get("area_id"):
        r = conn.execute("SELECT key FROM areas WHERE id = ?", (p["area_id"],)).fetchone()
        area_key = r[0] if r else None

    blockers: list[str] = []
    if not p.get("category_id"):
        blockers.append("keine Kategorie erkannt")
    if cand.confidence < auto_threshold:
        blockers.append(f"Sicherheit {round(cand.confidence * 100)} % unter Schwelle {round(auto_threshold * 100)} %")
    if doc["upload_area"] == "auto" and cand.source not in ("rule", "history"):
        blockers.append("Bereich muss bestätigt werden")
    if doc["upload_area"] != "auto" and area_key and area_key != doc["upload_area"]:
        blockers.append("Vorschlag liegt im anderen Bereich")
    if area_key == "vermietung" and not p.get("property_id") and \
            conn.execute("SELECT COUNT(*) FROM properties WHERE archived = 0").fetchone()[0] > 0:
        blockers.append("Mietobjekt unklar")
    if not auto_file:
        blockers.append("automatische Ablage ausgeschaltet")
    if p.get("property_chosen") and doc["upload_area"] == "vermietung" and area_key == "vermietung" and p.get("category_id"):
        # Objekt haben Sie beim Upload gewählt: direkt ablegen; Ordner prüfen Sie unter „Automatisch abgelegt“
        blockers = [b for b in blockers if not b.startswith("Sicherheit")]

    if p.get("correspondent_name") and not p.get("correspondent_id"):
        p["correspondent_id"] = get_or_create_correspondent(conn, p["correspondent_name"])

    has_suggestion = cand.confidence >= suggest_threshold and bool(p.get("category_id"))
    payload_json = json.dumps(p, ensure_ascii=False)
    ts = now()

    # Felder, die unabhängig von der Ablage helfen (Datum, Absender, Titel), sofort setzen
    updates: dict[str, Any] = {}
    if p.get("doc_date") and not doc["doc_date"]:
        updates["doc_date"] = p["doc_date"]
        updates["date_status"] = "sure" if p.get("date_sure") else "unsure"
    if p.get("date_candidates") is not None:
        updates["date_candidates"] = json.dumps(p["date_candidates"], ensure_ascii=False)
    if p.get("correspondent_id") and not doc["correspondent_id"]:
        updates["correspondent_id"] = p["correspondent_id"]
    # Nur nichtssagende Namen ersetzen (IMG_…, Scan …, document(3)); selbst vergebene Dateinamen bleiben.
    auto_title = not doc["title"] or titles.is_generic(doc["title"])
    if p.get("title") and auto_title:
        updates["title"] = p["title"]
    if p.get("doc_type") and not doc["doc_type"]:
        updates["doc_type"] = p["doc_type"]

    if has_suggestion and not blockers:
        status = "filed"
        updates.update({
            "area_id": p["area_id"], "category_id": p["category_id"],
            "property_id": p.get("property_id"), "status": "filed", "filed_by": cand.source,
            "filed_reason": cand.reason, "confidence": cand.confidence, "confirmed": 0, "filed_at": ts,
        })
        sug_status = "applied"
    else:
        status = "inbox"
        updates.update({"status": "inbox", "confidence": cand.confidence,
                        "filed_reason": cand.reason + ((" Nicht automatisch: " + "; ".join(blockers) + ".") if has_suggestion and blockers else "")})
        if doc["upload_area"] != "auto" and not doc["area_id"]:
            updates["area_id"] = conn.execute("SELECT id FROM areas WHERE key = ?", (doc["upload_area"],)).fetchone()[0]
        sug_status = "open" if has_suggestion else "info"

    updates["updated_at"] = ts
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE documents SET {sets} WHERE id = ?", (*updates.values(), document_id))
    conn.execute("UPDATE suggestions SET status = 'superseded' WHERE document_id = ? AND status IN ('open','info')", (document_id,))
    conn.execute(
        "INSERT INTO suggestions(document_id, source, payload, confidence, reason, evidence, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (document_id, cand.source, payload_json, cand.confidence, cand.reason, cand.evidence, sug_status, ts),
    )
    audit(conn, "auto_filed" if status == "filed" else "suggested", "document", document_id,
          {"source": cand.source, "confidence": cand.confidence, "blockers": blockers}, actor="system")
    if status == "filed":
        invoices.ensure_expense(conn, document_id)  # automatisch ausgelesen, als Aufwand erfasst
    else:
        invoices.capture_on_upload(conn, document_id, p)  # Betrag sofort erfassen, Objekt folgt nach Rückfrage
    return status


_GENERIC_NAME = re.compile(
    r"(?:(?:img|image|scan|dsc|dcim|pxl|photo|foto|bild|doc|dokument|document|cam|screenshot|whatsapp)[\W_]*)?"
    r"(?:[\d\W_]|wa\d+)*", re.I)


def apply_preset(conn: sqlite3.Connection, document_id: int, preset: dict[str, Any]) -> str:
    """Ablage für importierte Dokumente: Ziel steht aus der Ordner-Zuordnung fest.

    category_id = None bedeutet „automatisch im Bereich“: Stichwörter entscheiden, sonst der Ersatzordner.
    Importierte Dokumente gelten als bestätigt, weil Sie sie selbst schon einsortiert hatten.
    """
    doc = dict(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
    text_row = conn.execute("SELECT content FROM document_text WHERE document_id = ?", (document_id,)).fetchone()
    text = text_row[0] if text_row else ""
    area_id = preset["area_id"]
    category_id = preset.get("category_id")
    reason = f"Aus Dropbox-Ordner „{(doc['source_path'] or '').rsplit('/', 1)[0] or '/'}“ übernommen."
    if not category_id:
        area_key = conn.execute("SELECT key FROM areas WHERE id = ?", (area_id,)).fetchone()[0]
        cats = [c for c in category_paths(conn) if c["area_key"] == area_key]
        scored = extract.keyword_scores(text + "\n" + (doc["source_path"] or ""), cats)
        if scored and scored[0][1] >= 2:
            category_id = scored[0][0]["id"]
            reason += " Ordner per Stichwort gewählt: " + ", ".join(scored[0][2][:4]) + "."
        else:
            category_id = preset["fallback_category_id"]

    correspondents = rows(conn.execute("SELECT id, name, aliases FROM correspondents ORDER BY name"))
    corr_id, _ = extract.detect_correspondent(text, correspondents)
    da = extract.date_analysis(text)
    doc_date = da["date"]
    doc_type = extract.detect_doc_type(text)
    title = doc["title"]
    if _GENERIC_NAME.fullmatch(title or ""):
        corr = next((c["name"] for c in correspondents if c["id"] == corr_id), "")
        folder = (doc["source_path"] or "").rsplit("/", 2)[-2] if (doc["source_path"] or "").count("/") else ""
        parts = [doc_type or folder or "Dokument", corr if corr.lower() not in folder.lower() else ""]
        if doc_date:
            parts.append(f"{doc_date[5:7]}/{doc_date[:4]}")
        title = " ".join(x for x in parts if x)[:80]

    ts = now()
    conn.execute(
        "UPDATE documents SET area_id = ?, category_id = ?, property_id = ?, correspondent_id = COALESCE(correspondent_id, ?), "
        "date_status = CASE WHEN doc_date IS NULL AND ? IS NOT NULL THEN ? ELSE date_status END, date_candidates = ?, "
        "doc_date = COALESCE(doc_date, ?), doc_type = CASE WHEN doc_type = '' THEN COALESCE(?, '') ELSE doc_type END, "
        "title = ?, status = 'filed', filed_by = 'import', filed_reason = ?, confidence = 1.0, confirmed = 1, "
        "filed_at = ?, updated_at = ? WHERE id = ?",
        (area_id, category_id, preset.get("property_id"), corr_id, doc_date, "sure" if da["sure"] else "unsure",
         json.dumps(da["candidates"], ensure_ascii=False), doc_date, doc_type, title, reason, ts, ts, document_id),
    )
    for name in preset.get("tags") or []:
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO document_tags(document_id, tag_id) VALUES(?,?)", (document_id, tag_id))
    audit(conn, "import_filed", "document", document_id, {"category_id": category_id, "property_id": preset.get("property_id")},
          actor="import")
    return "filed"
