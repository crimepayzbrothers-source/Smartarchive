#!/usr/bin/env bash
# Nächtliches Backup auf die Hetzner Storage Box mit restic (clientseitig verschlüsselt).
# Einrichtung und Cron-Eintrag: siehe README, Abschnitt „Backup“.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY setzen, z. B. sftp:u123456@u123456.your-storagebox.de:smartarchive}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE setzen (Datei mit dem restic-Passwort)}"

# 1. Datenbank konsistent kopieren (läuft im Container, App bleibt online)
docker compose exec -T app python -m app.backup

# 2. Originale, abgeleitete Dateien und Datenbankkopie sichern
restic backup --tag smartarchive --exclude data/cache data/files data/backup

# 3. Aufbewahrung: 14 Tage, 8 Wochen, 24 Monate
restic forget --tag smartarchive --keep-daily 14 --keep-weekly 8 --keep-monthly 24 --prune
echo "Backup fertig: $(date -Is)"
