"""Anbindung an Mistral La Plateforme (EU-Verarbeitung).

Es wird nur ein gekürzter Textauszug des Dokuments gesendet, nie die Datei.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from . import config

log = logging.getLogger("smartarchive.ai")

MAX_TEXT_CHARS = 6000

SYSTEM_PROMPT = """Du ordnest Dokumente eines privaten Archivs ein. Antworte ausschließlich mit JSON.
Regeln:
- Wähle category_id NUR aus der gegebenen Liste. Erfinde keine Kategorien. Passt keine, nimm die Kategorie „Sonstiges“ des passenden Bereichs und setze confidence niedrig.
- property_id nur, wenn das Dokument eindeutig ein gelistetes Mietobjekt betrifft (Adresse, Objektname). Sonst null.
- evidence ist ein wörtliches, kurzes Zitat (max. 120 Zeichen) aus dem Dokument, das die Einordnung begründet.
- confidence zwischen 0 und 1: 0.95 nur bei eindeutigen Fällen, unter 0.6 bei Unsicherheit.
- Beträge in Cent als Ganzzahl (Endbetrag/zu zahlender Betrag), Datumsangaben als YYYY-MM-DD.
- Gib keine Informationen an, die nicht im Text stehen; unbekannte Felder sind null."""

RESPONSE_SHAPE = {
    "category_id": "int",
    "property_id": "int|null",
    "correspondent": "string|null  (Absender/Aussteller, kurzer Firmen- oder Behördenname)",
    "doc_type": "string|null  (z. B. Rechnung, Bescheid, Vertrag, Kontoauszug, Kassenbon, Abrechnung)",
    "title": "string  (kurzer sprechender Titel, max. 60 Zeichen, z. B. „Heizungswartung 2026“)",
    "doc_date": "YYYY-MM-DD|null",
    "amount_cents": "int|null  (nur bei Rechnungen/Belegen)",
    "invoice_number": "string|null  (Rechnungs- oder Belegnummer, nur wenn im Text)",
    "expense_category": "string|null  (nur bei Rechnungen zu einem Mietobjekt; Schlüssel aus der Liste der Ausgabenkategorien)",
    "confidence": "float 0..1",
    "reason": "string  (ein Satz, warum)",
    "evidence": "string  (wörtliches Zitat)",
}


class AIError(RuntimeError):
    pass


def build_prompt(text: str, categories: list[dict[str, Any]], properties: list[dict[str, Any]],
                 correspondents: list[str], area_hint: str) -> str:
    cat_lines = "\n".join(f"{c['id']}: {c['path']}" for c in categories)
    prop_lines = "\n".join(f"{p['id']}: {p['name']} – {p['address']}" for p in properties) or "(keine)"
    corr = ", ".join(correspondents[:150]) or "(noch keine)"
    from .seed import EXPENSE_CATEGORIES
    exp_lines = "\n".join(f"{c['key']}: {c['label']}" for c in EXPENSE_CATEGORIES)
    hint = {"privat": "Der Nutzer hat das Dokument im Bereich Privat hochgeladen.",
            "vermietung": "Der Nutzer hat das Dokument im Bereich Vermietung hochgeladen."}.get(area_hint, "Der Bereich ist offen.")
    return (
        f"{hint}\n\nKategorien (id: Bereich › Kategorie):\n{cat_lines}\n\n"
        f"Mietobjekte (id: Name – Adresse):\n{prop_lines}\n\n"
        f"Bekannte Absender: {corr}\n\n"
        f"Ausgabenkategorien (Schlüssel: Bezeichnung):\n{exp_lines}\n\n"
        f"Antwortformat (JSON): {json.dumps(RESPONSE_SHAPE, ensure_ascii=False)}\n\n"
        f"Dokumenttext (gekürzt):\n<<<\n{text[:MAX_TEXT_CHARS]}\n>>>"
    )


def classify_with_ai(prompt: str, timeout: float = 45) -> dict[str, Any]:
    cfg = config.settings
    if not cfg.mistral_api_key:
        raise AIError("Kein API-Schlüssel konfiguriert")
    body = json.dumps({
        "model": cfg.mistral_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(
        cfg.mistral_base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {cfg.mistral_api_key}", "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise AIError(f"Mistral antwortete mit HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AIError(f"Mistral nicht erreichbar: {exc}") from exc
    try:
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AIError("Antwort der KI war kein gültiges JSON") from exc


TX_SYSTEM_PROMPT = """Du ordnest Umsätze eines reinen Mietkontos zu. Antworte ausschließlich mit JSON.
Regeln:
- kind: "ausgabe" (Werbungskosten eines Mietobjekts), "einnahme" (Miete, Nebenkosten-Vorauszahlung) oder
  "ignoriert" (Umbuchung zwischen eigenen Konten, Darlehensauszahlung, Kaution).
- property_id nur, wenn der Text eindeutig ein gelistetes Objekt betrifft, sonst null.
- category_key nur bei Ausgaben und nur aus der Liste.
- Eine Darlehensrate enthält Tilgung: dann category_key "schuldzinsen" nur mit confidence unter 0.5.
- confidence zwischen 0 und 1; reason ist ein kurzer Satz. Erfinde nichts."""


def classify_transaction(tx: dict[str, Any], properties: list[dict[str, Any]], timeout: float = 30) -> dict[str, Any]:
    """Vorschlag für einen Kontoumsatz. Gesendet werden Datum, Betrag, Zahlungspartner und Verwendungszweck – keine IBAN."""
    from .seed import EXPENSE_CATEGORIES
    cfg = config.settings
    if not cfg.mistral_api_key:
        raise AIError("Kein API-Schlüssel konfiguriert")
    prop_lines = "\n".join(f"{p['id']}: {p['name']} – {p['address']}" for p in properties) or "(keine)"
    exp_lines = "\n".join(f"{c['key']}: {c['label']}" for c in EXPENSE_CATEGORIES)
    prompt = (
        f"Mietobjekte (id: Name – Adresse):\n{prop_lines}\n\nAusgabenkategorien:\n{exp_lines}\n\n"
        'Antwortformat: {"kind": "...", "property_id": "int|null", "category_key": "string|null", '
        '"confidence": "float", "reason": "string"}\n\n'
        f"Umsatz: {tx['booking_date']}, {tx['amount_cents'] / 100:.2f} EUR\n"
        f"Zahlungspartner: {tx['counterparty']}\nVerwendungszweck: {tx['purpose'][:600]}"
    )
    body = json.dumps({"model": cfg.mistral_model, "temperature": 0, "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": TX_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(cfg.mistral_base_url.rstrip("/") + "/chat/completions", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {cfg.mistral_api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
        return json.loads(payload["choices"][0]["message"]["content"])
    except urllib.error.HTTPError as exc:
        raise AIError(f"Mistral antwortete mit HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AIError(f"KI-Antwort nicht verwertbar: {exc}") from exc
