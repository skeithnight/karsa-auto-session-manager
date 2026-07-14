# Karsa Executive Overview Dashboard

This document provides a visual and technical wireframe for the **Executive Overview** Grafana Dashboard.

> [!NOTE] 
> This dashboard is designed to provide an end-to-end view of the system, starting from high-level health down to the most granular trade outcomes.

---

## Row 1: System Health
*Monitoring the immediate operational safety and connectivity of the Karsa infrastructure.*

| VPN Connection | Bybit Connection | ASM Status | Event Loop Lag |
| :--- | :--- | :--- | :--- |
| **Type:** Stat <br> **Source:** Prometheus <br> **Query:** `karsa_vpn_status` | **Type:** Stat <br> **Source:** Prometheus <br> **Query:** `karsa_bybit_status` | **Type:** Stat <br> **Source:** Prometheus <br> **Query:** `karsa_asm_session_active` | **Type:** Time Series <br> **Source:** Prometheus <br> **Query:** `karsa_event_loop_lag_ms` |
| 🟢 `Connected` | 🟢 `Connected` | 🟢 `Active` | 📈 `Line chart of lag` |

---

## Row 2: Analyst & Confidence
*Monitoring the AI generation funnel and the quality of signals passing the risk gate.*

| Signal Confidence | Signal Funnel |
| :--- | :--- |
| **Type:** Bar Gauge (Gradient) <br> **Source:** Prometheus <br> **Query:** `sum(rate(karsa_signal_confidence_bucket[5m])) by (le)` | **Type:** Stat (Multi-value) <br> **Source:** Prometheus <br> **Queries:** <br> - A: `sum(karsa_signals_generated_total)`<br> - B: `sum(karsa_signals_skipped_total)`<br> - C: `sum(karsa_risk_gate_reject_total)` |
| 📊 Horizontal gradient bars showing confidence buckets | **Generated:** 142 <br> **Skipped:** 85 <br> **Risk Rejected:** 45 |

---

## Row 3: Live Trading
*A consolidated table view of currently open positions, unrealized PnL, and wallet state.*

> [!TIP]
> This panel uses Grafana's "Series to columns" transformation to join multiple Prometheus metrics by the `symbol` label into a single clean table.

**Panel Type:** Table
**Data Source:** Prometheus

| Symbol | Unrealized PnL | Wallet Balance | Active Position | Entry | Duration |
| :--- | :--- | :--- | :--- | :--- | :--- |
| BTC/USDT | `+ $45.20` | `$1,050.00` | `0.015` | `$64,200.50` | `2h 15m` |
| ETH/USDT | `- $12.10` | `$1,050.00` | `0.45` | `$3,400.10` | `45m` |

*Underlying Queries:*
- A: `karsa_position_unrealized_pnl_usdt`
- B: `karsa_wallet_balance_usdt`
- C: `karsa_position_size`
- D: `karsa_position_entry_price_usdt`
- E: `karsa_position_duration_seconds`

---

## Row 4: Trade History
*A historical ledger of completed round-trips for the autonomous session.*

> [!IMPORTANT]
> This panel relies on the **PostgreSQL** data source rather than Prometheus, as historical trade ledgers are stored in the Postgres `trades` table.

**Panel Type:** Table
**Data Source:** PostgreSQL

| Timestamp | Symbol | Entry Price | Realized PnL | Result |
| :--- | :--- | :--- | :--- | :--- |
| `2024-01-15 14:30:00` | BTC/USDT | `$64,100.00` | `+ $150.00` | 🟢 Win |
| `2024-01-15 13:15:00` | SOL/USDT | `$145.00` | `- $25.00` | 🔴 Loss |
| `2024-01-15 12:00:00` | ETH/USDT | `$3,350.00` | `+ $80.00` | 🟢 Win |

*Underlying SQL Query:*
```sql
SELECT 
  timestamp, 
  symbol, 
  entry_price, 
  pnl_usdt AS realized_pnl, 
  CASE WHEN pnl_usdt > 0 THEN 'Win' ELSE 'Loss' END as result 
FROM trades 
ORDER BY timestamp DESC 
LIMIT 50;
```
