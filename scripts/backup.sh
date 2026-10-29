#!/usr/bin/env bash
# Nächtliches Backup mit restic (clientseitig verschlüsselt), z. B. nach Backblaze B2.
# Einrichtung und Cron-Eintrag: siehe README, Abschnitt „Backup“.
#
# Optional: HEALTHCHECK_URL (z. B. https://hc-ping.com/<uuid>) setzen. Dann meldet das Skript
# Start, Erfolg und Fehler an healthchecks.io; bleibt eine Meldung aus oder schlägt die Sicherung
# fehl, verschickt der Dienst eine Mail.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY setzen, z. B. b2:bucket:smartarchive}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE setzen (Datei mit dem restic-Passwort)}"

HC="${HEALTHCHECK_URL:-}"
LOG="$(mktemp)"
ping_hc() {  # $1 = "" | /start | /fail ; Rumpf = letzte Logzeilen
  [ -n "$HC" ] || return 0
  curl -fsS -m 15 --retry 3 -o /dev/null --data-binary @<(tail -c 10000 "$LOG" 2>/dev/null) "$HC$1" || true
}
on_exit() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "Backup FEHLGESCHLAGEN (Code $rc): $(date -Is)" | tee -a "$LOG" >&2
    ping_hc /fail
  fi
  rm -f "$LOG"
}
trap on_exit EXIT
exec > >(tee -a "$LOG") 2>&1

ping_hc /start

# Sperren abgebrochener Läufe wegräumen (nur verwaiste, laufende Vorgänge bleiben unberührt)
restic unlock || true

# 1. Datenbank konsistent kopieren (läuft im Container, App bleibt online)
docker compose exec -T app python -m app.backup

# 2. Originale, abgeleitete Dateien und Datenbankkopie sichern
restic backup --tag smartarchive --exclude data/cache data/files data/backup

# 3. Aufbewahrung: 14 Tage, 8 Wochen, 24 Monate
restic forget --tag smartarchive --keep-daily 14 --keep-weekly 8 --keep-monthly 24 --prune

echo "Backup fertig: $(date -Is)"
ping_hc ""
