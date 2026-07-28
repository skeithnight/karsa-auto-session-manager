#!/bin/bash
# ============================================================================
# ASM 5-Hour Observation Capture
# ============================================================================
# Purpose: Monitor live trading system for 5 hours after Phase 1-3 + fixes
# Focus: New features (ELO, correlation, sector, vol surface) + critical fixes
# ============================================================================

set -euo pipefail

# --- Configuration ---
SESSION_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SESSION_DIR/logs"
SNAPSHOT_DIR="$SESSION_DIR/snapshots"
REDIS_DIR="$SESSION_DIR/redis"
DURATION_HOURS=5
INTERVAL_SECONDS=60  # Snapshot every 60 seconds

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "[$(date '+%H:%M:%S')] $1"; }

# --- Setup ---
mkdir -p "$LOG_DIR" "$SNAPSHOT_DIR" "$REDIS_DIR"

START_TIME=$(date +%s)
END_TIME=$((START_TIME + DURATION_HOURS * 3600))
SNAPSHOT_COUNT=0

log "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
log "${BLUE}  ASM 5-Hour Observation Capture${NC}"
log "${BLUE}  Started: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
log "${BLUE}  Duration: ${DURATION_HOURS} hours${NC}"
log "${BLUE}  Interval: ${INTERVAL_SECONDS}s${NC}"
log "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

# --- Capture Functions ---

capture_container_logs() {
    local container=$1
    local outfile=$2
    docker logs --tail 200 "$container" 2>&1 | grep -E \
        -e "DecisionEngine" \
        -e "PortfolioRisk" \
        -e "ActivePosition" \
        -e "BybitExecutor" \
        -e "ShadowExecutor" \
        -e "MarketAnalyzer" \
        -e "RegimeClassifier" \
        -e "ELO" \
        -e "elo" \
        -e "correlation" \
        -e "sector" \
        -e "VolSurface" \
        -e "vol_surface" \
        -e "regime" \
        -e "ENTRY" \
        -e "EXIT" \
        -e "SL" \
        -e "TP" \
        -e "breakeven" \
        -e "scale_out" \
        -e "ERROR" \
        -e "WARN" \
        -e "orphan" \
        -e "reconcil" \
        -e "rebalance" \
        -e "gate" \
        -e "signal" \
        > "$outfile" 2>/dev/null || true
}

capture_redis_state() {
    local snapshot_file=$1
    local timestamp=$2

    # Trade store stats
    echo "=== TRADE STORE ===" >> "$snapshot_file"
    docker exec karsa-redis redis-cli keys "karsa:trade:*" 2>/dev/null | wc -l >> "$snapshot_file" 2>/dev/null || echo "0" >> "$snapshot_file"

    # ELO ratings
    echo -e "\n=== ELO RATINGS ===" >> "$snapshot_file"
    for key in $(docker exec karsa-redis redis-cli keys "karsa:elo:*" 2>/dev/null); do
        echo "$key: $(docker exec karsa-redis redis-cli get "$key" 2>/dev/null)" >> "$snapshot_file"
    done

    # Correlation data
    echo -e "\n=== CORRELATION ===" >> "$snapshot_file"
    for key in $(docker exec karsa-redis redis-cli keys "karsa:correlation:*" 2>/dev/null); do
        echo "$key: $(docker exec karsa-redis redis-cli get "$key" 2>/dev/null)" >> "$snapshot_file"
    done

    # Volatility surface
    echo -e "\n=== VOL SURFACE ===" >> "$snapshot_file"
    for key in $(docker exec karsa-redis redis-cli keys "karsa:vol_surface:*" 2>/dev/null); do
        echo "$key: $(docker exec karsa-redis redis-cli get "$key" 2>/dev/null)" >> "$snapshot_file"
    done

    # Sector scores
    echo -e "\n=== SECTOR SCORES ===" >> "$snapshot_file"
    for key in $(docker exec karsa-redis redis-cli keys "karsa:sector:*" 2>/dev/null); do
        echo "$key: $(docker exec karsa-redis redis-cli get "$key" 2>/dev/null)" >> "$snapshot_file"
    done

    # Dynamic gate threshold
    echo -e "\n=== GATE THRESHOLD ===" >> "$snapshot_file"
    docker exec karsa-redis redis-cli get "karsa:gate:dynamic_threshold" 2>/dev/null >> "$snapshot_file" || echo "not set" >> "$snapshot_file"

    # HMM regime
    echo -e "\n=== HMM REGIME ===" >> "$snapshot_file"
    docker exec karsa-redis redis-cli get "system:hmm:regime" 2>/dev/null >> "$snapshot_file" || echo "not set" >> "$snapshot_file"

    # Active positions (live)
    echo -e "\n=== LIVE POSITIONS ===" >> "$snapshot_file"
    for key in $(docker exec karsa-redis redis-cli keys "karsa:position:*" 2>/dev/null); do
        echo "$key: $(docker exec karsa-redis redis-cli get "$key" 2>/dev/null)" >> "$snapshot_file"
    done

    # Shadow positions
    echo -e "\n=== SHADOW POSITIONS ===" >> "$snapshot_file"
    for key in $(docker exec karsa-redis redis-cli keys "shadow:position:*" 2>/dev/null); do
        echo "$key: $(docker exec karsa-redis redis-cli get "$key" 2>/dev/null)" >> "$snapshot_file"
    done

    # Ranking decision
    echo -e "\n=== RANKING DECISION ===" >> "$snapshot_file"
    docker exec karsa-redis redis-cli get "karsa:ranking:decision" 2>/dev/null >> "$snapshot_file" || echo "not set" >> "$snapshot_file"
    docker exec karsa-redis redis-cli get "karsa:ranking:details" 2>/dev/null >> "$snapshot_file" || echo "not set" >> "$snapshot_file"
}

capture_position_summary() {
    local snapshot_file=$1

    echo -e "\n=== POSITION SUMMARY ===" >> "$snapshot_file"

    # Live positions count
    local live_count=$(docker exec karsa-redis redis-cli keys "karsa:position:*" 2>/dev/null | wc -l)
    echo "Live positions: $live_count" >> "$snapshot_file"

    # Shadow positions count
    local shadow_count=$(docker exec karsa-redis redis-cli keys "shadow:position:*" 2>/dev/null | wc -l)
    echo "Shadow positions: $shadow_count" >> "$snapshot_file"

    # Recent trades
    echo -e "\n=== RECENT TRADES (last 10) ===" >> "$snapshot_file"
    docker exec karsa-live python -c "
import sys
sys.path.insert(0, '/app')
from app.core.trade_store import TradeStore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

db_url = os.getenv('DATABASE_URL', 'postgresql://karsa:karsa@postgres:5432/karsa')
engine = create_engine(db_url)
Session = sessionmaker(bind=engine)
session = Session()

from app.core.trade_store import Trade
trades = session.query(Trade).order_by(Trade.id.desc()).limit(10).all()
for t in trades:
    print(f'{t.symbol} {t.side} entry={t.entry_price} exit={t.exit_price} pnl={t.realized_pnl} regime={t.entry_regime}')
session.close()
" 2>/dev/null >> "$snapshot_file" || echo "Could not fetch trades" >> "$snapshot_file"
}

capture_wallet_balance() {
    local snapshot_file=$1

    echo -e "\n=== WALLET BALANCE ===" >> "$snapshot_file"
    docker exec karsa-live python -c "
import sys
sys.path.insert(0, '/app')
from app.execution.bybit_client import BybitClient
from app.core.config import get_settings
import asyncio

async def get_balance():
    settings = get_settings()
    client = BybitClient(settings)
    balance = await client.get_balance()
    print(f'Total: {balance.get(\"total\", 0):.2f} USDT')
    print(f'Available: {balance.get(\"available\", 0):.2f} USDT')
    await client.close()

asyncio.run(get_balance())
" 2>/dev/null >> "$snapshot_file" || echo "Could not fetch balance" >> "$snapshot_file"
}

# --- Main Capture Loop ---
log "${GREEN}Starting capture loop...${NC}"

while [ $(date +%s) -lt $END_TIME ]; do
    SNAPSHOT_COUNT=$((SNAPSHOT_COUNT + 1))
    TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
    ELAPSED=$(( $(date +%s) - START_TIME ))
    REMAINING=$(( (END_TIME - $(date +%s)) / 60 ))

    log "${YELLOW}[$SNAPSHOT_COUNT] Elapsed: ${ELAPSED}s | Remaining: ~${REMAINING}min${NC}"

    # Container logs
    for container in karsa-live karsa-shadow karsa-data-engine; do
        if docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
            capture_container_logs "$container" "$LOG_DIR/${container}_${SNAPSHOT_COUNT}.log"
        fi
    done

    # Redis state snapshot
    capture_redis_state "$SNAPSHOT_DIR/snapshot_${SNAPSHOT_COUNT}.txt" "$TIMESTAMP"

    # Position summary
    capture_position_summary "$SNAPSHOT_DIR/snapshot_${SNAPSHOT_COUNT}.txt"

    # Wallet balance every 10 snapshots (every 10 minutes)
    if [ $((SNAPSHOT_COUNT % 10)) -eq 0 ]; then
        capture_wallet_balance "$SNAPSHOT_DIR/snapshot_${SNAPSHOT_COUNT}.txt"
        log "${GREEN}  Wallet balance captured${NC}"
    fi

    # Sleep until next interval
    sleep $INTERVAL_SECONDS
done

# --- Final Summary ---
log "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
log "${BLUE}  Capture Complete${NC}"
log "${BLUE}  Snapshots: $SNAPSHOT_COUNT${NC}"
log "${BLUE}  Duration: $(( ( $(date +%s) - START_TIME ) / 60 )) minutes${NC}"
log "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
