#!/usr/bin/env bash
# ==============================================================================
# Athenea - Automated Database Backup & Retention Script
# ==============================================================================
set -e

# Load environment configuration if available
if [ -f /etc/athenea.env ]; then
    set -a
    . /etc/athenea.env
    set +a
elif [ -f /etc/bookserver.env ]; then
    set -a
    . /etc/bookserver.env
    set +a
elif [ -f "$(dirname "$0")/.env" ]; then
    set -a
    . "$(dirname "$0")/.env"
    set +a
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/athenea}"
mkdir -p "$BACKUP_DIR"

DATE_STR=$(date +"%Y%m%d_%H%M%S")
DOW=$(date +"%u") # 1-7 (7 is Sunday)
DB_NAME="${DB_NAME:-athenea}"
TARGET_FILE="$BACKUP_DIR/${DB_NAME}_${DATE_STR}.sql.gz"
LOG_FILE="${LOG_FILE:-/var/log/athenea_backup.log}"

echo "[$(date -Iseconds)] Starting PostgreSQL database backup for $DB_NAME..." >> "$LOG_FILE"

export PGPASSWORD="${DB_PASSWORD:-$PGPASSWORD}"
DB_USER="${DB_USER:-athenea}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

COMPRESS_CMD="gzip -6"
if command -v pigz >/dev/null 2>&1; then
    COMPRESS_CMD="pigz -p 4"
fi

if pg_dump -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" "$DB_NAME" | $COMPRESS_CMD > "$TARGET_FILE"; then
    FILE_SIZE=$(du -h "$TARGET_FILE" | cut -f1)
    echo "[$(date -Iseconds)] Backup created successfully: $TARGET_FILE ($FILE_SIZE)" >> "$LOG_FILE"
    ln -sf "$TARGET_FILE" "$BACKUP_DIR/${DB_NAME}_latest.sql.gz"
else
    echo "[$(date -Iseconds)] ERROR: Backup failed!" >> "$LOG_FILE"
    rm -f "$TARGET_FILE"
    exit 1
fi

# Retention Policy:
# Delete non-Sunday daily backups older than 7 days, keep Sunday backups for 30 days
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +7 ! -name "*_latest.sql.gz" | while read -r f; do
    F_NAME=$(basename "$f")
    F_DATE=$(echo "$F_NAME" | grep -oE '[0-9]{8}')
    if [ -n "$F_DATE" ] && date -d "$F_DATE" >/dev/null 2>&1; then
        F_DOW=$(date -d "$F_DATE" +"%u")
        if [ "$F_DOW" != "7" ]; then
            rm -f "$f"
            echo "[$(date -Iseconds)] Rotated daily backup: $f" >> "$LOG_FILE"
        fi
    fi
done

find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +30 ! -name "*_latest.sql.gz" -delete

echo "[$(date -Iseconds)] Backup and rotation complete." >> "$LOG_FILE"
