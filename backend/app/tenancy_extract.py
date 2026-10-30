"""Mietverhältnis aus Mietvertrag oder Screenshot auslesen (Vorschlag – gespeichert wird erst nach Ihrer Prüfung).

Ablauf: Text gewinnen (PDF-Textebene, sonst Texterkennung auf dem Server) → beschriftete Angaben suchen
(Mietbeginn, Kaltmiete, Vorauszahlungen, Vertragspartner, Wohnung …). Zu jedem Fund wird die Fundstelle mitgegeben.
Optional (nur auf ausdrücklichen Wunsch) liest die KI (Mistral, EU) zusätzlich aus – sie erkennt auch mehrere
Mietverhältnisse auf einem Listen-Screenshot.
"""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import ocr

MAX_PAGES = 8
MONTHS = {"januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
          "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12}


# --- Text gewinnen ---------------------------------------------------------------------

def _ocr_image(im: Image.Image) -> str:
    import pytesseract
    im = ImageOps.exif_transpose(im).convert("L")
    if max(im.size) < 1800:  # Screenshots vom Handy: hochskalieren hilft der Texterkennung deutlich
        factor = 1800 / max(im.size)
        im = im.resize((int(im.width * factor), int(im.height * factor)), Image.LANCZOS)
    if im.resize((1, 1)).getpixel((0, 0)) < 110:  # dunkles Design (heller Text auf dunklem Grund): invertieren
        im = ImageOps.invert(im)
    return pytesseract.image_to_string(im, lang=ocr.ocr_languages(), config="--psm 4")


def text_from_file(data: bytes, mime: str) -> str:
    if mime.startswith("image/"):
        with Image.open(io.BytesIO(data)) as im:
            return _ocr_image(im)
    if mime == "application/pdf":
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.pdf"
            src.write_bytes(data)
            pages = ocr.extract_text_pages(src)[:MAX_PAGES]
            if ocr.has_text_layer(pages, len(pages) or 1):
                return "\n".join(pages)
            subprocess.run(["pdftoppm", "-r", "250", "-png", "-l", str(MAX_PAGES), str(src), f"{tmp}/p"], check=True, timeout=600)
            texts = []
            for p in sorted(Path(tmp).glob("p*.png")):
                with Image.open(p) as im:
                    texts.append(_ocr_image(im))
            return "\n".join(texts)
    raise ValueError("Bitte PDF oder Bilddateien (Foto, Screenshot) verwenden.")


# --- Bausteine ----------------------------------------------------------------------------

_AMOUNT = r"(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+,\d{1,2}|\d+(?=\s*(?:€|EUR|Euro)))"
_DATE = re.compile(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})\b|(\d{1,2})\.\s?(" + "|".join(MONTHS) + r")\s+(\d{4})", re.I)


def _norm(text: str) -> list[str]:
    text = text.replace(" ", " ").replace("|", " ").replace("\t", " ")
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]


def _cents(raw: str) -> int | None:
    s = raw.replace(".", "").replace(" ", "")
    if "," in s:
        euros, cents = s.split(",", 1)
        cents = (cents + "0")[:2]
    else:
        euros, cents = s, "00"
    try:
        return int(euros) * 100 + int(cents)
    except ValueError:
        return None


def _date(m: re.Match) -> str | None:
    try:
        if m.group(1):
            y = int(m.group(3))
            y = y + 2000 if y < 100 else y
            return date(y, int(m.group(2)), int(m.group(1))).isoformat()
        return date(int(m.group(6)), MONTHS[m.group(5).lower()], int(m.group(4))).isoformat()
    except (ValueError, KeyError):
        return None


def _labelled(lines: list[str], label: str, kind: str, exclude: str | None = None) -> tuple[Any, str] | None:
    """Sucht label und den Wert dahinter in derselben Zeile. Steht hinter dem label nichts (Formular/Screenshot:
    Bezeichnung und Wert untereinander), wird die nächste Zeile genommen."""
    lab = re.compile(label, re.I)
    exc = re.compile(exclude, re.I) if exclude else None

    def find(chunk: str) -> Any:
        if kind == "amount":
            a = re.search(_AMOUNT, chunk)
            return _cents(a.group(1)) if a else None
        if kind == "date":
            d = _DATE.search(chunk)
            return _date(d) if d else None
        n = re.search(r"\b(\d{1,2})\b", chunk)
        return int(n.group(1)) if n else None

    for i, ln in enumerate(lines):
        m = lab.search(ln)
        if not m or (exc and exc.search(ln)):
            continue
        rest = ln[m.end():]
        v = find(rest)
        if v is not None:
            return v, ln
        if not re.search(r"[0-9A-Za-zÄÖÜäöü]", rest) and i + 1 < len(lines) and not (exc and exc.search(lines[i + 1])):
            v = find(lines[i + 1])
            if v is not None:
                return v, f"{ln} {lines[i + 1]}"
    return None


# --- Vertragspartner ---------------------------------------------------------------------

_SALUTATION = re.compile(r"^(herrn?|frau|fr\.|hr\.|dr\.?|prof\.?|eheleute|familie)\s+", re.I)
_NAME = r"[A-ZÄÖÜ][a-zäöüßéèç\-]+(?:\s+[A-ZÄÖÜ][a-zäöüßéèç\-]+){1,3}"


def _split_names(raw: str) -> list[dict[str, str]]:
    raw = re.sub(r"\(.*?\)|geb\.\s*\d.*|,?\s*wohnhaft.*|,?\s*\d{5}.*", "", raw)
    parts = re.split(r"\s*(?:,|;|&|\bund\b|\bsowie\b|/)\s*", raw)
    out, shared_last = [], ""
    for p in parts:
        p = _SALUTATION.sub("", p.strip(" .:-")).strip()
        p = _SALUTATION.sub("", p).strip()
        if re.fullmatch(r"(?i:herrn?|frau|eheleute|familie|dr\.?)", p):
            continue
        if not p or re.search(r"\d|straße|str\.|weg\b|platz|@", p, re.I) or len(p) > 60:
            continue
        toks = p.split()
        if len(toks) == 1:  # „Max und Erika Mustermann“ → Vorname allein
            out.append({"first_name": toks[0], "last_name": ""})
            continue
        out.append({"first_name": " ".join(toks[:-1]), "last_name": toks[-1]})
        shared_last = toks[-1]
    for o in out:
        if not o["last_name"]:
            o["last_name"] = shared_last
    return [o for o in out if o["last_name"] and o["first_name"][:1].isupper()]


def _parties(lines: list[str], text: str, exclude_names: list[str]) -> tuple[list[dict[str, str]], str]:
    found: list[dict[str, str]] = []
    evidence = ""
    # a) Formularfelder (z. B. Screenshot): Vorname / Nachname
    def field(pattern: str) -> list[str]:
        vals = []
        for i, ln in enumerate(lines):
            m = re.match(r"^" + pattern + r"\s*:?\s*(.*)$", ln, re.I)
            if m:
                v = m.group(1).strip() or (lines[i + 1] if i + 1 < len(lines) else "")
                if v and not re.match(r"^(vorname|nachname|familienname|name)\b", v, re.I):
                    vals.append(v.strip())
        return vals

    firsts, lasts = field(r"vorname"), field(r"(?:nach|familien)name")
    for f, l in zip(firsts, lasts):
        if f and l:
            found.append({"first_name": f.strip(), "last_name": l.strip()})
            evidence = f"Vorname {f} / Nachname {l}"
    # b) „Mieter: …“ (nicht Vermieter, nicht Mietereigenschaften)
    if not found:
        for ln in lines:
            m = re.match(r"^(?:\d+\.\s*)?(?:mieter(?:in|innen)?|mietpartei|vertragspartner)\s*(?:\d)?\s*[:\-–]\s*(.+)$", ln, re.I)
            if m:
                names = _split_names(m.group(1))
                if names:
                    found += names
                    evidence = ln
    # c) Vertragsformulierung „… und Herrn Max Mustermann … – nachfolgend Mieter –“
    if not found:
        flat = re.sub(r"\s+", " ", text)
        sal = r"(?:(?i:herrn?|frau|eheleute)\s+)?"
        m = re.search(r"\b(?i:und)\s+(" + sal + _NAME + r"(?:\s*(?:,|(?i:und)|&)\s*" + sal + _NAME + r")?)"
                      r"[^.]{0,120}?(?i:nachfolgend|im folgenden|–|-|\()\s*[„\"]?(?i:mieter)", flat)
        if m:
            found = _split_names(m.group(1))
            evidence = m.group(0)[:160]
    excl = {e.lower() for name in exclude_names for e in name.split() if len(e) > 2}
    found = [f for f in found if f["last_name"].lower() not in excl]
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phones = [re.sub(r"\s+", " ", p).strip() for p in re.findall(r"(?:\+49|0)\d[\d /\-()]{6,}\d", text)]
    for i, f in enumerate(found):
        f.setdefault("email", emails[i] if i < len(emails) else "")
        f.setdefault("phone", phones[i] if i < len(phones) else "")
        f.setdefault("address", "")
    return found, evidence


# --- Wohnung ------------------------------------------------------------------------------

def _variants(name: str) -> list[str]:
    n = name.lower().strip()
    v = {n}
    rep = [(r"\beg\b", "erdgeschoss"), (r"\bog\b", "obergeschoss"), (r"\bdg\b", "dachgeschoss"), (r"\bug\b", "untergeschoss")]
    for a, b in rep:
        if re.search(a, n):
            v.add(re.sub(a, b, n))
            v.add(re.sub(a, "1. " + b, n))
            v.add(re.sub(a, "1. " + a.strip("\\b"), n))
    return sorted(v, key=len, reverse=True)


def match_unit(text: str, units: list[dict[str, Any]]) -> tuple[int | None, str]:
    low = re.sub(r"\s+", " ", text.lower())
    hits = []
    for u in units:
        for v in _variants(u["name"]):
            if re.search(r"(?<![a-zäöü])" + re.escape(v) + r"(?![a-zäöü])", low):
                hits.append((len(v), u))
                break
    if not hits:
        return None, ""
    hits.sort(key=lambda h: -h[0])
    best = hits[0][1]
    # Mehrdeutig (z. B. nur „OG“ gefunden, aber drei OG-Wohnungen): nicht raten
    if len(hits) > 1 and hits[1][0] == hits[0][0]:
        return None, "mehrere Wohnungen kommen in Frage: " + ", ".join(h[1]["name"] for h in hits)
    return best["id"], best["name"]


# --- Gesamt ----------------------------------------------------------------------------------

FIELDS = ("parties", "contract_start", "cold_rent_cents", "bk_advance_cents", "hk_advance_cents")


def parse(text: str, units: list[dict[str, Any]], exclude_names: list[str] | None = None) -> dict[str, Any]:
    lines = _norm(text)
    ev: dict[str, str] = {}
    r: dict[str, Any] = {}

    def take(key: str, found: tuple[Any, str] | None) -> None:
        if found and found[0] is not None:
            r[key], ev[key] = found[0], found[1][:180]

    take("contract_start", _labelled(lines, r"miet(?:vertrags)?beginn|beginn des miet|mietverhältnis beginnt|mietzeit beginnt|vertragsbeginn|beginnt am", "date"))
    take("move_in", _labelled(lines, r"\beinzug(?:sdatum|stermin)?\b|übergabe der (?:wohnung|mieträume)", "date"))
    take("contract_end", _labelled(lines, r"mietende|vertragsende|befristet bis|mietverhältnis endet|endet am", "date"))
    take("move_out", _labelled(lines, r"\bauszug(?:sdatum)?\b", "date"))
    take("cold_rent_cents", _labelled(lines, r"(?:netto)?kaltmiete|grundmiete|nettomiete|miete \(kalt\)|kaltmiete", "amount", exclude=r"gesamt|warm"))
    take("bk_advance_cents", _labelled(lines, r"betriebskosten|nebenkosten|bk-?vorauszahlung", "amount",
                                       exclude=r"heiz|warmwasser|abrechnung über|gesamt"))
    take("hk_advance_cents", _labelled(lines, r"heiz(?:ungs)?kosten|heizung(?: und warmwasser)?|hk-?vorauszahlung|warmwasser", "amount",
                                       exclude=r"gesamt|abrechnung über"))
    take("persons", _labelled(lines, r"personen(?:anzahl|zahl)?|anzahl (?:der )?personen|bewohner", "int"))
    if "persons" not in r:
        m = re.search(r"\b(\d{1,2})\s*personen\b", text, re.I)
        if m:
            r["persons"], ev["persons"] = int(m.group(1)), m.group(0)
    area = re.search(r"(?:wohnfläche|fläche)[^\d\n]{0,40}(\d{1,3}(?:,\d{1,2})?)\s*(?:m²|m2|qm)", text, re.I)
    if area:
        r["living_area"], ev["living_area"] = float(area.group(1).replace(",", ".")), area.group(0)[:120]
    parties, pev = _parties(lines, text, exclude_names or [])
    if parties:
        r["parties"], ev["parties"] = parties, pev
    uid, uname = match_unit(text, units)
    r["unit_id"] = uid
    if uname:
        ev["unit"] = uname
    if r.get("move_in") and not r.get("contract_start"):
        r["contract_start"], ev["contract_start"] = r["move_in"], ev["move_in"] + " (Einzug als Mietbeginn angenommen – bitte prüfen)"
    r["evidence"] = ev
    r["missing"] = [k for k in FIELDS if not r.get(k)] + ([] if uid or not units else ["unit_id"])
    r["source"] = "lokal"
    return r


def ai_parse(text: str, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """KI-Auslese (Mistral, EU) – nur auf ausdrücklichen Wunsch. Liefert eine Liste (auch mehrere Mieter je Screenshot)."""
    from . import mistral
    res = mistral.extract_tenancies(text, [u["name"] for u in units])
    out = []
    for t in (res.get("tenancies") or [])[:20]:
        if not isinstance(t, dict):
            continue
        item: dict[str, Any] = {"source": "ki", "evidence": {}}
        for k in ("contract_start", "move_in", "contract_end", "move_out"):
            v = t.get(k)
            item[k] = v if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None
        for k in ("cold_rent_cents", "bk_advance_cents", "hk_advance_cents", "persons"):
            v = t.get(k)
            item[k] = v if isinstance(v, int) and v >= 0 else None
        item["parties"] = [{"first_name": str(p.get("first_name") or "").strip(), "last_name": str(p.get("last_name") or "").strip(),
                            "email": str(p.get("email") or "").strip(), "phone": str(p.get("phone") or "").strip(), "address": ""}
                           for p in (t.get("parties") or []) if isinstance(p, dict) and p.get("last_name")]
        uid, uname = match_unit(str(t.get("unit") or ""), units) if t.get("unit") else (None, "")
        item["unit_id"] = uid
        if uname:
            item["evidence"]["unit"] = uname
        item["missing"] = [k for k in FIELDS if not item.get(k)] + ([] if uid or not units else ["unit_id"])
        out.append(item)
    return out


def merge(local: dict[str, Any], ai: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bei genau einem Mietverhältnis: KI ergänzt fehlende Angaben; Abweichungen werden als Rückfrage markiert."""
    if not ai:
        return [local]
    if len(ai) > 1:
        return ai
    a = ai[0]
    out = {**local, "source": "lokal+ki", "conflicts": {}}
    for k in ("contract_start", "move_in", "contract_end", "move_out", "cold_rent_cents", "bk_advance_cents", "hk_advance_cents",
              "persons", "unit_id"):
        if a.get(k) is None:
            continue
        if local.get(k) is None:
            out[k] = a[k]
            out["evidence"] = {**out.get("evidence", {}), k: "von der KI ausgelesen"}
        elif local[k] != a[k]:
            out["conflicts"][k] = {"lokal": local[k], "ki": a[k]}
    if not local.get("parties") and a.get("parties"):
        out["parties"] = a["parties"]
    out["missing"] = [k for k in FIELDS if not out.get(k)]
    return [out]


# --- Vorschau zum Abtippen (z. B. handschriftlich ausgefüllte Verträge) -------------------

def preview_pages(data: bytes, mime: str, max_pages: int = MAX_PAGES) -> list[bytes]:
    """JPEG-Seiten zum Ansehen beim Ausfüllen. Es wird nichts gespeichert."""
    out: list[bytes] = []
    if mime.startswith("image/"):
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1800, 1800))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82)
            out.append(buf.getvalue())
        return out
    if mime == "application/pdf":
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.pdf"
            src.write_bytes(data)
            subprocess.run(["pdftoppm", "-r", "130", "-jpeg", "-jpegopt", "quality=80", "-l", str(max_pages), str(src), f"{tmp}/p"],
                           check=True, timeout=300)
            for p in sorted(Path(tmp).glob("p*.jpg")):
                out.append(p.read_bytes())
        return out
    raise ValueError("Bitte PDF oder Bilddateien verwenden.")
