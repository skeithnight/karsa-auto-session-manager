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

Our architecture is split into critical paths ensuring robustness and modularity:

| # | Component | Responsibility | Location |
|---|---|---|---|
| 1 | **Global Data Engine** | CCXT Pro WS ingestion, normalization, bad-tick filtering | `app/data/` |
| 2 | **Alpha Bridge** | VWAP/Skew/Lead-Lag calculation, signal generation | `app/alpha/` |
| 3 | **3-Layer Risk Gate** | Liquidity, spread health, circuit breaker checks | `app/risk/` |
| 4 | **Bybit Executor** | SOR (Post-Only → Reprice → Market), private WS via WireGuard VPN (`gluetun` sidecar) | `app/execution/` |
| 5 | **State Manager** | Postgres sync, startup reconciliation | `app/core/state.py` |
| 6 | **Watchdog & Telemetry** | Heartbeats, latency tracking, dead man's switch, Prometheus | `app/watchdog/` |
| 7 | **Session Orchestrator** | UTC time-block regime logic | `app/core/session.py` |

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

# Run the stack
docker-compose up -d
```

## ⚠️ Safety Warning

This bot operates with real capital in live environments. Any changes to the **Risk Gate**, **Bybit Executor**, or **Watchdog** must undergo rigorous review and pass the `TESTING_STRATEGY.md` requirements. Never weaken the kill switch or circuit breakers for development convenience.
