#!/bin/bash
# 1-Hour ASM Forensic Capture
CAPDIR="$(cd "$(dirname "$0")" && pwd)"
DURATION=3600
INTERVAL=30
SNAPSHOTS=$((DURATION / INTERVAL))

echo "=== 1-Hour ASM Capture ===" | tee "$CAPDIR/capture.log"
echo "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$CAPDIR/capture.log"
echo "ASM: 70% risk, max 5 positions" | tee -a "$CAPDIR/capture.log"
echo "" >> "$CAPDIR/capture.log"

# Filtered logs
docker logs karsa-live --since 0s -f 2>&1 | grep --line-buffered -E \
  "APM:|regime shift|force closed|synced orphan|skipping orphan|grace|phantom|PURGING|UNKNOWN|ML Prefilter|AI.*gate|AI.*reject|AI.*pass|execute:|Signal:|entry_price|exit detected|trade_memory|heartbeat|skip.*position|Worker.*queue|pipeline" \
  > "$CAPDIR/live_filtered.log" &
LIVE_PID=$!

docker logs karsa-shadow --since 0s -f 2>&1 | grep --line-buffered -E \
  "APM:|regime shift|force closed|execute:|entry_price|exit detected|shadow_score" \
  > "$CAPDIR/shadow_filtered.log" &
SHADOW_PID=$!

echo "PIDs: live=$LIVE_PID shadow=$SHADOW_PID" >> "$CAPDIR/capture.log"

for i in $(seq 1 $SNAPSHOTS); do
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  SNAP_FILE="$CAPDIR/snapshot_$(printf '%04d' $i).json"

  LIVE_COUNT=$(docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null | wc -l | tr -d ' ')
  SHADOW_COUNT=$(docker exec karsa-redis redis-cli KEYS "shadow:position:*" 2>/dev/null | wc -l | tr -d ' ')
  LIVE_SYMS=$(docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null | sed 's/karsa:position://;s/:LONG//;s/:SHORT//' | tr '\n' ',' | sed 's/,$//')
  SHADOW_SYMS=$(docker exec karsa-redis redis-cli KEYS "shadow:position:*" 2>/dev/null | sed 's/shadow:position://;s/:LONG//;s/:SHORT//' | tr '\n' ',' | sed 's/,$//')
  UNIVERSE=$(docker exec karsa-redis redis-cli GET "system:universe:symbols" 2>/dev/null | python3 -c "import sys,json; print(len(json.loads(sys.stdin.read())))" 2>/dev/null || echo "0")

  cat > "$SNAP_FILE" <<EOF
{
  "timestamp": "$TS",
  "snapshot": $i,
  "live_positions": $LIVE_COUNT,
  "shadow_positions": $SHADOW_COUNT,
  "live_symbols": "$LIVE_SYMS",
  "shadow_symbols": "$SHADOW_SYMS",
  "universe_count": $UNIVERSE
}
EOF

  if [ $((i % 10)) -eq 0 ]; then
    echo "[$TS] Snap $i/$SNAPSHOTS | live=$LIVE_COUNT shadow=$SHADOW_COUNT | $LIVE_SYMS" | tee -a "$CAPDIR/capture.log"
  fi

  sleep $INTERVAL
done

kill $LIVE_PID $SHADOW_PID 2>/dev/null
echo "=== Capture Complete ===" | tee -a "$CAPDIR/capture.log"
echo "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$CAPDIR/capture.log"
