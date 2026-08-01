#!/bin/bash
# Monitor pipeline performance for 1 hour
# Checks every5 minutes, logs to monitor-pipeline.log
set -uo pipefail

PROM="http://localhost:9090"
DURATION=3600  # 1 hour
INTERVAL=300  #5 minutes
LOGFILE="monitor-pipeline.log"

echo "=== Pipeline Monitor Started: $(date) ===" | tee "$LOGFILE"
echo "Duration: ${DURATION}s, Interval: ${INTERVAL}s" | tee -a "$LOGFILE"
echo "" | tee -a "$LOGFILE"

query() {
  local expr="$1"
  local raw
  raw=$(curl -sf "${PROM}/api/v1/query?query=${expr}" 2>/dev/null) || { echo "0"; return; }
  local val
  val=$(echo "$raw" | jq -r '.data.result[0].value[1] // "0"' 2>/dev/null) || val="0"
  echo "$val"
}

query_labels() {
  local expr="$1"
  local raw
  raw=$(curl -sf "${PROM}/api/v1/query?query=${expr}" 2>/dev/null) || { echo ""; return; }
  echo "$raw" | jq -r '.data.result[] | [.metric | to_entries | map(.value) | join("="), .value[1]] | @tsv' 2>/dev/null
}

prev_scored=""
elapsed=0
while [ "$elapsed" -lt "$DURATION" ]; do
  timestamp=$(date "+%Y-%m-%d %H:%M:%S")

  # Core counters
  regime=$(query "sum(karsa_regime_classified_total)")
  scored=$(query "sum(karsa_strategy_scored_total)")
  confidence=$(query "sum(karsa_signal_confidence_passed_total)")
  risk_gate=$(query "sum(karsa_risk_gate_pass_total)")
  executed=$(query "sum(karsa_orders_placed_total)")

  # Score distribution
  bucket_0_50=$(query "karsa_strategy_scored_total{score_bucket=\"0-50\"}")
  bucket_50_65=$(query "karsa_strategy_scored_total{score_bucket=\"50-65\"}")
  bucket_65_85=$(query "karsa_strategy_scored_total{score_bucket=\"65-85\"}")
  bucket_85_100=$(query "karsa_strategy_scored_total{score_bucket=\"85-100\"}")

  # Kill reasons
  kills=$(query_labels "sum(karsa_signals_killed_total) by (stage, reason)")

  # Entry filter skips
  entry_spread=$(query "sum(karsa_signals_skipped_total{reason=~\"entry_filter:spread.*\"})")
  strategy_gate=$(query "sum(karsa_signals_skipped_total{reason=~\"strategy_gate.*\"})")
  neutral_skew=$(query "sum(karsa_signals_skipped_total{reason=~\"strategy_neutral.*\"})")

  # Regime info
  adx=$(query "karsa_regime_adx")
  hurst=$(query "karsa_regime_hurst")
  regime_state=$(query "karsa_regime_state")

  # Log
  echo "[$timestamp]" >> "$LOGFILE"
  echo "  Regime: $regime_state (ADX=$adx, Hurst=$hurst)" >> "$LOGFILE"
  echo "  Classified=$regime Scored=$scored Confidence=$confidence RiskGate=$risk_gate Executed=$executed" >> "$LOGFILE"
  echo "  Buckets: 0-50=$bucket_0_50 50-65=$bucket_50_65 65-85=$bucket_65_85 85-100=$bucket_85_100" >> "$LOGFILE"
  echo "  Skips: spread=$entry_spread strategy_gate=$strategy_gate neutral=$neutral_skew" >> "$LOGFILE"

  # Score progression (delta from last check)
  if [ -n "$prev_scored" ] && [ "$prev_scored" != "0" ]; then
    delta=$(echo "$scored $prev_scored" | awk '{printf "%.0f", $1 - $2}')
    echo "  Delta: +$delta scored this interval" >> "$LOGFILE"

    # Check if any high scores appeared
    if [ "$bucket_65_85" != "0" ] || [ "$bucket_85_100" != "0" ]; then
      echo "  ** HIGH SCORES DETECTED: 65-85=$bucket_65_85 85-100=$bucket_85_100 **" >> "$LOGFILE"
    fi
  fi

  prev_scored=$scored
  echo "" >> "$LOGFILE"

  # Console output
  echo "[$timestamp] R=$regime S=$scored C=$confidence E=$executed | 0-50:$bucket_0_50 50-65:$bucket_50_65 65-85:$bucket_65_85 85+:$bucket_85_100 | ADX:$adx"

  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
done

echo "=== Monitor Complete: $(date) ===" | tee -a "$LOGFILE"
