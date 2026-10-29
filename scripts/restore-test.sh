#!/usr/bin/env bash
# Monatlicher Wiederherstellungstest – ohne Platz für eine Vollkopie zu brauchen:
# 1. Datenbank aus der letzten Sicherung zurückholen und prüfen (Integrität, Anzahl Dokumente).
# 2. Dateiliste der Sicherung mit der Datenbank abgleichen (sind die Originale enthalten?).
# 3. Einen wechselnden Zwölftel der gesicherten Daten tatsächlich herunterladen und prüfen;
#    über ein Jahr wird so jedes Byte einmal gelesen.
set -euo pipefail
: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD_FILE:?}"
HC="${HEALTHCHECK_URL:-}"
tmp=$(mktemp -d /var/tmp/sa-restoretest.XXXXXX)
trap 'rc=$?; rm -rf "$tmp"; if [ $rc -ne 0 ]; then echo "Wiederherstellungstest FEHLGESCHLAGEN: $(date -Is)"; [ -n "$HC" ] && curl -fsS -m 15 -o /dev/null --data-raw "Wiederherstellungstest fehlgeschlagen" "$HC/fail" || true; fi' EXIT

listing="$tmp/ls.txt"
restic ls --tag smartarchive latest > "$listing"
dbpath=$(grep -E '/backup/smartarchive\.sqlite3$' "$listing" | head -1)
[ -n "$dbpath" ] || { echo "FEHLER: keine Datenbank in der Sicherung"; exit 1; }
restic dump --tag smartarchive latest "$dbpath" > "$tmp/db.sqlite3"

result=$(python3 - "$tmp/db.sqlite3" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("PRAGMA integrity_check").fetchone()[0])
print(c.execute("SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL").fetchone()[0])
PY
)
integrity=$(echo "$result" | head -1)
docs=$(echo "$result" | tail -1)
originals=$(grep -c '/originals/.*\.' "$listing" || true)
echo "Datenbank: Integrität $integrity, Dokumente $docs; Originaldateien in der Sicherung: $originals"
[ "$integrity" = "ok" ] || { echo "FEHLER: Datenbank beschädigt"; exit 1; }
[ "$originals" -ge "$docs" ] || { echo "FEHLER: weniger Originaldateien als Dokumente"; exit 1; }

part="$(date +%-m)/12"
echo "Prüfe Datenteil $part …"
restic check --read-data-subset="$part"
echo "Wiederherstellungstest erfolgreich."
