<div align="center">
  <img src="assets/karsa_asm_logo.jpg" alt="Karsa Auto Session Manager Logo" width="300" />
  
# Karsa Auto Session Manager
  
  **Autonomous Crypto Perpetuals Trading Bot**  
  *"Read Global, Execute Local"*
</div>

---

## 📖 TL;DR

An autonomous crypto perpetuals trading bot that reads market data from multiple exchanges (Binance, OKX, Bybit) to build a "true" global price picture, but only ever *trades* on Bybit. By reading global sentiment and trading on a single venue via a 15m–4h swing/intraday timeframe, we mitigate proxy latency while capturing alpha.

Everything runs as a single Python `asyncio` process specifically designed to prioritize execution safety and state integrity over High-Frequency Trading (HFT) speed.

## 🏗 System Architecture (The 7 Keys)

![Karsa E2E Workflow](assets/karsa_asm_e2e.png)

Our architecture is split into critical paths ensuring robustness and modularity:

| # | Component | Responsibility | Location |
|---|---|---|---|
| 1 | **Global Data Engine** | CCXT Pro WS ingestion, normalization, bad-tick filtering | `app/data/` |
| 2 | **Alpha Bridge** | Multi-signal composite, regime classification, strategy routing, AI analysis | `app/alpha/` |
| 3 | **3-Layer Risk Gate** | Liquidity, spread health, circuit breaker, portfolio risk, sector cap, correlation, spread detection | `app/risk/` |
| 4 | **Bybit Executor** | SOR (Post-Only → Reprice → Market), APM (SL 5% cap, breakeven lock), shadow execution | `app/execution/` |
| 5 | **State Manager** | Postgres sync, startup reconciliation, trade store | `app/core/state.py` |
| 6 | **Watchdog & Telemetry** | Heartbeats, latency tracking, dead man's switch, system health | `app/watchdog/` |
| 7 | **Session Orchestrator** | UTC time-block regime logic | `app/core/session.py` |
| 8 | **Market Consumer** | Live/shadow loops, candle buffering, decision engine (ELO, sector, correlation scoring) | `app/consumer/` |
| 9 | **Backtest Engine** | Historical candle replay, multi-symbol orchestration, live gates, walk-forward optimization | `app/backtest/` |
| 10 | **Commander** | CLI interface for bot management | `app/commander/` |
| 11 | **Analytics** | Performance metrics (Sharpe, Sortino, drawdown), trade reconciliation | `app/analytics/` |
| 12 | **Data Engine Service** | Standalone data ingestion container (gluetun VPN) | `app/data_engine/` |
| 13 | **Research Platform** | Ranking engine, ELO ratings, metrics engine, CLI with autocomplete | `app/research/` |
| 14 | **Volatility Surface** | Cross-asset BTC/ETH term structure, vol regime detection | `app/risk/volatility_surface.py` |

## 🛡 Key Architectural Decisions

- **Single-process monolith:** Avoids internal IPC/Redis pub-sub latency and state divergence on partial failures.
- **Swing/Intraday (15m–4h):** Proxy latency is mathematically irrelevant at this timeframe. HFT is strictly avoided.
- **Bybit-only execution:** Avoids cross-exchange arbitrage complexity in V1.
- **Strict Data Types:** `Decimal` is used everywhere for financial calculations to prevent float precision loss.
- **Mandatory Exchange-side Stop-Loss:** Guarantees protection even if the process or proxy dies.
- **"Trust Nothing" Startup Reconciliation:** Postgres and Bybit can diverge after any crash; strict reconciliation safely resumes the bot.

## 📚 Documentation Map

The project is heavily documented to ensure safety and clarity. Please read the core documents before contributing:

- [CONTEXT.md](CONTEXT.md) - Project context, glossary, and open issues. Start here.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - System design and component breakdown.
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) - Exact schemas, Postgres DDL, Redis keys, Pydantic models.
- [docs/MVP_SCOPE.md](docs/MVP_SCOPE.md) - Project scope, phased delivery plan.
- [docs/DEFINITION_OF_DONE.md](docs/DEFINITION_OF_DONE.md) - Quality gates every PR must pass.
- [docs/RISK_AND_RUNBOOK.md](docs/RISK_AND_RUNBOOK.md) - Kill switch, circuit breakers, disaster recovery.
- [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) - Test guidelines and verification.
- [AGENTS.md](AGENTS.md) - Instructions and rules for AI agents.

## 🚀 Getting Started

The bot is designed to run via Docker Compose, handling the Python application, Postgres database, Redis store, and the WireGuard VPN network stack (via `gluetun`, see `docs/SETUP.md`).

```bash
# Clone the repository
git clone git@github.com:skeithnight/karsa-auto-session-manager.git
cd karsa-auto-session-manager

# Set up environment variables
cp .env.example .env

# Run the stack (infra + apps)
make up
```

## ⚠️ Safety Warning

This bot operates with real capital in live environments. Any changes to the **Risk Gate**, **Bybit Executor**, or **Watchdog** must undergo rigorous review and pass the `TESTING_STRATEGY.md` requirements. Never weaken the kill switch or circuit breakers for development convenience.

## 🧠 Adaptive Strategy Features (Phase 1-3)

| Feature | Description | Redis Key |
|---------|-------------|-----------|
| **Regime Confidence** | HMM probabilities blended into scoring | `system:hmm:regime` |
| **Dynamic Threshold** | Gate calibrated from historical EV | `karsa:gate:dynamic_threshold` |
| **ELO Rating** | Per-strategy win/loss tracking | `karsa:elo:{strategy}` |
| **Correlation Sizing** | Score penalty for correlated positions | `karsa:correlation:{symbol}` |
| **Sector Scoring** | Bonus/penalty based on sector momentum | (computed in-memory) |
| **Rebalance Trigger** | Alert when pending signal beats open position | `karsa:alert:rebalance` |
| **Spread Detection** | Multi-leg spread risk evaluation | (computed in PRM) |
| **Volatility Surface** | BTC/ETH term structure and vol regime | `karsa:vol_surface:*` |
| **Walk-Forward Optimization** | Robustness scoring for strategy validation | (CLI output) |
| **Research CLI** | `python -m app.research.cli {ranking,elo,gate,vol,trades,metrics}` | (various) |

## 🤖 Hybrid Intelligence Trading System

| Feature | Description | Module |
|---------|-------------|--------|
| **Statistical Feature Engine** | Beta, correlation, ATR, volume metrics computation | `app/alpha/statistical_engine.py` |
| **AI Decision Engine** | Multi-provider fallback with circuit breaker | `app/ai/nine_router_service.py` |
| **Hybrid Decision Engine** | 10 hard + 5 soft guardrails for entry/exit decisions | `app/alpha/hybrid_decision_engine.py` |
| **Smart Order Routing** | MARKET/LIMIT_RETEST/WAIT_PULLBACK execution modes | `app/execution/sor.py` |
| **Close-based Trailing Stop** | Trail based on close price, not wick | `app/execution/exit_manager.py` |
| **Position Reconciler** | Continuous local/exchange state synchronization | `app/execution/position_reconciler.py` |
| **Take-Profit Manager** | Dynamic TP management with multiple strategies | `app/execution/tp_manager.py` |

## 📱 Telegram Bot Features

| Feature | Description | Module |
|---------|-------------|--------|
| **Hybrid Dashboard** | Real-time trading dashboard with hybrid decisions | `app/bot/handlers/dashboard.py` |
| **Position Management** | View, monitor, and close positions from Telegram | `app/bot/handlers/positions.py` |
| **Daily Summary Alerts** | Automated daily performance reports | `app/bot/daily_summary.py` |
| **Settings Persistence** | User settings saved to database | `app/core/settings_store.py` |
| **Hybrid Backtest Reports** | Backtest results with guardrail analysis | `app/backtest/hybrid_report.py` |
| **Report Generation** | Performance, trade history, and analytics reports | `app/bot/handlers/reports.py` |
