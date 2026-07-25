#!/bin/bash
# ASM Forensic Capture — 1-hour monitoring
# Captures live/shadow logs + Redis state snapshots every 30s

OUTDIR="/Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/monitoring/asm_forensic_20260724_1925"
DURATION=3600
INTERVAL=30

echo "[$(date -Iseconds)] ASM forensic capture started — ${DURATION}s"
echo "Output: $OUTDIR"

# --- Continuous filtered log capture ---
docker logs -f karsa-live 2>&1 | grep -E --line-buffered \
  "signal:|executed|skip|ERROR|CRITICAL|AI|analyst|PortfolioRisk|risk_gate|MarketOrder|SL|TP|breakeven|trailing|regime_shift|position saved|position removed|wallet|ws_disconnect|WebSocket|psubscribe|insufficient|degraded|phantom|orphan|funding|spread|liquidity" \
  > "$OUTDIR/live_filtered.log" &
PID_LIVE=$!

docker logs -f karsa-shadow 2>&1 | grep -E --line-buffered \
  "signal:|executed|skip|ERROR|CRITICAL|AI|analyst|PortfolioRisk|risk_gate|shadow_skip|shadow_score|shadow_exit|position|regime|reject|Low Score|Extreme" \
  > "$OUTDIR/shadow_filtered.log" &
PID_SHADOW=$!

docker logs -f karsa-data-engine 2>&1 | grep -E --line-buffered \
  "publish|candle|universe|regime|ERROR|CRITICAL|scan|ATR" \
  > "$OUTDIR/de_filtered.log" &
PID_DE=$!

echo "Log capture PIDs: live=$PID_LIVE shadow=$PID_SHADOW de=$PID_DE"

# --- Redis state snapshots ---
SNAP=0
END=$(($(date +%s) + DURATION))

while [ $(date +%s) -lt $END ]; do
    TS=$(date +%Y%m%d_%H%M%S)
    SNAP=$((SNAP + 1))

    # Position counts + keys
    docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null > "$OUTDIR/s${SNAP}_live_pos.txt"
    docker exec karsa-redis redis-cli KEYS "shadow:position:*" 2>/dev/null > "$OUTDIR/s${SNAP}_shadow_pos.txt"

    LIVE_COUNT=$(wc -l < "$OUTDIR/s${SNAP}_live_pos.txt" 2>/dev/null || echo 0)
    SHADOW_COUNT=$(wc -l < "$OUTDIR/s${SNAP}_shadow_pos.txt" 2>/dev/null || echo 0)
    echo "[$(date -Iseconds)] snap=$SNAP live=$LIVE_COUNT shadow=$SHADOW_COUNT"

    # Dump position details
    for key in $(cat "$OUTDIR/s${SNAP}_live_pos.txt" 2>/dev/null); do
        docker exec karsa-redis redis-cli GET "$key" 2>/dev/null >> "$OUTDIR/s${SNAP}_live_pos_detail.json"
    done
    for key in $(cat "$OUTDIR/s${SNAP}_shadow_pos.txt" 2>/dev/null); do
        docker exec karsa-redis redis-cli GET "$key" 2>/dev/null >> "$OUTDIR/s${SNAP}_shadow_pos_detail.json"
    done

    # Settings / risk params
    docker exec karsa-redis redis-cli GET "karsa:settings:max_positions" 2>/dev/null > "$OUTDIR/s${SNAP}_maxpos.txt"
    docker exec karsa-redis redis-cli GET "karsa:settings:risk_pct" 2>/dev/null > "$OUTDIR/s${SNAP}_riskpct.txt"

    # Circuit breaker
    docker exec karsa-redis redis-cli GET "karsa:circuit_breaker:pnl" 2>/dev/null > "$OUTDIR/s${SNAP}_cb.txt"
    docker exec karsa-redis redis-cli GET "karsa:circuit_breaker:state" 2>/dev/null >> "$OUTDIR/s${SNAP}_cb.txt"

    # Blocked symbols
    docker exec karsa-redis redis-cli KEYS "karsa:blocked_symbol:*" 2>/dev/null > "$OUTDIR/s${SNAP}_blocked.txt"

    # Global state count
    docker exec karsa-redis redis-cli KEYS "global:state:*" 2>/dev/null | wc -l > "$OUTDIR/s${SNAP}_global_state_count.txt"

    sleep $INTERVAL
done

# --- Cleanup ---
kill $PID_LIVE $PID_SHADOW $PID_DE 2>/dev/null
wait $PID_LIVE $PID_SHADOW $PID_DE 2>/dev/null

echo "[$(date -Iseconds)] Capture complete. Snapshots: $SNAP"
