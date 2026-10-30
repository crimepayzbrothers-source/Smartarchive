# Mieterverwaltung und Betriebskostenabrechnung

Stand: 30.10.2026 · Code: `backend/app/tenancy.py`, `backend/app/betriebskosten.py`, `backend/app/bk_pdf.py`, API `api/tenants.py`, `api/bk.py`, Oberfläche `pages/Tenants.tsx`, `pages/Betriebskosten.tsx` · Tests: `tests/test_tenancy.py`, `tests/test_betriebskosten.py`

> Die App rechnet und prüft. Sie ersetzt keine Rechtsberatung. Rechtssicherheit wird nicht zugesichert. Verbleibende Unsicherheiten stehen in Abschnitt 6.

## 1. Ausgangslage (Analyse der bestehenden App)

| Vorhanden | Wiederverwendet für |
| --- | --- |
| Objekte (`properties`) und Einheiten (`units`) mit Wohnfläche | Mietsache, Flächenschlüssel |
| Ausgaben mit Kategorie, Beleg und Zahlungsdatum | Übernahme der Kosten (Abflussprinzip), Verknüpfung zum Originalbeleg |
| Mietkonto (CSV-Import, Zuordnung als Einnahme je Objekt/Einheit) | Vorschlag für die tatsächlich geleisteten Vorauszahlungen |
| Dokumentenarchiv mit Volltextsuche | Ablage der freigegebenen Abrechnungen (Vermietung › Nebenkosten) |
| reportlab, Änderungsprotokoll, Login mit 2FA, Zugriff nur über Tailscale, verschlüsseltes Backup | PDF, Nachvollziehbarkeit, Datenschutz |

Objektart laut Nutzer: ganze Häuser mit mehreren Wohnungen, Einzelrechnungen. Heizung: Zentralheizung mit Ablesefirma.

## 2. Datenmodell

- **Migration 9:** `tenancies` (Mietsache, Mietbeginn/-ende laut Vertrag, tatsächlicher Ein-/Auszug, Umlagevereinbarungen), `tenancy_parties` (mehrere Vertragspartner) und `tenancy_terms` (Kaltmiete, BK- und HK-Vorauszahlung, Personenzahl, jeweils gültig ab Datum).
- **Migration 10:**
  - `bk_periods` (Abrechnungszeitraum je Objekt, Status, CO2-Angaben, bestätigte Warnungen)
  - `bk_costs` (Kostenpositionen, optional verknüpft mit Ausgabe und Beleg)
  - `bk_keys` (Umlageschlüssel je Kostenart mit Begründung)
  - `bk_values` (Verbrauch, Beträge der Ablesefirma, individuelle Anteile)
  - `bk_advances` (Ist-Vorauszahlungen, bestätigt)
  - `bk_statements` (freigegebene Fassungen als unveränderlicher Snapshot)

**Grundsätze:**
- Mietverhältnisse werden nie überschrieben. Ein Mieterwechsel beendet das alte Mietverhältnis und legt ein neues an, Überschneidungen werden abgelehnt.
- Einheiten mit Mietverhältnissen lassen sich nicht löschen.
- Ins Änderungsprotokoll kommen keine Namen oder Kontaktdaten der Mieter.

## 3. Rechenregeln

- **Zeitanteile:** Tage werden kalendergenau gezählt (Monatslängen, Schaltjahre), Beginn und Ende jeweils einschließlich.
- **Maßgeblicher Zeitraum:** Für die Abrechnung zählt die Vertragslaufzeit, siehe 6.1.
- **Schlüssel je Kostenart:**
  - Wohnfläche (m² × Tage)
  - Wohneinheiten (Wohnung × Tage)
  - Personentage (Personenzahl laut Konditionen × Tage)
  - Verbrauch (abgelesener Wert, Schätzung nur gekennzeichnet und begründet)
  - Direkt zugeordnet (nur eine Wohnung)
  - Abrechnung der Ablesefirma (Beträge je Nutzer)
  - Individuell (vereinbarte Anteile)
- **Leerstand:** Leerstand ist ein eigener Nutzungsabschnitt. Sein Anteil wird nicht auf die Mieter verteilt, sondern als Vermieteranteil ausgewiesen (BGH, Urteil vom 31.5.2006, VIII ZR 159/05).
- **Heiz- und Warmwasserkosten:** Sie werden nicht nach Tagen verteilt, sondern je Nutzer aus der Abrechnung der Ablesefirma übernommen. Dort liegt die Verteilung nach §§ 7–9b HeizkostenV, einschließlich Zwischenablesung bei Mieterwechsel.
  - Weicht die Summe der Einzelbeträge um mehr als 1 € von den erfassten Kosten ab, ist das ein kritischer Fehler.
  - Eine kleinere Differenz wird verhältnismäßig ausgeglichen und als Warnung gemeldet.
- **Geld:** Beträge werden mit exakten Brüchen gerechnet und erst je Kostenart auf Cent gerundet, nach der Methode des größten Rests. Die Summe der Anteile entspricht damit immer genau den umlagefähigen Gesamtkosten, was ein Test absichert.
- **Vorauszahlungen:**
  - Das Soll (vereinbart) wird taggenau je Monat aus den Konditionen berechnet.
  - Abgezogen wird nur das bestätigte Ist.
  - Der Vorschlag aus dem Mietkonto (zugeordnete Mieteingänge im Verhältnis zur Gesamtmiete) wird nie automatisch übernommen. Ohne bestätigtes Ist gilt die Prüfung als kritisch.

## 4. Prüfung vor der Freigabe

**Kritisch (Freigabe gesperrt):**
- Zeitraum länger als zwölf Monate
- keine Kosten oder keine Mieter
- Wohnfläche, Personenzahl, Verbrauchswert oder Betrag der Ablesefirma fehlt
- Summe laut Ablesefirma weicht um mehr als 1 € ab
- Position aus einer nicht umlagefähigen Ausgabenkategorie (Verwaltung, Instandhaltung, Zinsen …) als Betriebskosten eingeordnet
- Kabel-TV-Entgelt nach dem 30.6.2024
- Vorauszahlungen nicht bestätigt
- Verteilungssumme stimmt nicht

**Warnung (nach Prüfung einzeln zu bestätigen):**
- Abrechnungsfrist abgelaufen
- mögliche Doppelerfassung (gleicher Beleg bzw. gleiche Kostenart, gleicher Betrag, gleiches Datum)
- geschätzter Verbrauch
- unplausibler Verbrauch (mehr als das Dreifache des Durchschnitts oder 0)
- „Sonstige Betriebskosten“ (nur bei Einzelvereinbarung)
- CO2-Angaben fehlen
- Kostenart aus dem Vorjahr fehlt oder ist um mehr als 50 % gestiegen
- Summe der Wohnungsflächen weicht von der Objektfläche ab

**Hinweis:**
- Leerstand, Nutzungszeit kürzer als der Zeitraum
- keine Umlagevereinbarung hinterlegt
- Ist ≠ Soll
- Zwischenablesung
- Fernablesbarkeit ab 2027
- Frist endet bald
- Rechnungsdatum außerhalb des Zeitraums
- Gutschrift, übliche Kostenarten fehlen

Jede Änderung nach einer Freigabe setzt den Zeitraum auf „Entwurf“ zurück und hebt bestätigte Warnungen auf. Eine erneute Freigabe erzeugt eine neue Fassung, und jede Fassung lässt sich aus ihrem Snapshot unverändert als PDF reproduzieren.

## 5. PDF (formelle Anforderungen)

Nach BGH, Urteil vom 29.1.2020, VIII ZR 244/18 (und frühere Rechtsprechung) gehören in die Abrechnung: Gesamtkosten je Kostenart, Umlageschlüssel (erläutert, wenn zum Verständnis nötig), Berechnung des Mieteranteils und Abzug der geleisteten Vorauszahlungen.

Das PDF enthält je Mietverhältnis:
- Vermieter, Mieter (alle Vertragspartner), Mietobjekt und Wohnung
- Abrechnungszeitraum und individuellen Nutzungszeitraum
- Tabelle mit Gesamtkosten, Schlüssel, Gesamteinheiten, Ihren Einheiten und Ihrem Betrag
- Summen für Betriebs- und Heizkosten, die geleisteten Vorauszahlungen und das Ergebnis (Nachzahlung oder Guthaben)
- Zahlungshinweis
- Erläuterungen zu Schlüsseln, Leerstand, Heizkosten/CO2, Zeitanteil, Rundung, Belegeinsicht und Einwendungsfrist (§ 556 Abs. 3 S. 5 BGB)
- Anlage mit allen Einzelpositionen, optional Logo, Erstellungsdatum und Fassungsnummer

Das Entwurfs-PDF trägt ein Wasserzeichen. Bei der Freigabe wird je Mieter ein PDF im Archiv abgelegt. Zur Belegeinsicht gibt es ein ZIP mit allen Belegen und eine Belegliste.

## 6. Verbleibende rechtliche Unsicherheiten (ausdrücklich)

1. **Vertragslaufzeit oder tatsächliche Nutzung:** Die App rechnet nach Mietbeginn und Mietende laut Vertrag. Bei Abweichungen (früherer Auszug, Einzug vor Vertragsbeginn, Aufhebungsvereinbarung) ist im Einzelfall zu prüfen, wer welche Kosten trägt.
2. **Personenschlüssel bei Leerstand:** Leere Wohnungen werden standardmäßig mit 1 Person angesetzt (einstellbar). Abschließend geklärt ist das nicht. Im Zweifel sollte der Wert so gewählt werden, dass er nicht zu Lasten der Mieter geht.
3. **Abfluss- oder Leistungsprinzip:** Übernommen werden Ausgaben, die im Zeitraum bezahlt wurden (Abflussprinzip). Für kalte Betriebskosten hält der BGH das grundsätzlich für zulässig. Für Heiz- und Warmwasserkosten verlangt die HeizkostenV die Abrechnung des Verbrauchs im Zeitraum. Deshalb kommen diese aus der Abrechnung der Ablesefirma. Periodenfremde Rechnungen werden nur als Hinweis gemeldet.
4. **Umlagevereinbarung:** Die App prüft nicht, ob eine Kostenart im Mietvertrag wirksam vereinbart ist. Sie erinnert nur daran. Für „Sonstige Betriebskosten“ (§ 2 Nr. 17 BetrKV) ist eine ausdrückliche Einzelvereinbarung nötig.
5. **§ 2 Nr. 15 BetrKV:** Kabel-TV-Entgelte sind seit dem 1.7.2024 nicht mehr umlagefähig. Erkannt wird das nur über Stichwörter in der Beschreibung. Für das Glasfaserbereitstellungsentgelt (§ 72 TKG: höchstens 60 €/Jahr, 540 € insgesamt, freie Anbieterwahl) prüft die App weder Höchstbeträge noch Voraussetzungen.
6. **Wirtschaftlichkeitsgebot:** Einen Kostenanstieg von mehr als 50 % meldet die App als Warnung. Ob die Kosten wirtschaftlich sind, beurteilt sie nicht.
7. **Heizkosten:**
   - Die richtige Verteilung nach HeizkostenV (Verteilungsverhältnis 50–70 % nach Verbrauch, Gradtagszahlen, Nutzergruppen, Ersatzverfahren nach § 9a) liegt bei der Ablesefirma und wird von der App nicht nachgerechnet.
   - Kürzungsrechte des Mieters: 15 % nach § 12 Abs. 1 HeizkostenV, 3 % ab 1.1.2027 bei fehlender Fernablesbarkeit und 3 % nach § 7 Abs. 4 CO2KostAufG bei fehlenden CO2-Angaben.
   - Die CO2-Angaben (Stufe, Vermieteranteil, Mieteranteil) werden aus der Abrechnung der Ablesefirma eingetragen und nicht selbst berechnet.
8. **Gewerbeeinheiten (Vorwegabzug)** werden nicht gesondert behandelt. Bei gemischt genutzten Häusern ist ein Vorwegabzug zu prüfen.
9. **Fristen:** Die Abrechnungsfrist (§ 556 Abs. 3 S. 2 BGB) knüpft an den Zugang beim Mieter an, nicht an das Erstellungsdatum. Die App kennt das Zugangsdatum nicht.
10. **Belegeinsicht:** Mieter haben grundsätzlich Anspruch auf Einsicht in die Belege, nach BGH auch in Verträge mit Dienstleistern (z. B. VIII ZR 114/21). Ob digitale Kopien statt der Originale genügen, hängt vom Einzelfall ab. Papieroriginale bitte aufbewahren.
11. **Aufbewahrung und Löschung (DSGVO):** Eine automatische Löschfrist für Mieterdaten ist nicht umgesetzt.
    - Daten beendeter Mietverhältnisse werden für Abrechnung, Verjährung und steuerliche Aufbewahrung gebraucht (Belege der Vermietung in der Regel zehn Jahre).
    - Danach sind sie zu löschen. Das Löschen eines Mietverhältnisses entfernt die personenbezogenen Daten. Freigegebene Abrechnungen bleiben als Fassung und PDF erhalten.

## 7. Quellen (geprüft am 30.10.2026)

- deutschesmietrecht.de – § 2 Nr. 15 BetrKV (Antenne, Breitband, Glasfaser): https://deutschesmietrecht.de/betriebskosten/betriebskostenarten/785-nr-15-betrkv-gemeinschaftsantenne-breitbandnetz-glasfaserbereitstellungsentgelt.html
- vhw-Kommentierung zu BGH VIII ZR 244/18 (formelle Anforderungen): https://www.vhw.de/fileadmin/user_upload/10_dokumente/kommentierungen/Kommentierungen2020_Beyer/235._Kommentierung_244-18_Form._Anfordergen_an_Bk.-Abrechng.pdf
- Berliner Mieterverein zu BGH VIII ZR 159/05 (Leerstand): https://www.berliner-mieterverein.de/recht/bgh/betriebskosten-bei-leerstand-bgh0632.htm
- § 9b HeizkostenV (Nutzerwechsel): https://gesetze.legal/bund/heizkostenv/9b
- § 7 CO2KostAufG: https://www.gesetze-im-internet.de/co2kostaufg/__7.html
- Berliner Mieterverein, Kürzungsrechte bei der Heizkostenabrechnung: https://www.berliner-mieterverein.de/recht/infoblaetter/info-186-die-kuerzungsrechte-bei-der-heizkostenabrechnung.htm
- vhw-Kommentierung zu BGH VIII ZR 114/21 (Belegeinsicht): https://www.vhw.de/fileadmin/user_upload/10_dokumente/kommentierungen/Kommentierungen2021_Beyer/282._Kommentierung_114-21__Betriebsk.Abrechng__Belegeinsicht_-_Grundsaetze.pdf

## 8. Bedienung (Kurzfassung)

1. **Objekte:** Wohnungen mit Wohnfläche anlegen.
2. **Mieter:** Mietverhältnisse anlegen (Vertragspartner, Laufzeit, Konditionen ab Datum). Beim Mieterwechsel „Beenden / Mieterwechsel“ wählen und danach den Nachmieter anlegen.
3. **Betriebskosten:** Objekt und Jahr wählen, dann der Reihe nach:
   - Kosten (aus Ausgaben übernehmen oder manuell erfassen)
   - Schlüssel
   - Werte (Verbrauch, Beträge der Ablesefirma, CO2)
   - Nutzungszeiten
   - Vorauszahlungen (Ist bestätigen)
   - Ergebnis
   - Prüfung (Warnungen bestätigen)
   - Freigabe (PDF-Vorschau, freigeben, im Archiv ablegen)
4. Die Vermieterangaben (Name, Anschrift, IBAN, Logo) stehen unten auf der Betriebskosten-Seite.
