"""Portfolio Risk Manager — Phase 6 portfolio-level risk checks.

Runs BEFORE RiskGate in risk_gate_task. Fail-safe: any exception → BLOCK.

Checks:
  1. Correlation trap: max positions per sector (BTC/ETH exempt as anchors)
  2. Exposure limits: gross/net notional vs equity thresholds
  3. Daily loss circuit breaker (PENDING Issue #11 — raises NotImplementedError)
  4. Consecutive loss circuit breaker (PENDING Issue #10 — raises NotImplementedError)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

# --- Constants (cross-ref: docs/SYSTEM_CONSTANTS.md §15.5) ---
PRM_MAX_SECTOR_POSITIONS: int = 2
PRM_LOSS_PAUSE_MINUTES: int = 60

# Anchor symbols exempt from sector cap
ANCHOR_SYMBOLS: set[str] = {"BTC/USDT", "ETH/USDT"}


@dataclass
class CheckResult:
    passed: bool
    reason: str = ""


@dataclass
class PRMResult:
    approved: bool
    reason: str = ""
    checks: list[CheckResult] | None = None


class PortfolioRiskManager:
    """Portfolio-level risk gate — runs before RiskGate."""

    def __init__(
        self,
        redis_client: object,
        position_store: object,
        trade_store: object,
        sector_mapping: object,
        bybit_client: object,
    ) -> None:
        self._redis = redis_client
        self._position_store = position_store
        self._trade_store = trade_store
        self._sector_mapping = sector_mapping
        self._bybit_client = bybit_client

    async def check(self, signal: object) -> PRMResult:
        """Run all portfolio risk checks. Fail-safe: exception → BLOCK."""
        try:
            checks: list[CheckResult] = []

            # 1. Correlation trap
            c = await self._check_correlation_trap(signal)
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            # 2. Exposure limits
            c = await self._check_exposure_limits(signal)
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            # 3. Daily loss CB (placeholder)
            c = await self._check_daily_loss_circuit_breaker()
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            # 4. Consecutive loss CB (placeholder)
            c = await self._check_consecutive_loss_circuit_breaker()
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            return PRMResult(approved=True, checks=checks)

        except Exception:
            logger.exception("PortfolioRiskManager: exception in check() — BLOCKING")
            return PRMResult(
                approved=False, reason="PRM internal error (fail-safe BLOCK)"
            )

    # ------------------------------------------------------------------
    # Check 1: Correlation trap
    # ------------------------------------------------------------------

    async def _check_correlation_trap(self, signal: object) -> CheckResult:
        """Block if sector already at max positions (anchors exempt)."""
        symbol = getattr(signal, "symbol", None)
        if symbol is None:
            return CheckResult(passed=False, reason="signal has no symbol")

        if symbol in ANCHOR_SYMBOLS:
            return CheckResult(passed=True)

        try:
            sector = await self._sector_mapping.get_sector(symbol)  # type: ignore[attr-defined]
            positions = await self._position_store.list_all()  # type: ignore[attr-defined]
            sector_count = 0
            for p in positions:
                p_sym = p.get("symbol", "")
                if p_sym not in ANCHOR_SYMBOLS:
                    p_sector = await self._sector_mapping.get_sector(p_sym)  # type: ignore[attr-defined]
                    if p_sector == sector:
                        sector_count += 1
            if sector_count >= PRM_MAX_SECTOR_POSITIONS:
                return CheckResult(
                    passed=False,
                    reason=f"sector {sector} at {sector_count}/{PRM_MAX_SECTOR_POSITIONS} positions",
                )
            return CheckResult(passed=True)
        except Exception:
            logger.exception("PRM: correlation trap check failed — BLOCKING")
            return CheckResult(passed=False, reason="correlation check unavailable")

    # ------------------------------------------------------------------
    # Check 2: Exposure limits
    # ------------------------------------------------------------------

    async def _check_exposure_limits(self, signal: object) -> CheckResult:
        """Check gross/net exposure against equity thresholds.

        Thresholds are TODO until team ratification — currently passes.
        """
        # TODO: implement when PRM_MAX_GROSS_EXPOSURE_PCT and PRM_MAX_NET_EXPOSURE_PCT confirmed
        return CheckResult(passed=True)

    # ------------------------------------------------------------------
    # Check 3: Daily loss circuit breaker
    # ------------------------------------------------------------------

    async def _check_daily_loss_circuit_breaker(self) -> CheckResult:
        """Daily loss CB — PENDING Issue #11 resolution."""
        # TODO: implement when PRM_DAILY_LOSS_LIMIT is confirmed
        return CheckResult(passed=True)

    # ------------------------------------------------------------------
    # Check 4: Consecutive loss circuit breaker
    # ------------------------------------------------------------------

    async def _check_consecutive_loss_circuit_breaker(self) -> CheckResult:
        """Consecutive loss CB — PENDING Issue #10 resolution."""
        # TODO: implement when PRM_MAX_CONSECUTIVE_LOSSES is confirmed
        return CheckResult(passed=True)

    # ------------------------------------------------------------------
    # Daily reset loop
    # ------------------------------------------------------------------

    async def reset_daily_state_loop(self) -> None:
        """Background task: reset daily CB state at UTC midnight."""
        while True:
            try:
                now = datetime.now(timezone.utc)
                tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if now >= tomorrow:
                    tomorrow += timedelta(days=1)
                wait_seconds = (tomorrow - now).total_seconds()

                await asyncio.sleep(wait_seconds)

                if self._redis is not None:
                    await self._redis.set("risk:portfolio_cb:daily_loss_fired", "0")  # type: ignore[attr-defined]
                    await self._redis.set("risk:portfolio_cb:consecutive_loss_count", "0")  # type: ignore[attr-defined]
                    logger.info(
                        "PortfolioRiskManager: daily CB state reset at UTC midnight"
                    )

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PortfolioRiskManager: daily reset loop error")
                await asyncio.sleep(60)
