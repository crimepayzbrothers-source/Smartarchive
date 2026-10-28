"""Lokale Erkennung ohne KI: Datum, Betrag, Mietobjekt, Absender."""
from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3, "april": 4, "apr": 4,
    "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}

_DATE_NUM = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_WORD = re.compile(r"\b(\d{1,2})\.\s?(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{4})\b", re.I)
_DATE_LABEL = re.compile(r"(rechnungsdatum|belegdatum|datum|ausstellungsdatum|bescheiddatum|vom)\s*[:.]?\s*$", re.I)

_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})(?!\d)\s*(?:€|eur|euro)?", re.I)
_AMOUNT_LABEL = re.compile(
    r"(gesamtbetrag|rechnungsbetrag|endbetrag|zahlbetrag|zu zahlen|zahlbarer betrag|bruttobetrag|"
    r"gesamtsumme|summe brutto|brutto|summe|gesamt|total|betrag)", re.I)


def _valid(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    if not (1990 <= dt.year <= date.today().year + 2):
        return None
    return dt


def find_dates(text: str) -> list[tuple[date, int]]:
    found: list[tuple[date, int]] = []
    for m in _DATE_NUM.finditer(text):
        dt = _valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            found.append((dt, m.start()))
    for m in _DATE_ISO.finditer(text):
        dt = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt:
            found.append((dt, m.start()))
    for m in _DATE_WORD.finditer(text):
        dt = _valid(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        if dt:
            found.append((dt, m.start()))
    return sorted(found, key=lambda x: x[1])


def detect_date(text: str) -> tuple[str | None, str]:
    """Bevorzugt ein Datum hinter „Datum:“/„Rechnungsdatum“, sonst das erste im Kopfbereich."""
    head = text[:4000]
    dates = find_dates(head)
    if not dates:
        return None, ""
    for dt, pos in dates:
        before = head[max(0, pos - 30):pos]
        if _DATE_LABEL.search(before):
            return dt.isoformat(), _line_at(head, pos)
    dt, pos = dates[0]
    return dt.isoformat(), _line_at(head, pos)


def parse_amount(raw_int: str, raw_dec: str) -> int:
    return int(raw_int.replace(".", "")) * 100 + int(raw_dec)


def detect_amount(text: str) -> tuple[int | None, str]:
    """Sucht den Endbetrag: zuerst Zeilen mit Beschriftung, die unterste gewinnt."""
    best: tuple[int, int, str] | None = None  # (Priorität, Cent, Zeile)
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        label = _AMOUNT_LABEL.search(line)
        amounts = list(_AMOUNT.finditer(line))
        if not amounts and label and idx + 1 < len(lines):
            amounts = list(_AMOUNT.finditer(lines[idx + 1]))
        if not amounts:
            continue
        cents = parse_amount(amounts[-1].group(1), amounts[-1].group(2))
        if cents <= 0 or cents > 10_000_000_00:
            continue
        prio = 0
        if label:
            word = label.group(1).lower()
            prio = 3 if word in {"gesamtbetrag", "rechnungsbetrag", "endbetrag", "zahlbetrag", "zu zahlen",
                                 "zahlbarer betrag", "gesamtsumme", "summe brutto"} else 2
        cand = (prio, cents, line.strip())
        if best is None or cand[0] > best[0] or (cand[0] == best[0] and prio > 0):
            best = cand
    if not best or best[0] == 0:
        return None, ""
    return best[1], best[2][:160]


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else None].strip()[:160]


def street_pattern(address: str) -> re.Pattern[str] | None:
    """Macht aus „Hauptstraße 15, 55758 …“ ein tolerantes Suchmuster (Str./Strasse/Straße)."""
    m = re.match(r"\s*([^\d,]+?)\s*(\d+\s*[a-zA-Z]?)\b", address or "")
    if not m:
        return None
    street, number = m.group(1).strip(), re.sub(r"\s+", "", m.group(2))
    street_re = re.escape(street)
    street_re = re.sub(r"(?i)stra(ß|ss)e$", r"(?:straße|strasse|str\\.?)", street_re)
    street_re = street_re.replace(r"\ ", r"[\s-]*")
    num_re = re.escape(number[:-1]) + r"\s*" + re.escape(number[-1]) if number[-1:].isalpha() else re.escape(number)
    return re.compile(street_re + r"\s*" + num_re + r"(?!\d)", re.I)


def detect_property(text: str, properties: list[dict]) -> tuple[int | None, str]:
    hits: list[tuple[int, str]] = []
    for p in properties:
        pat = street_pattern(p.get("address", ""))
        evidence = ""
        if pat:
            m = pat.search(text)
            if m:
                evidence = _line_at(text, m.start())
        if not evidence:
            for kw in [k.strip() for k in (p.get("keywords") or "").split(",") if len(k.strip()) >= 4]:
                idx = text.lower().find(kw.lower())
                if idx >= 0:
                    evidence = _line_at(text, idx)
                    break
        if evidence:
            hits.append((p["id"], evidence))
    if len(hits) == 1:
        return hits[0]
    return None, ""


def detect_correspondent(text: str, correspondents: list[dict]) -> tuple[int | None, str]:
    head = text[:3000].lower()
    best: tuple[int, int, str] | None = None
    for c in correspondents:
        names = [c["name"]] + [a.strip() for a in (c.get("aliases") or "").split(",") if a.strip()]
        for name in names:
            if len(name) < 3:
                continue
            idx = head.find(name.lower())
            if idx >= 0 and (best is None or idx < best[0]):
                best = (idx, c["id"], name)
    return (best[1], best[2]) if best else (None, "")


DOC_TYPES = [
    ("Jahresabrechnung", r"jahresabrechnung|hausgeldabrechnung|wohngeldabrechnung"),
    ("Nebenkostenabrechnung", r"nebenkostenabrechnung|betriebskostenabrechnung|heizkostenabrechnung"),
    ("Bescheid", r"\w*bescheid\b"),
    ("Mahnung", r"\bmahnung\b|zahlungserinnerung"),
    ("Gutschrift", r"\bgutschrift\b"),
    ("Rechnung", r"\brechnung\b|\brechnungs(nummer|datum)|\binvoice\b"),
    ("Kassenbon", r"kassenbon|\bbon[- ]?nr|\bkassenbeleg"),
    ("Quittung", r"\bquittung\b"),
    ("Kontoauszug", r"kontoauszug"),
    ("Angebot", r"\bangebot\b|kostenvoranschlag"),
    ("Vertrag", r"\bvertrag\b|vereinbarung zwischen"),
    ("Versicherungsschein", r"versicherungsschein|police\b"),
]


def detect_doc_type(text: str) -> str | None:
    head = text[:2500].lower()
    for label, pattern in DOC_TYPES:
        if re.search(pattern, head):
            return label
    return None


def _keyword_re(word: str) -> re.Pattern[str]:
    """Kurze Stichwörter nur als ganzes Wort („gas“ ≠ „Gasbrennwertkessel“), längere auch als Wortanfang
    („rechnung“ trifft „Rechnungsdatum“)."""
    esc = re.escape(word)
    return re.compile(r"(?<![\wäöüß])" + esc + (r"(?![\wäöüß])" if len(word) < 5 else ""), re.I)


def keyword_scores(text: str, categories: list[dict]) -> list[tuple[dict, int, list[str]]]:
    low = text.lower()
    scored = []
    for c in categories:
        words = [w.strip().lower() for w in (c.get("keywords") or "").split(",") if len(w.strip()) >= 3]
        matched = [w for w in words if _keyword_re(w).search(low)]
        if matched:
            scored.append((c, len(matched), matched))
    scored.sort(key=lambda x: (-x[1], x[0].get("depth", 0) * -1))
    return scored


_NAME_DATES = [
    re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)"),  # 2023-05-12, 20230512
    re.compile(r"(?<!\d)(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.((?:19|20)\d{2})(?!\d)"),          # 12.05.2023
]


def date_from_name(*names: str | None) -> str | None:
    """Datum aus Datei- oder Ordnernamen (z. B. „IMG_20230512_1205.jpg“, „2023-05-12 Rechnung.pdf“)."""
    for name in names:
        if not name:
            continue
        for i, pat in enumerate(_NAME_DATES):
            m = pat.search(name)
            if not m:
                continue
            y, mo, d = (m.group(1), m.group(2), m.group(3)) if i == 0 else (m.group(3), m.group(2), m.group(1))
            dt = _valid(int(y), int(mo), int(d))
            if dt:
                return dt.isoformat()
    return None
