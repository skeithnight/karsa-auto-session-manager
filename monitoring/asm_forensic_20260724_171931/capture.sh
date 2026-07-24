#!/bin/bash
# ASM Forensic Capture — 1-hour monitoring script
# Captures live, shadow, data-engine logs + Redis state snapshots

OUTDIR="/Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/monitoring/asm_forensic_20260724_171931"
DURATION=3600  # 1 hour
INTERVAL=30   # Redis state snapshot every 30s

echo "[$(date -Iseconds)] ASM forensic capture started — ${DURATION}s duration"
echo "Output: $OUTDIR"

# --- Continuous log capture (background) ---
docker logs -f karsa-live --since 1s > "$OUTDIR/live_full.log" 2>&1 &
PID_LIVE=$!
docker logs -f karsa-shadow --since 1s > "$OUTDIR/shadow_full.log" 2>&1 &
PID_SHADOW=$!
docker logs -f karsa-data-engine --since 1s > "$OUTDIR/data_engine_full.log" 2>&1 &
PID_DE=$!
docker logs -f karsa-commander --since 1s > "$OUTDIR/commander_full.log" 2>&1 &
PID_CMD=$!

echo "Log PIDs: live=$PID_LIVE shadow=$PID_SHADOW de=$PID_DE cmd=$PID_CMD"

# --- Redis state snapshots ---
SNAP_COUNT=0
END_TIME=$(($(date +%s) + DURATION))

while [ $(date +%s) -lt $END_TIME ]; do
    TS=$(date +%Y%m%d_%H%M%S)
    SNAP_COUNT=$((SNAP_COUNT + 1))

    # Position counts
    docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null | wc -l > "$OUTDIR/snap_${TS}_live_pos_count.txt"
    docker exec karsa-redis redis-cli KEYS "shadow:position:*" 2>/dev/null | wc -l > "$OUTDIR/snap_${TS}_shadow_pos_count.txt"

    # All live positions
    docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null > "$OUTDIR/snap_${TS}_live_pos_keys.txt"
    for key in $(docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null); do
        echo "=== $key ===" >> "$OUTDIR/snap_${TS}_live_positions.json"
        docker exec karsa-redis redis-cli GET "$key" 2>/dev/null >> "$OUTDIR/snap_${TS}_live_positions.json"
        echo "" >> "$OUTDIR/snap_${TS}_live_positions.json"
    done

    # All shadow positions
    docker exec karsa-redis redis-cli KEYS "shadow:position:*" 2>/dev/null > "$OUTDIR/snap_${TS}_shadow_pos_keys.txt"
    for key in $(docker exec karsa-redis redis-cli KEYS "shadow:position:*" 2>/dev/null); do
        echo "=== $key ===" >> "$OUTDIR/snap_${TS}_shadow_positions.json"
        docker exec karsa-redis redis-cli GET "$key" 2>/dev/null >> "$OUTDIR/snap_${TS}_shadow_positions.json"
        echo "" >> "$OUTDIR/snap_${TS}_shadow_positions.json"
    done

    # Market state (sample a few symbols)
    docker exec karsa-redis redis-cli KEYS "karsa:market:*" 2>/dev/null | head -20 > "$OUTDIR/snap_${TS}_market_keys.txt"

    # Global state keys
    docker exec karsa-redis redis-cli KEYS "global:state:*" 2>/dev/null | head -10 > "$OUTDIR/snap_${TS}_global_state_keys.txt"

    # Settings / risk params
    docker exec karsa-redis redis-cli GET "karsa:settings:max_positions" 2>/dev/null > "$OUTDIR/snap_${TS}_max_positions.txt"
    docker exec karsa-redis redis-cli GET "karsa:settings:risk_pct" 2>/dev/null > "$OUTDIR/snap_${TS}_risk_pct.txt"
    docker exec karsa-redis redis-cli GET "system:universe:symbols" 2>/dev/null > "$OUTDIR/snap_${TS}_universe.txt"

    # Circuit breaker state
    docker exec karsa-redis redis-cli GET "karsa:circuit_breaker:pnl" 2>/dev/null > "$OUTDIR/snap_${TS}_circuit_breaker.txt"
    docker exec karsa-redis redis-cli GET "karsa:circuit_breaker:state" 2>/dev/null >> "$OUTDIR/snap_${TS}_circuit_breaker.txt"

    # Blocked symbols
    docker exec karsa-redis redis-cli KEYS "karsa:blocked_symbol:*" 2>/dev/null > "$OUTDIR/snap_${TS}_blocked_symbols.txt"

    sleep $INTERVAL
done

# --- Stop log capture ---
kill $PID_LIVE $PID_SHADOW $PID_DE $PID_CMD 2>/dev/null
wait $PID_LIVE $PID_SHADOW $PID_DE $PID_CMD 2>/dev/null

echo "[$(date -Iseconds)] Capture complete. Snapshots: $SNAP_COUNT"
echo "Files:"
ls -la "$OUTDIR"/*.log "$OUTDIR"/*.txt "$OUTDIR"/*.json 2>/dev/null | wc -l
