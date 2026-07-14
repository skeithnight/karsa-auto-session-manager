# System Constants
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Single canonical location for every numeric threshold, timeout, limit, and calibration value in the system. If a constant exists in code, it must be listed here. If it's listed here, it must exist in code.

---

## 1. How to Use This File

- **Before changing any threshold:** check this file for the rationale and cross-references.
- **After changing any threshold:** update this file in the same PR.
- **Conflict resolution:** if a value here disagrees with another doc, this file is the source of truth for the *current code value*. The other doc may be the source of truth for the *intended value* (e.g., RISK_AND_RUNBOOK.md for safety thresholds). Flag the conflict in CONTEXT.md §7.

---

## 2. Risk Gate (`app/risk/gates.py`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `min_24h_volume` | `1,000,000` | USD | `gates.py:16` | Minimum 24h aggregated volume for liquidity gate |
| `max_spread_pct` | `0.005` | ratio (0.5%) | `gates.py:17` | Maximum bid-ask spread for spread health gate |
| `daily_drawdown_limit` | `-0.02` | ratio (-2%) | `gates.py:18` | Code authoritative. All docs aligned at -2%. |

---

## 3. Circuit Breaker (`app/risk/circuit_breaker.py`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `daily_drawdown_limit` | `-0.02` | ratio (-2%) | `circuit_breaker.py:18` | Same conflict as Risk Gate |
| `max_consecutive_losses` | `3` | count | `circuit_breaker.py:19` | Triggers 60-minute pause |
| `loss_pause_minutes` | `60` | minutes | `circuit_breaker.py:20` | Pause duration after consecutive losses |
| `max_latency_ms` | `1500` | ms | `circuit_breaker.py:21` | Execution latency threshold for halt |

---

## 4. Regime Detection (`app/alpha/regime.py`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `HURST_TREND_THRESHOLD` | `0.55` | — | `regime.py:30` | Hurst > 0.55 = trending |
| `HURST_MR_THRESHOLD` | `0.45` | — | `regime.py:31` | Hurst < 0.45 = mean-reverting |
| `ADX_TREND_THRESHOLD` | `25` | — | `regime.py:32` | ADX > 25 = strong trend |
| `ADX_CHOP_THRESHOLD` | `20` | — | `regime.py:33` | ADX < 20 = choppy (no trades) |
| Regime candle requirement | `200` | candles | `regime.py:52` | Minimum 1H candles for classification |
| Regime refresh interval | `15` | minutes | `main.py` | How often regime is re-classified |

---

## 5. Signal Generation (`app/alpha/signals.py`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `W_SKEW` | `0.40` | weight | `signals.py:76` | Skew signal weight in composite |
| `W_LEAD_LAG` | `0.30` | weight | `signals.py:77` | Lead-lag signal weight |
| `W_FUNDING` | `0.20` | weight | `signals.py:78` | Funding rate signal weight |
| `W_OI` | `0.10` | weight | `signals.py:79` | Open interest signal weight |
| `min_confidence` | `0.60` | ratio | `signals.py:63` | Minimum confidence to generate signal |
| Skew normalization ceiling | `0.80` | — | `signals.py:108` | `s_skew = skew / 0.8` |
| Lead-lag normalization ceiling | `0.005` | ratio | `signals.py` | `s_lead_lag = delta / 0.005` |
| Funding normalization ceiling | `0.0003` | ratio | `signals.py` | Contrarian: `s_funding = -rate / 0.0003` |
| `REGIME_MULTIPLIERS["TREND_BULL"]` | `1.2` | multiplier | `signals.py:69` | Confidence boost in bull trend |
| `REGIME_MULTIPLIERS["TREND_BEAR"]` | `1.2` | multiplier | `signals.py:70` | Confidence boost in bear trend |
| `REGIME_MULTIPLIERS["MEAN_REVERSION"]` | `0.8` | multiplier | `signals.py:71` | Confidence reduction in MR |
| `REGIME_MULTIPLIERS["CHOP"]` | `0.0` | multiplier | `signals.py:72` | Force FLAT in CHOP |
| AI confidence blend | `0.5 / 0.5` | ratio | `analyst.py` | `final = quant * 0.5 + AI * 0.5` |
| AI confidence gate | `0.65` | ratio | `analyst.py` | Minimum final confidence for signal |

---

## 6. Entry Filter (`app/alpha/entry_filter.py`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `max_spread_pct` | `0.003` | ratio (0.3%) | `entry_filter.py:24` | Tighter than risk gate's 0.5% |
| `min_depth_ratio` | `0.7` | ratio | `entry_filter.py:25` | Minimum ask/bid depth ratio |
| `max_depth_ratio` | `1.4` | ratio | `entry_filter.py:26` | Maximum ask/bid depth ratio |
| `blocked_hour_start` | `0` | hour UTC | `entry_filter.py:27` | Start of blocked trading window |
| `blocked_hour_end` | `1` | hour UTC | `entry_filter.py:28` | End of blocked trading window |

---

## 7. Smart Order Router (`app/execution/sor.py`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Reprice attempts | `2` | count | `sor.py` | Max reprices before market fallback |
| SL distance percentage | `0.02` | ratio (2%) | `sor.py` | Default stop-loss distance from fill price |

---

## 8. Position Lifecycle (`app/execution/position_lifecycle.py`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Trailing stop cooldown | `60` | seconds | `position_lifecycle.py` | Min time between SL amendments per symbol |
| Checkpoint interval | `5` | minutes | `position_lifecycle.py` | How often checkpoints evaluate positions |
| HARD_FAIL (30min) | `-0.02` | ratio (-2%) | `position_lifecycle.py` | Max loss in first 30 minutes |
| HARD_FAIL (ever) | `-0.03` | ratio (-3%) | `position_lifecycle.py` | Max loss at any time |
| CLEAR_WIN multiplier | `3.0` | × ATR | `position_lifecycle.py` | Gain > 3× ATR = clear win |
| TIME_STOP | `72` | hours | `position_lifecycle.py` | Max hold duration |
| ATR trailing multiplier | `2.0` | × ATR | `position_lifecycle.py` | SL distance = ATR × 2.0 |
| AI hold counter forced exit | `3` | consecutive | `position_judge.py` | 3 HOLDs on loser = forced EXIT |

---

## 9. Data Engine (`app/data/`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Bad tick threshold | `0.05` | ratio (5%) | `filters.py` | Price spike > 5% in < 1s = rejected |
| Bad tick time window | `1` | second | `filters.py` | Time window for spike detection |
| Stale threshold | `15` | seconds | `ccxt_manager.py` | No update in 15s = STALE |
| OHLCV cache TTL | `300` | seconds (5min) | `ohlcv_fetcher.py` | In-memory cache for REST OHLCV |
| GlobalState Redis TTL | `60` | seconds | `main.py` | TTL for `global:state:{symbol}` |

---

## 10. Watchdog (`app/watchdog/`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Heartbeat check interval | `10` | seconds | `monitor.py` | Watchdog cycle frequency |
| Heartbeat stale threshold | `10` | seconds | `monitor.py` | No WS data in 10s = pause alpha |
| Execution latency warning | `1500` | ms | `monitor.py` | Switch SOR to skip-to-market |
| Event loop lag threshold | `100` | ms | `monitor.py` | Loop blocking detection |
| Loop lag consecutive checks | `3` | count | `monitor.py` | 3 consecutive lag checks = flatten |
| Dead man's switch interval | `60` | seconds | `config.py:64` | External health ping frequency |

---

## 11. AI Layer (`app/core/ai_client.py`, `app/alpha/analyst.py`)

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| AI client timeout | `15` | seconds | `ai_client.py` | HTTP request timeout to 9router |
| AI cache TTL | `300` | seconds (5min) | `ai_client.py` | Redis cache for analyst results |
| AI analyst model | `claude-haiku-3-5` | — | `config.py:76` | Default model for analyst |
| AI judge cheap model | `claude-haiku-3-5` | — | `position_judge.py` | Tier 1 judge model |
| AI judge escalated model | `claude-sonnet-4-5` | — | `position_judge.py` | Tier 2 judge model |
| AI analyst ambiguous zone | `0.55 – 0.85` | ratio | `analyst.py` | Only runs for signals in this range |
| AI estimated cost (5 symbols) | `$0.60 – $1.20` | USD/day | `ai_layer_analysis.md` | At 15-min scan cadence |

---

## 12. Infrastructure

| Constant | Value | Unit | Source | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Prometheus metrics port | `8001` | port | `docker-compose.yml` | App exposes metrics (not 8000 — gluetun uses 8000) |
| 9router port | `20129` | port | `docker-compose.yml` | AI proxy endpoint |
| PostgreSQL default | `karsa:karsa@db:5432/karsa` | — | `config.py:25` | Default connection string |
| Redis default | `redis://redis:6379/0` | — | `config.py:28` | Default Redis URL |
| WARP proxy type | SOCKS5 | — | `docker-compose.yml` | `socks5h://host.docker.internal:1080` |

---

## 13. Resolved Conflicts

| Constant | Value | Resolution |
| :--- | :--- | :--- |
| Daily drawdown limit | `-0.02` | Code authoritative. All docs aligned at -2%. (Issue #2) |
| Symbol count | 60 | Config.py confirmed. MVP_SCOPE.md updated. (Issue #6) |
