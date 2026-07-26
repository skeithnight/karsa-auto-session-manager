#!/usr/bin/env bash
# db_maintenance.sh — Daily database backup and cleanup for KARSA
#
# Runs daily via launchd (macOS) or cron (Linux).
# Backs up the database, cleans old data, and VACUUMs to reclaim space.
#
# Usage:
#   ./scripts/db_maintenance.sh           # Full maintenance
#   ./scripts/db_maintenance.sh --backup-only  # Backup only, no cleanup
#   ./scripts/db_maintenance.sh --cleanup-only # Cleanup only, no backup
#
# Retention policy:
#   - historical_candles: 30 days
#   - backtest_results: 10,000 rows (keep most recent)
#   - signals: 7 days
#   - shadow_trades: 90 days
#   - trades: keep all (permanent record)

set -euo pipefail

# Configuration
BACKUP_DIR="${BACKUP_DIR:-/Users/dwiki.nugraha/backups/karsa}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"  # Keep backups for 7 days
LOG_FILE="/tmp/karsa-db-maintenance.log"
CONTAINER_NAME="karsa-postgres"
DB_USER="${POSTGRES_USER:-karsa}"
DB_NAME="${POSTGRES_DB:-karsa}"

# Functions
log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

check_postgres() {
    if ! docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        log "ERROR: Postgres is not ready. Attempting restart..."
        docker restart "$CONTAINER_NAME"
        sleep 15
        if ! docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
            log "ERROR: Postgres still not ready after restart. Aborting."
            exit 1
        fi
        log "Postgres restarted successfully."
    fi
}

do_backup() {
    log "=== Starting Database Backup ==="

    # Create backup directory
    mkdir -p "$BACKUP_DIR"

    # Generate backup filename with timestamp
    local TIMESTAMP
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    local BACKUP_FILE="$BACKUP_DIR/karsa_${TIMESTAMP}.sql"
    local BACKUP_FILE_GZ="${BACKUP_FILE}.gz"

    # Run pg_dump
    log "Backing up database to $BACKUP_FILE_GZ"
    if docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" \
        --format=custom --compress=9 > "$BACKUP_FILE_GZ" 2>/dev/null; then
        local SIZE
        SIZE=$(du -h "$BACKUP_FILE_GZ" | cut -f1)
        log "Backup completed: $BACKUP_FILE_GZ ($SIZE)"
    else
        log "ERROR: Backup failed!"
        exit 1
    fi

    # Cleanup old backups
    log "Cleaning up backups older than $BACKUP_RETENTION_DAYS days..."
    local DELETED
    DELETED=$(find "$BACKUP_DIR" -name "karsa_*.sql.gz" -mtime +"$BACKUP_RETENTION_DAYS" -delete -print | wc -l | tr -d ' ')
    log "Removed $DELETED old backup(s)"

    log "=== Backup Complete ==="
}

do_cleanup() {
    log "=== Starting Database Cleanup ==="

    # Get table sizes before cleanup
    log "Table sizes before cleanup:"
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
         FROM pg_tables WHERE schemaname='public'
         ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;" 2>/dev/null | tee -a "$LOG_FILE"

    # 1. Clean old historical candles (keep last $RETENTION_DAYS days)
    log "Cleaning historical_candles older than $RETENTION_DAYS days..."
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "DELETE FROM historical_candles WHERE ts < NOW() - INTERVAL '${RETENTION_DAYS} days';" 2>/dev/null || true
    log "Historical candles cleaned"

    # 2. Clean old backtest_results (keep last 10,000)
    log "Cleaning backtest_results (keeping last 10,000)..."
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "DELETE FROM backtest_results WHERE id NOT IN
         (SELECT id FROM backtest_results ORDER BY id DESC LIMIT 10000);" 2>/dev/null || true
    log "Backtest results cleaned"

    # 3. Clean old signals (keep last 7 days)
    log "Cleaning signals older than 7 days..."
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "DELETE FROM signals WHERE timestamp < NOW() - INTERVAL '7 days';" 2>/dev/null || true
    log "Signals cleaned"

    # 4. Clean old shadow_trades (keep last 90 days)
    log "Cleaning shadow_trades older than 90 days..."
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "DELETE FROM shadow_trades WHERE exit_time < NOW() - INTERVAL '90 days';" 2>/dev/null || true
    log "Shadow trades cleaned"

    # 5. VACUUM to reclaim space
    log "Running VACUUM FULL on tables..."
    for TABLE in historical_candles backtest_results signals shadow_trades; do
        docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c \
            "VACUUM FULL $TABLE;" 2>/dev/null || true
        log "VACUUM completed: $TABLE"
    done

    # Get table sizes after cleanup
    log "Table sizes after cleanup:"
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
         FROM pg_tables WHERE schemaname='public'
         ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;" 2>/dev/null | tee -a "$LOG_FILE"

    # 6. Check disk usage
    log "Checking Docker VM disk usage..."
    local DISK_USAGE
    DISK_USAGE=$(docker exec "$CONTAINER_NAME" df -h /var/lib/postgresql/data 2>/dev/null | tail -1 | awk '{print $5}' || echo "unknown")
    log "Disk usage: $DISK_USAGE"

    if [[ "$DISK_USAGE" == *"100%"* ]]; then
        log "WARNING: Disk is 100% full! Manual intervention may be needed."
    elif [[ "$DISK_USAGE" == *"9"* ]] || [[ "$DISK_USAGE" == *"8"* ]]; then
        log "WARNING: Disk usage is high ($DISK_USAGE). Consider increasing Docker VM disk size."
    fi

    log "=== Cleanup Complete ==="
}

# Main
main() {
    log "=== KARSA Database Maintenance ==="
    log "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

    check_postgres

    case "${1:-}" in
        --backup-only)
            do_backup
            ;;
        --cleanup-only)
            do_cleanup
            ;;
        *)
            do_backup
            do_cleanup
            ;;
    esac

    log "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    log "=== Maintenance Complete ==="
}

main "$@"
