#!/bin/bash
# Karsa ASM — Pipeline Funnel (lightweight ASCII)
# Usage: bash scripts/pipeline-funnel.sh [--window 24h] [--prom http://localhost:9090] [--json]
set -uo pipefail

PROM="http://localhost:9090"
WINDOW="24h"
JSON=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --window) WINDOW="$2"; shift 2 ;;
    --prom) PROM="$2"; shift 2 ;;
    --json) JSON=true; shift ;;
    *) shift ;;
  esac
done

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

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

bar() {
  local val=$1 max=$2 width=${3:-30}
  [[ "$max" == "0" || "$max" == "0.0" ]] && max=1
  local len=$(echo "$val $max $width" | awk '{printf "%d", ($1/$2)*$3}')
  [[ "$len" -lt 1 && "$val" != "0" ]] && len=1
  printf '%0.s█' $(seq 1 "$len" 2>/dev/null) || true
}

# ── QUERY ALL ──
# Raw counters for live view
s1=$(query "sum(karsa_regime_classified_total)")
s2=$(query "sum without(score_bucket, regime)(karsa_strategy_scored_total)")
s3=$(query "sum(karsa_signal_confidence_passed_total)")
s4=$(query "sum(karsa_risk_gate_pass_total)")
s5=$(query "sum(karsa_orders_placed_total)")

# Convert to int for display
s1i=$(printf "%.0f" "$s1" 2>/dev/null || echo 0)
s2i=$(printf "%.0f" "$s2" 2>/dev/null || echo 0)
s3i=$(printf "%.0f" "$s3" 2>/dev/null || echo 0)
s4i=$(printf "%.0f" "$s4" 2>/dev/null || echo 0)
s5i=$(printf "%.0f" "$s5" 2>/dev/null || echo 0)

# Max for bar scaling
MAX=$s1i
[[ "$MAX" == "0" ]] && MAX=1

# Percentages
pct2=$(echo "$s1i $s2i" | awk '{if($1>0) printf "%.0f", ($2/$1)*100; else print 0}')
pct3=$(echo "$s1i $s3i" | awk '{if($1>0) printf "%.0f", ($2/$1)*100; else print 0}')
pct4=$(echo "$s1i $s4i" | awk '{if($1>0) printf "%.0f", ($2/$1)*100; else print 0}')
pct5=$(echo "$s1i $s5i" | awk '{if($1>0) printf "%.0f", ($2/$1)*100; else print 0}')

# Kill reasons
KILLS=$(query_labels "sum(karsa_signals_killed_total) by (stage, reason)")

# Regime breakdown
REGIME_CLASS=$(query_labels "sum(karsa_regime_classified_total) by (regime)")
REGIME_SCORE85=$(query_labels "sum(karsa_strategy_scored_total) by (regime, score_bucket)" | grep 'score_bucket=85-100' || true)
REGIME_CONF=$(query_labels "sum(karsa_signal_confidence_passed_total) by (regime)")

# JSON mode
if $JSON; then
  echo "{"
  echo "  \"window\": \"${WINDOW}\","
  echo "  \"funnel\": {"
  echo "    \"regime_classified\": ${s1i},"
  echo "    \"strategy_scored\": ${s2i},"
  echo "    \"confidence_passed\": ${s3i},"
  echo "    \"risk_gate_passed\": ${s4i},"
  echo "    \"executed\": ${s5i}"
  echo "  },"
  echo "  \"kill_reasons\": ["
  if [[ -n "$KILLS" ]]; then
    first=true
    while IFS=$'\t' read -r labels count; do
      [[ -z "$labels" ]] && continue
      $first || echo ","
      first=false
      stage=$(echo "$labels" | grep -o 'stage=[^,]*' | cut -d= -f2)
      reason=$(echo "$labels" | grep -o 'reason=[^,]*' | cut -d= -f2)
      echo -n "    {\"stage\": \"${stage}\", \"reason\": \"${reason}\", \"count\": ${count}}"
    done <<< "$KILLS"
    echo ""
  fi
  echo "  ]"
  echo "}"
  exit 0
fi

# ── ASCII OUTPUT ──
echo ""
echo -e "${BOLD}📊 KARSA PIPELINE FUNNEL (${WINDOW})${NC}"
echo -e "${DIM}─────────────────────────────────────────────────────────────${NC}"
echo ""
printf "  ${CYAN}%-24s${NC} " "Regime Classified"
echo -e "$(bar "$s1i" "$MAX") ${BOLD}${s1i}${NC}"
printf "  ${CYAN}%-24s${NC} " "Strategy Scored"
echo -e "$(bar "$s2i" "$MAX") ${BOLD}${s2i}${NC}  ${DIM}(${pct2}%)${NC}"
printf "  ${CYAN}%-24s${NC} " "Confidence Passed"
echo -e "$(bar "$s3i" "$MAX") ${BOLD}${s3i}${NC}  ${DIM}(${pct3}%)${NC}"
printf "  ${CYAN}%-24s${NC} " "Risk Gate Passed"
echo -e "$(bar "$s4i" "$MAX") ${BOLD}${s4i}${NC}  ${DIM}(${pct4}%)${NC}"
printf "  ${CYAN}%-24s${NC} " "EXECUTED"
echo -e "$(bar "$s5i" "$MAX") ${BOLD}${s5i}${NC}  ${DIM}(${pct5}%)${NC}"
echo ""

# Kill reasons
if [[ -n "$KILLS" ]]; then
  echo -e "${BOLD}🔴 KILL REASONS${NC}"
  echo -e "${DIM}─────────────────────────────────────────────────────────────${NC}"
  while IFS=$'\t' read -r labels count; do
    stage=$(echo "$labels" | grep -o 'stage=[^,]*' | cut -d= -f2)
    reason=$(echo "$labels" | grep -o 'reason=[^,]*' | cut -d= -f2)
    ci=$(printf "%.0f" "$count" 2>/dev/null || echo 0)
    printf "  ${RED}%-10s${NC}%-24s ${BOLD}%s${NC}\n" "$stage" ":$reason" "$ci"
  done <<< "$KILLS"
  echo ""
fi

# Regime breakdown
echo -e "${BOLD}📈 REGIME BREAKDOWN${NC}"
echo -e "${DIM}─────────────────────────────────────────────────────────────${NC}"
printf "  ${BOLD}%-14s %8s %8s %8s${NC}\n" "REGIME" "CLASS" "85+" "CONF"
for regime_line in $REGIME_CLASS; do
  rname=$(echo "$regime_line" | cut -f1 | sed 's/.*=//')
  rcount=$(printf "%.0f" "$(echo "$regime_line" | cut -f2)" 2>/dev/null || echo 0)
  r85=$(echo "$REGIME_SCORE85" | grep "regime=${rname}" | head -1 | cut -f2)
  r85i=$(printf "%.0f" "${r85:-0}" 2>/dev/null || echo 0)
  rconf=$(echo "$REGIME_CONF" | grep "regime=${rname}" | head -1 | cut -f2)
  rconfi=$(printf "%.0f" "${rconf:-0}" 2>/dev/null || echo 0)
  printf "  ${CYAN}%-14s${NC} %8s %8s %8s\n" "$rname" "$rcount" "$r85i" "$rconfi"
done
echo ""
