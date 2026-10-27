#!/usr/bin/env bash
# Monatlicher Wiederherstellungstest: letzten Stand in ein Temp-Verzeichnis holen und prüfen.
set -euo pipefail
: "${RESTIC_REPOSITORY:?}" "${RESTIC_PASSWORD_FILE:?}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
restic restore latest --tag smartarchive --target "$tmp"
db=$(find "$tmp" -name smartarchive.sqlite3 -path "*backup*" | head -1)
[ -n "$db" ] || { echo "FEHLER: keine Datenbank im Backup"; exit 1; }
check=$(python3 -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);print(c.execute('PRAGMA integrity_check').fetchone()[0]);print(c.execute('select count(*) from documents').fetchone()[0])" "$db")
files=$(find "$tmp" -path "*originals*" -type f | wc -l)
echo "Integrität: $(echo "$check" | head -1), Dokumente: $(echo "$check" | tail -1), Originaldateien: $files"
[ "$(echo "$check" | head -1)" = "ok" ] || exit 1
echo "Wiederherstellungstest erfolgreich."
