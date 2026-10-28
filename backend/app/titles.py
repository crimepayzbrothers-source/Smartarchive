"""Sprechende Titel aus dem erkannten Text.

Aufbau: <Art> <Absender> – <Gegenstand> <Monat/Jahr>, z. B.
„Rechnung Sanitär Schmidt – Reparatur Spülkasten 10/2026“ oder „Grundsteuerbescheid Verbandsgemeinde Nahe-Glan 2026“.
Ein Dateiname, den Sie selbst vergeben haben, bleibt stehen; ersetzt werden nur nichtssagende
Namen wie „IMG_20261028_1605“, „Scan …“ oder „document(3)“.
"""
from __future__ import annotations

import re
from pathlib import Path

_GENERIC_WORDS = (
    r"img|image|bild|foto|photo|pxl|dsc|dcim|cam|camscanner|scan|scanned|scanner|screenshot|bildschirmfoto|whatsapp|"
    r"signal|telegram|doc|docs|document|dokument|datei|file|unnamed|unbenannt|download|neu|new|kopie|copy|seite|seiten|page|pages|"
    r"und|von|am|um|wa|at|jpe?g|pdf|png|heic|"
    r"rechnung|rechnungen|beleg|belege|quittung|kassenbon|bon|brief|schreiben|anschreiben|anhang|anlage|anlagen|"
    r"attachment|unterlagen|post|eingang|neues?"
)


def is_generic(name: str | None) -> bool:
    """True, wenn der Name nichts über den Inhalt verrät (nur Kamera-/Scan-Wörter, Zahlen, Datum)."""
    stem = Path(name or "").stem if "." in (name or "")[-6:] else (name or "")
    rest = re.sub(rf"\b(?:{_GENERIC_WORDS})\b", " ", re.sub(r"[_\d]+", " ", stem), flags=re.I)
    return not re.search(r"[A-Za-zÄÖÜäöüß]{3,}", rest)


_SUBJECT = re.compile(
    r"^\s*(?:betreff|betr\.|gegenstand|leistung(?:sbeschreibung)?|ihr auftrag|auftrag|thema)\s*[:.]\s*(.{4,90})$",
    re.I | re.M)
_POSITION = re.compile(
    r"^\s*(?:pos(?:ition)?\.?\s*)?\d{1,3}[.)]?\s+(?:\d+(?:[.,]\d+)?\s*(?:x|stk\.?|st\.?|std\.?|h|m²|m2|m|psch\.?|pauschal)?\s+)?"
    r"([A-Za-zÄÖÜäöüß][^\d€]{3,60}?)\s+-?\d{1,3}(?:\.\d{3})*,\d{2}\b",
    re.I | re.M)
_ITEM_LINE = re.compile(r"^\s*([A-Za-zÄÖÜäöüß][^\d€]{3,60}?)\s{2,}-?\d{1,3}(?:\.\d{3})*,\d{2}\b", re.M)
_NOT_ITEM = re.compile(r"(?i)^(summe|gesamt|netto|brutto|mwst|ust|zwischensumme|rechnungsbetrag|endbetrag|zahlbetrag|"
                       r"betrag|total|bar|gegeben|rückgeld|rueckgeld|anfahrt|porto|versand)")
_SPECIFIC_TYPE = re.compile(
    r"\b([A-Za-zÄÖÜäöü]*(?:bescheid|abrechnung|bescheinigung|vertrag|police|mahnung|kündigung|kuendigung))\b", re.I)


def _clean(s: str, limit: int = 40) -> str:
    s = re.sub(r"\s+", " ", s).strip(" -–:;,.")
    if len(s) > 4 and s.isupper():
        s = " ".join(w if w.upper() in {"GMBH", "AG", "KG", "UG", "OHG", "GBR", "EG", "EV", "E.V."} or len(w) <= 2
                     else w.capitalize() for w in s.split())
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0]
    return s


def specific_type(text: str, doc_type: str | None) -> str | None:
    """„Bescheid“ -> „Grundsteuerbescheid“, „Vertrag“ -> „Mietvertrag“ usw., wenn das genaue Wort im Kopf steht."""
    if not doc_type:
        return None
    base = doc_type.lower()
    stem = {"jahresabrechnung": "abrechnung", "nebenkostenabrechnung": "abrechnung",
            "versicherungsschein": "police"}.get(base, base)
    for m in _SPECIFIC_TYPE.finditer(text[:2500]):
        word = m.group(1)
        low = word.lower()
        if low.endswith(stem) and 6 <= len(word) <= 32 and low != stem:
            return word[0].upper() + word[1:].lower()
    return doc_type


def subject(text: str, doc_type: str | None) -> str | None:
    m = _SUBJECT.search(text[:4000])
    if m:
        s = re.sub(r"\b(?:vom|nr\.?|kundennummer|rechnungsnummer)\b.*$", "", m.group(1), flags=re.I)
        s = _clean(s, 40)
        if len(s) >= 4:
            return s
    if doc_type in ("Rechnung", "Kassenbon", "Quittung", "Angebot"):
        for pattern in (_POSITION, _ITEM_LINE):
            for m in pattern.finditer(text[:5000]):
                words = re.findall(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß-]*", m.group(1))
                s = _clean(" ".join(words[:4]), 36)
                if len(s) >= 4 and not _NOT_ITEM.match(s):
                    return s
    return None


def first_line(text: str) -> str | None:
    """Notbehelf: erste aussagekräftige Zeile (kein Datum, keine Anrede, keine reine Adresse)."""
    for line in text.splitlines()[:15]:
        s = line.strip()
        if not re.search(r"[A-Za-zÄÖÜäöüß]{3,}", s):
            continue
        if re.match(r"(?i)(herrn?|frau|familie|sehr geehrte|seite|tel|fax|e-?mail|www\.|datum|\d{5}\s)", s):
            continue
        s = re.split(r"\s+[-–·|•]\s+|,\s*|\s{3,}", s)[0]
        s = _clean(s, 40)
        if len(s) >= 4:
            return s
    return None


def make_title(text: str, *, doc_type: str | None, correspondent: str | None, doc_date: str | None,
               payee_guess: str | None = None, created: str | None = None) -> str:
    kind = specific_type(text, doc_type)
    sender = _clean(correspondent or payee_guess or "", 36) or None
    subj = subject(text, doc_type)
    if sender and subj and subj.lower() in sender.lower():
        subj = None
    parts: list[str] = []
    if kind:
        parts.append(kind)
    if sender and not (kind and sender.lower() in kind.lower()):
        parts.append(sender)
    if not parts:
        fl = first_line(text)
        if fl:
            parts.append(fl)
    base = " ".join(parts)
    when = doc_date or (created or "")[:10] or None
    stamp = ""
    if when:
        yearly = (kind or "").lower().endswith(("abrechnung", "bescheid", "bescheinigung"))
        stamp = when[:4] if yearly else f"{when[5:7]}/{when[:4]}"
    if subj and base and subj.lower() in base.lower():
        subj = None
    if subj:
        room = 80 - len(base) - len(stamp) - 4
        if room < 8:
            subj = None
        elif len(subj) > room:
            subj = subj[:room].rsplit(" ", 1)[0]
    title = f"{base} – {subj}" if base and subj else (base or subj or "Dokument")
    if stamp and stamp not in title:
        title = f"{title} {stamp}".strip()
    if not title.strip():
        title = f"Dokument {when[8:10]}.{when[5:7]}.{when[:4]}" if when else "Dokument"
    return title[:80].strip()
