# SmartArchive – Ergänzung zur Projektübergabe (Stand 29.10.2026)

> Diese Ergänzung gehört zu `docs/PROJEKT-UEBERGABE.md` und enthält nur Informationen, die dort fehlen oder ungenau sind. Die Abschnittsnummern beziehen sich auf die Übergabe.
>
> **Kennzeichnung:** **[Code]** = im Quellcode belegt (Commit `0ead263`) · **[Chat]** = im noch vorhandenen Chatverlauf belegt · **[Zusammenfassung]** = nur aus der Zusammenfassung des älteren, nicht mehr vollständig vorliegenden Verlaufs · **[Annahme]** = Schlussfolgerung, nicht belegt · **[offen]** = nicht überprüfbar, muss beim Nutzer oder auf dem Server geklärt werden

---

## A. Korrekturen an der Übergabe

| Stelle | Korrektur |
| --- | --- |
| 7 A „Letzter Code-Commit `5d931ee`“ | Danach kamen die Übergabedokumente hinzu (`0ead263`). Am Programmcode hat sich seit `5d931ee` nichts geändert. **[Code]** |
| 2.7 „App erreichbar unter …ts.net“ | Die genaue Tailscale-Adresse stammt aus der Zusammenfassung. **[Zusammenfassung]** |
| 10 „keyID `003081230220af20000000001`“ (steht nur in der alten Zusammenfassung) | **Diese keyID ist veraltet.** Der Nutzer sollte alle eigenen Schlüssel löschen und einen neuen Application Key anlegen. Mit diesem neuen Schlüssel hat die Anmeldung funktioniert. Die neue keyID ist nicht bekannt und steht nur in `/root/.restic-env`. **[Chat]** |
| 3.8 „Wiederherstellungstest … rotierend 1/12“ | Welcher Teil gelesen wird, richtet sich nach dem Monat (`date +%-m`/12). Im November wird also Teil 11/12 gelesen. **[Code]** |

---

## B. Fehlende technische Details (Code)

### B.1 Sicherheit (zu 2.4)
- **Bremse gegen Passwort-Raten** (`security.LoginThrottle`): Ab dem 5. Fehlversuch wächst die Sperre exponentiell (5 s, 10 s, 20 s … höchstens 900 s). Der Zähler liegt nur im Arbeitsspeicher und ist nach einem Neustart weg. **[Code]**
- **Sicherheits-Header** (`main.SecurityHeaders`):
  - `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: SAMEORIGIN`
  - `Permissions-Policy: camera=(self), microphone=(), geolocation=()`
  - eine strikte Content-Security-Policy (nur `'self'`, `img-src` zusätzlich `data:`/`blob:`)
  - für API-Antworten `Cache-Control: no-store`

  Folge: externe Skripte, Schriften oder CDNs funktionieren im Frontend nicht. **[Code]**
- Schreibende API-Aufrufe ohne den Header `X-Requested-With: SmartArchive` beantwortet der Server mit 403. `api.ts` setzt ihn automatisch. **[Code]**

### B.2 Server und Worker (zu 2.3)
- Der Worker versucht einen fehlgeschlagenen Job **höchstens 3-mal** (`MAX_ATTEMPTS = 3`). Der n-te neue Versuch startet frühestens n Minuten später. Die Abarbeitung läuft nach `priority`, dann nach ID. **[Code]**
- Mit `INLINE_WORKER=1` läuft der Worker als Thread im App-Prozess. Das ist für lokale Entwicklung und Tests gedacht, produktiv läuft ein eigener `worker`-Container. **[Code]**
- Die einmaligen Startläufe in `main.py` sind in `try/except` gekapselt. Ein Fehler darin blockiert den Start nie, er wird nur geloggt. **[Code]**
- Statische Dateien: `assets/*` mit `max-age=31536000, immutable`, `index.html` und alles andere mit `no-cache`. Ohne Build antwortet der Server mit 503 und dem Hinweis „Frontend nicht gebaut“. **[Code]**
- Ist der Service Worker nicht aktiv, leitet `/share-target` per POST auf `/#/eingang` um (303). **[Code]**
- Seitenvorschauen: `GET /api/documents/{id}/pages/{n}?dpi=` mit 50 bis 200 dpi, Standard 110. Die Bilder werden je SHA-256 unter `data/cache` zwischengespeichert. **[Code]**
- Office-Konvertierung: LibreOffice mit Zeitlimit 900 s (`ocr.office_to_pdf`). **[Code]**

### B.3 Scanner (zu 3.1)
- Kantenerkennung (`scan.detectQuad`): Graustufen bei etwa 360 px, Box-Blur, Otsu-Schwelle, die größte helle Fläche wird zum Viereck. Wird nichts gefunden, setzt ein eingerückter Standardrahmen ein (`found: false`). Die Entzerrung (`warp`) arbeitet mit höchstens 2.400 px Kantenlänge. **[Code]**
- Drei Filter im Scanner: **Dokument** (Standard), **Farbe**, **Original**. Dazu **Drehen** in 90°-Schritten und die Schaltfläche „**Weitere Seite aufnehmen**“ für mehrseitige Scans. **[Code]**

### B.4 Mietkonto (zu 3.5)
- **Lernen aus dem Verlauf:** Ein Zahlungspartner gilt erst dann als erkannt, wenn er **mindestens 2-mal** gleich zugeordnet wurde **und** diese Zuordnung mindestens 80 % seiner Umsätze ausmacht. Die Sicherheit beträgt dann 0,92, ab 3 gleichen Zuordnungen 0,95. Ausgaben und Einnahmen werden getrennt gelernt. **[Code]**
- **Teilbeträge** (z. B. nur der Zinsanteil einer Darlehensrate): Der Betrag muss größer als 0 sein und darf den Umsatz nicht übersteigen. Zahlungspartner mit Teilbeträgen werden **nie automatisch** übernommen, weil der Betrag jedes Mal geprüft werden muss. **[Code]**
- **Hinweistexte im Umsatz:**
  - Darlehen/Tilgung: „nur der Zinsanteil ist Werbungskosten …“
  - Hausgeld: „Zuführung zur Erhaltungsrücklage erst bei Verwendung abziehbar …“

  Das sind reine Hinweise, keine Berechnung. **[Code]**
- **Stichwort-Kategorien der Bank** (`BANK_KEYWORDS`):
  - grundsteuer, versicherung (Gebäude/Grundbesitzerhaftpflicht)
  - hausgeld_laufend, verwaltung (u. a. Kontoführung/Entgelt)
  - grundbesitzabgaben (Abfall, Abwasser …), hausnebenkosten (Schornsteinfeger, Allgemeinstrom, Hausmeister)
  - danach greift die Rechnungs-Stichwortliste

  **[Code]**
- Passen mehrere erfasste Rechnungen mit gleichem Betrag, fragt die App, welche gemeint ist. Eindeutig ist ein Treffer erst bei mindestens 0,1 Abstand zum zweitbesten. **[Code]**
- Gibt es genau ein Mietobjekt, wird es Umsätzen ohne erkennbares Objekt automatisch vorgeschlagen. **[Code]**

### B.5 Prüfhinweise im Export (zu 3.4)
- **Mögliche Doppelerfassung:** gleicher Betrag, gleicher Empfänger (Groß-/Kleinschreibung egal), Datum höchstens **10 Tage** auseinander, verschiedene Dokumente. **[Code]**
- **Anschaffungsnah:** Hinweis, sobald es innerhalb von **3 Jahren** nach `acquired_on` Erhaltungspositionen gibt (`erhaltung_sofort`/`erhaltung_verteilt`). Die 15-%-Grenze wird bewusst **nicht** berechnet, nur angezeigt. Ohne Kaufdatum am Objekt gibt es keinen Hinweis. **[Code]**

### B.6 Backup (zu 3.8 und 10)
- **Was gesichert wird und was nicht:**
  - gesichert: `data/files`, `data/backup`
  - ausgeschlossen: `data/cache`
  - nicht in der Sicherung: die Live-Datenbank `data/smartarchive.sqlite3` (sie wird vorher per `app.backup` konsistent nach `data/backup/` kopiert und auf Integrität geprüft), die **`.env`** und die Dateien `/root/.restic-*`. Die `.env` ist wiederherstellbar (neuer `SETUP_TOKEN`, ggf. neuer Mistral-Schlüssel).

  **[Code]**
- **Pfade in der Sicherung:** restic speichert die Pfade relativ zum Projektordner (`/data/files/...`, `/data/backup/...`), obwohl `restic snapshots` `/root/smartarchive/data/...` anzeigt. Ein Wiederherstellungsfilter mit `--include /root/smartarchive/data/backup` stellte deshalb **0 Dateien** wieder her. **Für gezielte Wiederherstellungen** `restic ls latest` nutzen und den dort gezeigten Pfad verwenden, so wie es `restore-test.sh` macht. **[Chat]**
- `backup.sh` meldet Start, Erfolg und Fehler an `$HEALTHCHECK_URL` (`/start`, ohne Zusatz, `/fail` mit den letzten 10 KB Log). Ohne gesetzte URL läuft alles wie bisher. Der Rückgabewert bleibt bei einem Fehler ungleich 0. **[Code]**
- Erster Snapshot `2f2b060e` (28.10.2026 22:07), erste automatische Nachtsicherung `e9b07c8a` (29.10. 03:30, 4 s, rund 14 KB neu). Repository-ID `b5a5a564`, restic-Repo-Format Version 2 mit Kompression. Gespeichert sind 3,057 GiB bei 3,332 GiB Rohdaten. **[Chat]**
- Am 29.10. lief um 12:22 eine weitere, manuelle Sicherung (`499ebaf6`). **[Chat]**

---

## C. Server-Sonderfälle (belegt im Chat)

| Thema | Sachverhalt |
| --- | --- |
| `/tmp` liegt im RAM | Debian 13 nutzt `tmpfs` für `/tmp`. Große Wiederherstellungen dorthin scheitern mit „no space left on device“. Große temporäre Daten gehören nach `/var/tmp` oder auf die Festplatte. **[Chat]** |
| Zeitzone des Servers | CET/MEZ (`date` zeigte „CET“). Der Cron-Eintrag `30 3 * * *` bedeutet 3:30 Uhr deutscher Zeit. **[Chat]** |
| cron | Installiert und aktiv (`systemctl is-active cron` → active). **[Chat]** |
| Auf dem Host nicht installiert | `sqlite3` (Kommandozeile). Für Prüfungen auf dem Host `python3` nutzen. **[Chat]** |
| restic auf dem Host | Installiert. Die genaue Version ist nicht bekannt, Repo-Format v2 setzt restic ≥ 0.14 voraus. **[Chat] / Version [offen]** |
| Backblaze-Gratisgrenzen | Täglich 1 GB Download und 2.500 Class-B-Transaktionen frei. Bei Überschreitung antwortet B2 mit „403 … cap exceeded“. Das Kontingent setzt sich um 0:00 UTC zurück (1:00 Uhr MEZ). Am 29.10. war das Download-Kontingent durch einen Vollrestore aufgebraucht. Ein restic-Lock im Repository könnte deshalb nicht entfernt worden sein, das nächste `backup.sh` räumt es mit `restic unlock` auf. **[Chat]** |
| Git-Remote | Beim Push meldet GitHub „This repository moved“. Die gültige Adresse ist `https://github.com/crimepayzbrothers-source/Smartarchive.git`. Lokal ist das korrigiert. **Ob der Klon auf dem Server die alte Adresse nutzt, ist [offen].** `git pull` funktioniert trotzdem, weil GitHub weiterleitet. |
| Arbeitskopie für die Entwicklung | Klon unter `/home/claude/smartarchive` in der Claude-Arbeitsumgebung. Er existiert nur in dieser Sitzung. Ein neuer Chat muss das Repository neu einbinden. **[Chat]** |

---

## D. Arbeitsweise und Regeln, die in der Übergabe fehlen

1. **Screenshots als Rückkanal:** Der Nutzer schickt die Terminal-Ausgaben als Screenshot vom Handy oder Tablet. Bei der Auswertung genau auf die Uhrzeit und darauf achten, welcher Befehl die Ausgabe erzeugt hat. Am 29.10. stammte eine angeblich fehlende Nachtsicherung aus alter, noch sichtbarer Ausgabe. Vor einer Diagnose mit einem frischen Befehl prüfen. **[Chat]**
2. **Befehlsblöcke robust bauen:** Blöcke mit `&&` verketten, damit nach einem Fehler nichts weiterläuft. Eingaben über `read -rp`/`read -rsp`, Längen zur Kontrolle ausgeben, Geheimnisse nie in der Ausgabe zeigen. Lange Ausgaben mit `tail`/`grep` kürzen, weil der Handy-Bildschirm wenig zeigt. **[Chat]**
3. **Abbruch erklären:** Der Nutzer bricht mit `ctrl`+`c` in der Termius-Leiste ab. Läuft ein Prozess endlos (z. B. Wiederholversuche von restic), ausdrücklich zum Abbruch auffordern. **[Chat]**
4. **Ping-URL von healthchecks.io:** Sie gilt nicht als Geheimnis, soll aber nicht ohne Grund weitergegeben werden. Auf einem Screenshot vom 29.10. war sie sichtbar. Der Nutzer wurde auf die mögliche Neuerzeugung hingewiesen, ob er das gemacht hat, ist **[offen]**. **[Chat]**
5. **Fachbegriffe erklären:** Der Nutzer fragt bei Fachbegriffen nach (z. B. „Cron-Expression“). Begriffe beim ersten Auftreten kurz erklären und genau sagen, wo in fremden Oberflächen was einzutragen ist. **[Chat]**
6. **Ehrliche Zwischenstände:** Auf die Frage „ist das vollumfänglich gewährleistet?“ wurde mit Einschränkungen geantwortet: Wiederherstellung nicht getestet, keine Ausfallmeldung, Passwort nur auf dem Server. Das ist so gewollt: nicht beschönigen, Restrisiken benennen. **[Chat]**
7. **Eigene Fehler offen benennen:** Als der erste Test-Einzeiler am Server scheiterte (`sqlite3` fehlte, `--include` fand nichts), wurde das ausdrücklich gesagt und das Skript ersetzt. **[Chat]**
8. **Nach Codeänderungen am Backup-Skript:** Auf dem Server reicht `git pull`, kein Neubau. Ein Neubau mit `docker compose up -d --build` ist nur bei Änderungen an der App oder am Frontend nötig. **[Code/Chat]**
9. **Dokumentation pflegen:** Die Übergabedokumente liegen in `docs/`. Bei größeren Änderungen am Stand sollten sie fortgeschrieben werden. **[Annahme: sinnvoll, aber nicht ausdrücklich vom Nutzer verlangt]**

---

## E. Fakten zum Nutzer und Umfeld (relevant für Entscheidungen)

- **Hosting:** netcup **VPS Lite 1 G12.5s**, Standort Nürnberg, **6 Monate Mindestlaufzeit**, bestellt und bezahlt im Oktober 2026. Was beim Vertragsende mit den Daten geschieht, wurde im alten Verlauf beantwortet. Der genaue Inhalt dieser Antwort liegt nicht mehr vor **[Zusammenfassung]**. Für ein Vertragsende sind der Gesamtexport (ZIP) und das B2-Backup die Wege, die Daten mitzunehmen. **[Code]**
- **Vermietete Objekte des Nutzers** (laut Nutzerangaben): Berliner Straße 21, 23 und 32 in Bad Sobernheim sowie Hauptstraße 15 in Schmidt-Hachenbach. Hausverwalter: Herr Schmitz. **Ob und wie diese Objekte in der App angelegt sind, ist [offen].** Für die automatische Objekterkennung zählen die Anschrift und die Stichwörter am Objekt (`extract.detect_property`, `street_pattern`). **[Code]**
- **Bank:** Commerzbank, reines Mietkonto. Umsätze kommen als CSV-Export aus dem Online-Banking. **[Zusammenfassung]**
- **Geräte:** Android-Handy (PWA), dazu offenbar ein Tablet mit Termius, erkennbar am Bildschirmformat der Screenshots. **[Annahme]**
- **Kosten:** Ziel ist der Ersatz von Dropbox (11,99 €/Monat). Das Backup soll kostenlos bleiben, deshalb wurde Backblaze statt Hetzner gewählt. Wird das Archiv größer als etwa 10 GB, fallen bei B2 Kosten an (rund 6 $/TB/Monat) **[Annahme: Preisstand nicht geprüft]**.
- **Fachlicher Hintergrund:** Der Nutzer arbeitet beruflich im Finanzamt. Steuerliche Fachbegriffe kennt er, eine steuerliche Bewertung durch die App ist trotzdem ausdrücklich nicht gewünscht. **[Chat/Profil]**

---

## F. Ergänzungen zu Historie und Entscheidungen (zu 6)

| Thema | Ergänzung |
| --- | --- |
| Frage „Wofür netcup?“ | Beantwortet: Der Server betreibt die App rund um die Uhr samt Texterkennung und ersetzt den Speicher der Dropbox. Der genaue Wortlaut liegt nicht mehr vor. **[Zusammenfassung]** |
| Zeitpunkt für Verbesserungen | Der Nutzer fragte, ob er mit Verbesserungen warten soll, bis die OCR-Warteschlange (1.146 Jobs) abgearbeitet ist. Die Antwort ist nicht mehr im Wortlaut vorhanden, die Arbeit ging danach parallel weiter. **[Zusammenfassung]** |
| Termius | Der Nutzer hatte versehentlich eine Eingabe in Termius gemacht und fragte, wie er sie löscht. Das wurde beantwortet, ohne Auswirkung auf das Projekt. **[Zusammenfassung]** |
| Ausgaben-Automatik | Bei der Frage „Entwurf oder sofort zählen“ wählte der Nutzer **„Sofort zählen (Empfohlen)“**. Das ist die Grundlage für `status='bestaetigt'` bei automatischer Erfassung mit bekanntem Objekt. **[Zusammenfassung]** |
| Backup-Ziel | Erst „Ja“ zum Backup, dann der Wunsch nach einer kostenlosen Lösung. Datenmenge 3,4 GB, deshalb Backblaze B2 mit 10 GB frei in der Region EU Central. **[Zusammenfassung]** |
| Backup-Anmeldung | Die Ursache des 401 war ein Paar aus keyID und applicationKey, das nicht zusammengehörte. Die Prüfung mit `curl … b2_authorize_account` ergab `unauthorized`. Gelöst wurde es mit einem neuen Schlüssel, bei dem beide Werte kopiert statt abgetippt wurden. **[Chat]** |
| Wiederherstellungstest v1 → v2 | v1 stellte alles nach `/tmp` wieder her und scheiterte am RAM-tmpfs. v2 kommt ohne Vollkopie aus. Den einmaligen Vollcheck `restic check --read-data` hat B2 wegen des Kontingents abgewiesen. **Ein vollständiges Lesen ist trotzdem belegt:** v1 hat 6.718 Dateien und Ordner (3,332 GiB) heruntergeladen und entschlüsselt, nur das Schreiben schlug fehl. **[Chat]** |

---

## G. Zusätzliche offene Punkte (zu 11)

| Punkt | Status |
| --- | --- |
| Hat der Nutzer nach dem B2-403 mit Strg+C abgebrochen, `git pull` ausgeführt und den Health-Check mit `curl "$HEALTHCHECK_URL"` wieder auf grün gesetzt? | **[offen]** |
| Steht der healthchecks.io-Check auf „Cron 30 3 * * *, Europe/Berlin, Grace 2 h“ oder auf „Simple, 1 Tag“? Beides wurde angeboten. | **[offen]** |
| Bekommt der Nutzer die Fehler-Mail von healthchecks.io? Der fehlgeschlagene Test am 29.10. hätte eine auslösen müssen. | **[offen]** |
| Welche keyID hat der neue B2-Schlüssel und sind alte Schlüssel gelöscht? | **[offen]** (Nutzer soll sie nicht in den Chat schicken, die keyID allein ist aber unkritisch) |
| Ist auf dem Server-Klon `scripts/restore-test.sh` in der neuen Fassung (`a8385cb` oder neuer)? | **[offen]**, prüfbar mit `git log --oneline -1` |
| Wurden die Bestandsdaten durch die Startläufe korrekt nacherfasst und korrigiert (Logzeilen „Rechnungen nacherfasst“, „Datumserkennung erneuert“, „Zahlungsdatum angenommen“, „Unsichere Belegdaten markiert“)? | **[offen]**, prüfbar mit `docker compose logs app | grep -E "nacherfasst|erneuert|angenommen|markiert"` |
| Wie viele Belege stehen aktuell unter „Datum prüfen“, „Mögliche Duplikate“ und „Rechnungen ohne Ausgabe“? | **[offen]**, in der App sichtbar |
