#!/bin/bash
DIR="monitoring/soft_gate_20260725_1641"
LOGFILE="$DIR/capture.log"
INTERVAL=30
DURATION=3600
START=$(date +%s)

echo "=== Soft Gate Monitor ===" > "$LOGFILE"
echo "Start: $(date -Iseconds)" >> "$LOGFILE"
echo "" >> "$LOGFILE"

SNAP=0
while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    if [ $ELAPSED -ge $DURATION ]; then
        echo "=== Monitor Complete ===" >> "$LOGFILE"
        break
    fi
    SNAP=$((SNAP + 1))
    TS=$(date -Iseconds)

    LIVE_POS=$(docker compose -f docker-compose.apps.yml logs karsa-live --tail 200 2>/dev/null | grep -c "shadow executed\|live executed\|signal:")
    SHADOW_POS=$(docker compose -f docker-compose.apps.yml logs karsa-shadow --tail 200 2>/dev/null | grep -c "shadow executed")
    AI_REJECT=$(docker compose -f docker-compose.apps.yml logs --tail 200 2>/dev/null | grep -c "AI GATE REJECT\|AI DISAGREES")
    AI_CONFIRM=$(docker compose -f docker-compose.apps.yml logs --tail 200 2>/dev/null | grep -c "AI CONFIRMED")
    ERRORS=$(docker compose -f docker-compose.apps.yml logs --tail 100 2>/dev/null | grep -c "ERROR\|CRITICAL")

    echo "[$TS] snap=$SNAP elapsed=${ELAPSED}s signals=$LIVE_POS shadow_exec=$SHADOW_POS ai_reject=$AI_REJECT ai_confirm=$AI_CONFIRM errors=$ERRORS" >> "$LOGFILE"
    sleep $INTERVAL
done
