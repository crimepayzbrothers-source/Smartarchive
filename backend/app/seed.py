"""Startstruktur: Bereiche, Kategorien und Ausgabenkategorien."""
from __future__ import annotations

import sqlite3

from .db import get_setting, set_setting

# Bereich -> [(Kategorie, Stichwörter, KI erlaubt, [(Unterordner, Stichwörter)])]
DEFAULT_TREE: dict[str, list[tuple[str, str, bool, list[tuple[str, str]]]]] = {
    "privat": [
        ("Finanzen und Banken", "kontoauszug, girokonto, depot, kreditkarte, sparkasse, volksbank, iban, zinsen, bank", True,
         [("Kontoauszüge", "kontoauszug, kontostand"), ("Depot", "depot, wertpapier, dividende, etf")]),
        ("Versicherungen", "versicherung, versicherungsschein, police, beitragsrechnung, haftpflicht, hausrat", True, []),
        ("Verträge", "vertrag, vertragsnummer, kündigung, laufzeit, mobilfunk, strom, gas, internet", True,
         [("Strom und Gas", "strom, gas, abschlag, zählerstand, kwh, energie"), ("Telefon und Internet", "mobilfunk, dsl, internet, telefon")]),
        ("Steuern", "finanzamt, steuerbescheid, einkommensteuer, steuer-id, elster, lohnsteuerbescheinigung", True, []),
        ("Fahrzeuge", "kfz, fahrzeug, kennzeichen, werkstatt, tüv, hauptuntersuchung, kfz-steuer, reifen", True, []),
        ("Gesundheit", "arzt, praxis, krankenkasse, rezept, diagnose, befund, apotheke, zahnarzt, krankenhaus, attest", False, []),
        ("Familie", "geburtsurkunde, heiratsurkunde, kita, schule, kindergeld, elterngeld", False, []),
        ("Arbeit", "gehaltsabrechnung, bezüge, besoldung, dienstherr, arbeitgeber, entgelt", True, []),
        ("Sonstiges", "", True, []),
    ],
    "vermietung": [
        ("Rechnungen und Belege", "rechnung, quittung, kassenbon, beleg, handwerker, reparatur, material, baumarkt", True, []),
        ("Versicherungen", "gebäudeversicherung, wohngebäude, haus- und grundbesitzerhaftpflicht, versicherungsschein, police", True, []),
        ("Grundbesitzabgaben", "grundsteuer, grundbesitzabgaben, abfall, müllabfuhr, abwasser, niederschlagswasser, straßenreinigung", True, []),
        ("Hausgeld und WEG", "hausgeld, wohngeld, jahresabrechnung, wirtschaftsplan, eigentümerversammlung, weg, erhaltungsrücklage", True, []),
        ("Nebenkosten", "betriebskosten, nebenkostenabrechnung, heizkosten, wasser, schornsteinfeger, ista, techem, brunata", True, []),
        ("Finanzierung", "darlehen, kredit, tilgung, zinsbescheinigung, baufinanzierung, sollzins", True, []),
        ("Mietverträge und Mieter", "mietvertrag, mieter, kaution, übergabeprotokoll, mieterhöhung, kündigung mietverhältnis", True, []),
        ("Kauf und Objektunterlagen", "kaufvertrag, notar, grundbuch, teilungserklärung, energieausweis, grundriss", True, []),
        ("Sonstiges", "", True, []),
    ],
}

AREAS = [("privat", "Privat"), ("vermietung", "Vermietung")]

# Ausgabenkategorien nach Werbungskosten-Gruppen der Anlage V.
# deductible: False = nicht sofort abziehbar (wird im Export getrennt ausgewiesen).
EXPENSE_CATEGORIES: list[dict] = [
    {"key": "schuldzinsen", "label": "Schuldzinsen", "deductible": True},
    {"key": "geldbeschaffung", "label": "Geldbeschaffungskosten", "deductible": True},
    {"key": "erhaltung_sofort", "label": "Erhaltungsaufwand (sofort)", "deductible": True},
    {"key": "erhaltung_verteilt", "label": "Erhaltungsaufwand (Verteilung § 82b EStDV)", "deductible": True},
    {"key": "grundsteuer", "label": "Grundsteuer", "deductible": True},
    {"key": "grundbesitzabgaben", "label": "Müll, Straßenreinigung, Wasser, Abwasser", "deductible": True},
    {"key": "heizung", "label": "Heizung und Warmwasser", "deductible": True},
    {"key": "versicherung", "label": "Gebäudeversicherungen", "deductible": True},
    {"key": "hausnebenkosten", "label": "Hausbeleuchtung, Schornsteinfeger, Hauswart, Reinigung", "deductible": True},
    {"key": "verwaltung", "label": "Verwaltungskosten", "deductible": True},
    {"key": "hausgeld_laufend", "label": "Hausgeld (laufende Kosten)", "deductible": True},
    {"key": "fahrtkosten", "label": "Fahrtkosten", "deductible": True},
    {"key": "sonstige", "label": "Sonstige Werbungskosten", "deductible": True},
    {"key": "nicht_sofort", "label": "Nicht sofort abziehbar (z. B. Rücklagenzuführung, Anschaffungskosten)", "deductible": False},
]
EXPENSE_KEYS = {c["key"] for c in EXPENSE_CATEGORIES}

DEFAULT_SETTINGS = {
    "auto_file": True,          # sichere Vorschläge sofort ablegen
    "auto_threshold": 0.9,      # ab dieser Sicherheit automatisch
    "suggest_threshold": 0.5,   # darunter kein Vorschlag
    "ai_enabled": True,         # KI grundsätzlich erlaubt (wirkt nur mit API-Schlüssel)
}


def seed(conn: sqlite3.Connection) -> None:
    for key, name in AREAS:
        conn.execute("INSERT OR IGNORE INTO areas(key, name) VALUES(?, ?)", (key, name))
    for key, value in DEFAULT_SETTINGS.items():
        if get_setting(conn, key) is None:
            set_setting(conn, key, value)
    if get_setting(conn, "seeded_categories"):
        return
    for area_key, cats in DEFAULT_TREE.items():
        area_id = conn.execute("SELECT id FROM areas WHERE key = ?", (area_key,)).fetchone()[0]
        for sort, (name, keywords, ai, children) in enumerate(cats):
            cur = conn.execute(
                "INSERT INTO categories(area_id, parent_id, name, keywords, ai_enabled, sort) VALUES(?,?,?,?,?,?)",
                (area_id, None, name, keywords, int(ai), sort),
            )
            for csort, (child, ckw) in enumerate(children):
                conn.execute(
                    "INSERT INTO categories(area_id, parent_id, name, keywords, ai_enabled, sort) VALUES(?,?,?,?,?,?)",
                    (area_id, cur.lastrowid, child, ckw, int(ai), csort),
                )
    set_setting(conn, "seeded_categories", True)
