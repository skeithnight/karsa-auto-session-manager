#!/usr/bin/env bash
# docker_cleanup.sh — Automated Docker disk cleanup for KARSA
#
# Safe pruning: only unused containers, images, build cache, volumes.
# Never touches running containers or named volumes in use.
#
# Usage:
#   ./scripts/docker_cleanup.sh           # Full cleanup
#   ./scripts/docker_cleanup.sh --check-only  # Print disk usage, no cleanup
#
# Exit codes:
#   0 — success
#   1 — disk usage critical (>90%) even after cleanup

set -euo pipefail

DISK_WARN_THRESHOLD=80
DISK_CRIT_THRESHOLD=90
LOG_FILE="/tmp/karsa-docker-cleanup.log"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

get_disk_usage() {
    # Returns disk usage percentage for the Docker VM disk
    local usage
    if [[ "$(uname)" == "Darwin" ]]; then
        usage=$(docker run --rm -v /:/host alpine sh -c 'df /host 2>/dev/null | tail -1 | awk "{print \$5}" | tr -d "%"')
    else
        usage=$(df /var/lib/docker 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
    fi
    echo "${usage:-0}"
}

check_disk() {
    local usage
    usage=$(get_disk_usage)
    log "Disk usage: ${usage}%"

    if (( usage >= DISK_CRIT_THRESHOLD )); then
        log "⚠️  CRITICAL: Disk usage ${usage}% >= ${DISK_CRIT_THRESHOLD}%"
        return 1
    elif (( usage >= DISK_WARN_THRESHOLD )); then
        log "⚠️  WARNING: Disk usage ${usage}% >= ${DISK_WARN_THRESHOLD}%"
        return 0
    else
        log "✅ Disk usage healthy (${usage}%)"
        return 0
    fi
}

do_cleanup() {
    log "Starting Docker cleanup..."

    # 1. Prune stopped containers
    local before after
    before=$(docker ps -a -q | wc -l | tr -d ' ')
    docker container prune -f 2>/dev/null || true
    after=$(docker ps -a -q | wc -l | tr -d ' ')
    log "Containers: ${before} → ${after} (removed $((before - after)))"

    # 2. Prune dangling images
    before=$(docker images -f "dangling=true" -q | wc -l | tr -d ' ')
    docker image prune -f 2>/dev/null || true
    after=$(docker images -f "dangling=true" -q | wc -l | tr -d ' ')
    log "Dangling images: ${before} → ${after} (removed $((before - after)))"

    # 3. Prune build cache (keep last 24h)
    docker builder prune -f --filter "until=24h" 2>/dev/null || true
    log "Build cache pruned (kept last 24h)"

    # 4. Prune unused volumes (only anonymous volumes, not named)
    docker volume prune -f 2>/dev/null || true
    log "Unused anonymous volumes pruned"

    # 5. Summary
    local total_images
    total_images=$(docker images -q | wc -l | tr -d ' ')
    local total_containers
    total_containers=$(docker ps -a -q | wc -l | tr -d ' ')
    log "Remaining: ${total_images} images, ${total_containers} containers"
}

# --- Main ---
if [[ "${1:-}" == "--check-only" ]]; then
    check_disk
    exit $?
fi

log "=== KARSA Docker Cleanup ==="

# Check before cleanup
check_disk || true

# Run cleanup
do_cleanup

# Check after cleanup
if ! check_disk; then
    log "❌ Disk still critical after cleanup — manual intervention needed"
    exit 1
fi

log "=== Cleanup complete ==="
