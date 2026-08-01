#!/bin/bash
# Karsa ASM — Health monitor (1-hour session)
# Usage: bash scripts/monitor_health.sh
# Appends results to /tmp/karsa_monitor.log for post-analysis

set -uo pipefail

LOGFILE="/tmp/karsa_monitor.log"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
ISSUES=0

header() { echo -e "\n${BOLD}━━━ $1 ━━━${NC}"; }
ok() { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; ((ISSUES++)) || true; }
fail() { echo -e "  ${RED}✗${NC} $1"; ((ISSUES++)) || true; }

echo -e "${BOLD}🤖 Karsa Health Check — ${TIMESTAMP}${NC}" | tee -a "$LOGFILE"

# ── Docker Services ──
header "Docker Services"
EXPECTED_SERVICES="karsa-app karsa-gluetun karsa-db karsa-redis karsa-9router karsa-prometheus karsa-grafana"
for svc in $EXPECTED_SERVICES; do
    status=$(docker inspect -f '{{.State.Status}}' "$svc" 2>/dev/null || echo "not_found")
    if [ "$status" = "running" ]; then ok "$svc: running"
    else fail "$svc: $status"; fi
done

# ── VPN Tunnel ──
header "VPN Tunnel"
vpn_ip=$(docker exec karsa-gluetun wget -qO- --timeout=3 http://ipinfo.io/ip 2>/dev/null || echo "")
if [ -n "$vpn_ip" ]; then
    ok "VPN active — external IP: $vpn_ip"
else
    fail "VPN down — no external IP"
fi

# ── App Errors (last 5 min, excluding transient) ──
header "App Errors (last 5 min)"
# Filter out CancelledError (transient from restarts) and "Task died" (same)
error_count=$(docker logs karsa-app --since 5m 2>&1 | grep -iE 'ERROR|CRITICAL' | grep -v 'CancelledError' | grep -v 'Task died' | wc -l | tr -d ' ')
if [ "$error_count" -eq 0 ]; then
    ok "No real errors in last 5 min"
else
    warn "$error_count errors in last 5 min"
    docker logs karsa-app --since 5m 2>&1 | grep -iE 'ERROR|CRITICAL' | grep -v 'CancelledError' | grep -v 'Task died' | tail -3 | sed 's/^/    /'
fi

# ── AI Status ──
header "AI (9router)"
ai_errors=$(docker logs karsa-app --since 5m 2>&1 | grep -iE 'AI unavailable|ai_client.*error|Cannot connect.*2012' | wc -l | tr -d ' ')
if [ "$ai_errors" -eq 0 ]; then
    ok "AI: no errors in last 5 min"
else
    fail "AI: $ai_errors connection errors — check 9router"
fi

# ── Signal Activity ──
header "Signal Activity (last 15 min)"
signals=$(docker logs karsa-app --since 15m 2>&1 | grep 'signal' | wc -l | tr -d ' ')
rejected_liq=$(docker logs karsa-app --since 15m 2>&1 | grep 'rejected: liquidity' | wc -l | tr -d ' ')
rejected_other=$(docker logs karsa-app --since 15m 2>&1 | grep 'rejected' | grep -v 'liquidity' | wc -l | tr -d ' ')
ok "Signals generated: ~$signals | Rejected (liquidity): $rejected_liq | Rejected (other): $rejected_other"

# ── Top 5 Symbols (from Prometheus) ──
header "Top 5 Symbols (cumulative)"
prom_data=$(curl -s --max-time 5 "http://localhost:9090/api/v1/query" \
  --data-urlencode 'query=karsa_signals_entered_pipeline_total' 2>/dev/null || echo "")
if [ -n "$prom_data" ] && echo "$prom_data" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['data']['result']" 2>/dev/null; then
    # Build symbol | analyzed | passed | rejected table
    symbol_table=$(python3 -c "
import json, sys
data = json.loads('''$prom_data''')
results = data['data']['result']
# Get analyzed counts per symbol
analyzed = {}
for r in results:
    sym = r['metric'].get('symbol','?')
    analyzed[sym] = int(float(r['value'][1]))
# Fetch passed counts
import urllib.request
passed = {}
try:
    req = urllib.request.urlopen('http://localhost:9090/api/v1/query?query=karsa_risk_gate_pass_total', timeout=5)
    for r in json.loads(req.read())['data']['result']:
        passed[r['metric'].get('symbol','?')] = int(float(r['value'][1]))
except: pass
# Fetch rejected counts
rejected = {}
try:
    req = urllib.request.urlopen('http://localhost:9090/api/v1/query?query=karsa_risk_gate_reject_total', timeout=5)
    for r in json.loads(req.read())['data']['result']:
        sym = r['metric'].get('symbol','?')
        reason = r['metric'].get('reason','')
        key = f'{sym}'
        rejected[key] = rejected.get(key, 0) + int(float(r['value'][1]))
except: pass
# Sort by analyzed desc, top 5
top = sorted(analyzed.items(), key=lambda x: x[1], reverse=True)[:5]
for sym, cnt in top:
    p = passed.get(sym, 0)
    rej = rejected.get(sym, 0)
    print(f'{sym}|{cnt}|{p}|{rej}')
" 2>/dev/null)
    if [ -n "$symbol_table" ]; then
        printf "  %-14s %10s %8s %8s\n" "SYMBOL" "ANALYZED" "PASSED" "REJECTED"
        printf "  %-14s %10s %8s %8s\n" "──────" "────────" "──────" "────────"
        echo "$symbol_table" | while IFS='|' read -r sym analyzed passed rejected; do
            printf "  %-14s %10s %8s %8s\n" "$sym" "$analyzed" "$passed" "$rejected"
        done | tee -a "$LOGFILE"
    else
        warn "Prometheus returned empty symbol data"
    fi
else
    warn "Prometheus not reachable for symbol breakdown"
fi

# ── PostgreSQL ──
header "PostgreSQL"
pg_result=$(docker exec karsa-db psql -U karsa -d karsa -t -A -c "SELECT 1;" 2>/dev/null || echo "FAIL")
if [ "$pg_result" = "1" ]; then
    ok "Postgres: connected"
    trade_stats=$(docker exec karsa-db psql -U karsa -d karsa -t -A -c "
        SELECT
            COUNT(*) FILTER (WHERE exit_time IS NULL),
            COUNT(*) FILTER (WHERE exit_time IS NOT NULL),
            COALESCE(SUM(pnl) FILTER (WHERE exit_time IS NOT NULL), 0)::text,
            COUNT(*) FILTER (WHERE pnl > 0 AND exit_time IS NOT NULL),
            COUNT(*) FILTER (WHERE pnl <= 0 AND exit_time IS NOT NULL)
        FROM trades;
    " 2>/dev/null || echo "0|0|0|0|0")
    IFS='|' read -r open closed net_pnl wins losses <<< "$trade_stats"
    total=$((wins + losses))
    if [ "$total" -gt 0 ]; then
        wr=$(echo "scale=1; $wins * 100 / $total" | bc)
    else
        wr="N/A"
    fi
    ok "Open: $open | Closed: $closed | W/L: $wins/$losses | WR: ${wr}% | Net: \$$net_pnl"
else
    fail "Postgres: connection failed"
fi

# ── AI Confidence ──
header "AI Confidence"
ai_stats=$(docker exec karsa-db psql -U karsa -d karsa -t -A -c "
    SELECT
        COALESCE(ROUND(AVG(ai_confidence))::text, '0'),
        COUNT(*)::text,
        COALESCE(MIN(ai_confidence)::text, '0'),
        COALESCE(MAX(ai_confidence)::text, '0')
    FROM trades WHERE ai_confidence IS NOT NULL AND entry_time > NOW() - INTERVAL '24 hours';
" 2>/dev/null || echo "0|0|0|0")
IFS='|' read -r avg_conf total_ai min_conf max_conf <<< "$ai_stats"
if [ "$total_ai" -gt 0 ]; then
    ok "Avg: ${avg_conf}% | Range: ${min_conf}-${max_conf}% | Samples: $total_ai (24h)"
else
    warn "No AI confidence data yet (no trades entered)"
fi

# ── Redis ──
header "Redis"
redis_ok=$(docker exec karsa-redis redis-cli ping 2>/dev/null || echo "FAIL")
if [ "$redis_ok" = "PONG" ]; then
    ok "Redis: PONG"
    pos_count=$(docker exec karsa-redis redis-cli KEYS "karsa:position:*" 2>/dev/null | grep "karsa:position" | wc -l | tr -d ' ')
    ok "Open positions in Redis: $pos_count"
else
    fail "Redis: not responding"
fi

# ── Circuit Breaker ──
header "Circuit Breaker"
cb_state=$(docker exec karsa-redis redis-cli GET "system:circuit_breaker" 2>/dev/null || echo "")
if [ -z "$cb_state" ] || [ "$cb_state" = "(nil)" ]; then
    ok "Circuit breaker: inactive"
else
    fail "Circuit breaker: ACTIVE — $cb_state"
fi

# ── Grafana ──
header "Grafana"
grafana_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://localhost:3000/api/health" 2>/dev/null || echo "000")
if [ "$grafana_code" = "200" ]; then
    ok "Grafana: healthy"
else
    warn "Grafana: HTTP $grafana_code"
fi

# ── Summary ──
header "Summary"
if [ "$ISSUES" -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}All checks passed ✓${NC}" | tee -a "$LOGFILE"
else
    echo -e "  ${YELLOW}${BOLD}$ISSUES issue(s) found ⚠${NC}" | tee -a "$LOGFILE"
fi

# Append structured line to log for post-analysis
echo "${TIMESTAMP}|issues=${ISSUES}|open=${open:-0}|closed=${closed:-0}|wr=${wr:-N/A}|net=${net_pnl:-0}|errors=${error_count}|ai_err=${ai_errors}" >> "$LOGFILE"
echo "" | tee -a "$LOGFILE"
