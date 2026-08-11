# Definition of Done (DoD)
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Live Specification  
**Last Revised:** 2026-08-11

---

## 1. Core Philosophy

> **"In a live automated trading platform, 'done' means 'safe, deterministic, and verifiable'. Code that executes but lacks exchange-side Stop Loss protection, uses float for prices, or bypasses PortfolioRiskManager is strictly UNACCEPTABLE."**

Every feature, refactor, or fix must pass the universal DoD pillars and component checklists before being merged or declared complete.

---

## 2. Universal Quality Gates

### Pillar 1: Code Quality & Strict Typing
- [ ] **Linting & Formatting**: Passes `ruff check .` and formatting cleanly.
- [ ] **Strict Typing**: Passes `mypy --strict app/` without errors or unhandled `Any` types.
- [ ] **Financial Precision**: **Zero use of `float` for money, prices, sizes, or PnL.** All monetary calculations strictly use `decimal.Decimal`.
- [ ] **Async Loop Protection**: No blocking synchronous calls (`time.sleep()`, synchronous `requests`) inside `asyncio` code. All I/O is non-blocking and `await`ed.

### Pillar 2: Unit & Integration Testing
- [ ] **Coverage**: > 90% unit test coverage using `pytest` for core domain logic in `app/alpha/`, `app/risk/`, `app/execution/`, and `app/data/`.
- [ ] **Edge Case Testing**: Covers divide-by-zero, missing exchange feeds, bad tick anomalies, and zero-depth orderbooks.
- [ ] **Live Verification**: Integration tested against live exchange endpoints via `BybitClient` with micro position limits.

### Pillar 3: Safety & Risk Gate Enforcement
- [ ] **Mandatory Exchange-Side Stop Loss**: Every opened position MUST have a hard Stop Loss placed directly on the Bybit server upon fill.
- [ ] **Pre-Trade Risk Gate (`PortfolioRiskManager`)**: All order submissions MUST pass `PortfolioRiskManager` (sector correlation cap, gross/net exposure caps, daily circuit breaker).
- [ ] **Active Position Management (APM)**: Async loop includes mandatory 5% SL cap, breakeven lock at +1R, orphan minimum clean-up (< 5 USDT), and regime shift exit.

### Pillar 4: Telemetry & Bot Reliability
- [ ] **Single-Owner Telegram Polling**: Telegram bot polling (`run_bot`) runs exclusively in `karsa-commander` (`KARSA_ROLE=commander`) to prevent polling conflicts.
- [ ] **Network DoH Bypass**: Outbound exchange connections utilize standard-library DoH (`setup_dns_bypass()`) to bypass ISP UDP 53 DNS hijacking.
- [ ] **Structured Telemetry**: Exposes Prometheus metrics and writes audit logs to Postgres/Redis.

---

## 3. The "Definition of NOT Done" (Anti-Patterns)

A PR or change will be **rejected immediately** if it contains any of the following anti-patterns:

1. ❌ **Using `float` for Money**: e.g., `price = 64000.50` instead of `Decimal("64000.50")`.
2. ❌ **Hardcoded Secrets**: Inlining API keys, Telegram tokens, or DB passwords outside of `.env` via Pydantic `Settings`.
3. ❌ **Silent Failures**: Swallowing exceptions (`except: pass`) without logging to Postgres, Redis, or Telegram.
4. ❌ **Bypassing PortfolioRiskManager**: Calling execution clients directly without passing pre-trade risk checks.
5. ❌ **Missing Exchange-Side SL**: Relying solely on internal software state to manage Stop-Loss exits.
6. ❌ **Competing Telegram Polling**: Spawning `run_bot` polling updaters in multiple containers simultaneously.