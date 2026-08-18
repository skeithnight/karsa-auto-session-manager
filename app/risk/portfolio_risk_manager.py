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
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from loguru import logger

from app.risk.risk_checks import (
    ANCHOR_SYMBOLS,
    CheckResult,
    PRM_MAX_SECTOR_POSITIONS,
    PRM_LOSS_PAUSE_MINUTES,
    PRMResult,
    RiskChecksMixin,
)

try:
    from app.risk.macro_filter import MacroFilter
except ImportError:
    class MacroFilter:  # type: ignore[no-redef]
        def __init__(self, check_interval_seconds: int = 900) -> None:
            self.is_kill_switch_active = False
            self.reason = ""
        async def update(self) -> None:
            pass


class PortfolioRiskManager(RiskChecksMixin):
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
        self._velocity_threshold = Decimal(_s.velocity_1h_loss_pct)
        self._velocity_pause_seconds = int(_s.velocity_pause_seconds)
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

            # 0. Global Max Positions Cap (5 Slots)
            c = await self._check_max_active_positions(signal)
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            # 0.1 Entry Burst Rate Limiter (Max 2 per 15m window)
            c = await self._check_entry_burst_limiter(signal)
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            # 0.2 MTF Regime Alignment (Layer 3)
            c = await self._check_mtf_regime_alignment(signal)
            checks.append(c)
            if not c.passed:
                return PRMResult(approved=False, reason=c.reason, checks=checks)

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

            # 1.2 Multi-Leg Spread Detection
            c = await self._check_spread_legs(signal)
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

            # 6. Drawdown Velocity Breaker (Sprint 1)
            c = await self._check_drawdown_velocity()
            checks.append(c)
            if not c.passed:
                logger.warning("🛡️ [STAGE 3: PORTFOLIO RISK MANAGER] REJECTED %s — Reason: %s", getattr(signal, 'symbol', 'UNKNOWN'), c.reason)
                return PRMResult(approved=False, reason=c.reason, checks=checks)

            # Record entry timestamp in burst limiter set
            if self._redis is not None:
                try:
                    import time
                    now_ts = time.time()
                    sym = getattr(signal, "symbol", "UNKNOWN")
                    key = "karsa:risk:recent_entry_timestamps"
                    await self._redis.zadd(key, {f"{sym}:{now_ts}": now_ts})  # type: ignore[attr-defined]
                    await self._redis.expire(key, 3600)  # type: ignore[attr-defined]
                except Exception:
                    pass

            logger.info("🛡️ [STAGE 3: PORTFOLIO RISK MANAGER] APPROVED %s — Passed all 8 pre-trade risk gates (Slots, Burst, Sector, Exposure, CB, Velocity, Macro)", getattr(signal, 'symbol', 'UNKNOWN'))
            return PRMResult(approved=True, checks=checks)

        except Exception:
            logger.exception("PortfolioRiskManager: exception in check() — BLOCKING")
            return PRMResult(
                approved=False, reason="PRM internal error (fail-safe BLOCK)"
            )

    async def evaluate_capital_reallocation(
        self, new_signal: object, open_positions: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """Evaluate if incoming signal has >1.5x expected value over open consolidating winners.

        Returns dict with reallocation target position or None if no scale-out needed.
        Guarded by idempotency flag `proactive_scale_out_executed`.
        """
        try:
            sig_conf = float(getattr(new_signal, "confidence", 0.5))
            sig_tp_dist = float(getattr(new_signal, "tp_distance_pct", 0.03))
            sig_sl_dist = float(getattr(new_signal, "sl_distance_pct", 0.01))

            new_signal_ev = sig_conf * (sig_tp_dist / sig_sl_dist if sig_sl_dist > 0 else 2.0)

            positions = open_positions if open_positions is not None else await self._position_store.list_all()  # type: ignore[attr-defined]
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

                momentum_multiplier = 1.0
                try:
                    if self._redis:
                        cvd_slope_raw = await self._redis.get(f"karsa:market:{sym}:cvd_slope")
                        if cvd_slope_raw:
                            cvd_slope = float(cvd_slope_raw)
                            if (side == "LONG" and cvd_slope < -0.2) or (side == "SHORT" and cvd_slope > 0.2):
                                momentum_multiplier = 0.5
                except Exception:
                    pass

                open_position_ev = (pnl * (rem_tp_dist / rem_sl_dist if rem_sl_dist > 0 else 1.0)) * momentum_multiplier

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
                now = datetime.now(timezone.utc)
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
        """Background task: reset daily CB state at timezone.utc midnight.

        Also handles 4-hour cooldown clearing.
        """
        # Clear stuck CB on startup
        await self._clear_stuck_cb()

        while True:
            try:
                now = datetime.now(timezone.utc)
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
                        "PortfolioRiskManager: daily CB state reset at timezone.utc midnight"
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
                age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
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
