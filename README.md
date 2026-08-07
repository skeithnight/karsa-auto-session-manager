<div align="center">
  <img src="assets/karsa_asm_logo.jpg" alt="Karsa Auto Session Manager Logo" width="280" />
  
  # Karsa Auto Session Manager (KASM)

  **Autonomous 4-Stage Streamlined Quant-AI Perpetuals Trading Bot**  
  *"Read Global, Execute Local"*
</div>

---

## 📖 TL;DR

**Karsa Auto Session Manager (KASM 2.1)** is an autonomous crypto perpetuals trading engine designed for Bybit Futures. It reads global multi-exchange price feeds (Binance, OKX, Bybit) via CCXT Pro WS to compute true market structure (Hurst Exponent, ADX Trend Strength, ELO Ratings, and Cross-Asset Correlation), filters entries using a strict **Quant Expected Value ($EV \ge 0.55$)** threshold, and validates every trade via **9Router AI Proxy** before executing via **Bybit Post-Only Maker** orders.

---

## 🏗️ 4-Stage Streamlined Quant-AI Pipeline

![Karsa E2E Workflow](assets/karsa_asm_e2e.png)

Our end-to-end execution pipeline operates in 4 strict, non-bypassable stages:

```
[ 1. UNIVERSE SCAN ] ──► [ 2. QUANT EV ENGINE ] ──► [ 3. RISK GATE (PRM) ] ──► [ 4. AI & MAKER FILL ]
  41 Active Pairs          Hurst + ADX + ELO          Correlation Trap &         9Router AI Proxy &
  CCXT Pro WS Feed         Composite EV >= 0.55       Sector Exposure Caps       Bybit Post-Only Maker
```

| Stage | Name | Key Function | Responsibility |
| :--- | :--- | :--- | :--- |
| **Stage 1** | **Global Data & Universe Scan** | Market Ingestion | Scans 41 active volume/momentum symbols via CCXT Pro WS and normalizes feeds into GlobalState. |
| **Stage 2** | **Quant EV Engine** | Signal Generation | Computes Hurst Exponent, ADX, ELO rating, and composite Expected Edge. Requires $EV \ge 0.55$. |
| **Stage 3** | **Portfolio Risk Manager** | Pre-Trade Gate | Enforces sector diversification (max 2 per sector), correlation caps, and portfolio gross/net exposure. |
| **Stage 4** | **9Router AI & Bybit Execution** | AI Validation & Fill | Runs 9Router AI Analyst evaluation and routes orders via Bybit Post-Only Maker (0.02% fee optimization). |

---

## 🛡️ Non-Negotiable System Invariants

1. **No Float for Financial Math:** All prices, sizes, and PnL values use `decimal.Decimal`.
2. **Exchange-Side Stop-Loss:** Hard Stop-Loss is placed directly on Bybit server immediately upon fill.
3. **Mandatory Pre-Trade Risk Gate:** All orders MUST pass `PortfolioRiskManager` before hitting Bybit Executor.
4. **9Router AI Proxy Integration:** Pre-entry CryptoAnalyst and post-entry PositionJudge operate via 9Router proxy.
5. **Crash-Safe Active Position Manager (APM):** Continuous 2s monitoring loop with automatic breakeven lock at +1R and regime-shift kill switch.

---

## 📂 System Architecture & Directory Map

```text
app/
├── main.py                     # Asyncio loop entrypoint (All services start here)
├── core/
│   ├── config.py                # Pydantic Settings & environment variables
│   ├── database.py              # Async SQLAlchemy PostgreSQL pool
│   ├── redis_client.py          # High-speed Redis async client
│   ├── shadow_store.py          # ShadowPositionStore & ShadowTradeStore
│   └── metrics.py               # Prometheus metrics & funnel counters
├── data/                        # Key 1 — Global Data Engine
│   ├── ccxt_manager.py          # CCXT Pro WS ingestion & symbol validation
│   ├── normalizer.py            # Normalized tick/candle data mapping
│   └── universe_scorer.py       # Dynamic 41-symbol universe selection
├── alpha/                       # Key 2 — Quant Alpha Engine
│   ├── regime_classifier.py     # Hurst + ADX regime classifier (The Hub)
│   ├── strategy_router.py       # Regime-specific signal scoring (The Spokes)
│   ├── ev_scorer.py             # Composite EV scoring (EV >= 0.55)
│   └── analyst.py               # 9Router AI pre-entry analyst
├── risk/                        # Key 3 — Risk Gate
│   ├── portfolio_risk_manager.py# Pre-trade correlation trap & exposure caps
│   └── circuit_breaker.py       # Daily drawdown circuit breaker (-2%)
├── execution/                   # Key 4 — Execution & APM
│   ├── bybit_client.py          # Bybit REST/WS client & exchange SL
│   ├── sor.py                   # Smart Order Router (Post-Only Maker)
│   ├── position_manager.py      # Active Position Manager (APM)
│   ├── shadow_executor.py       # Shadow mode simulated order router
│   └── shadow_apm.py            # Shadow Active Position Manager (Wick & SL/TP tracking)
├── consumer/                    # Market Data Consumers
│   ├── live_loop.py             # Real money live trading loop
│   └── shadow_loop.py           # Paper trading shadow mode loop
├── bot/                         # Key 7 — Telegram Command Interface
│   ├── handlers/                # /start, /dashboard, /positions, /reports handlers
│   └── utils/formatters/        # Single-block monospace ASCII card formatters
└── backtest/                    # Backtesting Engine
    └── formatter.py             # Backtest summary & list formatters
```

---

## 📱 Telegram Command & Monitoring Interface

The bot provides real-time Telegram control with clean monospace `<pre>` ASCII cards:

- **`/start` / `/dashboard`** ➔ Real-time account balance, active symbols, and system status.
- **`/history`** ➔ Trade history with integrated 9Router AI accuracy breakdown.
- **`/reports`** ➔ Access Live Pipeline Funnel, Shadow Funnel, and Hybrid Backtest reports.
- **`/ai_status`** ➔ 9Router Proxy health, total evaluations, and average confidence score.

---

## 🚀 Getting Started

Launch the entire infrastructure and application stack via Docker Compose:

```bash
# Clone the repository
git clone git@github.com:skeithnight/karsa-auto-session-manager.git
cd karsa-auto-session-manager

# Copy environment template
cp .env.example .env

# Rebuild and start all containers
make up
```

---

## ⚠️ Risk & Safety Notice

This system executes real-capital transactions on Bybit Futures. Any modifications to `PortfolioRiskManager`, `BybitExecutor`, or `ActivePositionManager` must pass unit tests (`pytest`) and adhere strictly to `docs/DEFINITION_OF_DONE.md`. Never bypass circuit breakers or risk gates.
