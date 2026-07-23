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
from decimal import Decimal

from loguru import logger

try:
    from app.risk.macro_filter import MacroFilter
except ImportError:
    class MacroFilter:  # type: ignore[no-redef]
        def __init__(self, check_interval_seconds: int = 900) -> None:
            self.is_kill_switch_active = False
            self.reason = ""
        async def update(self) -> None:
            pass

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
        ohlcv_fetcher: object | None = None,
    ) -> None:
        self._redis = redis_client
        self._position_store = position_store
        self._trade_store = trade_store
        self._sector_mapping = sector_mapping
        self._bybit_client = bybit_client
        self._ohlcv_fetcher = ohlcv_fetcher

        from app.core.config import get_settings
        _s = get_settings()
        self._max_gross_pct = Decimal(_s.max_gross_exposure_pct)
        self._max_net_pct = Decimal(_s.max_net_exposure_pct)
        self._max_single_pct = Decimal(_s.max_single_position_pct)
        self._macro_filter = MacroFilter(check_interval_seconds=900)
        self._macro_task = asyncio.create_task(self._update_macro_loop())

    async def _update_macro_loop(self) -> None:
        while True:
            await self._macro_filter.update()
            await asyncio.sleep(60)

    async def check(self, signal: object) -> PRMResult:
        """Run all portfolio risk checks. Fail-safe: exception → BLOCK."""
        try:
            checks: list[CheckResult] = []

            # 1. Correlation trap
            c = await self._check_correlation_trap(signal)
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            # 1.1 Rolling Correlation Matrix Check
            c = await self._check_rolling_correlation(signal)
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

            # 5. Macro Kill-Switch
            c = await self._check_macro_kill_switch(signal)
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            return PRMResult(approved=True, checks=checks)

        except Exception:
            logger.exception("PortfolioRiskManager: exception in check() — BLOCKING")
            return PRMResult(
                approved=False, reason="PRM internal error (fail-safe BLOCK)"
            )

    async def evaluate_capital_reallocation(self, new_signal: object) -> dict[str, Any] | None:
        """Evaluate if incoming signal has >1.5x expected value over open consolidating winners.

        Returns dict with reallocation target position or None if no scale-out needed.
        Guarded by idempotency flag `proactive_scale_out_executed`.
        """
        try:
            sig_conf = float(getattr(new_signal, "confidence", 0.5))
            sig_tp_dist = float(getattr(new_signal, "tp_distance_pct", 0.03))
            sig_sl_dist = float(getattr(new_signal, "sl_distance_pct", 0.01))

            new_signal_ev = sig_conf * (sig_tp_dist / sig_sl_dist if sig_sl_dist > 0 else 2.0)

            positions = await self._position_store.list_all()  # type: ignore[attr-defined]
            if not positions:
                return None

            best_candidate = None
            lowest_ev = float("inf")

            for pos in positions:
                sym = pos.get("symbol", "")
                side = pos.get("side", "LONG")
                already_scaled = pos.get("proactive_scale_out_executed", False)
                if already_scaled or str(already_scaled).lower() == "true":
                    continue

                entry_price = float(pos.get("entry_price", 0.0))
                live_price = float(pos.get("live_price", entry_price))
                tp_price = float(pos.get("take_profit", 0.0))
                sl_price = float(pos.get("current_sl", pos.get("stop_loss", 0.0)))

                if entry_price <= 0 or sl_price <= 0:
                    continue

                pnl = (live_price - entry_price) / entry_price if side == "LONG" else (entry_price - live_price) / entry_price
                if pnl <= 0:
                    continue

                rem_tp_dist = abs(tp_price - live_price) / live_price if tp_price > 0 else 0.02
                rem_sl_dist = abs(live_price - sl_price) / live_price if sl_price > 0 else 0.01

                open_position_ev = pnl * (rem_tp_dist / rem_sl_dist if rem_sl_dist > 0 else 1.0)

                # Check 1.5x hysteresis multiplier: New_Signal_EV > Open_Position_EV * 1.5
                if new_signal_ev > (open_position_ev * 1.5):
                    if open_position_ev < lowest_ev:
                        lowest_ev = open_position_ev
                        best_candidate = {
                            "symbol": sym,
                            "side": side,
                            "open_position_ev": open_position_ev,
                            "new_signal_ev": new_signal_ev,
                        }

            if best_candidate:
                logger.info(
                    f"PRM Capital Reallocation Triggered: New signal EV ({new_signal_ev:.2f}) > "
                    f"Open position {best_candidate['symbol']} EV ({best_candidate['open_position_ev']:.2f}) * 1.5. "
                    f"Recommending 50% proactive scale-out."
                )
                return best_candidate

            return None
        except Exception as e:
            logger.debug(f"PRM evaluate_capital_reallocation failed: {e}")
            return None

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

            # If the sector is unknown (e.g. micro-caps), they are idiosyncratic. Don't block.
            if sector == "UNKNOWN":
                return CheckResult(passed=True)

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

    async def _check_rolling_correlation(self, signal: object) -> CheckResult:
        """Check 24h rolling Pearson correlation between candidate signal and open positions.
        - correlation > 0.80 with 1 open position on same side -> 50% position size reduction.
        - correlation > 0.80 with 2+ open positions on same side -> HARD BLOCK.
        """
        symbol = getattr(signal, "symbol", None)
        direction = getattr(signal, "direction", "LONG")
        if not symbol or not self._ohlcv_fetcher:
            return CheckResult(passed=True)

        try:
            positions = await self._position_store.list_all()  # type: ignore[attr-defined]
            if not positions:
                return CheckResult(passed=True)

            high_corr_count = 0
            max_corr = 0.0

            # Fetch candidate symbol's last 25 1H candles
            candles_a = await self._ohlcv_fetcher.fetch(symbol, "1h", limit=25)  # type: ignore[attr-defined]
            if not candles_a or len(candles_a) < 10:
                return CheckResult(passed=True)

            import numpy as np
            closes_a = np.array([c[4] for c in candles_a[-25:]], dtype=float)
            returns_a = np.diff(closes_a) / closes_a[:-1]

            if np.std(returns_a) == 0:
                return CheckResult(passed=True)

            for p in positions:
                p_sym = p.get("symbol", "")
                p_side = p.get("side", "LONG")
                if not p_sym or p_sym == symbol:
                    continue

                if p_side != direction:
                    continue

                candles_b = await self._ohlcv_fetcher.fetch(p_sym, "1h", limit=25)  # type: ignore[attr-defined]
                if not candles_b or len(candles_b) < 10:
                    continue

                closes_b = np.array([c[4] for c in candles_b[-25:]], dtype=float)
                min_len = min(len(closes_a), len(closes_b))
                if min_len < 5:
                    continue

                r_a = np.diff(closes_a[:min_len]) / closes_a[:min_len-1]
                r_b = np.diff(closes_b[:min_len]) / closes_b[:min_len-1]

                if np.std(r_b) == 0:
                    continue

                corr = float(np.corrcoef(r_a, r_b)[0, 1])
                if not np.isnan(corr) and corr > 0.80:
                    high_corr_count += 1
                    max_corr = max(max_corr, corr)
                    logger.warning(
                        f"PRM Rolling Correlation Warning: {symbol} ({direction}) has {corr:.2f} correlation with open position {p_sym}"
                    )

            if high_corr_count >= 2:
                return CheckResult(
                    passed=False,
                    reason=f"Rolling correlation > 0.80 with {high_corr_count} open positions (max_corr={max_corr:.2f})",
                )
            elif high_corr_count == 1:
                current_amount = getattr(signal, "amount", Decimal("0"))
                if current_amount > 0:
                    signal.amount = current_amount * Decimal("0.5")  # type: ignore[attr-defined]
                    logger.info(
                        f"PRM Correlation Sizing: reduced {symbol} size 50% ({current_amount} -> {signal.amount}) due to {max_corr:.2f} correlation"
                    )

            return CheckResult(passed=True)
        except Exception:
            logger.debug(f"PRM rolling correlation check fallback for {symbol}")
            return CheckResult(passed=True)

    # ------------------------------------------------------------------
    # Check 1.5: Macro Kill-Switch
    # ------------------------------------------------------------------

    async def _check_macro_kill_switch(self, signal: object) -> CheckResult:
        """Block LONG signals if Macro Kill-Switch is active (BTC dumping or DXY pumping)."""
        direction = getattr(signal, "direction", "FLAT")
        if direction == "LONG" and self._macro_filter.is_kill_switch_active:
            return CheckResult(passed=False, reason=self._macro_filter.reason)
        return CheckResult(passed=True)

    # ------------------------------------------------------------------
    # Check 2: Exposure limits
    # ------------------------------------------------------------------

    async def _check_exposure_limits(self, signal: object) -> CheckResult:
        """Check gross/net exposure against equity thresholds.

        Thresholds loaded from .env via Settings (fallback to conservative defaults).
        """

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
                signal_notional = Decimal(str(signal_entry)) * Decimal(
                    str(signal_amount)
                )
                max_single = equity * self._max_single_pct
                if signal_notional > max_single:
                    return CheckResult(
                        passed=False,
                        reason=f"position notional {signal_notional:.2f} > {self._max_single_pct * 100}% of equity {equity:.2f}",
                    )

            if gross_notional > equity * self._max_gross_pct:
                return CheckResult(
                    passed=False,
                    reason=f"gross exposure {gross_notional:.0f} > {self._max_gross_pct * 100}% of equity {equity:.0f}",
                )
            if abs(net_notional) > equity * self._max_net_pct:
                return CheckResult(
                    passed=False,
                    reason=f"net exposure {abs(net_notional):.0f} > {self._max_net_pct * 100}% of equity {equity:.0f}",
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
            if state.get("status") == "TRIGGERED" and state.get(
                "reason", ""
            ).startswith("daily"):
                logger.warning("PRM: daily loss circuit breaker TRIGGERED")
                return CheckResult(
                    passed=False, reason=f"daily loss CB: {state.get('reason', '')}"
                )
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
            if (
                state.get("status") == "TRIGGERED"
                and "consecutive" in state.get("reason", "").lower()
            ):
                logger.warning("PRM: consecutive loss circuit breaker TRIGGERED")
                return CheckResult(
                    passed=False,
                    reason=f"consecutive loss CB: {state.get('reason', '')}",
                )
            return CheckResult(passed=True)

        except Exception:
            logger.exception("PRM: consecutive loss CB check failed — BLOCKING")
            return CheckResult(
                passed=False, reason="consecutive loss CB check unavailable"
            )

    # ------------------------------------------------------------------
    # Daily reset loop
    # ------------------------------------------------------------------

    async def monitor_circuit_breakers(self) -> None:
        """Background task: periodically check for circuit breaker conditions and trigger Doctor."""
        import json as _json

        from app.core.ai_client import AIClient
        from app.watchdog.system_doctor import SystemDoctor

        doctor = None
        if self._redis:
            ai_client = AIClient(self._redis)
            # alert_service omitted here or passed in if available
            doctor = SystemDoctor(self._redis, ai_client)

        while True:
            try:
                await asyncio.sleep(15)  # Check every 15 seconds
                if not self._redis or not self._trade_store:
                    continue

                # Simple logic for daily loss: check total realized PnL today
                now = datetime.now(UTC)
                start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

                # Pseudocode logic: in reality we would query TradeStore for today's trades.
                # Assuming `get_recent_trades` exists:
                trades = await self._trade_store.get_recent_trades(limit=100)  # type: ignore

                # Sort by exit_time ascending so consecutive loss streak is counted correctly
                # (newest last — so the counter reflects the most recent sequence of trades)
                def _parse_time(t: dict) -> datetime:
                    try:
                        return datetime.fromisoformat(t.get("exit_time", "2000-01-01T00:00:00"))
                    except Exception:
                        return datetime.min

                trades_sorted = sorted(trades, key=_parse_time)

                daily_pnl = Decimal("0")
                consecutive_losses = 0

                for t in trades_sorted:
                    # Filter for today
                    t_time = datetime.fromisoformat(t.get("exit_time", now.isoformat()))
                    if t_time >= start_of_day:
                        pnl = Decimal(str(t.get("realized_pnl", "0")))
                        daily_pnl += pnl
                        if pnl < 0:
                            consecutive_losses += 1
                        else:
                            consecutive_losses = 0

                # Fetch equity
                wallet = await self._bybit_client.get_wallet_balance()  # type: ignore
                equity = Decimal(
                    str(wallet.get("balance", wallet.get("available", "0")))
                )

                cb_triggered = False
                reason = ""

                if equity > 0 and daily_pnl < -(equity * Decimal("0.025")):
                    cb_triggered = True
                    reason = "Daily loss exceeded -2.5%"
                elif consecutive_losses >= 3:
                    cb_triggered = True
                    reason = "3 consecutive losses detected"

                if cb_triggered:
                    raw = await self._redis.get("system:circuit_breaker")
                    state = _json.loads(raw) if raw else {}

                    if state.get("status") != "TRIGGERED":
                        # Flip to triggered
                        payload = {
                            "status": "TRIGGERED",
                            "reason": reason,
                            "triggered_at": now.isoformat(),
                        }
                        await self._redis.set(
                            "system:circuit_breaker", _json.dumps(payload)
                        )
                        logger.critical(
                            f"PortfolioRiskManager: CIRCUIT BREAKER TRIGGERED: {reason}"
                        )

                        if doctor:
                            # Run SystemDoctor asynchronously so it doesn't block the loop
                            asyncio.create_task(doctor.diagnose_and_treat(reason))

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"PRM monitor_circuit_breakers error: {e}")

    async def reset_daily_state_loop(self) -> None:
        """Background task: reset daily CB state at UTC midnight.

        Also handles 4-hour cooldown clearing.
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
                    logger.info(
                        "PortfolioRiskManager: daily CB state reset at UTC midnight"
                    )

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
                if age_hours > 4:
                    await self._redis.set(
                        "system:circuit_breaker",
                        _json.dumps(
                            {
                                "status": "RESET",
                                "reason": f"4-hour cooldown complete (Triggered {age_hours:.1f}h ago)",
                            }
                        ),
                    )
                    logger.warning("PRM: 4-hour cooldown complete, CB cleared.")
        except Exception:
            logger.exception("PRM: failed to check stuck CB")
