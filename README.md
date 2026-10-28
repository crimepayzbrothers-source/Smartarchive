# SmartArchive

Privates Dokumentenarchiv mit zwei getrennten Bereichen – **Privat** und **Vermietung** –, Texterkennung, Volltextsuche, automatischer Ablage beim Upload und Ausgabenerfassung pro Beleg mit Export für die Anlage V.

Stand: Version 0.1 (MVP). Läuft als installierbare Web-App (PWA) auf Android und im Desktop-Browser.

## Was die App kann

| Bereich | Funktion |
| --- | --- |
| Erfassen | Foto mit der Handykamera (mehrere Fotos → ein PDF), PDF- und Bild-Upload, Drag-and-drop, „Teilen mit SmartArchive“ aus anderen Android-Apps |
| Ablage | Originale bleiben unverändert (Prüfsumme SHA-256), Dubletten werden beim Upload erkannt |
| Texterkennung | Digitale PDFs direkt, Scans mit OCRmyPDF/Tesseract (Deutsch) → durchsuchbares PDF |
| Automatische Ablage | Reihenfolge: eigene Regeln → bestätigter Verlauf je Absender → KI (optional, Mistral EU) → Stichwörter. Sichere Treffer werden sofort abgelegt, unsichere landen mit Vorschlag im Eingang. Bereichswechsel nie automatisch. |
| Suche | Volltext inkl. Wortteilen („wartung“ findet „Heizungswartung“), Filter nach Bereich, Ordner, Objekt, Jahr, Tag, Favoriten |
| Vermietung | Mietobjekte und Einheiten, Ausgabenpositionen am Beleg (Zahlungsdatum bestimmt das Steuerjahr), Prüfhinweise (fehlendes Zahlungsdatum, Dubletten, anschaffungsnahe Aufwendungen) |
| Export | Excel (Blatt je Objekt + „Offen“), CSV (Excel-tauglich, Semikolon), PDF-Paket mit Übersicht und allen Belegen, Gesamtexport als ZIP |
| Sicherheit | Passwort (scrypt) + optional Zwei-Faktor (TOTP), Sitzungs-Cookies HttpOnly/SameSite, CSRF-Schutz, Protokoll aller Änderungen, Papierkorb |

Bewusst **nicht** enthalten: Berechnung von AfA, Überschuss oder Anlage-V-Zeilenwerten. Die App bereitet vor, sie berät nicht.

## Aufbau

```
backend/   Python (Starlette), SQLite mit FTS5-Volltextsuche, Worker für OCR und Einordnung
frontend/  React-PWA, gebaut mit esbuild nach backend/static
scripts/   Backup und Wiederherstellungstest (restic)
data/      Laufzeitdaten: Datenbank, Originale, abgeleitete PDFs (nicht im Git)
```

SQLite statt PostgreSQL: Für einen Nutzer schneller, ohne eigenen Datenbankserver und mit einer Datei leicht zu sichern. Ein Umstieg ist bei Mehrbenutzerbetrieb möglich.

## Installation auf dem Server (netcup VPS, Debian/Ubuntu)

1. **Docker installieren**

   ```bash
   curl -fsSL https://get.docker.com | sh
   ```

2. **Projekt kopieren und konfigurieren**

   ```bash
   git clone <ihr-repository> smartarchive && cd smartarchive
   cp .env.example .env
   sed -i "s/^SETUP_TOKEN=.*/SETUP_TOKEN=$(openssl rand -hex 16)/" .env
   nano .env            # optional MISTRAL_API_KEY eintragen
   mkdir -p data && sudo chown 1000:1000 data
   docker compose up -d --build
   ```

   Die App läuft danach nur lokal auf dem Server (`127.0.0.1:8080`).

3. **Zugriff vom Handy über Tailscale** (privates VPN mit HTTPS-Zertifikat)

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   sudo tailscale serve --bg 8080
   ```

   Tailscale zeigt eine Adresse wie `https://vps-name.tailnet-xyz.ts.net`. Auf dem Android-Handy die Tailscale-App installieren, mit demselben Konto anmelden, die Adresse in Chrome öffnen.

4. **Einrichten:** Beim ersten Aufruf nach dem Einrichtungscode fragen lassen (`grep SETUP_TOKEN .env`), Benutzer und Passwort festlegen. Danach unter *Einstellungen → Konto und Sicherheit* die Zwei-Faktor-Anmeldung aktivieren.

5. **Als App installieren:** In Chrome Menü → „Zum Startbildschirm hinzufügen“ bzw. „App installieren“. Danach erscheint SmartArchive auch im Android-Teilen-Menü.

## Backup (Hetzner Storage Box)

Einmalig auf dem Server:

```bash
sudo apt install restic
echo "ein-langes-zufaelliges-passwort" | sudo tee /root/.restic-pass && sudo chmod 600 /root/.restic-pass
export RESTIC_REPOSITORY=sftp:u123456@u123456.your-storagebox.de:smartarchive
export RESTIC_PASSWORD_FILE=/root/.restic-pass
restic init
```

Das restic-Passwort zusätzlich offline aufbewahren (z. B. Passwortmanager) – ohne es sind die Backups nicht lesbar.

Cron-Einträge (`sudo crontab -e`):

```
RESTIC_REPOSITORY=sftp:u123456@u123456.your-storagebox.de:smartarchive
RESTIC_PASSWORD_FILE=/root/.restic-pass
30 3 * * *  cd /pfad/zu/smartarchive && ./scripts/backup.sh >> /var/log/smartarchive-backup.log 2>&1
0 5 1 * *   cd /pfad/zu/smartarchive && ./scripts/restore-test.sh >> /var/log/smartarchive-backup.log 2>&1
```

Für SFTP ohne Passworteingabe einen SSH-Schlüssel auf der Storage Box hinterlegen (Hetzner-Doku „Storage Box – SSH-Keys“).

## Import aus Dropbox

Der Import übernimmt PDFs, Bilder und Office-Dateien und legt sie anhand Ihrer Dropbox-Ordner direkt ab. Die Zuordnung steht in `backend/app/import_mappings/dropbox.json`. Bereits importierte Dateien werden bei jedem weiteren Lauf übersprungen, Dubletten erkannt.

1. **Zugriffsschlüssel erzeugen** (gilt 4 Stunden, nur Leserechte): dropbox.com/developers/apps → *Create app* → *Scoped access* → *Full Dropbox* → Name vergeben. Reiter *Permissions*: `files.metadata.read` und `files.content.read` anhaken, *Submit*. Reiter *Settings* → *Generated access token* → *Generate*.
2. **Probelauf** (verändert nichts):

   ```bash
   cd ~/smartarchive
   read -rs DROPBOX_TOKEN     # Schlüssel einfügen, Enter (Eingabe bleibt unsichtbar)
   docker compose exec -e DROPBOX_TOKEN="$DROPBOX_TOKEN" app python -m app.importer plan
   ```
3. **Import starten** (läuft im Hintergrund weiter, auch wenn die SSH-Verbindung abbricht):

   ```bash
   docker compose exec -d -e DROPBOX_TOKEN="$DROPBOX_TOKEN" app sh -c 'python -m app.importer run > /data/import.log 2>&1'
   tail -3 data/import.log                                   # Fortschritt beim Herunterladen
   docker compose exec app python -m app.importer status     # Fortschritt der Texterkennung
   ```
4. Danach die Dropbox-App unter dropbox.com/developers/apps wieder löschen. Das macht den Schlüssel sofort ungültig.

Aus einem lokalen Ordner auf dem Server importieren: `python -m app.importer run --dir /pfad/im/container`.

## Update

```bash
git pull && docker compose up -d --build
```

Datenbankänderungen laufen beim Start automatisch (versionierte Migrationen).

## KI-Einordnung (optional)

Mit `MISTRAL_API_KEY` in der `.env` fragt die App Mistral (Verarbeitung in der EU), wenn weder Regel noch Verlauf greift. Gesendet werden nur ein Textauszug (max. 6.000 Zeichen), Ihre Ordnerliste und die Objektliste – nie die Datei selbst. Für Ordner mit gesperrter KI (voreingestellt: Gesundheit, Familie) prüft die App vorher lokal per Stichwörtern und fragt die KI dann gar nicht. Im Mistral-Konto die Trainingsnutzung prüfen und abschalten; einen Auftragsverarbeitungsvertrag abschließen.

Das Zitat, mit dem die KI ihre Einordnung begründet, muss wörtlich im Dokument stehen – sonst wird die Sicherheit gedeckelt und das Dokument landet im Eingang.

## Lokal entwickeln

```bash
cd frontend && npm install && npm run build      # baut nach backend/static
cd ../backend && pip install -r requirements.txt pytest
SETUP_TOKEN=dev COOKIE_SECURE=0 INLINE_WORKER=1 DATA_DIR=./data uvicorn app.main:app --reload --port 8080
python -m pytest                                  # Tests
```

`INLINE_WORKER=1` startet die Verarbeitung im Webprozess (nur für die Entwicklung; im Betrieb läuft der eigene Worker-Container). Für die Texterkennung werden `tesseract` (mit Sprachpaket `deu`) und `poppler-utils` benötigt, optional `ocrmypdf`.

## Grenzen von Version 0.1

- Anmeldung mit Passwort + TOTP; Passkeys folgen.
- Hausgeldabrechnungen werden als Beleg erfasst und manuell in Positionen aufgeteilt (z. B. Rücklagenzuführung als „nicht sofort abziehbar“); ein Aufteilungsassistent ist für Phase 2 geplant.
- Semantische Suche, Fristen-Erinnerungen, E-Mail-Import und der Dokumenten-Assistent sind Phase 2/3.
- Mieteinnahmen werden noch nicht erfasst.
