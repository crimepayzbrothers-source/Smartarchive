# SmartArchive – Ergänzung zur Projektübergabe vom 31.10.2026

> Gehört zu `docs/PROJEKT-UEBERGABE.md` (Stand `c614f78`/`cbfcec1`). Enthält nur, was dort fehlt, ungenau ist oder ihr widerspricht. Abschnittsverweise wie „Ü 3.4“ beziehen sich auf die Übergabe.
>
> **Kennzeichnung:** **[Code]** im Quellcode belegt · **[Chat]** im noch vorhandenen Verlauf belegt · **[ZF]** nur aus der Zusammenfassung des älteren Verlaufs · **[Alt-Ü]** aus der Übergabe bzw. Ergänzung vom 29.10. (im Git: `git show c614f78:docs/PROJEKT-UEBERGABE.md` und `git show c614f78:docs/PROJEKT-UEBERGABE-ERGAENZUNG.md`) · **[Annahme]** nicht belegt · **[offen]** nicht überprüfbar

---

## A. Korrekturen und Widersprüche zur Übergabe

| Stelle | Korrektur |
| --- | --- |
| Ü 3.14 und 7 B „Wiederherstellungstest: erster erfolgreicher Serverlauf ❓“ | **Falsch eingestuft.** Der Nutzer hat am Anfang dieses Verlaufs einen Screenshot geschickt: „Backup OK, Wiederherstellungstest OK“ **[ZF]**. Der genaue Zeitpunkt (30.10., vermutlich morgens) und ob es der monatliche Teiltest oder ein manueller Lauf war, ist **[offen]**. |
| Ü 7 D.1 „Serverstand unbekannt“ | Die Reihenfolge im Verlauf grenzt das ein: „Das app update wurde eingestellt“ folgte auf die Suchverbesserung (`589678c`) **[ZF]**. „Der vorherige Befehl hat nicht funktioniert“ kam direkt nach dem Auftrag „Übernahme aus Mietvertrag/Screenshot“, also nach dem Update-Befehl für `2ff5c73` **[ZF]**. Der Server steht daher mindestens auf `589678c`, wahrscheinlich nicht über `2ff5c73` hinaus **[Annahme]**. Belegt ist das erst mit `git log --oneline -1` auf dem Server. |
| Alt-Ü 4.2 „Untere Navigation mit Start, Eingang, Archiv, Objekte, Ausgaben“ | Tatsächlich: **Eingang, Archiv, Kamera-FAB, Objekte, Ausgaben**. Zur Startseite führt das Logo „SmartArchive“ oben links (`<a className="brand" href="#/">`) **[Code, Chat-Screenshots]**. Die Übergabe vom 31.10. ist hier richtig, die alte war ungenau. |
| Ü 3.4 „Verlauf je Zahlungspartner (Vorschläge schon ab der ersten gleichen Zuordnung …)“ | Genau **[Code `banking._history`]**: Automatisch zugeordnet wird nur, wenn ein Zahlungspartner **mindestens 2-mal** gleich zugeordnet wurde **und** das ≥ 80 % seiner erledigten Umsätze sind (Sicherheit 0,92; ab 3-mal 0,95). Sonst gibt es nur einen **Vorschlag mit 0,7**, ab dem ersten früheren Umsatz („Wie beim letzten Umsatz …“). Ausgaben und Einnahmen werden getrennt gelernt. Teilbeträge (z. B. Zinsanteil) werden **nie automatisch** übernommen. |
| Ü Kopf „ersetzt die Ergänzung vom 29.10.“ | Die alte Ergänzung wurde mit `cbfcec1` aus dem Repository entfernt. Ihr Inhalt ist nur teilweise in die Übergabe eingeflossen; der Rest steht in dieser Datei (Abschnitte B und E). |

---

## B. Weiterhin gültige Details aus den alten Übergabedokumenten (bei der Zusammenführung weggefallen)

### B.1 Oberfläche **[Code]**

- **Eingang, Gruppen in dieser Reihenfolge:** Nacharbeit (Fehler), Wird verarbeitet, Bitte ablegen, Mögliche Duplikate, Datum prüfen, Umsätze klären, Umsätze automatisch zugeordnet, Automatisch abgelegt.
- **Start:** „Zuletzt hinzugefügt“ und „Ausgaben Vermietung {Jahr}“.
- **Einstellungen:**
  - Automatische Ablage und KI
  - Ordner (KI-Chip je Ordner), Regeln (auch „Aus Ihren Korrekturen“)
  - Konto und Sicherheit (2FA, Passwort)
  - Darstellung (System/Hell/Dunkel, in `localStorage`)
  - Daten (Gesamtexport, Papierkorb)
- **Ausgaben-Seite:**
  - Jahr und Objekt wählen, Kennzahlen (erfasst, im Export, automatisch ausgelesen, noch offen)
  - „Zu prüfen“, „Ohne Datum“, Export-Panel, Summen je Objekt als Kategorie-Balken (`.cat-bars`)
  - „Ohne Zahlungsdatum“, Mietkonto-Panel (`BankPanel`, mit Anleitung zum Commerzbank-Export), „Rechnungen ohne Ausgabe“
- **Verschachtelte Dialoge:** `stopPropagation` ist nötig, damit das Schließen eines inneren Dialogs nicht das UploadSheet schließt (behoben). Bei den Mieter-Dialogen gilt dasselbe Muster mit `setMode(cur => cur === "x" ? "" : cur)`, siehe D.2.

### B.2 Fachliche Stammdaten **[Code `seed.py`]**

- **Ausgabenkategorien:** `schuldzinsen`, `geldbeschaffung`, `erhaltung_sofort`, `erhaltung_verteilt` (§ 82b EStDV), `grundsteuer`, `grundbesitzabgaben`, `heizung`, `versicherung`, `hausnebenkosten`, `verwaltung`, `hausgeld_laufend`, `fahrtkosten`, `sonstige`, `nicht_sofort` (`deductible=False`, im Export getrennt).
- **Standard-Ordner Privat:** Finanzen und Banken (Kontoauszüge, Depot), Versicherungen, Verträge (Strom und Gas, Telefon und Internet), Steuern, Fahrzeuge, Gesundheit (KI aus), Familie (KI aus), Arbeit, Sonstiges.
- **Standard-Ordner Vermietung:** Rechnungen und Belege, Versicherungen, Grundbesitzabgaben, Hausgeld und WEG, Nebenkosten, Finanzierung, **Mietverträge und Mieter** (Ablageort aller erzeugten Mieterschreiben), Kauf und Objektunterlagen, Sonstiges.
- **Betriebskosten-Zuordnung beim Übernehmen aus Ausgaben:** `schuldzinsen`, `geldbeschaffung`, `erhaltung_*`, `verwaltung`, `fahrtkosten`, `nicht_sofort`, `hausgeld_laufend` gelten als nicht umlagefähig (`NOT_ALLOCABLE_CATEGORIES`).

### B.3 Erkennungsregeln **[Code]**

- **Datum** (`extract.date_analysis`):
  - Formate: TT.MM.JJJJ (auch `/ - ,`, zweistellige Jahre), ISO, „12. März 2026“; OCR-Korrektur `O→0`, `I/l/|→1`.
  - Rangfolge: „Rechnungsdatum“ > „Datum/vom“ > „Leistungsdatum“ (auch in der Vorzeile), sonst das erste Datum im Kopf.
  - Gemieden werden Daten nach „bis, fällig, zahlbar, gültig, geb., ab, zum, spätestens, Frist“.
  - Sicher nur bei einem einzigen Datum oder einer eindeutigen Beschriftung ohne gleichrangige Konkurrenz.
  - `date_from_name` und `largest_amount` existieren noch im Code, werden aber **bewusst nicht verwendet**.
- **Dubletten** (`duplicates.find`, mindestens 30 Zeichen Text): 1) gleiche Rechnungsnummer (≥ 4 Zeichen) und gleicher Betrag oder ≥ 35 % Ähnlichkeit; 2) gleicher Endbetrag und gleiches Datum und ≥ 30 %; 3) gleiches Datum und ≥ 80 %. Ähnlichkeit: Jaccard über Wort-3-Gramme (je höchstens 6.000 Zeichen).
- **Rechnungsarten** für die Ausgaben-Automatik: `Rechnung`, `Kassenbon`, `Quittung`; `Bescheid` nur bei Grundsteuer oder Grundbesitzabgaben. Barzahlungsmuster (`_POS_PAYMENT`, `_RECEIPT_PAYMENT`) machen das Zahlungsdatum sicher.
- **Prüfhinweise Ausgaben-Export:** Mögliche Doppelerfassung bei gleichem Betrag und gleichem Empfänger (Groß-/Kleinschreibung egal), höchstens 10 Tage auseinander, verschiedene Dokumente. Hinweis „anschaffungsnah“ nur bei gesetztem Kaufdatum am Objekt; die 15-%-Grenze wird nicht berechnet.
- **Exporte:**
  - Spalten Lfd. Nr., Beleg-Nr., Datum, Monat, Jahr, Zahlungsdatum, Rechnungsdatum, Jahr nach, Objekt …
  - Excel: Übersicht Objekt × Monat, Blatt je Objekt mit Monats-Zwischensummen, Blatt „Offen“
  - PDF-Belegstempel im zusätzlichen Kopfrand (`STAMP_BAND=22`)

### B.4 Server und Laufzeit **[Code]**

- **Statische Dateien:** `assets/*` mit `max-age=31536000, immutable`, sonst `no-cache`. Ohne Build antwortet der Server mit 503 „Frontend nicht gebaut“.
- **Share-Target ohne Service Worker:** `/share-target` (POST) leitet mit 303 auf `/#/eingang`.
- **Seitenvorschauen:** `GET /api/documents/{id}/pages/{n}?dpi=50–200` (Standard 110), Cache je SHA-256 unter `data/cache`.
- **Worker:** `MAX_ATTEMPTS = 3`, hängende Jobs werden zurückgesetzt (`recover_stale`). Startläufe in `main.py` sind in `try/except` gekapselt und blockieren den Start nie.
- **Scanner:**
  - `scan.detectQuad`: Graustufen bei etwa 360 px, Box-Blur, Otsu-Schwelle, größte helle Fläche; sonst eingerückter Standardrahmen.
  - `warp` arbeitet mit höchstens 2.400 px Kantenlänge.
- Zusätzlicher Einmal-Marker **`bank_suggest_v2`** (`main._bank_resuggest`): Offene Abbuchungen bekommen einmalig Zinsanteil und Standardkategorie „Sonstige Werbungskosten“; KI-Vorschläge bleiben unberührt.

### B.5 Backup und Server-Sonderfälle **[Alt-Ü, Chat vom 29.10.]**

- **Gesichert:** `data/files`, `data/backup` (konsistente DB-Kopie durch `app.backup`, vorher Integritätsprüfung). **Ausgeschlossen:** `data/cache`. **Nicht gesichert:** Live-DB, `.env`, `/root/.restic-*`.
- **Aufbewahrung:** 14 Tage / 8 Wochen / 24 Monate. Der Wiederherstellungstest liest den Teil `Monat/12`. `backup.sh` meldet `/start`, Erfolg und `/fail` (mit den letzten 10 KB Log) an `$HEALTHCHECK_URL`.
- **Kennungen:** erster Snapshot `2f2b060e` (28.10. 22:07), erste Nachtsicherung `e9b07c8a` (29.10. 03:30), Repo-ID `b5a5a564`, Format v2 mit Kompression (3,057 GiB bei 3,332 GiB roh).
- **Backblaze B2, Gratisgrenzen:** 1 GB Download und 2.500 Class-B-Transaktionen pro Tag; Reset um 0:00 UTC (1:00 MEZ). Bei Überschreitung „403 … cap exceeded“.
- **Alte keyID:** Die keyID aus der ersten Zusammenfassung ist veraltet. Der aktuelle Schlüssel steht nur in `/root/.restic-env`. Ob alte Schlüssel gelöscht sind, ist **[offen]**.
- **Server:**
  - `/tmp` liegt im RAM (tmpfs): große Daten nach `/var/tmp`.
  - Zeitzone CET, cron aktiv.
  - Auf dem Host **kein `sqlite3`**: für Prüfungen `python3` nutzen. Die restic-Version ist **[offen]** (≥ 0.14 wegen Repo-Format v2).
  - netcup-Vertrag mit **6 Monaten Mindestlaufzeit** (bestellt Oktober 2026). Bei Vertragsende Daten über Gesamt-ZIP oder B2 mitnehmen.
- **healthchecks.io:**
  - Die Ping-URL war am 29.10. auf einem Screenshot sichtbar. Neuerzeugung wurde empfohlen, ob umgesetzt, ist **[offen]**.
  - Ob der Check auf „Cron 30 3 * * *, Europe/Berlin, Grace 2 h“ oder auf „Simple, 1 Tag“ steht, ist **[offen]**.

---

## C. Seit 29.10. ergänzte Details, die in der Übergabe fehlen

### C.1 Mietkonto **[Code, ZF]**

- **Lücken- und Nachbuchungserkennung beim Import** (`import_csv`): Beginnt die neue Datei nach dem bisher erfassten Ende, meldet die App „Zwischen … enthält keine Datei Umsätze …“ (`gap`). Neue Umsätze in schon eingelesenen Zeiträumen werden gezählt (`new_in_known`).
- **Zinsanteil:** Erkannt werden beschriftete Beträge zu „Zins/Zinsen/Zinsanteil/Sollzins/Darlehenszins …“ bzw. „Tilgung/Tilgungsanteil“ im Verwendungszweck (`_INTEREST`, `_REDEMPTION`). Prozentangaben werden ausgeschlossen.
- **Hinweistexte** (reine Hinweise): Darlehen „nur der Zinsanteil ist Werbungskosten“, Hausgeld „Zuführung zur Erhaltungsrücklage erst bei Verwendung abziehbar“ **[Alt-Ü]**.
- Mehrere erfasste Rechnungen gleichen Betrags führen zur Rückfrage. Eindeutig ist ein Treffer erst bei ≥ 0,1 Abstand zum zweitbesten. Gibt es genau ein Objekt, wird es vorgeschlagen **[Alt-Ü]**.
- **Mieter-Zuordnung:** gilt nur für Eingänge. Eine Einnahme-Entscheidung setzt `tenancy_id`, Rückgängig entfernt sie. Wird nur Objekt und Wohnung gewählt, findet die App das Mietverhältnis selbst über die Wohnung und das Datum (`income.tenancy_for`).
- `income.match_by_name` berücksichtigt Mietverhältnisse bis **40 Tage nach Ende** (Nachzahlungen).

### C.2 Einnahmen **[Code]**

- **Barzahlung von Hand:** Datum, Betrag, „Für Monat“ (`for_month`, optional, sonst nach Zahlungsdatum), Notiz. Löschen mit Rückfrage.
- **Panel „Eingänge ohne Mietverhältnis“:** Umsätze, die als Einnahme, aber ohne Mietverhältnis zugeordnet sind, mit Link „In den Kontoumsätzen korrigieren“.
- **Hinweis** auf noch nicht zugeordnete Zahlungseingänge (`open_incoming`) mit Link zum Eingang.
- Die Übersicht ruft bei jedem Laden `link_unassigned()` auf: Bereits gebuchte Einnahmen werden nachträglich angelegten Mietverhältnissen zugeordnet.
- Der Schalter „automatisch als eingegangen“ wirkt sofort in der Oberfläche (optimistisch), das Laden folgt danach.

### C.3 Mieter **[Code, ZF]**

- **Dateigrenzen beim Anlegen/Auslesen:** höchstens 10 Dateien und 25 MB zusammen (`FILE_MAX`, `FILE_MAX_MB`; Server `MAX_FILES`/`MAX_BYTES`). Ausgelesen werden höchstens 8 Seiten je Datei (`MAX_PAGES`, OCR mit 250 dpi).
- **„Angaben aus Unterlagen auslesen“** füllt nur leere Felder. Danach zeigt die App, was übernommen wurde; dieser Anzeigefehler wurde behoben, siehe D.2.
- Die Mieterliste startet mit dem Filter „Aktiv“.
- Mehrere Mietverhältnisse je Wohnung gibt es nur **nacheinander**. Überschneidungen werden abgelehnt (mehrere Vertragspartner in einem Mietverhältnis sind dagegen möglich).
- **KI-Auslese** (`mistral.extract_tenancies`) erkennt auch Mieterlisten mit mehreren Mietverhältnissen.
- **objego:** Es gibt keine Exportfunktion. Der Weg ist die Übernahme aus Screenshot oder Vertrag **[Chat/ZF]**.

### C.4 Schreiben (Mahnung, Abmahnung, Kaution) **[Code]**

- Gemeinsame Vorgaben: Anrede „Guten Tag {Vorname Nachname} und …,“ (`dunning.default_salutation`), Frist **+14 Tage**, Briefdatum heute. Fehlende Vermieterangaben (Name, Anschrift, bei Mahnungen IBAN) werden im Formular mit Link zu „Betriebskosten › Vermieterangaben“ gemeldet.
- **Anschrift im Schreiben:** die beim Vertragspartner hinterlegte Anschrift, sonst „Wohnung {Einheit}“ und die Objektanschrift (`bk_pdf.letterhead`). Achtung: Steht beim Vertragspartner eine **frühere Anschrift** aus dem Vertrag, geht das Schreiben dorthin **[Code; Risiko]**.
- **Vorschau** (`openPdf`): Ein Fenster öffnet sich vor dem Laden. Je nach Gerät öffnet sich das PDF im Viewer oder wird heruntergeladen **[Annahme für Android, Download im Headless-Test belegt]**.
- **Abmahnung:**
  - Ein Wechsel des Anlasses setzt Aufforderung und Folgenhinweis auf die Bausteine zurück.
  - Leere Vorfallzeilen werden ignoriert, die Uhrzeit ist Freitext. Zustellung vorbelegt mit Einwurf-Einschreiben.
  - Verspätungsvorschläge nur aus echten Zahlungen (Bank oder bar), deren Zahlungsmonat dem Buchungsmonat entspricht.
- **Kaution:**
  - Der Kautionsbereich steht auf der Mieterseite vor „Mahnungen“. Die Kaution-Pill in der Liste fehlt bei beendeten Mietverhältnissen.
  - Kautionsschreiben nennen das Kautionskonto, sonst die Vermieter-IBAN. Verwendungszweck „Kaution {Einheit}“.

### C.5 Mieterliste **[Code, Chat]**

Die Monatssumme bezieht sich auf die **angezeigten** laufenden Mietverhältnisse, also auch auf eine eingegebene Suche. Auf Wunsch des Nutzers steht sie **unterhalb** der Liste (zuerst war sie oben).

### C.6 Belegprüfung **[Code]**

- **Ausgeschlossen:** Dokumente mit `tenancy_id` (Mieterschreiben, Verträge) und Dokumenttyp „Betriebskostenabrechnung“.
- **Vorauswahl:** Positionen aus der automatischen Belegliste sind vorausgewählt, wenn ein Betrag vorliegt. Treffer, die nur aus der Suche kommen, sind nicht vorausgewählt.
- **Merkschlüssel** je Position `{document_id}:{Fundstellentext}`, gespeichert unter `sa-bk-sel-{periodId}`. Ob eine Position schon übernommen ist, erkennt die App am **identischen Fundstellentext**.
- Nach dem Speichern bleibt die Belegprüfung offen und lädt Liste und Treffer neu.

### C.7 Mehrfachauswahl **[Code]**

Langes Drücken löst eine kurze Vibration aus (`navigator.vibrate`). Das Kontextmenü des Browsers wird im Auswahlmodus unterdrückt. Dateinamen beim Teilen kommen aus dem `Content-Disposition`-Header. Die ZIP-Antwort hat `Cache-Control: no-store`.

### C.8 Beantwortete Fragen ohne Umsetzung **[Chat]**

- **Wettbewerbsanalyse (30.10.):**
  - objego: Starter kostenlos (eingeschränkt), Basic 4,95 €, Premium 9,95 €/Monat bei 12 Monaten; Vorlagen u. a. Zahlungserinnerung und Abmahnung wegen Zahlungsverzug.
  - immocloud: ab 9,99 €/Monat netto; Textvorlagen mit Platzhaltern, digitale Mietverträge gegen Gebühr.
  - Haus & Grund: Mietvertragsportal seit 1.1.2026.
  - Quellen: heise-Tests zu objego und immocloud, Haus & Grund Kiel. Relevant, falls die Frage nach kommerziellem Potenzial wieder aufkommt.
- **Teilen und Dateiauswahl (31.10.):**
  - Teilen **an** die App braucht die Installation über Chrome → „App installieren“; nach älterer Installation ggf. neu installieren.
  - In der Dateiauswahl anderer Apps (wie Dropbox) erscheinen kann nur eine native Android-App (Dateianbieter); für eine PWA ist das nicht möglich.

---

## D. Technische Sonderfälle und Fallstricke der Entwicklung

### D.1 Arbeitsumgebung (Claude) **[Chat]**

- Prozesse **nur mit `fuser -k PORT/tcp` beenden**. `pkill -f`/`pgrep`-Muster haben die eigene Shell getroffen (Exit 144).
- **Lokaler Server:**
  - `DATA_DIR=… SETUP_TOKEN=… COOKIE_SECURE=0 uvicorn app.main:app --port …`; ohne `COOKIE_SECURE=0` hält das Login-Cookie über http nicht (401).
  - Uploads werden nur verarbeitet, wenn der Worker separat läuft (`python -m app.worker`).
- **Playwright:** `require('/home/claude/.npm-global/lib/node_modules/playwright')`, Gerät „Pixel 7“, Cookies aus dem Setup-Login. Elemente unter der unteren Navigation (Höhe 64 px) vor Zeigeraktionen in die Mitte scrollen. `navigator.share`/`canShare` lassen sich per `addInitScript` nachbilden.
- **Tests mit festem Datum:** `monkeypatch.setattr(income, "date", FakeDate)` bzw. auch `dunning.date`. Die Tests nutzen die Helfer `_setup` aus `test_income.py` und `test_betriebskosten.py` sowie `make_pdf`/`run_worker` aus `conftest.py`.
- Das Scratchpad der alten Sitzung ist gelöscht. Testskripte liegen nicht im Repository.

### D.2 Code-Fallstricke (aus behobenen Fehlern) **[ZF, Chat]**

- **`<dialog>`-close-Ereignis:** Das Schließen eines Dialogs setzte den Modus des nächsten zurück. Lösung: `setMode(cur => cur === "x" ? "" : cur)`.
- **Veraltete Werte in State-Updatern:** Ableitungen (z. B. Einzug = Mietbeginn) vor `setF` aus dem aktuellen Wert berechnen, nicht im Updater über Refs. Rückmeldungen („Übernommen: …“) ebenfalls aus dem aktuellen Zustand berechnen.
- **Seitenbreite:** Die Registerleiste mit 4 Reitern machte die Seite breiter als den Bildschirm (447 statt 412 px) und verschob die Toasts. Deshalb ist sie scrollbar.
- **CSS-Spezifität:** `.plain-list li > :first-child { flex: 1 }` überschreibt einfachere Regeln, also gezielter selektieren. **Klassennamen eindeutig wählen:** `.evidence` gab es bereits zweimal, deshalb `.bk-evidence`.
- **Datum + timedelta:** `(e or date.max) + timedelta(...)` läuft über (OverflowError). Lösung: `e + timedelta(...) if e else date.max`.
- **PDF-Dateinamen mit Umlauten/ß:** `Content-Disposition` mit `filename*=UTF-8''…` (RFC 5987).
- **Begründung des Umlageschlüssels:** Der Erklärtext darf beim Wechsel des Schlüssels nicht mitwandern. `key_for` liefert eine leere eigene Begründung, die API entfernt Standardtexte.

### D.3 Unerklärte lokale Änderungen **[ZF]**

Bei der Arbeit am 30.10. fanden sich nicht selbst erstellte Änderungen (FileCollector-ähnlicher Code) in `Tenants.tsx` und `styles.css`. Sie wurden übernommen und committet, der Nutzer wurde informiert. Die Herkunft ist **[offen]**. Vor Arbeiten im neuen Chat `git status` prüfen.

---

## E. Fehlende Arbeits- und Kommunikationsregeln

1. **Befehlsblöcke:**
   - Eingaben mit `read -rp` bzw. für Geheimnisse `read -rsp` abfragen, zur Kontrolle nur die Länge ausgeben.
   - In fremden Oberflächen (Backblaze, healthchecks.io) genau sagen, was wo einzutragen ist.
   - **[Alt-Ü/Chat]**
2. **Fragen des Nutzers:**
   - Fragt der Nutzer „Besteht die Möglichkeit …?“ oder „Bietet die App bereits …?“, zuerst ehrlich und konkret antworten (was es gibt, was nicht und warum), dann die Umsetzung anbieten. Erst nach Zustimmung bauen (zuletzt „Ja bitte ergänzen“).
   - Arbeitsaufträge („Bitte implementieren …“) dagegen ohne Rückfrage umsetzen.
   - **[Chat-Muster; Annahme als Regel]**
3. **Rechtliche Aussagen:**
   - Paragrafen und Urteile nennen und im Zweifel per Websuche prüfen. Bei Recherchen Quellen am Ende auflisten.
   - In der App nur rechnerische Hinweise mit dem Satz „Ob … möglich ist, prüft die App nicht“.
   - **[Chat]**
4. **Rückmeldung nach jeder Änderung** (feste Struktur **[Chat]**):
   - Ergebnis in einem Satz, Testanzahl und ob es im Browser geprüft wurde
   - Bedienung kurz in Stichpunkten
   - bewusste Grenzen
   - Update-Befehl mit erwarteter Commit-Zeile und Bitte um Screenshot
5. **Eigene Fehler** im nächsten Bericht klar als „mein Fehler bei der Umsetzung“ benennen, mit Ursache **[Chat]**.
6. **Arbeitszeiten:** Aufträge kommen oft spät abends oder nachts. Den Nutzer nicht mit Rückfragen aufhalten, wenn der Auftrag klar ist **[Chat; Annahme]**.

---

## F. Zusätzliche offene Punkte (über Ü 11 hinaus)

| Punkt | Status |
| --- | --- |
| Was war auf dem Screenshot „Wiederherstellungstest OK“ genau zu sehen (Datum, Teiltest oder voller Test)? | [offen] |
| Steht bei Vertragspartnern eine alte Anschrift aus dem Vertrag, sodass Schreiben an die falsche Adresse gingen? | [offen], in der App prüfen |
| Woher stammen die unerklärten Änderungen vom 30.10. (D.3)? | [offen] |
| Sind alte B2-Schlüssel gelöscht, ist die healthchecks-Ping-URL neu erzeugt? | [offen] |
| Funktioniert die PDF-Vorschau der Schreiben auf dem Handy (eigenes Fenster bzw. Download)? | [offen], auf dem Gerät testen |
| Teilen mehrerer Dateien an GMX/WhatsApp auf dem echten Gerät | [offen], nur mit nachgebildetem `navigator.share` getestet |
| Die genaue Antwort zur Frage nach dem kommerziellen Potenzial | nicht dokumentiert, kein Beschluss |
