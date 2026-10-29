# SmartArchive – Projektübergabe (Stand 29.10.2026, Commit `5d931ee`)

> Grundlage dieser Dokumentation: der tatsächliche Quellcode im Repository (geprüft am 29.10.2026, 100 Tests grün) und der Chatverlauf. Der allererste Teil der Entwicklung (Grundkonzept, MVP, Dropbox-Import) liegt nur als Zusammenfassung vor. Was daraus nicht sicher ableitbar ist, steht in Abschnitt 11.

---

## 1. Projektübersicht

| Punkt | Inhalt |
| --- | --- |
| Projektname | **SmartArchive** |
| Eigentümer / Nutzer | Can (Einzelnutzer, Landesbeamter, Vermieter mehrerer Objekte in Bad Sobernheim) |
| Zweck | Privates, selbst gehostetes Dokumentenarchiv, das das Dropbox-Abo (11,99 €/Monat) ablöst |
| Hauptziel | Dokumente per Foto/Upload erfassen, Texterkennung, Volltextsuche, automatische Ablage in zwei **strikt getrennten Bereichen „Privat“ und „Vermietung“**, Belege der Vermietung je Mietobjekt als Aufwand dokumentieren und für die Anlage V exportieren |
| Ausdrücklich nicht | **Keine steuerliche Berechnung oder Bewertung** (keine AfA, kein Überschuss, keine Anlage-V-Zeilenwerte). Die App dokumentiert und exportiert nur. |
| Zielgruppe | Ausschließlich Can selbst (privat). Veröffentlichung in App Stores später optional, nicht geplant. |
| Plattformen | Installierbare Web-App (PWA) auf Android (Chrome), zusätzlich Desktop-Browser |
| Nutzungskonzept | Beleg fotografieren oder hochladen, die App erkennt Text, Datum, Betrag, Absender und Objekt, legt sicher erkannte Dokumente selbst ab und stellt Unsicheres als Rückfrage in den „Eingang“ |
| Datenschutz | EU-Hosting (netcup, Nürnberg), Zugriff nur über Tailscale-VPN, optionale KI nur über Mistral (EU) |
| Stand | Version 0.1 (MVP) plus umfangreiche Erweiterungen, **produktiv im Einsatz**. Rund 3.857 Dateien und 3,3 GB wurden aus Dropbox importiert. Nächtliche verschlüsselte Sicherung nach Backblaze B2 ist eingerichtet und getestet. |
| Langfristige Vision | Vollständiger Dropbox-Ersatz mit möglichst wenig Handarbeit bei der Belegerfassung für die Vermietung |

---

## 2. Technologie und Architektur

### 2.1 Stack

| Schicht | Technologie |
| --- | --- |
| Backend | Python 3.12, **Starlette** (ASGI), uvicorn |
| Datenbank | **SQLite** (eine Datei, WAL-Modus), Volltextsuche **FTS5** mit Trigramm-Tokenizer (Fallbacks: `trigram`, `unicode61`) |
| Hintergrundverarbeitung | Eigener Worker-Prozess (`app.worker`) mit Auftragstabelle `jobs` (Priorität, Wiederholversuche) |
| Texterkennung | OCRmyPDF + Tesseract (deu, eng), Fallback Tesseract direkt. Digitale PDFs ohne OCR. Office-Dateien über LibreOffice (headless) nach PDF. |
| PDF/Bild | pypdf, img2pdf, Pillow, poppler-utils, ghostscript, qpdf, unpaper |
| Exporte | openpyxl (Excel), reportlab (PDF), csv |
| KI (optional) | Mistral API (EU), Modell `mistral-small-latest`, nur aktiv mit `MISTRAL_API_KEY` |
| Frontend | **React 19** + TypeScript, gebaut mit **esbuild** (`frontend/build.mjs`), keine weiteren Laufzeitbibliotheken, eigenes CSS |
| PWA | `manifest.webmanifest` mit Share-Target, Service Worker `sw.js` (Cache `sa-shell-v2`, nur die App-Hülle) |
| Deployment | Docker Compose (Dienste `app` und `worker` aus einem Image), netcup VPS Lite (Debian 13), Zugriff über **Tailscale Serve** (HTTPS) |
| Backup | **restic** → **Backblaze B2** (Region EU Central, Bucket `smartarchive-can-2303`), Überwachung über **healthchecks.io** |
| Repository | Privat: `github.com/crimepayzbrothers-source/Smartarchive`, Branch `main` |

**Python-Abhängigkeiten** (`backend/requirements.txt`): starlette, uvicorn[standard], pydantic, python-multipart, pillow, img2pdf, pypdf, pytesseract, openpyxl, reportlab.
**Frontend** (`frontend/package.json`): react, react-dom ^19.2; dev: esbuild ^0.25, typescript ^5.8, @types/react(-dom).

Die SQLite-Entscheidung war bewusst: Für einen Einzelnutzer ist sie schneller, braucht keinen Datenbankserver und lässt sich als eine Datei sichern.

### 2.2 Ordnerstruktur

```
backend/app/
  main.py            App, Routen, Middleware, einmalige Datenkorrekturen beim Start
  config.py          Einstellungen aus Umgebungsvariablen
  db.py              Verbindung, versionierte Migrationen (MIGRATIONS 1–8), Helfer (row/rows/audit/settings)
  seed.py            Bereiche, Ordnerbaum, Ausgabenkategorien, Standard-Einstellungen
  security.py        scrypt-Passwörter, TOTP (2FA), Sitzungen
  storage.py         Originale (SHA-256, schreibgeschützt), abgeleitete Dateien, MIME-Erkennung
  documents.py       Upload/Ingest, Verarbeitungspipeline, Volltextindex, Feldänderungen
  ocr.py             Text aus PDF, OCR, Office→PDF, Seitenbilder
  extract.py         Erkennung: Datum (mit Sicherheit), Beträge, Objekt, Absender, Dokumenttyp, Stichwörter
  classify.py        Automatische Ablage (Regel → Verlauf → KI → Stichwörter), Entscheidung, Voreinstellungen
  invoices.py        Rechnungslogik: Felder, Ausgaben automatisch erfassen, Datumsprüfung, „Aus Beleg auslesen“
  duplicates.py      Inhaltliche Dublettenerkennung und Auflösung
  titles.py          Automatische Titel
  banking.py         Kontoumsätze (CSV), Abgleich mit Rechnungen, Lernen, Entscheidungen
  exports.py         Journal, CSV, Excel, PDF-Paket (mit/ohne Belege), Gesamt-ZIP, Prüfhinweise
  search.py          FTS5-Suche mit Hervorhebung
  mistral.py         KI-Aufrufe (Dokumente, Umsätze, Rechnungsfelder)
  importer.py        Massenimport aus Dropbox oder Ordner (CLI)
  import_mappings/dropbox.json  Ordnerzuordnung für den Import
  worker.py          Auftragsabarbeitung (process, reclassify)
  backup.py          Konsistente DB-Kopie für das Backup
  api/ auth.py, docs.py, bank.py, realestate.py, meta.py, common.py
backend/static/      Gebaute PWA (wird vom Server ausgeliefert, im Git eingecheckt)
backend/tests/       pytest (100 Tests)
frontend/src/        App.tsx, main.tsx, api.ts, lib.tsx, extract.ts, scan.ts, styles.css, pages/, components/
frontend/public/     sw.js, manifest.webmanifest, icon.svg
scripts/             backup.sh, restore-test.sh
docs/                diese Übergabe
data/ (Server)       smartarchive.sqlite3, files/originals/…, files/derived…, backup/, cache/  (nicht im Git)
```

### 2.3 Zusammenspiel

1. **Upload** (`POST /api/upload`): Die Datei wird per SHA-256 als unveränderliches Original gespeichert. Byte-gleiche Dateien werden sofort als Dublette erkannt. Ein Dokument mit `status='processing'` und ein Job `process` entstehen.
2. Der **Worker** führt `documents.process` aus:
   1. Textextraktion: digital, OCR oder Office→PDF, durchsuchbares PDF als abgeleitete Datei.
   2. Inhaltliche Dublettenprüfung (`duplicates.find`).
   3. Einordnung (`classify.analyze`), dann Entscheidung (`classify.decide`): ablegen (`filed`) oder Eingang (`inbox`) mit Vorschlag.
   4. Bei Dublettenverdacht: `duplicates.mark`, nichts ablegen, nichts zählen.
   5. Ausgabe erfassen (`invoices.capture_on_upload` / `ensure_expense`).
   6. Volltextindex aktualisieren.
3. Das **Frontend** fragt `/api/inbox`, `/api/documents` usw. ab. Entscheidungen des Nutzers laufen über PATCH/POST-Endpunkte. Jede Änderung landet im `audit_log`.
4. **Import-Läufe** (`app.importer`) erzeugen Dokumente mit `preset` (fester Zielordner aus dem Mapping). Für sie entfällt die Einordnung.

### 2.4 Authentifizierung

- Ein Benutzerkonto. Einrichtung beim ersten Aufruf mit `SETUP_TOKEN` aus `.env`.
- Passwort mit scrypt (n=2^14, r=8, p=1). Optional TOTP-2FA (`/api/auth/totp/*`).
- Sitzungscookie `sa_session` (HttpOnly, SameSite, Secure). In der Datenbank liegt nur der SHA-256 des Tokens. Laufzeit 30 Tage (`SESSION_DAYS`).
- CSRF-Schutz: Schreibende Anfragen brauchen den Header `X-Requested-With: SmartArchive`. Alle Änderungen werden protokolliert. Gelöschte Dokumente gehen in den Papierkorb (`deleted_at`).

### 2.5 Konfiguration (`.env`)

`SETUP_TOKEN`, `MISTRAL_API_KEY` (optional), `MISTRAL_MODEL`, `MISTRAL_BASE_URL`, `COOKIE_SECURE` (Standard 1), `MAX_UPLOAD_MB` (Standard 40), `SESSION_DAYS` (30), `DATA_DIR` (/data), `STATIC_DIR`. Für den Import kommt `DROPBOX_TOKEN` hinzu, nur temporär.

### 2.6 Frontend-Zustand

Kein State-Management-Framework. Hash-Routing (`#/…`) mit `useRoute`, Datenladen über den Hook `useLoad`, API-Helfer `get/post/patch` in `api.ts`, Toasts über `ToastProvider`. Das Farbschema wird in `localStorage` gespeichert.

### 2.7 Build, Test, Deployment

- **Frontend bauen:** `cd frontend && npm install && npm run build`. Ergebnis landet in `backend/static`, mit Prüfsummen-Dateinamen `main-[hash].js/.css`, damit Updates auf dem Handy ankommen.
- **Tests:** `cd backend && python -m pytest -q` (100 Tests).
- **Server-Update:** `cd ~/smartarchive && git pull && docker compose up -d --build`. Migrationen laufen beim Start automatisch.
- Die App lauscht nur auf `127.0.0.1:8080`. Tailscale stellt sie per HTTPS unter `https://v2202610445998547434.taila12eaf.ts.net/` bereit.
- Im Docker-Image läuft die App als Benutzer `smartarchive` (UID 1000). Der Datenordner `./data` ist als `/data` eingebunden.

---

## 3. Vollständiger Funktionsumfang

Status-Legende: ✅ implementiert (Code vorhanden, getestet, gepusht) · 🟡 teilweise · 📋 beschlossen · 💡 vorgeschlagen · ❌ verworfen

### 3.1 Erfassen

| Funktion | Verhalten | Status |
| --- | --- | --- |
| Foto aufnehmen | Kamera, mehrere Fotos ergeben ein PDF | ✅ |
| **Scanner nach Aufnahme** | Automatische Kantenerkennung, Ecken manuell verschiebbar („Ecken prüfen“), Zuschnitt und Perspektivkorrektur (`Scanner.tsx`, `scan.ts`). Das Originalfoto bleibt zusätzlich als `capture` erhalten (`captures`/`capture_index` im Upload). | ✅ |
| PDF-/Bild-Upload | Auch Drag-and-drop. Akzeptiert PDF, JPEG, PNG, WebP, TIFF und beim Import Office-Dateien. | ✅ |
| Teilen aus Android-Apps | PWA-Share-Target `/share-target`, Service Worker puffert die Dateien | ✅ |
| **Upload direkt zum Mietobjekt** | Bereich Vermietung im Upload-Dialog: Objekt-Chips zur Auswahl. Gibt es nur ein Objekt, wird es automatisch gewählt. Das gewählte Objekt (`property_id`) wird übernommen, das Dokument direkt abgelegt, der Ordner ist prüfbar unter „Automatisch abgelegt“. | ✅ |
| Bereichswahl beim Upload | `upload_area`: `auto` / `privat` / `vermietung` | ✅ |

### 3.2 Verarbeitung und Ablage

| Funktion | Verhalten | Status |
| --- | --- | --- |
| Originale unveränderlich | SHA-256, schreibgeschützt gespeichert, abgeleitete PDFs getrennt | ✅ |
| Texterkennung | Digitale PDFs direkt, sonst OCR. Mindestens 30 Zeichen pro Seite gelten als Textlage. | ✅ |
| **Automatische Ablage** | Reihenfolge: 1. eigene Regeln (Sicherheit 0,99), 2. bestätigter Verlauf je Absender, 3. KI (optional), 4. Stichwörter. Automatisch abgelegt wird ab `auto_threshold` 0,9, ein Vorschlag erscheint ab `suggest_threshold` 0,5. | ✅ |
| Blocker für die Automatik | Keine Kategorie erkannt, Sicherheit unter der Schwelle, Bereich `auto` ohne Regel/Verlauf, Vorschlag im anderen Bereich als gewählt, Mietobjekt unklar (wenn Objekte existieren), Automatik ausgeschaltet | ✅ |
| **Bereichswechsel nie automatisch** | Privat ↔ Vermietung entscheidet nur der Nutzer | ✅ (Grundregel) |
| KI pro Ordner sperrbar | Voreingestellt gesperrt: Gesundheit, Familie. Bei gesperrter KI wird nur lokal per Stichwörtern geprüft. Die KI bekommt höchstens 6.000 Zeichen Text, nie die Datei. Ihr Begründungszitat muss wörtlich im Dokument stehen, sonst wird die Sicherheit gedeckelt. | ✅ |
| Lernen aus Korrekturen | Tabelle `corrections`, gelernte Regeln (`rules.origin='learned'`) | ✅ |
| Rückgängig | `POST /api/documents/{id}/undo` für automatische Ablagen | ✅ |
| **Automatische Titel** | `titles.make_title`: aus spezifischem Dokumenttyp, Betreff/Position, Absender und Datum. Generische Titel und Dateinamen („IMG_…“, „Scan…“) werden ersetzt (`is_generic`), eigene Titel nie. | ✅ |
| **Inhaltliche Dublettenprüfung** | Siehe 5.6. Treffer bleiben im Eingang unter „Mögliche Duplikate“ mit drei Optionen: **Überschreiben** (neue Dateien ersetzen das vorhandene Dokument, alte Originale bleiben als `replaced` erhalten), **Verwerfen** (Upload in den Papierkorb), **Behalten** (kein Duplikat) | ✅ |

### 3.3 Datum

| Funktion | Verhalten | Status |
| --- | --- | --- |
| **Datum nur aus dem erkannten Text** | Nie aus Upload-Datum, nie aus Dateiname oder Titel | ✅ |
| Sicherheitsstufe | `date_status` = `sure` / `unsure`, `date_candidates` (bis 6) mit Zeile, beschriftet, Fälligkeitsdatum | ✅ |
| **Rückfrage „Datum prüfen“** | Im Eingang erscheinen Belege ohne Datum oder mit unsicherem Datum. Vorschlag mit Bestätigung oder Auswahl aus den Kandidaten oder Eingabe (`DateQuestion.tsx`, `POST /api/documents/{id}/date`). Die Bestätigung übernimmt das Datum auch in die Ausgaben. | ✅ |

### 3.4 Ausgaben (Vermietung)

| Funktion | Verhalten | Status |
| --- | --- | --- |
| **Sofortige Erfassung beim Upload** | Ein erkannter Rechnungsbetrag wird sofort als Ausgabe angelegt (`capture_on_upload`). Solange das Objekt unklar ist, als `entwurf`. Mit der Ablage in Vermietung und bekanntem Objekt wird sie `bestaetigt` und zählt. Bei Ablage in Privat wird sie gelöscht. | ✅ |
| **Automatisch zählen** | Entscheidung „Sofort zählen“: automatisch ausgelesene Beträge zählen sofort, gekennzeichnet als `origin='auto'` („automatisch ausgelesen“) | ✅ |
| **Zahlungsdatum = Rechnungsdatum** | Ohne „bezahlt am“ oder Barzahlung im Beleg wird das Rechnungsdatum als Zahlungsdatum angenommen (`paid_assumed=1`, Anzeige „angenommen“). Jederzeit korrigierbar. Ein passender Kontoumsatz ersetzt es durch das Buchungsdatum. | ✅ |
| Barzahlung erkennen | „bar bezahlt“, Kassenbon, EC-/Kartenzahlung (`_POS_PAYMENT`, `_RECEIPT_PAYMENT`): Das Zahlungsdatum ist dann sicher | ✅ |
| Ausgabe manuell | Am Dokument: Betrag, Rechnungs-/Zahlungsdatum, Empfänger, Kategorie, Objekt/Einheit, Notiz, Status `entwurf`/`bestaetigt`/`ungeklaert` | ✅ |
| **„Aus Beleg auslesen“** | Knopf im Ausgabenformular: füllt Datum, Betrag, Empfänger, Kategorie, Rechnungsnummer (`GET /api/documents/{id}/extract`, lokal plus optional KI) | ✅ |
| **Betrags-Auswahlliste** | Kein Raten des Höchstbetrags mehr. Ist der Endbetrag nicht eindeutig beschriftet, zeigt `AmountPicker.tsx` alle erkannten Beträge mit Kennzeichnung (z. B. „Gesamt“, „MwSt“, „Netto“) zur Auswahl. | ✅ |
| **Rechnungs-Abfrage mit Objektzuordnung** | `InvoiceCard.tsx`: fragt bei Rechnungen das Objekt ab, oder „keine Ausgabe“ (`no_expense`, z. B. Angebot oder privat bezahlt) | ✅ |
| **Nacherfassung** | „Rechnungen ohne Ausgabe“ in Ausgaben (`MissingPanel`, `/api/expenses/missing`, `/drafts`) | ✅ |
| Grundsteuer/Abgaben | Bescheide mit Kategorie `grundsteuer`/`grundbesitzabgaben` werden ebenfalls erfasst | ✅ |
| Jahreszuordnung | `COALESCE(paid_on, invoice_date, doc_date)`, **kein Upload-Datum** | ✅ |
| Prüfhinweise | Objekt fehlt, Erhaltungsaufwand nur aus Kontoumsatz ohne Beleg, ungeklärt, ohne Zahlungsdatum, angenommene Zahlungsdaten, Dubletten, anschaffungsnahe Herstellungskosten (Hinweis § 6 Abs. 1 Nr. 1a EStG, keine Berechnung) | ✅ |

### 3.5 Mietkonto (Bank)

| Funktion | Verhalten | Status |
| --- | --- | --- |
| CSV-Import | Commerzbank-Export (reines Mietkonto). Spaltenerkennung für gängige deutsche Banken, Kodierung automatisch, Duplikatschutz per `fingerprint` | ✅ |
| Abgleich | 1. passende erfasste Rechnung (Betrag, Name) → `beleg`, 2. Verlauf je Gegenpartei (`party_key`: Gläubiger-ID > IBAN > Name), 3. KI (höchstens 40 je Import), 4. Stichwörter (Miete, Darlehen, Hausgeld) | ✅ |
| Automatik | Sichere Treffer (≥ 0,9) aus Beleg oder Verlauf werden angewendet (Status `auto`, zur Kontrolle im Eingang). Der Rest steht unter „Umsätze klären“ (`TxCard.tsx`). | ✅ |
| Entscheidung | Art: ausgabe / einnahme / ignoriert, Objekt, Kategorie, Teilbetrag (z. B. nur Zinsanteil). Rückgängig möglich. | ✅ |
| Online-Banking direkt (FinTS/PSD2) | Besprochen. Umgesetzt wurde stattdessen der CSV-Import. | ❌ / nicht umgesetzt |

### 3.6 Archiv, Suche, Objekte

| Funktion | Status |
| --- | --- |
| Archiv nach Bereich/Ordner, Filter Objekt, Jahr, Tag, Favorit | ✅ |
| Volltextsuche mit Wortteilen und Hervorhebung | ✅ |
| Dokumentansicht: Seitenbilder, Text, Felder ändern, neu verarbeiten (`/run/{kind}`) | ✅ |
| **PDF im nativen Viewer öffnen und teilen** (Web Share API, `FileActions` in `DocumentPage.tsx`) | ✅ |
| Mietobjekte und Einheiten (Stammdaten, Ausgaben nach Jahr, Dokumente je Objekt) | ✅ |
| Ordner, Stichwörter und KI-Sperre je Ordner, Regeln, Korrespondenten verwalten | ✅ |

### 3.7 Exporte

| Export | Inhalt | Status |
| --- | --- | --- |
| CSV | Journal, Semikolon, Excel-tauglich | ✅ |
| Excel | Übersicht Objekt × Monat, alle Positionen, ein Blatt je Objekt mit Monats-Zwischensummen, Blatt „Offen“ | ✅ |
| PDF mit Belegen | Übersicht, Halbjahrestabellen, danach alle Belege, jeweils mit Belegnummer im zusätzlichen Kopfrand (`STAMP_BAND=22`) | ✅ |
| **PDF ohne Belege** | Nur Übersicht und Tabellen, überlappungsfrei (umbrechende Zellen, per Test mit pdfplumber geprüft) | ✅ |
| Sortierung | Chronologisch, Spalten: Lfd. Nr., Beleg-Nr., Datum, Monat, Jahr, Zahlungsdatum, Rechnungsdatum, Jahr nach, Objekt … mit durchgehender Belegnummer | ✅ |
| Gesamtexport ZIP | Alle Originale, durchsuchbare PDFs, Metadaten | ✅ |

### 3.8 Betrieb

| Funktion | Status |
| --- | --- |
| Dropbox-Import (CLI `plan`/`run`/`status`, fortsetzbar, Mapping-Datei) | ✅, durchgeführt (ca. 3.857 Dateien) |
| Nächtliches Backup 3:30 Uhr (restic → B2, Aufbewahrung 14 Tage / 8 Wochen / 24 Monate) | ✅, läuft und ist verifiziert |
| Ausfall-Mail über healthchecks.io (Start/Erfolg/Fehler mit Log) | ✅, eingerichtet |
| Monatlicher Wiederherstellungstest (DB zurückholen und prüfen, Dateiliste abgleichen, rotierend 1/12 der Daten lesen) | ✅ im Code, erster Lauf mit neuer Version ausstehend (siehe 7 F) |
| Verwaiste restic-Sperren vor jedem Backup entfernen | ✅ |
| 2FA, Passwort ändern, Farbschema (System/Hell/Dunkel), Papierkorb | ✅ |

---

## 4. Benutzeroberfläche und UX

### 4.1 Gestaltung (implementiert)

- **Farben** (CSS-Variablen in `styles.css`): Grundton Petrol `--petrol #0d5c66` (stark `#0a4a52`, weich `#d8e8e8`), Papier `--paper #eef1f0`, Fläche `#ffffff`, Text `--ink #15202a`, Linien `#d3dbd8`. **Bereichsfarben:** Privat Blau `--privat #3b5a8f`, Vermietung Grün `--vermietung #4a7a33`. Hinweis Ocker `--ochre #a8760f`, Fehler `--danger #b0402c`, OK `--ok #2f7a55`. Dunkelmodus vorhanden.
- **Typografie:** Systemschriften (Segoe UI Variable / Roboto / system-ui), Überschriften in eigener Display-Variante.
- **Radien** 6/10/16 px, dezente Schatten, Navigationshöhe 64 px.
- **Sprache der Oberfläche:** Deutsch, Anrede mit „Sie“.
- **Logo:** `brand-mark` aus drei Streifen. Manifest: Name „SmartArchive“, Kurzname „Archiv“, Themenfarbe `#0d5c66`.

### 4.2 Navigation

- **Mobil:** Kopfzeile mit Logo, Suche und Einstellungen. Untere Navigation mit Start, Eingang (Zähler-Badge), Archiv, Objekte, Ausgaben, dazu der mittige Kamera-Knopf (FAB) „Beleg erfassen“.
- **Desktop:** Seitenleiste mit denselben Punkten.
- **Routen:** `#/` Start, `#/eingang`, `#/archiv` (`?bereich=`, `?objekt=`), `#/suche`, `#/objekte`, `#/objekt/{id}`, `#/ausgaben` (`?objekt=`), `#/dokument/{id}`, `#/einstellungen`, `#/teilen`.

### 4.3 Screens

| Screen | Inhalt |
| --- | --- |
| Login | Einrichtung (Setup-Code, Benutzer, Passwort), Anmeldung mit optionalem TOTP |
| Start | Zuletzt hinzugefügt, Ausgaben Vermietung des laufenden Jahres |
| **Eingang** | Gruppen in dieser Reihenfolge: Nacharbeit (Fehler), Wird verarbeitet, Bitte ablegen, **Mögliche Duplikate**, **Datum prüfen**, **Umsätze klären**, Umsätze automatisch zugeordnet, Automatisch abgelegt |
| Archiv/Suche | Liste mit Filtern, Treffer hervorgehoben |
| Dokument | Vorschau, „Öffnen“/„Teilen“, Angaben bzw. Ablage (im Eingang), Ausgaben mit „Aus Beleg auslesen“ |
| Objekte / Objekt | Liste; Stammdaten, Einheiten, Ausgaben nach Jahr, Dokumente |
| **Ausgaben** | Jahr wählen, Zu prüfen (Hinweise), Ohne Datum, Export (CSV, Excel, PDF mit und ohne Belege), Summen je Objekt, Ohne Zahlungsdatum, Mietkonto (CSV-Import, `BankPanel`), Rechnungen ohne Ausgabe |
| Einstellungen | Automatische Ablage und KI, Ordner (KI-Chip je Ordner), Regeln (auch „Aus Ihren Korrekturen“), Konto und Sicherheit (2FA, Passwort), Darstellung, Daten (Gesamtexport, Papierkorb) |

### 4.4 Wiederverwendbare Komponenten

`ui.tsx` (Sheet/Dialog, Icon, Empty, Dokumentkarte mit Highlight, Toasts), `UploadSheet`, `Scanner`, `Suggestion`, `InvoiceCard`, `DateQuestion`, `DuplicateCard`, `AmountPicker`, `TxCard`.
Bekannte Besonderheit: Bei verschachtelten Dialogen wird `stopPropagation` benötigt, damit das Schließen eines inneren Dialogs nicht das UploadSheet schließt (behoben).

### 4.5 Designideen (nicht umgesetzt)

- **App-Icon:** Empfohlen wurde **Option A „Register“**. Die Umsetzung ist angeboten, **aber nicht beauftragt und nicht umgesetzt** (💡). Derzeit sind `icon.svg`, `icon-192.png`, `icon-512.png` und `icon-maskable-512.png` (MVP-Icon) in `frontend/public` vorhanden und im Manifest eingetragen. Für Option A müssten diese Dateien ersetzt werden.

---

## 5. Datenstrukturen und Geschäftslogik

### 5.1 Datenmodell (SQLite, `PRAGMA user_version` = 8)

| Tabelle | Zweck / wichtige Felder |
| --- | --- |
| `users`, `sessions`, `settings` | Konto, Sitzungen (Token-Hash), Schlüssel-Wert-Einstellungen (JSON) |
| `areas` | `privat`, `vermietung` (+ `ai_enabled`) |
| `categories` | Ordnerbaum je Bereich (`parent_id`), `keywords`, `ai_enabled`, `sort` |
| `correspondents` | Absender (+ `aliases`) |
| `properties`, `units` | Mietobjekte (Adresse, Art, Fläche, Anteil, Status, Kaufdatum, Anschaffungskosten, Stichwörter, archiviert) und Einheiten |
| `documents` | Bereich, Ordner, Absender, Objekt/Einheit, `title`, `doc_type`, `doc_date`, `upload_area`, `status` (processing/inbox/filed/error), `filed_by` (user/rule/history/ai/keyword), `confirmed`, `confidence`, `favorite`, `text_source`, `deleted_at`; ab Migr. 2: `source_path`, `preset`; 5: `no_expense`; 7: `date_status`, `date_candidates`; 8: `duplicate_of`, `duplicate_reason` |
| `files` | Rollen: `original`, `capture`, `pdf`, `searchable`, `thumb`, `replaced`; `sha256`, `mime`, `path` |
| `document_text`, `docs_fts` | Volltext und Seiten, FTS5-Index (title, content, meta) |
| `tags`, `document_tags` | Schlagwörter |
| `suggestions` | Vorschläge je Dokument (source, payload JSON, confidence, reason, evidence, status) |
| `expenses` | `document_id`, `property_id`, `unit_id`, `invoice_date`, `paid_on`, `amount_cents`, `payee`, `category_key`, `note`, `status` (entwurf/bestaetigt/ungeklaert); 3: `origin` (user/auto/bank); 6: `paid_assumed` |
| `rules`, `corrections` | Ablageregeln (text/correspondent/filename) und gelernte Korrekturen |
| `jobs` | Aufträge (`process`, `reclassify`), Status, Versuche, `priority` |
| `audit_log` | Protokoll aller Änderungen |
| `import_items` | Importstand je Quelldatei |
| `bank_accounts`, `bank_imports`, `transactions` | Mietkonto, Importe, Umsätze (Betrag negativ = Ausgabe, `party_key`, `status` open/auto/done, `kind`, Verknüpfung zu Ausgabe/Dokument, `suggestion` JSON) |

**Beträge** werden immer in Cent (Integer) gespeichert. **Datumsangaben** als ISO-String `YYYY-MM-DD`, Zeitstempel als UTC-ISO.

### 5.2 Ausgabenkategorien (Anlage-V-Gruppen, `seed.EXPENSE_CATEGORIES`)

`schuldzinsen`, `geldbeschaffung`, `erhaltung_sofort`, `erhaltung_verteilt` (§ 82b EStDV), `grundsteuer`, `grundbesitzabgaben`, `heizung`, `versicherung`, `hausnebenkosten`, `verwaltung`, `hausgeld_laufend`, `fahrtkosten`, `sonstige`, `nicht_sofort` (`deductible=False`, wird im Export getrennt ausgewiesen).

### 5.3 Standard-Ordner (`seed.DEFAULT_TREE`)

- **Privat:** Finanzen und Banken (Kontoauszüge, Depot), Versicherungen, Verträge (Strom und Gas, Telefon und Internet), Steuern, Fahrzeuge, Gesundheit (KI aus), Familie (KI aus), Arbeit, Sonstiges.
- **Vermietung:** Rechnungen und Belege, Versicherungen, Grundbesitzabgaben, Hausgeld und WEG, Nebenkosten, Finanzierung, Mietverträge und Mieter, Kauf und Objektunterlagen, Sonstiges.

### 5.4 Einstellungen (Standard)

`auto_file=true`, `auto_threshold=0.9`, `suggest_threshold=0.5`, `ai_enabled=true` (wirkt nur mit Schlüssel). Einmal-Marker für Datenkorrekturen beim Start: `expense_autofill_v1`, `expense_dates_v2`, `paid_assumed_v1`, `date_status_v1` (in `main.py`: `_autofill_expenses`, `_fix_dates`, `_assume_paid`, `_assess_dates`).

### 5.5 Datumserkennung (`extract.date_analysis`)

- Formate: `TT.MM.JJJJ` (auch mit `/`, `-`, `,`, zweistellige Jahre), ISO, „12. März 2026“. OCR-Korrektur (`O→0`, `I/l/|→1`) mit `_ocr_fix`.
- Priorität: Beschriftung „Rechnungsdatum“ > „Datum“/„vom“ > „Leistungsdatum“, auch in der Zeile davor, sonst das erste Datum im Kopfbereich.
- Gemieden werden Daten nach „bis, fällig, zahlbar, gültig, geb., ab, zum, spätestens, Frist“.
- **Sicher**, wenn nur ein Datum vorkommt oder ein eindeutig beschriftetes Datum ohne gleichrangige Konkurrenz. Sonst `unsure`, dann Rückfrage.
- `date_from_name` (Dateiname) und `largest_amount` existieren noch im Code, werden aber **bewusst nicht mehr verwendet**.

### 5.6 Dublettenerkennung (`duplicates.find`)

Verglichen wird nur mit nicht gelöschten, fertig verarbeiteten Dokumenten. Es braucht mindestens 30 Zeichen Text. Die drei Regeln mit Rangfolge:
1. Gleiche **Rechnungsnummer** (mindestens 4 Zeichen nach Normalisierung) und dazu gleicher Betrag oder mindestens 35 % Textähnlichkeit (Score 3+sim).
2. Gleicher **Endbetrag und gleiches Datum** (auch über `expenses.invoice_date`) und mindestens 30 % Textähnlichkeit (2+sim).
3. Bei gleichem Datum und mindestens 80 % Textähnlichkeit, nur wenn 1 und 2 nichts finden (1+sim).

Die Ähnlichkeit ist ein Jaccard-Wert über Wort-3-Gramme (je höchstens 6.000 Zeichen). Byte-gleiche Dateien fängt schon der Upload ab (`documents.find_duplicate`).

### 5.7 Ausgaben-Automatik (Regeln)

- Als Rechnung gelten die Dokumenttypen `Rechnung`, `Kassenbon`, `Quittung`, dazu `Bescheid` nur bei Grundsteuer oder Grundbesitzabgaben.
- `capture_on_upload`: nicht bei Privat, nicht bei `no_expense`, nur wenn noch keine Ausgabe am Dokument hängt und Bereich oder Objekt auf Vermietung deuten. Status `entwurf`, `origin='auto'`.
- `ensure_expense` (bei Ablage): Vermietung und Objekt bekannt, dann wird ein Entwurf zu `bestaetigt` befördert oder eine neue Ausgabe angelegt. Bei Ablage in Privat oder `no_expense` werden die `auto`-Ausgaben gelöscht.
- Zahlungsdatum: aus dem Beleg, sonst Rechnungsdatum mit `paid_assumed=1`. Ein Kontoumsatz setzt das Buchungsdatum mit `paid_assumed=0`. Beim Rückgängigmachen wird der vorige Wert wiederhergestellt.
- **Grundsatz:** Kein Betrag wird geraten. Ohne eindeutig beschrifteten Endbetrag folgt die Auswahlliste.

### 5.8 Validierung und Fehlerbehandlung

- Upload-Größe höchstens `MAX_UPLOAD_MB`. MIME-Typ per Sniffing.
- Worker: fehlgeschlagene Jobs werden wiederholt (`attempts`), hängende Jobs zurückgesetzt (`recover_stale`). Dokumente mit Fehler stehen im Eingang unter „Nacharbeit“.
- Transaktionen mit `BEGIN IMMEDIATE` (`db.tx`), Fremdschlüssel aktiv.

---

## 6. Entwicklungshistorie (Kurzfassung, chronologisch)

1. **MVP:** Archiv mit zwei Bereichen, OCR, Suche, automatische Ablage, Objekte, Ausgaben, Exporte, Login mit 2FA, PWA, Docker und Tailscale auf netcup.
2. **Dropbox-Import** mit Ordner-Mapping, Office-Dateien, Auftragspriorität. Durchgeführt, danach rund 1.146 OCR-Jobs abgearbeitet.
3. **Rechnungs-Abfrage** mit Objektzuordnung, Ausgabe-Entwürfe, PDF öffnen und teilen.
4. **Mietkonto:** CSV-Import der Commerzbank mit Abgleich und Rückfrage. Statt Direktanbindung ans Online-Banking.
5. **Scanner** (Kanten, Zuschnitt, Entzerrung). Fehler: Updates kamen auf dem Handy nicht an. **Lösung:** Dateinamen mit Prüfsumme und Service Worker v2. Das muss so bleiben.
6. **Automatische Titel.**
7. **Nacherfassung** von Rechnungen ohne Ausgabe. Danach **automatische Erfassung, die sofort zählt** (Entscheidung des Nutzers).
8. **Fehler:** Belege ohne Datum landeten im Upload-Jahr. **Upload-Datum als Fallback entfernt.** Danach auf Wunsch auch der **Dateiname als Datumsquelle entfernt**. Ersatz ist die Rückfrage.
9. **Exporte** chronologisch mit Monat, Jahr und durchgehender Belegnummer.
10. **Zahlungsdatum = Rechnungsdatum** als Annahme, gekennzeichnet und korrigierbar.
11. **PDF ohne Belege**, überlappungsfrei. Belegstempel in einen eigenen Rand verschoben.
12. **Betrag sofort beim Upload** erfassen, Knopf **„Aus Beleg auslesen“**.
13. **Höchstbetrag-Raten verworfen**, weil es Beispielbeträge erwischte. Ersatz ist die **Auswahlliste**.
14. **Upload direkt zum Mietobjekt**, Datum mit Sicherheitsstufe und Bestätigung.
15. **Inhaltliche Duplikatprüfung** mit Überschreiben, Verwerfen oder Behalten.
16. **Backup:** zuerst Hetzner Storage Box geplant, **ersetzt durch Backblaze B2 (kostenlos, 10 GB)**. B2-Anmeldung scheiterte zuerst mit 401 (keyID und Schlüssel passten nicht zusammen), gelöst mit einem neuen Application Key. Erste Sicherung am 28.10.2026, die Nachtsicherung lief am 29.10. erfolgreich.
17. **healthchecks.io-Benachrichtigung**. Der Wiederherstellungstest wurde umgebaut, weil `/tmp` auf Debian 13 im RAM liegt und die Vollkopie daran scheiterte. **Verwaiste Sperren** werden vor dem Backup entfernt.

**Verworfen bzw. ersetzt:** Upload-Datum als Datumsquelle, Dateiname als Datumsquelle, automatisches Übernehmen des größten Betrags, Hetzner Storage Box als Backup-Ziel, Vollwiederherstellung nach `/tmp` im Test, Online-Banking-Direktanbindung (nicht umgesetzt, CSV stattdessen).

---

## 7. Aktueller Entwicklungsstand

### A. Vollständig umgesetzt
Alle Funktionen mit ✅ in Abschnitt 3. Letzter Code-Commit: `5d931ee`. 100 Tests grün. Die Server-Version entspricht `5d931ee`, sofern der Nutzer zuletzt `git pull` ausgeführt hat. Für die Skriptänderungen ist kein Neubau nötig, weil die Backup-Skripte auf dem Host laufen.

### B. Teilweise umgesetzt
- **Monatlicher Wiederherstellungstest (neue Fassung):** im Code und im Cron (`0 5 1 * *`), lokal getestet. **Auf dem Server noch nicht erfolgreich gelaufen.** Der erste Versuch am 29.10. scheiterte, weil das B2-Download-Kontingent (1 GB/Tag frei) durch den vorherigen Vollrestore aufgebraucht war. Die eigentliche Datenintegrität ist trotzdem belegt: Der vorherige Lauf hat alle 3,3 GB heruntergeladen und mit Prüfsummen verifiziert, nur das Ablegen in `/tmp` scheiterte.

### C. Beschlossen, noch nicht umgesetzt
- Keine offenen Code-Aufträge.

### D. Bekannte Probleme und Risiken
- **B2-Gratisgrenze:** höchstens 1 GB Download pro Tag. Eine Vollwiederherstellung im Ernstfall braucht ein angehobenes Limit unter „Caps & Alerts“ (rund 1 Cent/GB).
- Ab etwa 12 GB Archivgröße übersteigt der monatliche Teiltest (1/12) das Tageskontingent. Ab rund 10 GB ist auch der kostenlose B2-Speicher voll.
- `restic check` und `unlock` erzeugen Class-B-Transaktionen (2.500/Tag frei). Im Normalbetrieb ist das unkritisch.
- Kein Monitoring der App selbst, nur des Backups.
- Das Restore-Test-Kriterium „Originaldateien ≥ Dokumente“ könnte bei Dokumenten ohne Original falsch Alarm schlagen. Bisher ist kein solcher Fall bekannt.

### E. Offene Entscheidungen
- App-Icon Option A „Register“ umsetzen? (Nutzer hat nicht bestätigt.)
- Mistral-API-Schlüssel eintragen (KI-Einordnung)? Optional, Status unbekannt.

### F. Zuletzt bearbeitet
Backup-Härtung: healthchecks.io-Ping in `backup.sh`, Wiederherstellungstest ohne Vollkopie, `restic unlock` vor dem Backup. Der Nutzer sollte zuletzt den Abbruch mit Strg+C ausführen, dann `git pull` und den Check über `curl "$HEALTHCHECK_URL"` wieder auf grün setzen. **Ob er das getan hat, ist nicht bestätigt.**

---

## 8. Offene Aufgaben und Roadmap (priorisiert)

| Prio | Aufgabe | Art | Abhängigkeit |
| --- | --- | --- | --- |
| 1 | Prüfen: Nachtsicherung vom 30.10. vorhanden (`restic snapshots`), healthchecks.io grün | Betrieb | – |
| 2 | Wiederherstellungstest einmal manuell ausführen: `. /root/.restic-env && bash scripts/restore-test.sh` (frühestens nach dem Kontingent-Reset um 1:00 Uhr) | Unvollständig | 1 |
| 3 | Backup-Passwort (`/root/.restic-pass`), keyID und applicationKey offline sichern (Passwortmanager und Papier) | Sicherheit | – |
| 4 | **2FA aktivieren** in SmartArchive (Einstellungen → Konto und Sicherheit), bei Backblaze, netcup und GitHub | Sicherheit | – |
| 5 | **Dropbox-Entwickler-App löschen** (dropbox.com/developers/apps) | Sicherheit | – |
| 6 | **Dropbox kündigen**, erst nach einigen erfolgreichen Nachtsicherungen | Ziel | 1, 2 |
| 7 | Optional: Mistral-Schlüssel, Trainingsnutzung abschalten, Auftragsverarbeitungsvertrag | Erweiterung | – |
| 8 | Optional: App-Icon „Register“ umsetzen | Design | Entscheidung |

---

## 9. Verbindliche Entwicklungsregeln

**Kommunikation**
- Deutsch, **Sie-Form**. Fachlich präzise, ohne unnötige Einleitung.
- Der Nutzer arbeitet am **Android-Handy mit Termius (SSH)**. Serverbefehle kommen deshalb als **kopierfertige Blöcke** mit klarer Anweisung („einfügen und **Enter**“) und mit Angabe, welches Ergebnis erscheinen soll und was zu tun ist, wenn nicht. Bitte um einen Screenshot zur Kontrolle.
- Nicht unnötig nachfragen. Bei Wahlmöglichkeiten eine Empfehlung geben.

**Sicherheit**
- Der Nutzer darf **keine Schlüssel oder Passwörter** in den Chat schicken: kein applicationKey, kein Backup-Passwort, keine Tokens.
- Geheimnisse werden auf dem Server über `read -rsp` eingegeben (unsichtbar). **Keine Screenshots von Passwörtern.**
- Ist ein Geheimnis sichtbar geworden, Neuerzeugung empfehlen.

**Fachliche Grundregeln (nicht ändern ohne ausdrücklichen Auftrag)**
- **Keine steuerliche Berechnung oder Bewertung.** Die App dokumentiert nur. Prüfhinweise ja, Rechenwerte nein.
- **Privat und Vermietung strikt getrennt.** Ein Bereichswechsel geschieht nie automatisch.
- **Originale bleiben unverändert.** Ersetzte Dateien bleiben erhalten (Rolle `replaced`). Gelöscht wird nur in den Papierkorb.
- **Datum nur aus dem erkannten Dokumenttext.** Nie Upload-Datum, nie Dateiname oder Titel. Ist es unklar, fragt die App nach.
- **Kein Raten von Beträgen.** Ohne eindeutigen Endbetrag gibt es eine Auswahlliste.
- Automatisch erfasste Ausgaben zählen sofort, bleiben aber erkennbar (`origin`) und jederzeit korrigierbar. Angenommene Zahlungsdaten sind gekennzeichnet.
- Automatik nur bei hoher Sicherheit, alles Unsichere als Rückfrage in den Eingang.
- KI ist optional, EU-Anbieter, sendet nur Textauszüge und ist pro Ordner sperrbar.

**Technik und Code**
- Bestehende Architektur beibehalten: Starlette, SQLite/FTS5, Worker, React/esbuild, keine zusätzlichen Frameworks ohne Grund.
- Datenbankänderungen **nur als neue Migration** am Ende von `MIGRATIONS` in `db.py`, bestehende nie ändern.
- Einmalige Datenkorrekturen für Bestandsdaten als Startlauf in `main.py` mit Einmal-Marker in `settings`.
- Frontend-Assets **mit Prüfsumme im Namen** bauen (`build.mjs`). Bei Änderungen am Service Worker den Cache-Namen erhöhen.
- Zu jeder Änderung Tests ergänzen. `pytest` muss grün sein, das Frontend muss bauen (`npm run build`, `tsc --noEmit`). Das gebaute `backend/static` wird mit eingecheckt.
- Commits auf `main`, kurze deutsche Commit-Nachricht. Danach push und dem Nutzer den Server-Update-Befehl geben (`git pull && docker compose up -d --build`).
- Fehleranalyse: erst Ursache belegen (Logs, Screenshot), dann beheben. Kein Raten. Bei eigenem Fehler offen benennen.
- Keine ungefragten neuen Features. Vorschläge als Angebot formulieren.

---

## 10. Technischer Übergabekontext

**Zugänge und Orte**

| Was | Wo |
| --- | --- |
| Repository | `github.com/crimepayzbrothers-source/Smartarchive` (privat, `main`) |
| Server | netcup VPS Lite, Debian 13, Projekt unter `/root/smartarchive`, Daten in `/root/smartarchive/data` |
| App-URL | `https://v2202610445998547434.taila12eaf.ts.net/` (nur im Tailscale-Netz) |
| Backup | restic-Repo `b2:smartarchive-can-2303:smartarchive`, Zugangsdaten in `/root/.restic-env`, Passwort in `/root/.restic-pass` (beide chmod 600), Log `/var/log/smartarchive-backup.log` |
| Cron (root) | `30 3 * * *` Backup, `0 5 1 * *` Wiederherstellungstest; beide laden vorher `/root/.restic-env` |
| Überwachung | healthchecks.io-Check „SmartArchive Backup“ (Cron `30 3 * * *`, Europe/Berlin, Grace 2 h), URL in `HEALTHCHECK_URL` |

**Zentrale Stellen im Code**
- Pipeline: `documents.ingest` → `documents.process` → `classify.analyze`/`decide`, `duplicates.find`/`mark`, `invoices.capture_on_upload`/`ensure_expense`, `documents.index_document`.
- Datum: `extract.date_analysis`. Beträge: `extract.amount_candidates`, `extract.detect_amount`. Rechnungsfelder: `invoices.fields`, `invoices.extract_for_document`.
- Bank: `banking.parse_csv`, `analyze`, `auto_apply`, `decide`, `undo`.
- Exporte: `exports.journal`, `to_csv`, `to_xlsx`, `to_pdf_bundle(with_receipts)`, `full_archive_zip`, `find_issues`.
- API-Schwerpunkte: `api/docs.py` (Upload, Dokumente, Eingang, `/invoice`, `/date`, `/extract`, `/duplicate`, `/no-expense`), `api/realestate.py` (Objekte, Ausgaben, Exporte), `api/bank.py`.

**Nicht ohne Weiteres ändern:** Migrationsreihenfolge, die SHA-256-Ablage der Originale, das Jahresdatum `COALESCE(paid_on, invoice_date, doc_date)`, die Prüfsummen-Assets, die strikte Bereichstrennung, die lokale Bindung `127.0.0.1:8080` (Zugriff nur über Tailscale).

**Risiken:** SQLite ist auf einen Nutzer ausgelegt. Der VPS ist ein einzelner Server, abgesichert durch das B2-Backup. Für eine Wiederherstellung braucht man unbedingt das Backup-Passwort. B2-Gratisgrenzen siehe 7 D.

---

## 11. Unklarheiten und fehlende Informationen

| Punkt | Wie klären |
| --- | --- |
| Ursprüngliche Detailanforderungen und Übergabetext der allerersten Phase (nur als Zusammenfassung erhalten) | README und Code gelten als maßgeblich |
| Ist 2FA in SmartArchive aktiviert? | Nutzer fragen bzw. Einstellungen ansehen |
| Wurde das Backup-Passwort nach dem Screenshot-Vorfall neu erzeugt, **bevor** `restic init` lief, und ist es offline gesichert? | Nutzer fragen. Wichtig: Das Repo ist mit dem Passwort verschlüsselt, das beim Init in `/root/.restic-pass` stand. |
| Ist die Dropbox-Entwickler-App gelöscht? Ist Dropbox gekündigt? | Nutzer fragen |
| Ist `MISTRAL_API_KEY` gesetzt? | `grep -c MISTRAL_API_KEY=. ~/smartarchive/.env` auf dem Server |
| Anzahl und Namen der angelegten Mietobjekte in der App | In der App bzw. DB prüfen (laut Nutzer: Berliner Straße 21/23/32 Bad Sobernheim, Hauptstraße 15 Schmidt-Hachenbach; ob alle angelegt sind, ist unbekannt) |
| Hat der Nutzer zuletzt `git pull` ausgeführt und den Health-Check auf grün gesetzt? | `git log --oneline -1` auf dem Server sollte `5d931ee` zeigen |
| Wie genau sah das empfohlene Icon „Register“ (Option A) aus? Der Entwurf liegt nur im alten Chat. | Nutzer bei Bedarf um einen Screenshot oder eine Beschreibung bitten |
| Genaue Commerzbank-CSV-Spalten des Nutzers | Durch Tests abgedeckt. Bei Abweichungen eine echte Datei, ohne sensible Daten, ansehen. |

---

## 12. Konkreter nächster Schritt

1. **Zuletzt:** Backup nach Backblaze B2 fertig eingerichtet, Ausfall-Mail über healthchecks.io, Wiederherstellungstest neu gebaut (Commits `468711d`, `a8385cb`, `5d931ee`).
2. **Offen:** Bestätigung, dass der Health-Check grün ist. Erster erfolgreicher Lauf des neuen Wiederherstellungstests.
3. **Als Nächstes**, auf dem Server:
   ```bash
   cd ~/smartarchive && git pull -q && . /root/.restic-env && restic snapshots | tail -n 4 && bash scripts/restore-test.sh 2>&1 | grep -v "^\[" | tail -n 4
   ```
   Erwartet wird eine neue Sicherung von 3:30 Uhr, dazu „Datenbank: Integrität ok …“ und „Wiederherstellungstest erfolgreich.“ Danach die Punkte 3–6 aus Abschnitt 8.
4. **Benötigt:** Screenshot der Ausgabe. Für Codearbeiten das Repository (Klon von `main`).
5. **Fortsetzung ohne Grundsatzdiskussion:** Die Architektur und die Regeln in Abschnitt 9 sind gesetzt. Neue Wünsche des Nutzers werden in derselben Struktur umgesetzt: Migration, Backend-Logik mit Tests, Frontend, Build, Commit und Push, Update-Befehl für den Server.
