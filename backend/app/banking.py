"""Kontoumsätze: CSV einlesen, mit Belegen abgleichen, zuordnen.

Ablauf je Umsatz:
1. Passt eine erfasste Ausgabe ohne Zahlungsdatum (gleicher Betrag, Rechnungsnummer/Aussteller im Text)?
   -> Zahlungsdatum setzen und verknüpfen.
2. Wurde derselbe Zahlungspartner schon mindestens zweimal gleich zugeordnet? -> genauso zuordnen.
3. Sonst: Vorschlag aus KI (falls eingerichtet) oder Stichwörtern, und Rückfrage an Sie.

Automatisch Zugeordnetes erscheint zur Kontrolle im Eingang; eine neu angelegte Ausgabe bleibt
„Entwurf“, bis Sie sie bestätigen. Es wird nichts steuerlich bewertet.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import sqlite3
from datetime import date
from typing import Any

from . import config, extract, invoices
from .db import audit, get_setting, now, row, rows
from .seed import EXPENSE_KEYS

log = logging.getLogger("smartarchive.banking")


class BankImportError(ValueError):
    pass


# --- CSV einlesen ----------------------------------------------------------

def _norm(h: str) -> str:
    h = (h or "").strip().strip('"').lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        h = h.replace(a, b)
    return re.sub(r"\s+", " ", h)


COLS = {
    "booking_date": {"buchungstag", "buchungsdatum", "datum", "buchung", "buchungstag/valuta"},
    "value_date": {"wertstellung", "valutadatum", "valuta", "wertstellungsdatum", "wertstellungstag", "wert"},
    "amount": {"betrag", "umsatz", "betrag (eur)", "betrag in eur", "betrag (€)", "betrag eur", "umsatz in eur"},
    "currency": {"waehrung", "whrg", "whrg.", "wahrung"},
    "kind": {"umsatzart", "buchungsart", "vorgang"},
    "counterparty": {"beguenstigter/zahlungspflichtiger", "name zahlungsbeteiligter", "zahlungsempfaenger",
                     "empfaenger", "auftraggeber/empfaenger", "auftraggeber / beguenstigter", "beguenstigter/auftraggeber",
                     "zahlungspflichtiger/empfaenger", "name", "gegenpartei", "auftraggeber/beguenstigter"},
    "iban": {"kontonummer/iban", "iban zahlungsbeteiligter", "iban beguenstigter/zahlungspflichtiger", "iban gegenkonto",
             "iban auftraggeber/empfaenger", "kontonummer"},
    "creditor": {"glaeubiger id", "glaeubiger-id", "glaeubiger-identifikationsnummer", "glaeubiger id (ci)"},
}

_CREDITOR = re.compile(r"\b([A-Z]{2}\d{2}ZZZ[A-Z0-9]{6,15})\b")
_CUT = re.compile(r"\s(?:End-to-End|EREF|MREF|KREF|Mandatsref|Mandat\b|Gl[äa]ubiger|CRED|SVWZ|ABWA|IBAN\b|BIC\b|"
                  r"Kartenzahlung|Dauerauftrag|Lastschrift|SEPA|Referenz|Kundenreferenz|\d{1,2}\.\d{1,2}\.\d{2,4})", re.I)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def parse_amount(raw: str) -> int | None:
    s = (raw or "").strip().replace("€", "").replace("EUR", "").replace(" ", "").replace(" ", "").replace("+", "")
    if not s:
        return None
    neg = s.startswith("-") or s.endswith("-")
    s = s.strip("-")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") == 1 and len(s.split(".")[1]) != 2:
        s = s.replace(".", "")
    try:
        cents = round(float(s) * 100)
    except ValueError:
        return None
    return -cents if neg else cents


def parse_date(raw: str) -> str | None:
    s = (raw or "").strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})", s)
    try:
        if m:
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1))).isoformat()
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s[:10])
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None
    return None


def _counterparty_from_text(text: str) -> str:
    head = _CUT.split(" " + text, maxsplit=1)[0].strip()
    head = re.sub(r"\s+", " ", head)
    return head[:60]


def party_key(counterparty: str, iban: str, creditor_id: str) -> str:
    if creditor_id:
        return "ci:" + creditor_id.upper()
    if iban:
        return "iban:" + re.sub(r"\s", "", iban).upper()
    words = re.findall(r"[a-zäöüß]{3,}", counterparty.lower())
    return ("name:" + " ".join(words[:3])) if words else ""


def parse_csv(data: bytes) -> list[dict[str, Any]]:
    text = _decode(data)
    lines = text.splitlines()
    if not lines:
        raise BankImportError("Die Datei ist leer.")
    sample = "\n".join(lines[:15])
    delim = max([";", ",", "\t"], key=sample.count)
    reader = list(csv.reader(io.StringIO(text), delimiter=delim))

    header_idx, colmap = None, {}
    for idx, rec in enumerate(reader[:30]):
        names = [_norm(c) for c in rec]
        found: dict[str, int] = {}
        for key, options in COLS.items():
            for i, n in enumerate(names):
                if n in options and key not in found:
                    found[key] = i
        if "amount" not in found:
            for i, n in enumerate(names):
                if n.startswith("betrag") and "amount" not in found:
                    found["amount"] = i
        if "booking_date" in found and "amount" in found:
            header_idx, colmap = idx, found
            # Text: „Verwendungszweck“, sonst „Buchungstext“ (Commerzbank: dort steht alles)
            if "verwendungszweck" in names:
                colmap["purpose"] = names.index("verwendungszweck")
                if "buchungstext" in names and "kind" not in colmap:
                    colmap["kind"] = names.index("buchungstext")
            elif "buchungstext" in names:
                colmap["purpose"] = names.index("buchungstext")
            break
    if header_idx is None:
        raise BankImportError("Keine Spalten „Buchungstag“ und „Betrag“ gefunden. Ist das ein Umsatz-Export als CSV?")

    out = []
    for rec in reader[header_idx + 1:]:
        get = lambda k: rec[colmap[k]].strip() if k in colmap and colmap[k] < len(rec) else ""  # noqa: E731
        booking = parse_date(get("booking_date"))
        amount = parse_amount(get("amount"))
        if not booking or amount is None or amount == 0:
            continue
        purpose = re.sub(r"\s+", " ", get("purpose"))
        counterparty = re.sub(r"\s+", " ", get("counterparty")) or _counterparty_from_text(purpose)
        iban = re.sub(r"\s", "", get("iban")).upper()
        if iban and not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", iban):
            iban = ""
        creditor = get("creditor").upper()
        if not creditor:
            m = _CREDITOR.search(purpose)
            creditor = m.group(1) if m else ""
        out.append({
            "booking_date": booking, "value_date": parse_date(get("value_date")), "amount_cents": amount,
            "currency": (get("currency") or "EUR").upper()[:3], "counterparty": counterparty[:120],
            "counterparty_iban": iban, "creditor_id": creditor, "purpose": purpose[:1000], "booking_kind": get("kind")[:60],
        })
    if not out:
        raise BankImportError("In der Datei wurden keine Umsätze erkannt.")
    return out


# --- Abgleich und Vorschläge ---------------------------------------------------

_LOAN = re.compile(r"darlehen|tilgung|annuit|kreditrate|baufinanzierung|rate\s+\d", re.I)
_HAUSGELD = re.compile(r"hausgeld|wohngeld\b|weg\s|eigentuemergemeinschaft|eigentümergemeinschaft", re.I)
_RENT = re.compile(r"\bmiete\b|mietzahlung|kaltmiete|warmmiete|nebenkostenvorauszahlung|nk-?vorauszahlung", re.I)

BANK_KEYWORDS: list[tuple[str, list[str]]] = [
    ("grundsteuer", ["grundsteuer", "grundbesitzabgaben"]),
    ("versicherung", ["gebaeudeversicherung", "gebäudeversicherung", "wohngebäude", "wohngebaeude", "grundbesitzerhaftpflicht"]),
    ("hausgeld_laufend", ["hausgeld"]),
    ("verwaltung", ["hausverwaltung", "verwaltergebuehr", "verwaltergebühr", "kontofuehrung", "kontoführung", "kontoentgelt", "entgelt"]),
    ("grundbesitzabgaben", ["abfall", "muellgebuehr", "müllgebühr", "abwasser", "wasserzins", "niederschlagswasser", "strassenreinigung"]),
    ("hausnebenkosten", ["schornsteinfeger", "allgemeinstrom", "hausstrom", "hausmeister"]),
]


def _hint(text: str, amount: int) -> str:
    if amount < 0 and _LOAN.search(text):
        return ("Darlehensrate: Sie enthält meist Tilgung. Nur der Zinsanteil ist Werbungskosten – Betrag anpassen "
                "oder ignorieren und die Jahres-Zinsbescheinigung erfassen.")
    if amount < 0 and _HAUSGELD.search(text):
        return ("Hausgeld: Die Zuführung zur Erhaltungsrücklage ist erst bei Verwendung abziehbar – "
                "maßgeblich ist die Jahresabrechnung.")
    return ""


def _keyword_category(text: str) -> str | None:
    low = text.lower()
    for key, words in BANK_KEYWORDS:
        if any(w in low for w in words):
            return key
    cat, _ = invoices.suggest_expense_category(text)
    return cat


def _norm_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def expense_candidates(conn: sqlite3.Connection, tx: dict[str, Any]) -> list[dict[str, Any]]:
    """Erfasste Ausgaben ohne Zahlungsdatum mit gleichem Betrag, bewertet nach Rechnungsnummer und Aussteller."""
    if tx["amount_cents"] >= 0:
        return []
    cands = rows(conn.execute(
        "SELECT e.*, d.title AS document_title, p.name AS property_name FROM expenses e "
        "LEFT JOIN documents d ON d.id = e.document_id LEFT JOIN properties p ON p.id = e.property_id "
        "WHERE e.amount_cents = ? AND (e.paid_on IS NULL OR e.paid_assumed = 1 OR e.id = ?) AND e.origin != 'bank' "
        "AND (e.invoice_date IS NULL OR e.invoice_date BETWEEN date(?, '-200 days') AND date(?, '+5 days')) "
        "AND e.id NOT IN (SELECT expense_id FROM transactions WHERE expense_id IS NOT NULL AND id != ?) ORDER BY e.id",
        (-tx["amount_cents"], tx.get("expense_id") or -1, tx["booking_date"], tx["booking_date"], tx["id"])))
    text = _norm_token(f"{tx['counterparty']} {tx['purpose']}")
    for c in cands:
        score, why = 0.8, ["gleicher Betrag"]
        m = re.search(r"Nr\.?\s*(\S+)", c.get("note") or "")
        if m and len(_norm_token(m.group(1))) >= 3 and _norm_token(m.group(1)) in text:
            score += 0.17
            why.append(f"Rechnungsnummer {m.group(1)} im Verwendungszweck")
        words = [w for w in re.findall(r"[a-zäöüß]{4,}", (c.get("payee") or "").lower()) if w not in ("gmbh", "kg")]
        if words and any(_norm_token(w) in text for w in words):
            score += 0.12
            why.append("Aussteller passt")
        c["score"] = round(min(score, 0.97), 2)
        c["why"] = ", ".join(why)
    cands.sort(key=lambda c: -c["score"])
    return cands


def _history(conn: sqlite3.Connection, tx: dict[str, Any]) -> dict[str, Any] | None:
    if not tx["party_key"]:
        return None
    hist = rows(conn.execute(
        "SELECT kind, property_id, unit_id, category_key, COUNT(*) AS n, "
        "SUM(CASE WHEN json_extract(suggestion, '$.partial') THEN 1 ELSE 0 END) AS partial "
        "FROM transactions WHERE party_key = ? AND status = 'done' AND id != ? AND (amount_cents < 0) = ? "
        "GROUP BY kind, property_id, unit_id, category_key ORDER BY n DESC",
        (tx["party_key"], tx["id"], tx["amount_cents"] < 0)))
    if not hist:
        return None
    total = sum(h["n"] for h in hist)
    top = hist[0]
    if top["n"] < 2 or top["n"] / total < 0.8:
        return None
    return {"kind": top["kind"], "property_id": top["property_id"], "unit_id": top["unit_id"],
            "category_key": top["category_key"], "source": "history",
            "confidence": 0.95 if top["n"] >= 3 else 0.92, "partial": bool(top["partial"]),
            "reason": f"{top['n']} frühere Umsätze dieses Zahlungspartners wurden so zugeordnet."}


def _ai(conn: sqlite3.Connection, tx: dict[str, Any], props: list[dict]) -> dict[str, Any] | None:
    from . import mistral
    if not (bool(get_setting(conn, "ai_enabled", True)) and config.settings.ai_available):
        return None
    try:
        res = mistral.classify_transaction(tx, props)
    except mistral.AIError as exc:
        log.warning("KI-Vorschlag für Umsatz nicht möglich: %s", exc)
        return None
    kind = res.get("kind") if res.get("kind") in ("ausgabe", "einnahme", "ignoriert") else None
    if not kind:
        return None
    pid = res.get("property_id")
    pid = pid if isinstance(pid, int) and any(p["id"] == pid for p in props) else None
    cat = res.get("category_key") if res.get("category_key") in EXPENSE_KEYS else None
    conf = max(0.0, min(0.8, float(res.get("confidence") or 0)))  # KI allein entscheidet nie
    return {"kind": kind, "property_id": pid, "category_key": cat if kind == "ausgabe" else None, "source": "ai",
            "confidence": conf, "reason": (res.get("reason") or "Vorschlag der KI").strip()[:240]}


def analyze(conn: sqlite3.Connection, tx: dict[str, Any], use_ai: bool = True) -> dict[str, Any]:
    props = rows(conn.execute("SELECT id, name, address, keywords FROM properties WHERE archived = 0 ORDER BY id"))
    text = f"{tx['counterparty']} {tx['purpose']}"
    sug: dict[str, Any] = {"hint": _hint(text, tx["amount_cents"])}

    cands = expense_candidates(conn, tx)
    if cands:
        best = cands[0]
        clear = len(cands) == 1 or best["score"] - cands[1]["score"] >= 0.1
        sug.update({"kind": "ausgabe", "expense_id": best["id"] if clear else None, "property_id": best["property_id"],
                    "unit_id": best["unit_id"], "category_key": best["category_key"], "source": "beleg",
                    "confidence": best["score"] if clear else 0.6,
                    "reason": f"Passt zu erfasster Rechnung: {best['payee'] or best['document_title'] or 'Ausgabe'} ({best['why']})."
                    if clear else f"{len(cands)} erfasste Rechnungen mit gleichem Betrag – bitte wählen.",
                    "candidates": [c["id"] for c in cands[:5]]})
        return sug

    hist = _history(conn, tx)
    if hist:
        sug.update(hist)
        return sug

    ai = _ai(conn, tx, props) if use_ai else None
    if ai:
        sug.update(ai)
    else:
        if tx["amount_cents"] > 0:
            sug.update({"kind": "einnahme", "source": "keyword",
                        "confidence": 0.7 if _RENT.search(text) else 0.5,
                        "reason": "Zahlungseingang" + (" mit „Miete“ im Text." if _RENT.search(text) else ".")})
        else:
            cat = _keyword_category(text)
            sug.update({"kind": "ausgabe", "category_key": cat, "source": "keyword", "confidence": 0.6 if cat else 0.3,
                        "reason": "Kategorie nach Stichwörtern vorgeschlagen." if cat else "Keine passende Rechnung gefunden."})
    if not sug.get("property_id"):
        pid, _ = extract.detect_property(text, props)
        if pid:
            sug["property_id"] = pid
        elif len(props) == 1:
            sug["property_id"] = props[0]["id"]
    return sug


# --- Anwenden ------------------------------------------------------------------

def _create_bank_expense(conn: sqlite3.Connection, tx: dict[str, Any], *, property_id, unit_id, category_key, amount_cents,
                         status: str, note: str | None = None) -> int:
    ts = now()
    return conn.execute(
        "INSERT INTO expenses(document_id, property_id, unit_id, invoice_date, paid_on, amount_cents, payee, category_key, note, "
        "status, origin, created_at, updated_at) VALUES(NULL,?,?,NULL,?,?,?,?,?,?,?,?,?)",
        (property_id, unit_id, tx["booking_date"], amount_cents, tx["counterparty"][:120], category_key,
         (note if note is not None else f"Kontoumsatz: {tx['purpose']}")[:1000], status, "bank", ts, ts)).lastrowid


def _release_expense(conn: sqlite3.Connection, tx: dict[str, Any], sug: dict[str, Any]) -> None:
    """Löst die Verbindung zu einer Ausgabe: eigene Bank-Ausgabe löschen, gesetztes Zahlungsdatum zurücknehmen."""
    if not tx.get("expense_id"):
        return
    e = row(conn.execute("SELECT * FROM expenses WHERE id = ?", (tx["expense_id"],)))
    if not e:
        return
    if e["origin"] == "bank":
        conn.execute("DELETE FROM expenses WHERE id = ?", (e["id"],))
    elif sug.get("paid_on_set") and e["paid_on"] == tx["booking_date"]:
        prev, prev_assumed = sug.get("paid_prev") or (None, 0)
        conn.execute("UPDATE expenses SET paid_on = ?, paid_assumed = ?, updated_at = ? WHERE id = ?",
                     (prev, prev_assumed, now(), e["id"]))


def auto_apply(conn: sqlite3.Connection, tx: dict[str, Any], sug: dict[str, Any]) -> str:
    """Wendet sichere Vorschläge an (Status „auto“, zur Kontrolle im Eingang). Liefert den neuen Status."""
    threshold = float(get_setting(conn, "auto_threshold", 0.9))
    has_props = conn.execute("SELECT COUNT(*) FROM properties WHERE archived = 0").fetchone()[0] > 0
    conf = sug.get("confidence") or 0
    fields: dict[str, Any] = {}
    if conf >= threshold and sug.get("source") == "beleg" and sug.get("expense_id"):
        e = row(conn.execute("SELECT * FROM expenses WHERE id = ?", (sug["expense_id"],)))
        if e["paid_on"] is None or e["paid_assumed"]:
            sug["paid_prev"] = [e["paid_on"], e["paid_assumed"]]
            conn.execute("UPDATE expenses SET paid_on = ?, paid_assumed = 0, updated_at = ? WHERE id = ?",
                         (tx["booking_date"], now(), e["id"]))
            sug["paid_on_set"] = True
        fields = {"kind": "ausgabe", "expense_id": e["id"], "document_id": e["document_id"], "property_id": e["property_id"],
                  "unit_id": e["unit_id"], "category_key": e["category_key"]}
    elif conf >= threshold and sug.get("source") == "history":
        kind = sug.get("kind")
        if kind == "ausgabe":
            if sug.get("partial") or not sug.get("category_key") or (has_props and not sug.get("property_id")):
                return "open"  # z. B. nur Zinsanteil erfasst: Betrag muss jedes Mal geprüft werden
            eid = _create_bank_expense(conn, tx, property_id=sug.get("property_id"), unit_id=sug.get("unit_id"),
                                       category_key=sug["category_key"], amount_cents=-tx["amount_cents"], status="entwurf")
            fields = {"kind": kind, "expense_id": eid, "property_id": sug.get("property_id"), "unit_id": sug.get("unit_id"),
                      "category_key": sug["category_key"]}
        elif kind in ("einnahme", "ignoriert"):
            fields = {"kind": kind, "property_id": sug.get("property_id"), "unit_id": sug.get("unit_id")}
        else:
            return "open"
    else:
        return "open"
    fields.update({"status": "auto", "source": sug["source"], "confidence": conf, "reason": sug.get("reason", ""),
                   "suggestion": json.dumps(sug, ensure_ascii=False), "updated_at": now()})
    conn.execute(f"UPDATE transactions SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), tx["id"]))
    audit(conn, "auto_assigned", "transaction", tx["id"], {"source": sug["source"], "kind": fields["kind"]}, actor="system")
    return "auto"


def process(conn: sqlite3.Connection, tx_id: int, use_ai: bool = True) -> str:
    tx = row(conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)))
    sug = analyze(conn, tx, use_ai=use_ai)
    status = auto_apply(conn, tx, sug)
    if status == "open":
        conn.execute("UPDATE transactions SET status = 'open', source = ?, confidence = ?, reason = ?, suggestion = ?, updated_at = ? "
                     "WHERE id = ?", (sug.get("source"), sug.get("confidence"), sug.get("reason", ""),
                                      json.dumps(sug, ensure_ascii=False), now(), tx_id))
    return status


def refresh_suggestion(conn: sqlite3.Connection, tx_id: int) -> None:
    """Vorschlag neu berechnen, ohne automatisch anzuwenden (nach „Rückgängig“)."""
    tx = row(conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)))
    sug = analyze(conn, tx, use_ai=False)
    conn.execute("UPDATE transactions SET status = 'open', source = ?, confidence = ?, reason = ?, suggestion = ?, updated_at = ? "
                 "WHERE id = ?", (sug.get("source"), sug.get("confidence"), sug.get("reason", ""),
                                  json.dumps(sug, ensure_ascii=False), now(), tx_id))


MAX_AI_PER_IMPORT = 40


def import_csv(conn: sqlite3.Connection, account_id: int, filename: str, data: bytes) -> dict[str, Any]:
    parsed = parse_csv(data)
    ts = now()
    import_id = conn.execute("INSERT INTO bank_imports(account_id, filename, rows_total, created_at) VALUES(?,?,?,?)",
                             (account_id, filename[:200], len(parsed), ts)).lastrowid
    seen: dict[str, int] = {}
    new_ids: list[int] = []
    for r in parsed:
        base = f"{account_id}|{r['booking_date']}|{r['amount_cents']}|{r['counterparty']}|{r['purpose']}"
        seen[base] = seen.get(base, 0) + 1
        fp = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()
        if conn.execute("SELECT 1 FROM transactions WHERE fingerprint = ?", (fp,)).fetchone():
            continue
        key = party_key(r["counterparty"], r["counterparty_iban"], r["creditor_id"])
        cols = ["account_id", "import_id", "fingerprint", *r.keys(), "party_key", "created_at", "updated_at"]
        tid = conn.execute(f"INSERT INTO transactions({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                           (account_id, import_id, fp, *r.values(), key, ts, ts)).lastrowid
        new_ids.append(tid)
    conn.execute("UPDATE bank_imports SET rows_new = ? WHERE id = ?", (len(new_ids), import_id))
    counts = {"auto": 0, "open": 0}
    for i, tid in enumerate(sorted(new_ids, key=lambda t: t)):  # älteste zuerst, damit der Verlauf wächst
        counts[process(conn, tid, use_ai=i < MAX_AI_PER_IMPORT)] += 1
    audit(conn, "imported", "bank_import", import_id, {"datei": filename, "neu": len(new_ids), "gesamt": len(parsed)})
    first = min((r["booking_date"] for r in parsed), default=None)
    last = max((r["booking_date"] for r in parsed), default=None)
    return {"import_id": import_id, "total": len(parsed), "new": len(new_ids), "duplicates": len(parsed) - len(new_ids),
            "auto": counts["auto"], "open": counts["open"], "from": first, "to": last}


def decide(conn: sqlite3.Connection, tx_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Ihre Zuordnung eines Umsatzes. Ausgaben werden dabei bestätigt erfasst bzw. mit der Rechnung verknüpft."""
    tx = row(conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)))
    if not tx:
        raise KeyError(tx_id)
    sug = json.loads(tx["suggestion"] or "{}")
    kind = data["kind"]
    has_props = conn.execute("SELECT COUNT(*) FROM properties WHERE archived = 0").fetchone()[0] > 0
    property_id, unit_id = data.get("property_id"), data.get("unit_id")
    if property_id and not conn.execute("SELECT 1 FROM properties WHERE id = ?", (property_id,)).fetchone():
        raise BankImportError("Mietobjekt existiert nicht.")
    if unit_id and not conn.execute("SELECT 1 FROM units WHERE id = ? AND property_id = ?", (unit_id, property_id)).fetchone():
        raise BankImportError("Die Einheit gehört nicht zu diesem Objekt.")
    fields: dict[str, Any] = {"kind": kind, "property_id": property_id if kind != "ignoriert" else None,
                              "unit_id": unit_id if kind != "ignoriert" else None, "category_key": None,
                              "expense_id": None, "document_id": None}
    if kind == "ausgabe":
        if tx["amount_cents"] >= 0:
            raise BankImportError("Ein Zahlungseingang kann keine Ausgabe sein.")
        link = data.get("expense_id")
        if link:
            e = row(conn.execute("SELECT * FROM expenses WHERE id = ?", (link,)))
            if not e:
                raise BankImportError("Die Rechnung existiert nicht mehr.")
            other = conn.execute("SELECT id FROM transactions WHERE expense_id = ? AND id != ?", (link, tx_id)).fetchone()
            if other:
                raise BankImportError("Diese Rechnung ist schon mit einem anderen Umsatz verknüpft.")
            if tx["expense_id"] and tx["expense_id"] != link:
                _release_expense(conn, tx, sug)
            if e["paid_on"] != tx["booking_date"]:
                if e["paid_on"] is None or e["paid_assumed"]:
                    sug["paid_prev"] = [e["paid_on"], e["paid_assumed"]]
                sug["paid_on_set"] = e["paid_on"] is None or bool(e["paid_assumed"]) or sug.get("paid_on_set", False)
            conn.execute("UPDATE expenses SET paid_on = ?, paid_assumed = 0, status = 'bestaetigt', updated_at = ? WHERE id = ?",
                         (tx["booking_date"], now(), link))
            fields.update({"expense_id": link, "document_id": e["document_id"], "property_id": e["property_id"],
                           "unit_id": e["unit_id"], "category_key": e["category_key"]})
            sug["partial"] = False
        else:
            if has_props and not property_id:
                raise BankImportError("Bitte ein Mietobjekt wählen.")
            cat = data.get("category_key")
            if cat not in EXPENSE_KEYS:
                raise BankImportError("Bitte eine Ausgabenkategorie wählen.")
            amount = data.get("amount_cents") or -tx["amount_cents"]
            if amount <= 0 or amount > -tx["amount_cents"]:
                raise BankImportError("Der Betrag muss zwischen 0 und dem Umsatzbetrag liegen.")
            note = data.get("note")
            current = row(conn.execute("SELECT * FROM expenses WHERE id = ?", (tx["expense_id"],))) if tx["expense_id"] else None
            if current and current["origin"] == "bank":
                conn.execute("UPDATE expenses SET property_id = ?, unit_id = ?, amount_cents = ?, category_key = ?, paid_on = ?, "
                             "note = COALESCE(?, note), status = 'bestaetigt', updated_at = ? WHERE id = ?",
                             (property_id, unit_id, amount, cat, tx["booking_date"], note, now(), current["id"]))
                eid = current["id"]
            else:
                _release_expense(conn, tx, sug)
                eid = _create_bank_expense(conn, tx, property_id=property_id, unit_id=unit_id, category_key=cat,
                                           amount_cents=amount, status="bestaetigt", note=note)
            fields.update({"expense_id": eid, "category_key": cat})
            sug["partial"] = amount != -tx["amount_cents"]
    else:
        _release_expense(conn, tx, sug)
    changed = any((sug.get(k) or None) != (fields.get(k) or None) for k in ("kind", "property_id", "category_key"))
    fields.update({"status": "done", "source": "user" if changed or not tx["source"] else tx["source"],
                   "suggestion": json.dumps(sug, ensure_ascii=False), "decided_at": now(), "updated_at": now()})
    conn.execute(f"UPDATE transactions SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), tx_id))
    audit(conn, "assigned", "transaction", tx_id, {"kind": kind, "property_id": fields["property_id"],
                                                    "category_key": fields["category_key"], "expense_id": fields["expense_id"]})
    return fields


def undo(conn: sqlite3.Connection, tx_id: int) -> None:
    """Zuordnung zurücknehmen: Umsatz wieder offen, eigene Ausgabe/Zahlungsdatum zurück."""
    tx = row(conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)))
    if not tx:
        raise KeyError(tx_id)
    sug = json.loads(tx["suggestion"] or "{}")
    _release_expense(conn, tx, sug)
    sug.pop("paid_on_set", None)
    conn.execute("UPDATE transactions SET status = 'open', kind = NULL, expense_id = NULL, document_id = NULL, decided_at = NULL, "
                 "suggestion = ?, updated_at = ? WHERE id = ?", (json.dumps(sug, ensure_ascii=False), now(), tx_id))
    audit(conn, "assignment_undone", "transaction", tx_id)
