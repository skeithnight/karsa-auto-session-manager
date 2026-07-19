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
from datetime import UTC, datetime, timedelta

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
            return PRMResult(approved=False, reason="PRM internal error (fail-safe BLOCK)")

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

        Thresholds pending team ratification — using conservative defaults.
        """
        from decimal import Decimal

        # Pending team ratification — conservative defaults
        PRM_MAX_GROSS_EXPOSURE_PCT = Decimal("0.50")  # 50% of equity
        PRM_MAX_NET_EXPOSURE_PCT = Decimal("0.30")  # 30% of equity
        PRM_MAX_SINGLE_POSITION_PCT = Decimal("0.40")  # 40% equity per position

        try:
            wallet = await self._bybit_client.get_wallet_balance()  # type: ignore[attr-defined]
            equity = Decimal(str(wallet.get("balance", wallet.get("available", "0"))))
            if equity <= 0:
                return CheckResult(passed=True)

            positions = await self._position_store.list_all()  # type: ignore[attr-defined]
            gross_notional = Decimal("0")
            net_notional = Decimal("0")

            for p in positions:
                entry_price = Decimal(str(p.get("entry_price", "0")))
                amount = Decimal(str(p.get("amount", "0")))
                if entry_price <= 0 or amount <= 0:
                    continue
                notional = entry_price * amount
                gross_notional += abs(notional)
                side = p.get("side", "buy")
                if side in ("buy", "LONG"):
                    net_notional += notional
                else:
                    net_notional -= notional

            # Per-position allocation cap
            signal_entry = getattr(signal, "entry_price", None)
            signal_amount = getattr(signal, "amount", None)
            if signal_entry and signal_amount:
                signal_notional = Decimal(str(signal_entry)) * Decimal(str(signal_amount))
                max_single = equity * PRM_MAX_SINGLE_POSITION_PCT
                if signal_notional > max_single:
                    return CheckResult(
                        passed=False,
                        reason=f"position notional {signal_notional:.2f} > {PRM_MAX_SINGLE_POSITION_PCT*100}% of equity {equity:.2f}",
                    )

            if gross_notional > equity * PRM_MAX_GROSS_EXPOSURE_PCT:
                return CheckResult(
                    passed=False,
                    reason=f"gross exposure {gross_notional:.0f} > {PRM_MAX_GROSS_EXPOSURE_PCT*100}% of equity {equity:.0f}",
                )
            if abs(net_notional) > equity * PRM_MAX_NET_EXPOSURE_PCT:
                return CheckResult(
                    passed=False,
                    reason=f"net exposure {abs(net_notional):.0f} > {PRM_MAX_NET_EXPOSURE_PCT*100}% of equity {equity:.0f}",
                )
            return CheckResult(passed=True)

        except Exception:
            logger.exception("PRM: exposure check failed — BLOCKING")
            return CheckResult(passed=False, reason="exposure check unavailable")

    # ------------------------------------------------------------------
    # Check 3: Daily loss circuit breaker
    # ------------------------------------------------------------------

    async def _check_daily_loss_circuit_breaker(self) -> CheckResult:
        """Block if daily PnL loss exceeds threshold (-2% relative or $500 absolute).

        Reads from Redis: system:circuit_breaker
        """
        try:
            if self._redis is None:
                return CheckResult(passed=True)

            raw = await self._redis.get("system:circuit_breaker")
            if raw is None:
                return CheckResult(passed=True)

            import json

            state = json.loads(raw)
            if state.get("status") == "TRIGGERED" and state.get("reason", "").startswith("daily"):
                logger.warning("PRM: daily loss circuit breaker TRIGGERED")
                return CheckResult(passed=False, reason=f"daily loss CB: {state.get('reason', '')}")
            return CheckResult(passed=True)

        except Exception:
            logger.exception("PRM: daily loss CB check failed — BLOCKING")
            return CheckResult(passed=False, reason="daily loss CB check unavailable")

    async def _check_consecutive_loss_circuit_breaker(self) -> CheckResult:
        """Block if 3+ consecutive losses detected.

        Reads from Redis: system:circuit_breaker
        """
        try:
            if self._redis is None:
                return CheckResult(passed=True)

            raw = await self._redis.get("system:circuit_breaker")
            if raw is None:
                return CheckResult(passed=True)

            import json

            state = json.loads(raw)
            if state.get("status") == "TRIGGERED" and "consecutive" in state.get("reason", "").lower():
                logger.warning("PRM: consecutive loss circuit breaker TRIGGERED")
                return CheckResult(passed=False, reason=f"consecutive loss CB: {state.get('reason', '')}")
            return CheckResult(passed=True)

        except Exception:
            logger.exception("PRM: consecutive loss CB check failed — BLOCKING")
            return CheckResult(passed=False, reason="consecutive loss CB check unavailable")

    # ------------------------------------------------------------------
    # Daily reset loop
    # ------------------------------------------------------------------

    async def reset_daily_state_loop(self) -> None:
        """Background task: reset daily CB state at UTC midnight.

        Also guards against stuck CB: if circuit_breaker has been TRIGGERED
        for >24h (e.g. midnight reset missed due to restart), force clear.
        """
        # Clear stuck CB on startup
        await self._clear_stuck_cb()

        while True:
            try:
                now = datetime.now(UTC)
                tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if now >= tomorrow:
                    tomorrow += timedelta(days=1)
                wait_seconds = (tomorrow - now).total_seconds()

                await asyncio.sleep(wait_seconds)

                if self._redis is not None:
                    import json as _json

                    await self._redis.set(
                        "system:circuit_breaker",
                        _json.dumps({"status": "RESET", "reason": "midnight reset"}),
                    )
                    # Clear legacy key if present
                    await self._redis.delete("circuit_breaker:HALTED")
                    logger.info("PortfolioRiskManager: daily CB state reset at UTC midnight")

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PortfolioRiskManager: daily reset loop error")
                await asyncio.sleep(60)

    async def _clear_stuck_cb(self) -> None:
        """Clear circuit breaker if stuck >24h (missed midnight reset)."""
        if self._redis is None:
            return
        try:
            import json as _json

            raw = await self._redis.get("system:circuit_breaker")
            if raw is None:
                return
            state = _json.loads(raw)
            if state.get("status") != "TRIGGERED":
                return
            triggered_at = state.get("triggered_at")
            if triggered_at is not None:
                ts = datetime.fromisoformat(triggered_at)
                age_hours = (datetime.now(UTC) - ts).total_seconds() / 3600
                if age_hours > 24:
                    await self._redis.set(
                        "system:circuit_breaker",
                        _json.dumps({"status": "RESET", "reason": f"stuck CB cleared after {age_hours:.0f}h"}),
                    )
                    logger.warning("PRM: stuck CB cleared after %.0fh", age_hours)
        except Exception:
            logger.exception("PRM: failed to check stuck CB")
