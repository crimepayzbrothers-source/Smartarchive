# SmartArchive – Projektübergabe (Stand 31.10.2026, Commit `c614f78`)

> **Grundlage:** der Quellcode im Repository (Branch `main`, Commit `c614f78`; 141 Tests grün am 31.10.2026), die frühere Übergabe vom 29.10.2026 samt Ergänzung (in diese Fassung eingearbeitet, beide ersetzt) und der Chatverlauf vom 29.–31.10.2026.
>
> **Kennzeichnung:** ✅ implementiert (Code vorhanden, getestet, gepusht) · 🟡 teilweise · 📋 beschlossen, nicht umgesetzt · 💡 vorgeschlagen/angeboten, nicht entschieden · ❌ verworfen · ❓ unklar.
>
> **„Implementiert“ heißt hier: im Repository und durch Tests abgedeckt. Ob der Stand auf dem Server läuft, ist offen** (siehe 7 D und 11).

---

## 1. Projektübersicht

| Punkt | Inhalt |
| --- | --- |
| Projektname | **SmartArchive** (Manifest-Kurzname „Archiv“) |
| Eigentümer/Nutzer | Can, Einzelnutzer. Landesbeamter (Finanzamt), privater Vermieter. Kommuniziert auf Deutsch, Sie-Form. |
| Zweck | Selbst gehostetes Dokumentenarchiv als Ersatz für das Dropbox-Abo (11,99 €/Monat), erweitert um eine **Vermietungsverwaltung**: Mieter, Einnahmen, Betriebskostenabrechnung, Mahnwesen, Kaution |
| Kernprinzip | Zwei **strikt getrennte Bereiche „Privat“ und „Vermietung“**. Belege der Vermietung je Mietobjekt als Aufwand dokumentieren und exportieren. **Keine steuerliche Berechnung oder Bewertung.** |
| Zielgruppe | Nur der Nutzer selbst. Ein kommerzielles Angebot wurde einmal als Frage besprochen (Einschätzung „kommerzielles Potenzial“), **es gibt keinen Beschluss** dazu. |
| Plattformen | Installierbare Web-App (PWA) für Android/Chrome, zusätzlich Desktop-Browser |
| Nutzungskonzept | Beleg fotografieren, hochladen oder an die App teilen. Die App erkennt Text, Datum, Betrag, Absender und Objekt, legt sicher Erkanntes selbst ab und stellt Unsicheres als Rückfrage in den „Eingang“. Mietkonto per CSV-Import. |
| Vermietete Objekte (laut Nutzer) | **Nur Hauptstraße 15, 55758 Schmidt-Hachenbach**, 5 Parteien, Wohnungen **EG, OG links, OG Mitte, OG rechts, Galerie**. Berliner Straße 21 ist selbst bewohnt. Zu Berliner Straße 23 und 32 gibt es ältere Angaben (Hausverwalter Herr Schmitz); welche Rolle sie heute spielen, ist ❓. Häuser mit mehreren Wohnungen, keine WEG. Zentralheizung mit Ablesefirma. Mieterbestand bisher in objego (kein Export vorhanden). Reines Mietkonto bei der Commerzbank. |
| Stand | Produktiv im Einsatz (rund 3.857 Dateien bzw. 3,3 GB aus Dropbox importiert). Nächtliches Backup nach Backblaze B2 läuft. Seit dem 29.10. kamen sehr viele Vermietungsfunktionen hinzu (Abschnitt 3.9 ff.). |
| Langfristige Vision | Dropbox-Ersatz mit möglichst wenig Handarbeit. Die Vermietungsverwaltung soll objego ablösen (Mieter, Einnahmen, Betriebskostenabrechnung, Mahnwesen). |

---

## 2. Technologie und Architektur

### 2.1 Stack

| Schicht | Technologie |
| --- | --- |
| Backend | Python 3.12, **Starlette** (ASGI), uvicorn, pydantic v2 |
| Datenbank | **SQLite** (WAL), **FTS5** mit Trigramm-Tokenizer (findet Wortteile ab 3 Zeichen; Fallback `unicode61`) |
| Worker | Eigener Prozess `python -m app.worker`, Auftragstabelle `jobs` (Priorität, höchstens 3 Versuche, n-ter Versuch frühestens n Minuten später). `INLINE_WORKER=1` lässt ihn als Thread laufen (Tests/Entwicklung). |
| Texterkennung | OCRmyPDF und Tesseract (deu, eng), digitale PDFs ohne OCR, Office-Dateien per LibreOffice (headless, Zeitlimit 900 s) nach PDF |
| PDF/Bild | pypdf, img2pdf, Pillow, poppler-utils, ghostscript, qpdf, unpaper |
| PDF-Erzeugung | **reportlab** (Exporte, Betriebskosten, Mahnungen, Abmahnungen, Kautionsschreiben, Einnahmenübersicht). Schrift: DejaVu Sans, falls vorhanden, sonst Helvetica. |
| Exporte | openpyxl (Excel), csv, zipfile |
| KI (optional) | Mistral API (EU), Modell `mistral-small-latest`, nur aktiv mit `MISTRAL_API_KEY`. Ob der Schlüssel gesetzt ist, ist ❓. |
| Frontend | **React 19** und TypeScript, gebaut mit **esbuild** (`frontend/build.mjs`), keine weiteren Laufzeitbibliotheken, eigenes CSS (`styles.css`) |
| PWA | `frontend/public/manifest.webmanifest` (Share-Target für PDF und Bilder), Service Worker `sw.js` (Cache nur für die App-Hülle, Share-Target-Puffer `sa-share`) |
| Deployment | Docker Compose, Dienste `app` und `worker` aus einem Image. netcup VPS Lite 1 G12.5s (Nürnberg, Debian 13). Zugriff nur über **Tailscale Serve** (HTTPS), die App lauscht auf `127.0.0.1:8080`. |
| Backup | **restic** nach **Backblaze B2** (EU Central, Bucket `smartarchive-can-2303`), Überwachung über **healthchecks.io** |
| Repository | Privat: `github.com/crimepayzbrothers-source/Smartarchive`. GitHub meldet „repository moved“ von der kleingeschriebenen Adresse, die Weiterleitung funktioniert. Branch `main`. |

**Python-Abhängigkeiten** (`backend/requirements.txt`): starlette, uvicorn[standard], pydantic, python-multipart, pillow, img2pdf, pypdf, pytesseract, openpyxl, reportlab.
**Frontend** (`frontend/package.json`): react, react-dom ^19.2; zur Entwicklung esbuild ^0.25, typescript ^5.8, @types/react(-dom).

### 2.2 Ordnerstruktur

```
backend/app/
  main.py            App, Routen, Sicherheits-Header, einmalige Datenkorrekturen beim Start (Einmal-Marker)
  config.py          Einstellungen aus Umgebungsvariablen
  db.py              Verbindung, Migrationen MIGRATIONS 1–18, Helfer (row/rows/tx/audit/get_setting/set_setting/now)
  seed.py            Bereiche, Ordnerbaum, Ausgabenkategorien, Standard-Einstellungen
  security.py        scrypt, TOTP, Sitzungen, LoginThrottle
  storage.py         Originale (SHA-256, unveränderlich), abgeleitete Dateien, MIME-Sniffing
  documents.py       Upload/Ingest, Verarbeitung, Volltextindex, best_pdf, display_filename
  ocr.py, extract.py, classify.py, invoices.py, duplicates.py, titles.py, search.py, mistral.py
  banking.py         Mietkonto: CSV, Abgleich, Zinsanteil, Mieter-Erkennung, Entscheidungen
  exports.py         Ausgaben-Journal, CSV, Excel, PDF-Paket, Gesamt-ZIP
  importer.py, worker.py, backup.py
  tenancy.py         Mietverhältnisse: Prüfungen, Speichern, Beenden, Belegung/Leerstand
  tenancy_extract.py Auslesen aus Mietvertrag/Screenshot (Regeln, optional KI), Seitenbilder
  betriebskosten.py  Rechenkern der Betriebskostenabrechnung, Prüfungen, Fassungen
  bk_pdf.py          PDF der Abrechnung; gemeinsamer Briefkopf letterhead() für alle Schreiben
  bk_receipts.py     Belegprüfung: Bescheide zerlegen, Volltextsuche im Zeitraum, Übernahme
  income.py          Einnahmen: Soll/Ist je Monat, Aufteilung Kalt/BK/HK, Übersicht
  income_pdf.py      Einnahmenübersicht als PDF
  dunning.py         Mahnwesen (Zahlungserinnerung/Mahnung Miete); archive_letter(), default_salutation()
  abmahnung.py       Abmahnungen; Werktags-/Feiertagslogik RLP
  deposit.py         Mietkaution
  deposit_letter.py  Zahlungserinnerung/Mahnung zur Kaution
  api/  auth, docs, meta, realestate, bank, tenants, bk, income, dunning, abmahnung, deposit, common
backend/static/      Gebaute PWA (vom Server ausgeliefert, im Git eingecheckt, Assets mit Prüfsumme im Namen)
backend/tests/       pytest, 23 Testdateien, 141 Tests
frontend/src/        App.tsx, main.tsx, api.ts, lib.tsx, extract.ts, scan.ts, styles.css
  pages/             Home, Inbox, Archive, DocumentPage, Properties, Tenants, Income, Betriebskosten,
                     Dunning (Mahnungen), Abmahnung, Deposit (Kaution), Expenses, Transactions, Settings, Login
  components/        ui.tsx, UploadSheet, Scanner, Suggestion, InvoiceCard, DateQuestion, DuplicateCard,
                     AmountPicker, TxCard, SwipeRow (inkl. ConfirmDelete), shareDocs.ts
frontend/public/     sw.js, manifest.webmanifest, Icons
scripts/             backup.sh, restore-test.sh
docs/                PROJEKT-UEBERGABE.md (diese Datei), BETRIEBSKOSTEN.md (Fachdokumentation)
data/ (Server)       smartarchive.sqlite3, files/, backup/, cache/   (nicht im Git)
```

### 2.3 Zusammenspiel

1. **Upload** (`POST /api/upload`): Das Original wird per SHA-256 unveränderlich gespeichert. Byte-gleiche Dateien gelten sofort als Dublette. Es entstehen ein Dokument mit `status='processing'` und ein Job `process`. Optional sind `property_id` (Objekt), `upload_area` und `tenancy_id` möglich; mit `tenancy_id` wird das Dokument dem Mietverhältnis, Objekt und der Einheit zugeordnet.
2. Der **Worker** führt `documents.process` aus: Text (digital, OCR oder Office), inhaltliche Dublettenprüfung, Einordnung (`classify.analyze`/`decide`), Ausgabe erfassen (`invoices.capture_on_upload`/`ensure_expense`), Volltextindex.
3. Das **Frontend** nutzt die JSON-API. Jede Änderung wird in `audit_log` protokolliert, ohne Namen oder Kontaktdaten von Mietern.
4. **Vermietungsmodule** greifen auf gemeinsame Daten zu: Mietverhältnisse (`tenancies`) → Soll der Einnahmen. Mietkonto (`transactions.tenancy_id`) und Barzahlungen (`rent_receipts`) → Ist. Ausnahmen (`rent_exceptions`) → offene Monate → Mahnwesen und Kündigungsschwellen-Hinweis. Ausgaben und Archivbelege → Betriebskosten.
5. **Erzeugte Schreiben** (Mahnung, Abmahnung, Kautionsschreiben, Betriebskostenabrechnung) speichern einen **Snapshot** (JSON), damit das PDF später unverändert reproduzierbar ist, und werden als Dokument im Archiv abgelegt: Vermietung › „Mietverträge und Mieter“, bei Betriebskosten › „Nebenkosten“.

### 2.4 Authentifizierung und Sicherheit

- Ein Benutzerkonto. Einrichtung mit `SETUP_TOKEN`. Passwort mit scrypt, optional TOTP-2FA.
- Sitzungscookie `sa_session` (HttpOnly, SameSite, Secure, solange `COOKIE_SECURE` nicht 0 ist). In der Datenbank liegt nur der Hash. Laufzeit 30 Tage.
- **CSRF:** Schreibende Anfragen brauchen den Header `X-Requested-With: SmartArchive`, sonst 403 (`api.ts` setzt ihn).
- **Bremse bei Fehlversuchen** (`LoginThrottle`): ab dem 5. Fehlversuch exponentielle Sperre, höchstens 900 s, nur im Arbeitsspeicher.
- **Sicherheits-Header:** strikte CSP (nur `'self'`, Bilder auch `data:`/`blob:`), `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer`, `Permissions-Policy: camera=(self)`, API-Antworten mit `no-store`. **Folge: keine externen Skripte, Schriften oder CDNs im Frontend.**
- Papierkorb statt Löschen (`deleted_at`).

### 2.5 Konfiguration (`.env`)

`SETUP_TOKEN`, `MISTRAL_API_KEY` (optional), `MISTRAL_MODEL`, `MISTRAL_BASE_URL`, `COOKIE_SECURE` (Standard 1), `MAX_UPLOAD_MB` (40), `SESSION_DAYS` (30), `DATA_DIR` (/data), `STATIC_DIR`, `INLINE_WORKER`. Für den einmaligen Dropbox-Import kam `DROPBOX_TOKEN` hinzu.

### 2.6 Frontend-Zustand und Routing

- Kein State-Framework. Hash-Routing über `useRoute` in `lib.tsx`, Laden über `useLoad`, API-Helfer `get/post/put/patch/del` in `api.ts`.
- **Toasts:** als `popover="manual"` (Top-Layer), damit sie auch über offenen Dialogen sichtbar sind.
- **Dialoge** (`Sheet`): natives `<dialog>` mit `showModal()`.
- **Zurück-Navigation:**
  - `scrollMemo` merkt die Scrollposition je Adresse, `takeScroll()` stellt sie wieder her.
  - `rememberReturn`/`returnTarget()` (sessionStorage `sa-return`) liefern die letzte Seite außerhalb eines Dokuments, damit „Zurück“ auch nach einem Neuladen der App durch Android funktioniert.
  - `replaceRoute(path, params)` schreibt Filter und Zustand ohne neuen Verlaufseintrag in die Adresse.
- **Gerätespeicher** (`localStorage`): Farbschema; Auswahl der Belegprüfung je Abrechnung (`sa-bk-sel-{periodId}`).

**Routen (Hash):**
- Start, Eingang, Archiv und Suche: `#/`, `#/eingang`, `#/archiv` (`?bereich=&kategorie=&objekt=&jahr=&tag=&favorit=`), `#/suche?q=`.
- Dokument: `#/dokument/{id}`.
- Objekte und Mieter: `#/objekte`, `#/objekt/{id}`, `#/mieter`, `#/mieter/{id}`.
- Einnahmen, Ausgaben, Umsätze: `#/einnahmen` (`?jahr=&objekt=`), `#/ausgaben`, `#/umsaetze`.
- Betriebskosten: `#/betriebskosten`, `#/betriebskosten/{id}?schritt=&belege=1&suche=`.
- Sonstiges: `#/einstellungen`, `#/teilen`.

### 2.7 Build, Test, Deployment

- **Frontend:** `cd frontend && npm install && npm run build`. Das Ergebnis landet in `backend/static` mit Prüfsumme im Dateinamen (`main-[hash].js/.css`), damit Updates auf dem Handy ankommen. Das gebaute `backend/static` wird mit eingecheckt.
- **Tests:** `cd backend && python -m pytest -q`, derzeit 141 grün.
- **Server-Update:**
  ```
  cd ~/smartarchive && git pull && git log --oneline -1 && docker compose up -d --build 2>&1 | tail -n 25
  ```
  Migrationen laufen beim Start automatisch. Für Änderungen nur an `scripts/` genügt `git pull`.
- Besonderheiten der Claude-Arbeitsumgebung (für einen neuen Chat relevant):
  - Die npm-Registry war gesperrt. Gebaut wurde mit vorinstallierten Paketen per Symlink nach `frontend/node_modules` und `NODE_PATH=/opt/npm-tools/node_modules npm run build`.
  - `tsc` war nicht verfügbar. **Die TypeScript-Typprüfung (`tsc --noEmit`) ist seit dem 29.10. nicht gelaufen**, esbuild prüft keine Typen.
  - Zum lokalen Testen: uvicorn mit `DATA_DIR=…`, `SETUP_TOKEN=…`, `COOKIE_SECURE=0`; Worker separat mit `python -m app.worker`; Oberflächentests mit Playwright und Pixel-7-Emulation.

---

## 3. Vollständiger Funktionsumfang

### 3.1 Erfassen ✅

| Funktion | Verhalten |
| --- | --- |
| Foto aufnehmen und Scanner | Kantenerkennung, Ecken verschiebbar, Perspektivkorrektur, Filter (Dokument/Farbe/Original), Drehen, mehrere Seiten. Das Originalfoto bleibt als `capture` erhalten. |
| Upload | PDF, JPEG, PNG, WebP, TIFF (beim Import auch Office-Dateien), mehrere Dateien, Drag-and-drop |
| **Teilen an die App** | Share-Target `/share-target` (POST, mehrere Dateien, nur `application/pdf` und `image/*`). Der Service Worker puffert die Dateien, danach führt `#/teilen` in den Upload. Voraussetzung: Installation über Chrome → „App installieren“. |
| Upload direkt zum Objekt | Objekt-Chips im Bereich Vermietung; das Dokument wird mit `property_id` direkt abgelegt |
| Bereichswahl | `upload_area`: auto / privat / vermietung |

### 3.2 Verarbeitung, Ablage, Datum, Dubletten ✅

- **Automatische Ablage** in dieser Reihenfolge: eigene Regeln (0,99), bestätigter Verlauf je Absender, KI (optional), Stichwörter. Automatisch abgelegt wird ab `auto_threshold` 0,9, ein Vorschlag erscheint ab `suggest_threshold` 0,5.
- **Blocker:** keine Kategorie erkannt, Sicherheit zu niedrig, Bereich `auto` ohne Regel oder Verlauf, Vorschlag im anderen Bereich, Mietobjekt unklar, Automatik ausgeschaltet.
- **Bereichswechsel nie automatisch.** KI pro Ordner sperrbar (voreingestellt gesperrt: Gesundheit, Familie). Die KI bekommt höchstens 6.000 Zeichen Text, nie die Datei. Ihr Begründungszitat muss wörtlich im Text stehen.
- **Lernen aus Korrekturen** (Tabelle `corrections`, gelernte Regeln). „Rückgängig“ für automatische Ablagen.
- **Automatische Titel** (`titles.make_title`); generische Titel werden ersetzt, eigene Titel nie.
- **Datum nur aus dem erkannten Text** (`extract.date_analysis`), nie aus Upload-Datum, Dateiname oder Titel. `date_status` ist `sure` oder `unsure`, dazu bis zu 6 Kandidaten. Bei Unsicherheit erscheint die Rückfrage „Datum prüfen“ im Eingang.
- **Inhaltliche Dubletten** (`duplicates.find`, nach Rechnungsnummer, Betrag/Datum und Textähnlichkeit). Im Eingang unter „Mögliche Duplikate“ mit den Optionen Überschreiben, Verwerfen oder Behalten.

### 3.3 Ausgaben Vermietung ✅

- **Sofortige Erfassung beim Upload** (`capture_on_upload`): als `entwurf`, solange das Objekt unklar ist; mit Ablage in Vermietung und bekanntem Objekt `bestaetigt`; bei Ablage in Privat wird die Ausgabe gelöscht. Erkennbar an `origin='auto'`.
- **Zahlungsdatum = Rechnungsdatum** als Annahme (`paid_assumed=1`), solange kein Kontoumsatz oder Barzahlungshinweis vorliegt.
- **„Aus Beleg auslesen“.** Keine Beträge raten: Die Auswahlliste `AmountPicker` erscheint, wenn der Endbetrag nicht eindeutig ist.
- Rechnungs-Abfrage mit Objekt oder „keine Ausgabe“ (`no_expense`). „Rechnungen ohne Ausgabe“ zum Nacherfassen.
- Bescheide werden nur mit Kategorie `grundsteuer` oder `grundbesitzabgaben` als **eine** Ausgabe mit Gesamtbetrag erfasst. Die Zerlegung erfolgt erst in der Betriebskosten-Belegprüfung, siehe 3.13.
- Jahreszuordnung: `COALESCE(paid_on, invoice_date, doc_date)`. Prüfhinweise (u. a. anschaffungsnahe Aufwendungen innerhalb von 3 Jahren, nur Hinweis).
- **Exporte** (`/api/export/expenses.{csv|xlsx|pdf}`): chronologisch mit durchgehender Belegnummer; PDF mit oder ohne Belege (`belege=0`). Gesamt-ZIP des Archivs.

### 3.4 Mietkonto (Bank) ✅

- **CSV-Import** (Commerzbank, gängige deutsche Formate). Duplikatschutz per `fingerprint`. `coverage()` zeigt den bereits erfassten Zeitraum und den empfohlenen Beginn des nächsten Exports.
- **Folgeimporte:** Neue Umsätze in schon eingelesenen Zeiträumen werden erkannt und gemeldet.
- **Abgleich:**
  1. erfasste Rechnung gleichen Betrags (`expense_candidates`, Bonus für Rechnungsnummer und Aussteller)
  2. **Mieter-Erkennung** (`_tenant_match`): Nachname eines Vertragspartners im Buchungstext; Sicherheit 0,95, wenn der Betrag der vereinbarten Monatsmiete entspricht, sonst 0,8
  3. Verlauf je Zahlungspartner (Vorschläge schon ab der ersten gleichen Zuordnung; automatisch nur bei hoher Sicherheit)
  4. KI (höchstens 40 je Import)
  5. Stichwörter
- **Zinsanteil aus dem Verwendungszweck** (`interest_share`): Beschriftete Zins- bzw. Tilgungsbeträge werden erkannt, nur der Zinsanteil wird als Teilbetrag vorgeschlagen. **Standardkategorie** ohne Erkennung: `sonstige` („Sonstige Werbungskosten“).
- **Bestätigung:** automatische Zuordnungen stehen zur Kontrolle im Eingang (`confirm-auto`). Entscheidungen lassen sich jederzeit revidieren (`undo`).
- **Umsatzübersicht** `#/umsaetze` mit Filter und Korrektur bereits bestätigter Umsätze.
- Online-Banking direkt (FinTS/PSD2): ❌ nicht umgesetzt, der CSV-Import ersetzt es.

### 3.5 Archiv, Suche, Dokument ✅

- Archiv nach Bereich und Ordner, Filter für Objekt, Jahr, Tag und Favorit. Volltextsuche mit Hervorhebung.
- **Suche:** Die Tastatur schließt nach Enter. Suchbegriff und Filter stehen in der Adresse, die Trefferliste kommt beim „Zurück“ sofort wieder, an derselben Scrollposition.
- Dokumentansicht: Seitenbilder, Text, Felder, neu verarbeiten. **Öffnen** im PDF-Viewer und **Teilen** (Web Share API, einzelne Datei).
- **Mehrfachauswahl (31.10.):** „Auswählen“ oder langes Drücken (500 ms) startet die Auswahl.
  - Im Auswahlmodus wählt ein Antippen aus, statt das Dokument zu öffnen.
  - Leiste: Anzahl, Alle/Keine, **ZIP**, **Teilen**. Teilen übergibt alle PDFs in einem Schritt an das Android-Menü. Bei zu langer Ladezeit kommt die Meldung „Jetzt teilen“ für einen zweiten Tipp.
  - Höchstens 50 Dokumente. `GET /api/documents/bundle.zip?ids=…` liefert ein ZIP (Dateinamen wie beim Einzeldownload, doppelte nummeriert).
  - Code: `pages/Archive.tsx`, `components/shareDocs.ts`.

### 3.6 Objekte und Einheiten ✅

Stammdaten, Einheiten mit Wohnfläche, Ausgaben nach Jahr, Dokumente je Objekt. **Einheiten-Vorgaben** per Tipp: EG, OG links, OG Mitte, OG rechts, Galerie. Einheiten werden in der Reihenfolge der Anlage sortiert. Einheiten mit Mietverhältnissen lassen sich nicht löschen. Die Objektseite zeigt die **Belegung** (`OccupancyPanel`).

### 3.7 Mieterverwaltung ✅

| Funktion | Verhalten |
| --- | --- |
| Mietverhältnisse | Mehrere Vertragspartner (`tenancy_parties`), **Konditionen ab Datum** (`tenancy_terms`: Kaltmiete, BK- und HK-Vorauszahlung, Personen, Notiz), Vertragsbeginn/-ende, tatsächlicher Ein- und Auszug, Umlagevereinbarungen, Notizen |
| Prüfungen (`tenancy.py`) | Überschneidung in derselben Wohnung abgelehnt; Einheit Pflicht, wenn das Objekt Einheiten hat; mindestens ein Vertragspartner mit Nachname; erste Konditionen ab Mietbeginn; keine negativen Beträge; Daten plausibel |
| Status | `kuenftig` / `aktiv` / `beendet` (nach Vertrags- bzw. Auszugsdatum) |
| Mieterwechsel | „Beenden“ (Mietende, Auszug), danach direkt den Nachmieter anlegen. Lücken erscheinen automatisch als **Leerstand**. Vor- und Nachmieter samt Leerstand sind auf der Mieterseite verlinkt. |
| Mieterliste `#/mieter` | Filter Aktiv/Künftig/Beendet/Alle, Suche. Je Karte die **Gesamtmiete fett, dazu Kalt/BK/HK**. **Monatssumme aller angezeigten laufenden Mietverhältnisse unterhalb der Liste**; beendete und künftige zählen nicht mit. Hinweis bei offener oder nicht erfasster Kaution (`DepositPill`). |
| Mieterseite `#/mieter/{id}` | Laufzeit, Konditionen, Vertragspartner, Bereiche **Kaution**, **Mahnungen**, **Abmahnungen**, **Unterlagen** (Dokumente mit `tenancy_id`, Upload mehrerer Dateien), Bearbeiten, Beenden, „Versehentlich angelegt? Löschen“ |
| Formular | Fehler direkt im Formular (`form-error`), Wohnung auch beim Bearbeiten wählbar, mehrere Dateien anhängbar (Upload beim Speichern) |
| **Übernahme aus Vertrag/Screenshot** | `POST /api/tenancies/extract` liest PDF-Text bzw. OCR (`tenancy_extract.parse`) und füllt nur leere Felder vor, mit Fundstellen und Liste fehlender Angaben. Kein Raten: Bei mehrdeutiger Wohnung wird keine gewählt; der Vermieter wird nicht als Mieter übernommen; eine Gesamtmiete gilt nicht als Kaltmiete. **KI nur auf ausdrücklichen Wunsch** (`use_ai`) und nur mit Schlüssel. Gespeichert wird erst mit „Mietverhältnis anlegen“. |
| Selbst eintragen mit Ansicht | Für handschriftliche Verträge: `POST /api/tenancies/preview` liefert Seitenbilder, `DocViewer` zeigt sie beim Ausfüllen (höchstens 30vh, Zoom) |

### 3.8 Einnahmen `#/einnahmen` ✅

- **Soll** je Monat aus den Konditionen, bei Beginn oder Ende im Monat taggenau anteilig (`income.monthly_soll`).
- **Ist** aus dem Mietkonto (`transactions.tenancy_id`) und Barzahlungen (`rent_receipts`). **Eingänge ab dem 25. zählen für den Folgemonat** (`EARLY_DAY = 25`).
- **Automatisch als eingegangen** (Einstellung `rent_assume_paid`, Standard an): Fällige Monate ohne Buchung gelten ab dem Monatsersten als bezahlt (Status „angenommen“). Echte Zahlungen haben Vorrang. „**Ausgeblieben?**“ je Monat hinterlegt eine Ausnahme (`rent_exceptions`: tatsächlich gezahlt, Notiz), „aufheben“ entfernt sie. Fehlt bei einem sonst überweisenden Mieter ein Eingang, obwohl die Kontoumsätze den Zeitraum abdecken, wird der Monat markiert (`bank_missing`).
- **Monatsstatus:** frei / kuenftig / angenommen / bezahlt / teilweise / offen. Der Saldo läuft innerhalb des Jahres auf. Kennzahlen: Soll, Soll bis heute, Eingegangen, Rückstände, Überzahlungen, davon angenommen.
- **Diagramme:**
  - Säulen „Eingänge je Monat“ (mit Zahlung bzw. angenommen, Soll als gestrichelte Linie)
  - Balken „Zusammensetzung“ (Kaltmiete, BK-VZ, HK-VZ, Mehrbeträge)
  - Balken je Mietverhältnis und Objekt
- **Aufteilung des Ist** (`income.split_parts`): anteilig nach Soll in Kalt/BK/HK, centgenau (größter Rest), Überschuss als „Mehrbetrag“.
- **Einnahmenübersicht als PDF für das Finanzamt** (`GET /api/export/income.pdf?year=&property_id=`, `income_pdf.py`):
  - Zusammenfassung, je Objekt die Mietverhältnisse und eine Monatsübersicht
  - Eingänge ohne Zahlungsnachweis (nur angenommen) und Rückstände **getrennt ausgewiesen**
  - weitere Einnahmen ohne Mietverhältnis
  - Erläuterungen „keine steuerliche Berechnung, ersetzt nicht die Anlage V“
- Monatszeilen zeigen, wann ein Monat angemahnt wurde. Link „Rückstand anmahnen“ führt zur Mieterseite.

### 3.9 Mahnwesen (Miete) ✅ (`dunning.py`, `pages/Dunning.tsx`)

- **Offene Monate nur aus den Einnahmen.** Überzahlungen werden auf die ältesten Rückstände angerechnet (§ 366 Abs. 2 BGB).
- **Rechnerischer Hinweis** auf die Schwellen des § 543 Abs. 2 S. 1 Nr. 3 BGB (zwei aufeinanderfolgende Monate mit zusammen mehr als einer Monatsmiete offen, oder Rückstand von mindestens zwei Monatsmieten). Keine rechtliche Bewertung.
- **Zahlungserinnerung oder Mahnung** als PDF:
  - Inhalt: Briefkopf aus den Vermieterangaben, Tabelle der offenen Monate, Frist (Vorschlag +14 Tage), Bankverbindung, Verwendungszweck. Die Mahnung verweist auf frühere Schreiben und auf § 556b BGB.
  - Anpassbar: Anrede und ein Zusatztext. Die Beträge sind nicht änderbar.
  - Keine Verzugszinsen oder Mahngebühren.
  - Vorschau ohne Speichern.
- Ablage beim Mieter, Liste versandter Schreiben mit PDF. Löschen mit Rückfrage entfernt nur den Listeneintrag, das PDF bleibt im Archiv.

### 3.10 Abmahnungen ✅ (`abmahnung.py`, `pages/Abmahnung.tsx`)

- **Anlässe mit Textbausteinen:** unpünktliche Zahlung, Ruhestörung, unerlaubte Überlassung an Dritte, Tierhaltung ohne Erlaubnis, Verstoß gegen die Hausordnung, Sonstiges. Aufforderung und Folgenhinweis sind editierbar, `{frist}` wird durch das Fristdatum ersetzt (Pflichtfrist bei Überlassung und Tierhaltung).
- **Ohne konkreten Vorfall mit Datum kein Schreiben.** Vorfälle nach dem Briefdatum werden abgelehnt.
- **Zustellweg:** Einwurf-Einschreiben, Bote, persönlich, Brief, E-Mail. Die ersten drei stehen als Vermerk auf dem Schreiben.
- **Vorschläge verspäteter Zahlungen** aus tatsächlichen Eingängen der letzten 24 Monate:
  - fällig am 3. Werktag; Samstag zählt nicht (BGH VIII ZR 129/09); RLP-Feiertage berücksichtigt
  - erst mehr als 2 Werktage nach Fälligkeit wird vorgeschlagen, weil die Überweisungslaufzeit zählt (BGH VIII ZR 222/15)
- Ablage beim Mieter, Liste, Löschen mit Rückfrage.

### 3.11 Mietkaution ✅ (`deposit.py`, `deposit_letter.py`, `pages/Deposit.tsx`)

- **Arten:** `bar` (Barkaution/Kautionskonto), `sparbuch` (verpfändet), `buergschaft` (Bankbürgschaft), `versicherung` (Kautionsversicherung), `keine`.
- **Höhe:**
  - per Tipp **2 oder 3 Kaltmieten zu Mietbeginn** oder ein eigener Betrag
  - über 3 Nettokaltmieten gibt es einen Hinweis (§ 551 Abs. 1 BGB), gespeichert wird trotzdem
- **Barkaution:**
  - Einzahlungen erfassen und löschen (mit Rückfrage), Fortschrittsbalken
  - optional **3 Raten** (§ 551 Abs. 2 BGB): Mietbeginn, dann der 3. Werktag der beiden Folgemonate; überfällige Raten sind markiert
- **Urkunden-Arten:** Status nach Eingang der Urkunde (Datum). Fehlt die Urkunde nach Mietbeginn, ist das „offen“.
- **Rückgabe:** Datum, ausgezahlt, einbehalten. Status „zurückgegeben“ bzw. „abgerechnet“.
- **Zahlungserinnerung/Mahnung zur Kaution:**
  - Barkaution: die zum Briefdatum fälligen, offenen Raten
  - Urkunden-Arten: Aufforderung, das Original vorzulegen
  - Rechnerischer Hinweis auf § 569 Abs. 2a BGB (offen ≥ 2 Nettokaltmieten)
  - Ablage beim Mieter
- **Einschränkung:** Eine Kautionsüberweisung auf das Mietkonto zählt als Miete, wenn sie im Kontoumsatz dem Mieter zugeordnet wird. Eine Zuordnung „Kaution“ für Umsätze gibt es nicht (💡 angeboten, siehe 7 E).

### 3.12 Betriebskostenabrechnung ✅ (Details in `docs/BETRIEBSKOSTEN.md`)

- **Assistent** `#/betriebskosten/{id}` mit 8 Schritten: Kosten, Schlüssel, Werte, Nutzungszeiten, Vorauszahlungen, Ergebnis, Prüfung, Freigabe.
- **Übersicht** je Objekt und Jahr, Vermieterangaben (Name, Anschrift, Telefon, E-Mail, IBAN, Bank, Ort, Logo).
- **Kostenarten** nach § 2 BetrKV plus „nicht umlagefähig“.
- **Schlüssel:** Fläche, Einheiten, Personentage, Verbrauch, direkt, extern (Ablesefirma), individuell.
- **Rechnung:**
  - Tage kalendergenau
  - Leerstand als Vermieteranteil (BGH VIII ZR 159/05)
  - Heizkosten aus der Abrechnung der Ablesefirma
  - exakte Brüche, Rundung nach dem größten Rest
- **Prüfstufen:** kritisch, Warnung (einzeln zu bestätigen), Hinweis.
- **Fassungen:** freigegebene Fassungen als Snapshot; jede spätere Änderung setzt den Zeitraum auf „Entwurf“ zurück.
- **PDF:** nach BGH VIII ZR 244/18; Ablage im Archiv je Mieter; Belege als ZIP zur Belegeinsicht.
- **Kosten übernehmen:** aus Ausgaben (Abflussprinzip, Kostenart wird vorgeschlagen) oder manuell.
- **Wischen zum Löschen mit Bestätigungsdialog** (`SwipeRow`, `ConfirmDelete`):
  - für Kostenpositionen und Entwurfs-Abrechnungen in der Übersicht
  - freigegebene Abrechnungen sind nicht löschbar
  - der Papierkorb an Kostenpositionen fragt ebenfalls nach

### 3.13 Belegprüfung und Belegsuche (Betriebskosten) ✅ (`bk_receipts.py`)

- **„Belege prüfen“:** sucht Archivbelege des Objekts (Bereich Vermietung, abgelegt, ohne `tenancy_id`, keine Betriebskostenabrechnungen), die zum Zeitraum gehören. Relevant ist ein Beleg, wenn sein Datum im Zeitraum liegt, der Bescheid das Jahr nennt oder ein Dauerbescheid „für Folgejahre“ gilt.
- **Abgabenbescheide zeilenweise zerlegt** (`parse_bescheid`):
  - Kostenart je Zeile über `TYPE_PATTERNS`: Grundsteuer, Entwässerung, Straßenreinigung, Müll, Wasser, Schornstein, Versicherung; wiederkehrende Beiträge und Ausbaubeiträge werden „nicht umlagefähig“.
  - übergangen werden Fälligkeiten, Rückstände, Erstattungen, Zinsen, IBAN usw.
  - Betrag: hinter dem letzten „=“, sonst der letzte Betrag der Zeile; vier gleiche Beträge gelten als Vierteljahresraten und werden summiert (mit Prüfhinweis); ein Betrag in der Folgezeile wird erkannt.
  - „passt zur Summe“ nur, wenn alle Positionen zusammen den Gesamtbetrag des Bescheids ergeben.
- **Rechnungen ohne Ausgabe** erscheinen als Vorschlag. Rechnungen mit Ausgabe laufen über „Aus Ausgaben übernehmen“ oder die Suche.
- **Stichwortsuche** (Suchfeld direkt im Schritt „Kosten“):
  - fest auf Objekt und Belegdatum im Abrechnungszeitraum begrenzt (`search.search(date_from, date_to)`)
  - **Begriffserweiterung** (`SYNONYMS`, Zerlegung von „…bescheid/…rechnung/…gebühr…“), z. B. findet „Abgabenbescheid“ auch „Bescheid über Grundbesitzabgaben“
  - Treffer zeigen Positionen und **alle Beträge des erkannten Texts** (`amount_lines`) mit Kennzeichnung von Summen- und Fälligkeitszeilen
- **Übernahme:**
  - „+ Betrag speichern“ und „… Positionen speichern“ legen die Positionen **sofort dauerhaft** an: Herkunft `beleg`, `document_id`, Fundstelle (`evidence`), `verified_at = NULL`.
  - Teilweise übernommene Belege bleiben sichtbar, gespeicherte Positionen sind markiert, dieselbe Fundstelle wird nicht doppelt angelegt.
  - Noch nicht gespeicherte Auswahl und geänderte Beträge bleiben auf dem Gerät erhalten (localStorage).
- **Manuelle Prüfung:**
  - Jede automatisch erfasste Position (Herkunft `beleg`, oder `ausgabe` ohne Nutzerprüfung) ist „ungeprüft“, bis „geprüft“ angetippt wird; eine Änderung von Betrag oder Kostenart gilt als geprüft.
  - Die Prüfung meldet ungeprüfte Positionen als Warnung und Positionen ohne Beleg als Hinweis.
  - Doppelerfassung (Gesamtbetrag und Zerlegung desselben Belegs) wird gemeldet. Zerlegte Belege erscheinen nicht mehr unter „Aus Ausgaben übernehmen“; Belege mit Gesamtbetrag sind für die Zerlegung gesperrt.
- **Navigation:**
  - Belege öffnen im selben Fenster.
  - Schritt, geöffnete Belegprüfung und Suchbegriff stehen in der Adresse; „Zurück“ führt an dieselbe Stelle, mit wiederhergestellter Scrollposition.

### 3.14 Betrieb ✅

- **Dropbox-Import:** CLI `plan`/`run`/`status`, durchgeführt.
- **Backup:** nächtlich 3:30 Uhr, restic nach B2, Aufbewahrung 14 Tage / 8 Wochen / 24 Monate, `restic unlock` vorab, healthchecks.io-Ping (Start/Erfolg/Fehler).
- **Wiederherstellungstest:** monatlich (`0 5 1 * *`), rotierend 1/12 der Daten, ohne Vollkopie. 🟡 Der erste erfolgreiche Serverlauf der neuen Fassung ist ❓.
- **Konto und Darstellung:** 2FA, Passwort ändern, Farbschema, Papierkorb, Gesamtexport.

---

## 4. Benutzeroberfläche und UX

### 4.1 Gestaltung (implementiert)

- **Farben** (CSS-Variablen):
  - Petrol `--petrol #0d5c66`, Papier `#eef1f0`/`--paper`, Text `--ink #15202a`
  - Bereichsfarben: Privat `--privat #3b5a8f`, Vermietung `--vermietung #4a7a33`
  - Ocker `--ochre #a8760f`, Fehler `--danger #b0402c`, OK `--ok #2f7a55`
  - Dunkelmodus über `prefers-color-scheme` bzw. `data-theme`
- **Typografie und Formen:** Systemschriften, Radien 6/10/16 px, dezente Schatten, Navigationshöhe `--nav-h` (64 px).
- **Sprache:** Deutsch, Sie-Form, Beträge im deutschen Format.
- **Status-Pills:** `bestaetigt` (grün), `entwurf` (ocker), `ungeklaert` (rot).
- **Hinweise:** `.notice.warn`, `.notice.block` für Fließtext.

### 4.2 Navigation

- **Mobil:** Kopfzeile (Logo, Suche, Einstellungen); untere Navigation Eingang, Archiv, Kamera-FAB, Objekte, Ausgaben; Desktop mit Seitenleiste.
- **Vermietung:** Registerleiste **Objekte | Mieter | Einnahmen | Betriebskosten** (`.register.small`, horizontal scrollbar, damit die Seite nicht breiter als der Bildschirm wird).

### 4.3 Wiederverwendbare Komponenten und Muster

- **Grundbausteine:**
  - `Sheet` (Dialog), `Icon`, `Empty`, `ErrorBox`, `Loading`, `BackButton` (Ziel ist `history.back()`, sonst der übergebene Rückweg), `DocRow`
  - `SwipeRow` (Wischen nach links: kurz legt „Löschen“ frei, weit öffnet die Rückfrage) und `ConfirmDelete` (Abbrechen vorausgewählt)
  - `openPdf()` in `Dunning.tsx`: PDF-Vorschau per POST in einem vorab geöffneten Fenster
- **Fachliche Karten:** `FileCollector` (mehrere Dateien), `DocViewer`, `AmountPicker`, `TxCard`, `InvoiceCard`, `DateQuestion`, `DuplicateCard`, `Scanner`, `UploadSheet`.
- **Diagramme:** reines CSS (`.month-chart`, `.cat-bars`), keine Bibliothek.

### 4.4 Designideen (nicht umgesetzt)

- App-Icon Option A „Register“: 💡 empfohlen, nicht beauftragt.

---

## 5. Datenstrukturen und Geschäftslogik

### 5.1 Migrationen (`db.MIGRATIONS`, `PRAGMA user_version` = 18)

| Nr. | Inhalt |
| --- | --- |
| 1–8 | Grundschema (Dokumente, Dateien, Ordner, Absender, Objekte/Einheiten, Ausgaben, Regeln, Jobs, Audit, Volltext), Import-Herkunft, `expenses.origin`, Kontoumsätze, `no_expense`, `paid_assumed`, `date_status`/`date_candidates`, `duplicate_of`/`duplicate_reason` |
| 9 | `tenancies`, `tenancy_parties`, `tenancy_terms` |
| 10 | `bk_periods`, `bk_costs`, `bk_keys`, `bk_values`, `bk_advances`, `bk_statements` |
| 11 | `documents.tenancy_id` |
| 12 | `transactions.tenancy_id`, `rent_receipts` (Barzahlungen) |
| 13 | `rent_exceptions` (tenancy_id, month `YYYY-MM`, paid_cents, note; eindeutig je Monat) |
| 14 | `dunning_letters` (kind erinnerung/mahnung, letter_date, due_date, months JSON, total_cents, snapshot, document_id) |
| 15 | `warning_letters` (reason, letter_date, deadline, incidents JSON, delivery, snapshot, document_id) |
| 16 | `deposits` (je Mietverhältnis: kind, months, amount_cents, installments, account, provider, reference, document_received_on, returned_on, returned_cents, retained_cents, note) und `deposit_payments` |
| 17 | `deposit_letters` (kind, letter_date, due_date, total_cents, snapshot, document_id) |
| 18 | `bk_costs.evidence`, `bk_costs.verified_at`; Bestand: manuelle und von Hand geprüfte Ausgaben-Positionen gelten als geprüft |

**Konventionen:** Beträge in **Cent (Integer)**. Daten als ISO `YYYY-MM-DD`, Zeitstempel UTC-ISO. Monate als `YYYY-MM`.

### 5.2 Einstellungen (`settings`)

`auto_file`, `auto_threshold` 0,9, `suggest_threshold` 0,5, `ai_enabled`, `rent_assume_paid` (Standard true), `landlord` (Vermieterangaben inkl. Logo-Pfad). Einmal-Marker: `expense_autofill_v1`, `expense_dates_v2`, `paid_assumed_v1`, `date_status_v1`, `bank_suggest_v2`.

### 5.3 Wichtige Regeln und Formeln

- **Mietzeitraum:** `tenancy.span(t)` = Beginn bis Ende, wobei Auszug/Vertragsende maßgeblich sind. `term_at(terms, day)` liefert die am Tag gültigen Konditionen.
- **Monats-Soll:** Summe über die Tage des Monats von Kondition/Monatstage, gerundet je Bestandteil.
- **Zahlungsmonat:** Datum ≥ 25. → Folgemonat.
- **Offene Monate (Mahnwesen):** Monate mit Soll > Ist; Überzahlungen werden auf die älteste Schuld angerechnet.
- **Monatsmiete für Schwellen:** Kalt + BK + HK der am Stichtag gültigen Konditionen. Für die Kaution gilt dagegen die **Nettokaltmiete zu Mietbeginn**.
- **Werktage** (`abmahnung.third_workday`, `add_workdays`): Montag bis Freitag ohne Feiertage in RLP (Neujahr, Karfreitag, Ostermontag, 1.5., Himmelfahrt, Pfingstmontag, Fronleichnam, 3.10., 1.11., 25./26.12.).
- **Betriebskosten:** siehe `docs/BETRIEBSKOSTEN.md` (Brüche, größter Rest, Leerstand, Heizkosten extern, Prüfkatalog, Fassungen).

### 5.4 Grundsätze in allen Vermietungsmodulen

Keine geschätzten Beträge. Kein automatisches Übernehmen ohne Bestätigung, wo es fachlich darauf ankommt (Vorauszahlungen, Belegpositionen gelten bis zur Prüfung als ungeprüft). Rechtliche Schwellen nur **rechnerisch** anzeigen, nie bewerten. Schreiben mit Snapshot und Archivablage.

---

## 6. Entwicklungshistorie (chronologisch, Kurzfassung)

1. **Bis 29.10.:**
   - MVP (Archiv, OCR, Suche, Ablage, Ausgaben, Exporte, 2FA, PWA, Docker/Tailscale)
   - Dropbox-Import, Scanner, automatische Titel, Rechnungsautomatik (zählt sofort)
   - Datum nur aus dem Text, Zahlungsdatum angenommen, Betrags-Auswahlliste, Dubletten
   - Backup B2 mit healthchecks.io
   - **Verworfen:** Upload-Datum und Dateiname als Datumsquelle; Höchstbetrag raten; Hetzner Storage Box; Vollwiederherstellung nach `/tmp`; FinTS.
2. **29.10.:** Suche schließt die Tastatur nach Enter, der Suchstand bleibt beim Zurück erhalten.
3. **30.10. Mietkonto:** Zinsanteil, Standardkategorie „Sonstige Werbungskosten“, Folgeimporte mit Zeitraumerkennung, Vorschläge aus Vormonaten, Umsatzübersicht mit Korrektur.
4. **30.10. Mieterverwaltung:** Belegung und Leerstand, Betriebskostenabrechnung (Rechner, PDF, Fassungen, Assistent), Mieterwechsel mit Wohnungsvorgaben, Übernahme aus Vertrag/Screenshot. Fehlerbehebungen: Wohnung beim Bearbeiten wählbar; Fehlermeldungen waren hinter Dialogen verdeckt, jetzt Toasts im Top-Layer und Fehler direkt im Formular. Danach mehrere Unterlagen und eine Seitenansicht für handschriftliche Verträge.
5. **30.10. Einnahmen:** Soll/Ist, automatische Mieter-Zuordnung. Danach „automatisch als eingegangen“ mit „Ausgeblieben?“.
6. **30.10.:** Wischen zum Löschen; Wettbewerbsanalyse (objego, immocloud, Haus & Grund).
   - **Entscheidung:** Mahnungen und Abmahnungen bauen, **keinen eigenen Mietvertrags-Generator** (Haftungs- und Pflegerisiko; geprüfte Formulare wie Haus & Grund nutzen). Vom Nutzer mit „Ok“ angenommen.
   - Umgesetzt: Mahnwesen und Abmahnungen.
7. **30.10.:** Mieterliste mit Gesamtmiete; Summe zuerst oben, **auf Wunsch nach unten verschoben**. Einnahmen-Diagramme und PDF für das Finanzamt. Kaution und Kautionsmahnung.
8. **30./31.10. Belegprüfung:**
   - Belegprüfung mit Verknüpfung zum Beleg und Prüfvermerk.
   - **Fehler:** „Beleg öffnen“ öffnete ein neues Fenster ohne Verlauf, „Zurück“ führte zur Startseite. **Behoben:** selbes Fenster, Zustand in der Adresse, Scroll wiederhergestellt. Suche auf den Zeitraum begrenzt.
   - **Fehler:** „Abgabenbescheid“ wurde nicht gefunden. **Behoben:** Begriffserweiterung, Betragszeilen.
   - **Fehler:** „+ Betrag“ war nur vorgemerkt und ging beim Öffnen anderer Belege verloren; nach der ersten Übernahme verschwand der Beleg. **Behoben:** sofort speichern, Teilübernahme sichtbar.
9. **31.10.:**
   - Frage nach Integration ins System-Dateimenü beantwortet: Teilen **an** die App existiert, Teilen **aus** der App existiert, als Dateiquelle in der Dateiauswahl ist eine PWA **nicht möglich** (nur mit nativer Android-App).
   - Mehrfachauswahl und gemeinsames Teilen im Archiv umgesetzt.

---

## 7. Aktueller Entwicklungsstand

### A. Vollständig umgesetzt (im Repository, Tests grün)

Alles mit ✅ in Abschnitt 3. Letzter Commit `c614f78` „Archiv: Mehrfachauswahl und gemeinsames Teilen mehrerer Dokumente“. 141 Tests grün.

### B. Teilweise umgesetzt

- Monatlicher Wiederherstellungstest (neue Fassung): erster erfolgreicher Serverlauf ❓.
- TypeScript-Typprüfung seit dem 29.10. nicht gelaufen (kein `tsc` in der Arbeitsumgebung). Das Frontend baut mit esbuild, die Oberflächen sind per Playwright geprüft.

### C. Beschlossen, noch nicht umgesetzt

- Keine offenen Aufträge.

### D. Bekannte Probleme und Risiken

1. **Serverstand unbekannt.**
   - Bestätigt ist ein früheres Update („Das app update wurde eingestellt“), vermutlich mit dem Suchstand vom 29.10.
   - Danach meldete der Nutzer: „Der vorherige Befehl hat nicht funktioniert“. Die erbetene Diagnose mit Screenshot ist nicht eingegangen.
   - Seitdem wurde nach jedem Commit der Update-Befehl gegeben, **ohne Rückmeldung**. Ob Commits ab `27c9dcd` auf dem Server laufen, ist ❓.
2. **Kaution über das Mietkonto:** wird bei Zuordnung zum Mieter als Miete gezählt (keine Umsatzart „Kaution“).
3. **Handschrift:** Handschriftliche Verträge sind lokal nicht auslesbar; der Ausweg ist „Selbst eintragen mit Ansicht“. Mistral-Vision wurde angeboten, ist ❓ nicht entschieden und braucht einen Schlüssel sowie die Einwilligung des Nutzers.
4. **Bescheid-Zerlegung und Mieter-Erkennung** sind nur mit synthetischen Testtexten geprüft. Ein echter Bescheid der Verbandsgemeinde und ein anonymisierter objego-Screenshot wurden erbeten, sind aber **nicht eingegangen**.
5. **Teilen-Ziel** nimmt nur PDF und Bilder an (kein Word/Excel/.eml).
6. **„Angenommene“ Mieteingänge** sind kein Zahlungsnachweis. Das Finanzamt-PDF weist sie getrennt aus; vor dem Versand sollten die Kontoumsätze bis Jahresende eingelesen sein.
7. **Grenzen und Betrieb:**
   - B2-Gratisgrenzen: 1 GB Download pro Tag, 10 GB Speicher.
   - Kein Monitoring der App selbst.
   - `/tmp` auf dem Server liegt im RAM.

### E. Offene Entscheidungen (angeboten, keine Antwort)

- Teilen-Ziel um Word, Excel und E-Mail-Dateien erweitern? 💡
- Zuordnung „Kaution“ für Kontoumsätze? 💡
- Native Android-App als Dateiquelle in der Dateiauswahl (großer Aufwand, APK)? 💡, nicht empfohlen
- Mistral-Vision für handschriftliche Verträge? 💡
- App-Icon „Register“? 💡
- Mietvertrags-Generator: ❌ nicht empfohlen. Der Nutzer hat den Vorschlag (Mahnungen und Abmahnungen zuerst) angenommen; ein Generator war danach kein Thema mehr.

### F. Zuletzt bearbeitet

Mehrfachauswahl im Archiv und in der Suche mit gemeinsamem Teilen und ZIP (`c614f78`). Davor: sofortiges Speichern in der Belegprüfung (`ce71a3f`).

---

## 8. Offene Aufgaben und Roadmap (priorisiert)

| Prio | Aufgabe | Art |
| --- | --- | --- |
| 1 | **Serverstand klären:** Update-Befehl ausführen lassen, Screenshot mit `git log --oneline -1` (erwartet `c614f78` oder neuer) und Container-Status; bei Fehlern Ursache analysieren | Kritisch/Betrieb |
| 2 | Nach dem Update auf dem Handy testen: Mieter, Einnahmen, Betriebskosten-Belegprüfung, Mehrfachauswahl/Teilen | Verifikation |
| 3 | TypeScript-Prüfung `npx tsc --noEmit` in einer Umgebung mit npm nachholen, Typfehler beheben | Technische Verbesserung |
| 4 | Backup-Prüfung: Nachtsicherungen, healthchecks.io grün, Wiederherstellungstest einmal manuell | Betrieb |
| 5 | Mit echten (anonymisierten) Unterlagen Erkennung nachschärfen: Abgabenbescheid der Verbandsgemeinde, objego-Screenshot | Unvollständig |
| 6 | Mieterdaten der Hauptstraße 15 vollständig anlegen (5 Parteien), Vermieterangaben ausfüllen, Kautionen erfassen | Nutzung |
| 7 | Entscheidungen aus 7 E einholen | Offen |
| 8 | Sicherheit: 2FA in SmartArchive, B2, netcup, GitHub; Backup-Passwort offline; Dropbox-Entwickler-App löschen; Dropbox kündigen | Betrieb |
| 9 | Optional: Kaution-Umsatzart, weitere Dateitypen im Teilen-Ziel, Mistral-Vision, App-Icon | Optional |

---

## 9. Verbindliche Entwicklungsregeln

**Kommunikation**

- Deutsch, **Sie-Form**, fachlich präzise, ohne lange Einleitung. Ergebnis zuerst.
- Fachbegriffe beim ersten Auftreten kurz erklären.
- Restrisiken ehrlich benennen, nichts beschönigen.
- Der Nutzer arbeitet am **Android-Handy mit Termius (SSH)**:
  - Serverbefehle als **kopierfertige Blöcke** mit der Anweisung „einfügen und **Enter**“.
  - Dazu das erwartete Ergebnis, was zu tun ist, wenn es abweicht, und die **Bitte um einen Screenshot**.
  - Befehle mit `&&` verketten, Ausgaben mit `tail`/`grep` kürzen.
  - Abbruch mit Strg+C erklären.
- Nach jedem Commit den Update-Befehl nennen (siehe 2.7).
- Screenshots genau lesen: auf Uhrzeit und den Befehl achten, der die Ausgabe erzeugt hat.
- Nicht unnötig nachfragen. Bei Wahlmöglichkeiten eine Empfehlung geben.

**Sicherheit und Datenschutz**

- **Keine Schlüssel oder Passwörter in den Chat** (applicationKey, Backup-Passwort, Tokens). Geheimnisse werden auf dem Server mit `read -rsp` eingegeben. Keine Screenshots von Passwörtern. Ist ein Geheimnis sichtbar geworden, Neuerzeugung empfehlen.
- **Echte Mieterlisten nicht in den Chat**: Eine Kopfzeile mit Beispieldaten genügt.
- **KI-Nutzung für Verträge und Bilder nur mit ausdrücklicher Einwilligung** (personenbezogene Daten).
- Keine Namen oder Kontaktdaten von Mietern im Änderungsprotokoll.

**Fachliche Grundregeln**

- **Keine steuerliche Berechnung oder Bewertung**, keine rechtliche Bewertung; Schwellen nur rechnerisch als Hinweis.
- **Privat und Vermietung strikt getrennt**, Bereichswechsel nie automatisch.
- **Originale bleiben unverändert**, Löschen nur in den Papierkorb.
- **Datum nur aus dem erkannten Dokumenttext.**
- **Kein Raten von Beträgen.** Unklares wird zur Auswahl oder Rückfrage.
- **Automatik nur bei hoher Sicherheit**; automatisch Erfasstes bleibt erkennbar und korrigierbar.
- KI ist optional, EU (Mistral), sendet nur Textauszüge und ist pro Ordner sperrbar.
- Keine ungefragten neuen Features. Vorschläge als Angebot formulieren.

**Technik und Code**

- **Architektur beibehalten:** Starlette, SQLite/FTS5, Worker, React/esbuild, keine zusätzlichen Frameworks, keine externen CDNs (wegen der CSP).
- **Datenbank:**
  - Änderungen **nur als neue Migration am Ende von `MIGRATIONS`**, bestehende nie ändern.
  - Einmalige Bestandskorrekturen als Startlauf in `main.py` mit Einmal-Marker.
- **Build:**
  - Assets mit Prüfsumme im Namen.
  - Bei Änderungen am Service Worker den Cache-Namen erhöhen.
  - `backend/static` mit einchecken.
- **Tests:** Zu jeder Änderung Tests ergänzen. `pytest` muss grün sein, das Frontend muss bauen. Oberflächenänderungen im Browser auf Handygröße prüfen.
- **Commits:**
  - Commit auf `main` mit deutscher Commit-Nachricht, die die Ursache nennt.
  - Abschluss mit den Zeilen `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` und `Claude-Session: …` (die Session-URL des jeweiligen Chats).
  - Danach `git fetch`, `rebase`, `push`.
- **Fehleranalyse:** erst die Ursache belegen (Logs, Screenshot, Reproduktion), dann beheben. Eigene Fehler offen benennen.
- Bei größeren Ständen die Dokumentation in `docs/` fortschreiben.

---

## 10. Technischer Übergabekontext

**Orte und Zugänge**

| Was | Wo |
| --- | --- |
| Repository | `github.com/crimepayzbrothers-source/Smartarchive` (privat, `main`) |
| Server | netcup VPS, Debian 13, Projekt `~/smartarchive` (root), Daten `~/smartarchive/data` |
| App | `https://v2202610445998547434.taila12eaf.ts.net/`, nur im Tailscale-Netz |
| Backup | restic-Repo `b2:smartarchive-can-2303:smartarchive`; Zugang `/root/.restic-env`, Passwort `/root/.restic-pass`; Log `/var/log/smartarchive-backup.log`; Cron `30 3 * * *` (Backup) und `0 5 1 * *` (Wiederherstellungstest) |

**Zentrale Funktionen**

| Bereich | Funktionen |
| --- | --- |
| Pipeline | `documents.ingest/process`, `classify.analyze/decide`, `invoices.capture_on_upload/ensure_expense`, `duplicates.find` |
| Bank | `banking.import_csv/analyze/auto_apply/decide/undo/interest_share/coverage/_tenant_match` |
| Mieter | `tenancy.save/end/delete/load/span/term_at/occupancy/property_occupancy`; `tenancy_extract.parse/match_unit/ai_parse/merge/preview_pages` |
| Einnahmen | `income.monthly_soll/payment_month/month_rows/split_parts/overview/match_by_name/link_unassigned`; `income_pdf.render` |
| Schreiben | `dunning.open_months/threshold/build/render/create/archive_letter/default_salutation`; `abmahnung.build/render/late_payments/third_workday`; `deposit.get/save/schedule/brief`; `deposit_letter.build/render/create`; Briefkopf `bk_pdf.letterhead` |
| Betriebskosten | `betriebskosten.compute/importable_expenses/add_cost/suggest_type/_checks`; `bk_receipts.scan/search_period/import_positions/parse_bescheid/expanded_match/amount_lines` |
| Suche | `search.search(..., date_from, date_to, match)` |

**Nicht ohne Weiteres ändern:**

- Migrationsreihenfolge
- SHA-256-Ablage der Originale
- Jahreszuordnung `COALESCE(paid_on, invoice_date, doc_date)`
- `EARLY_DAY = 25`
- Snapshot-Prinzip der Schreiben
- Prüfsummen-Assets
- strikte Bereichstrennung
- Bindung an `127.0.0.1:8080`
- CSP

**Risiken:**

- Einzelner VPS, abgesichert durch das B2-Backup; ohne Backup-Passwort keine Wiederherstellung.
- Gezielte Wiederherstellungen: restic speichert die Pfade relativ (`/data/files/...`, `/data/backup/...`). Den Pfad deshalb vorher mit `restic ls latest` ermitteln, nicht mit `--include /root/smartarchive/...`. Die Live-Datenbank wird vor dem Backup konsistent nach `data/backup/` kopiert; `.env` und `/root/.restic-*` sind nicht in der Sicherung.
- SQLite ist auf einen Nutzer ausgelegt.
- Die Arbeitskopie in der Claude-Umgebung existiert nur in der jeweiligen Sitzung. Ein neuer Chat muss das Repository neu einbinden.

---

## 11. Unklarheiten und fehlende Informationen

| Punkt | Wie klären |
| --- | --- |
| Läuft auf dem Server `c614f78`? Woran scheiterte „Der vorherige Befehl hat nicht funktioniert“? | Update-Befehl ausführen lassen, Screenshot |
| Typfehler im Frontend? | `npx tsc --noEmit` |
| Ist `MISTRAL_API_KEY` gesetzt? | Auf dem Server prüfen: `grep -c MISTRAL_API_KEY=. ~/smartarchive/.env` |
| Welche Mietverhältnisse, Kautionen und Vermieterangaben sind in der App erfasst? | In der App ansehen |
| Wie sieht ein echter Abgabenbescheid bzw. objego-Screenshot aus (für die Erkennung)? | Anonymisierte Datei erbitten |
| Ist die App über Chrome „App installieren“ installiert (Voraussetzung fürs Teilen-Ziel)? | Nutzer fragen |
| Backup: Läuft der Wiederherstellungstest erfolgreich? Ist healthchecks.io grün? Ist 2FA aktiviert? Ist Dropbox gekündigt? | Nutzer bzw. Server |
| Welche Rolle spielen Berliner Straße 23/32 heute (vermietet laut älteren Angaben, laut Nutzer zuletzt „nur Hauptstraße 15 vermietet“)? | Nutzer fragen |
| Was genau zur Frage des kommerziellen Potenzials geantwortet wurde | Nicht dokumentiert; es gibt keinen Beschluss |
| Lokale Git-Historie beginnt bei `55cbe22` (flacher Klon); frühere Commit-IDs nur aus der alten Übergabe | Bei Bedarf vollständig klonen |

---

## 12. Konkreter nächster Schritt

1. **Zuletzt entwickelt:** Mehrfachauswahl und gemeinsames Teilen im Archiv (`c614f78`). Davor: Belegprüfung der Betriebskosten mit Stichwortsuche und sofortigem Speichern.
2. **Offen:** Bestätigung, dass der Server aktualisiert ist und alles läuft. Ausstehende Entscheidungen aus 7 E.
3. **Als Nächstes:** Den Nutzer bitten, diesen Befehl auszuführen:
   ```
   cd ~/smartarchive && git pull && git log --oneline -1 && docker compose up -d --build 2>&1 | tail -n 25 && docker compose ps
   ```
   Erwartet wird die Zeile `c614f78 …` (oder neuer), Container `app` und `worker` „running“. Bei einer Abweichung erst die Ursache aus Log oder Screenshot klären (`docker compose logs app --tail 50`).
4. **Benötigt:** Screenshot der Ausgabe. Für Codearbeit einen Klon von `main`, Python 3.12 mit den Abhängigkeiten aus `requirements.txt` und Node/npm für den Build.
5. **Fortsetzung ohne Grundsatzdiskussion:** Architektur, Regeln (Abschnitt 9) und Fachentscheidungen sind gesetzt. Neue Wünsche nach diesem Muster umsetzen: Ursache bzw. Ist-Stand im Code prüfen → Migration (falls nötig) → Backend mit Tests → Frontend → Build → Browserprüfung auf Handygröße → Commit und Push → Update-Befehl und Zusammenfassung auf Deutsch an den Nutzer.
