#!/bin/bash
# Big Gainers Alpha E2E Monitor — 1 hour capture
DIR="monitoring/big_gainers_20260725_1251"
LOGFILE="$DIR/capture.log"
INTERVAL=30
DURATION=3600
START=$(date +%s)

echo "=== Big Gainers Alpha Monitor ===" > "$LOGFILE"
echo "Start: $(date -Iseconds)" >> "$LOGFILE"
echo "Duration: ${DURATION}s (${INTERVAL}s intervals)" >> "$LOGFILE"
echo "" >> "$LOGFILE"

SNAP=0
while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    if [ $ELAPSED -ge $DURATION ]; then
        echo "=== Monitor Complete ===" >> "$LOGFILE"
        echo "End: $(date -Iseconds)" >> "$LOGFILE"
        break
    fi

    SNAP=$((SNAP + 1))
    TS=$(date -Iseconds)

    # Capture Redis regime + conviction
    REGIME_RAW=$(docker exec karsa-redis redis-cli GET "system:config:regime" 2>/dev/null || echo "{}")

    # Capture live positions
    LIVE_POSITIONS=$(docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null | wc -l)

    # Capture shadow positions
    SHADOW_POSITIONS=$(docker exec karsa-redis redis-cli KEYS "karsa:shadow:position:*" 2>/dev/null | wc -l)

    # Capture conviction scaling logs
    CONVICTION_LOGS=$(docker compose -f docker-compose.apps.yml logs karsa-live --tail 50 2>/dev/null | grep -c "conviction" || echo 0)

    # Capture moon bag logs
    MOON_BAG_LOGS=$(docker compose -f docker-compose.apps.yml logs karsa-live --tail 50 2>/dev/null | grep -c "MOON BAG" || echo 0)

    # Capture volume anomaly logs
    VOLUME_ANOMALY_LOGS=$(docker compose -f docker-compose.apps.yml logs karsa-shadow --tail 50 2>/dev/null | grep -c "VOLUME ANOMALY" || echo 0)

    # Capture dip buyer logs
    DIP_BUYER_LOGS=$(docker compose -f docker-compose.apps.yml logs karsa-shadow --tail 50 2>/dev/null | grep -c "DIP BUY" || echo 0)

    # Capture errors
    ERRORS=$(docker compose -f docker-compose.apps.yml logs --tail 100 2>/dev/null | grep -c "ERROR\|CRITICAL" || echo 0)

    echo "[$TS] snap=$SNAP elapsed=${ELAPSED}s regime=$REGIME_RAW live_pos=$LIVE_POSITIONS shadow_pos=$SHADOW_POSITIONS conviction_logs=$CONVICTION_LOGS moon_bag=$MOON_BAG_LOGS volume_anomaly=$VOLUME_ANOMALY_LOGS dip_buyer=$DIP_BUYER_LOGS errors=$ERRORS" >> "$LOGFILE"

    sleep $INTERVAL
done
