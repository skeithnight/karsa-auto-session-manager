#!/bin/bash
# 2-Hour Forensic Capture Script
# Captures Redis state snapshots + filtered logs every 30s for 2 hours
CAPDIR="$(cd "$(dirname "$0")" && pwd)"
DURATION=7200  # 2 hours
INTERVAL=30
SNAPSHOTS=$((DURATION / INTERVAL))

echo "=== 2-Hour Forensic Capture ===" | tee "$CAPDIR/capture.log"
echo "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$CAPDIR/capture.log"
echo "Snapshots: $SNAPSHOTS (every ${INTERVAL}s)" | tee -a "$CAPDIR/capture.log"
echo "" >> "$CAPDIR/capture.log"

# Live filtered log
docker logs karsa-live --since 0s -f 2>&1 | grep --line-buffered -E \
  "APM:|regime shift|force closed|synced orphan|skipping orphan|grace|bootstrap|AI.*gate|AI.*reject|AI.*pass|ML Prefilter|execute:|Signal:|entry_price|exit detected|trade_memory|phantom|heartbeat" \
  > "$CAPDIR/live_filtered.log" &
LIVE_PID=$!

# Shadow filtered log
docker logs karsa-shadow --since 0s -f 2>&1 | grep --line-buffered -E \
  "APM:|regime shift|force closed|synced orphan|skipping orphan|grace|execute:|Signal:|entry_price|exit detected" \
  > "$CAPDIR/shadow_filtered.log" &
SHADOW_PID=$!

# Data engine filtered log
docker logs karsa-data-engine --since 0s -f 2>&1 | grep --line-buffered -E \
  "UniverseScanner|OHLCV|exchange_connector|publish|redis_publisher" \
  > "$CAPDIR/dataengine_filtered.log" &
DATA_PID=$!

echo "PIDs: live=$LIVE_PID shadow=$SHADOW_PID data=$DATA_PID" >> "$CAPDIR/capture.log"

# Redis state snapshots
for i in $(seq 1 $SNAPSHOTS); do
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  SNAP_FILE="$CAPDIR/snapshot_$(printf '%04d' $i).json"

  LIVE_POSITIONS=$(docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null | wc -l)
  SHADOW_POSITIONS=$(docker exec karsa-redis redis-cli KEYS "shadow:position:*" 2>/dev/null | wc -l)
  LIVE_SYMS=$(docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null | sed 's/karsa:position://;s/:LONG//;s/:SHORT//' | tr '\n' ',' | sed 's/,$//')
  SHADOW_SYMS=$(docker exec karsa-redis redis-cli KEYS "shadow:position:*" 2>/dev/null | sed 's/shadow:position://;s/:LONG//;s/:SHORT//' | tr '\n' ',' | sed 's/,$//')

  # ASM settings
  ASM_SETTINGS=$(docker exec karsa-redis redis-cli GET "asm:settings" 2>/dev/null)

  # Circuit breaker
  CB_STATE=$(docker exec karsa-redis redis-cli GET "circuit_breaker:state" 2>/dev/null)

  # Universe
  UNIVERSE=$(docker exec karsa-redis redis-cli GET "system:universe:symbols" 2>/dev/null)

  # Wallet
  WALLET=$(docker exec karsa-redis redis-cli GET "wallet:balance" 2>/dev/null)

  cat > "$SNAP_FILE" <<EOF
{
  "timestamp": "$TS",
  "snapshot": $i,
  "live_positions": $LIVE_POSITIONS,
  "shadow_positions": $SHADOW_POSITIONS,
  "live_symbols": "$LIVE_SYMS",
  "shadow_symbols": "$SHADOW_SYMS",
  "asm_settings": $ASM_SETTINGS,
  "circuit_breaker": "$CB_STATE",
  "universe_count": $(echo "$UNIVERSE" | python3 -c "import sys,json; print(len(json.loads(sys.stdin.read())))" 2>/dev/null || echo "0"),
  "wallet": "$WALLET"
}
EOF

  if [ $((i % 10)) -eq 0 ]; then
    echo "[$TS] Snapshot $i/$SNAPSHOTS | live=$LIVE_POSITIONS shadow=$SHADOW_POSITIONS" | tee -a "$CAPDIR/capture.log"
  fi

  sleep $INTERVAL
done

# Cleanup
kill $LIVE_PID $SHADOW_PID $DATA_PID 2>/dev/null

echo "" | tee -a "$CAPDIR/capture.log"
echo "=== Capture Complete ===" | tee -a "$CAPDIR/capture.log"
echo "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$CAPDIR/capture.log"
echo "Snapshots: $(ls "$CAPDIR"/snapshot_*.json 2>/dev/null | wc -l)" | tee -a "$CAPDIR/capture.log"
