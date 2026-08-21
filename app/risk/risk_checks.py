"""Risk Checks Mixin — extracted _check_* methods from PortfolioRiskManager.

This mixin contains the 9 individual risk check methods that were previously
part of PortfolioRiskManager. It is mixed into PortfolioRiskManager to keep
the main class focused on lifecycle management.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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


class RiskChecksMixin:
    """Mixin containing all _check_* risk evaluation methods.

    Requires the host class to provide:
      self._redis
      self._position_store
      self._sector_mapping
      self._ohlcv_fetcher
      self._macro_filter
      self._trade_store
      self._bybit_client
      self._velocity_threshold
      self._velocity_pause_seconds
    """

    async def _check_mtf_regime_alignment(self, signal: object) -> CheckResult:
        """Layer 3: Reject signal if 15m signal direction contradicts broader market regime in Redis."""
        try:
            direction = getattr(signal, "direction", "LONG")
            if self._redis is None:
                return CheckResult(passed=False, reason="Layer 3 MTF Mismatch: Redis unavailable (fail-safe BLOCK)")

            raw = await self._redis.get("system:config:market_state")
            if raw is None:
                return CheckResult(passed=False, reason="Layer 3 MTF Mismatch: Market state unavailable in Redis (fail-safe BLOCK)")

            import json
            data = json.loads(raw)
            regime = data.get("regime", "RANGE")

            if regime == "TREND_BULL" and direction == "SHORT":
                return CheckResult(
                    passed=False, reason="Layer 3 MTF Mismatch: SHORT signal in TREND_BULL regime"
                )
            elif regime == "TREND_BEAR" and direction == "LONG":
                return CheckResult(
                    passed=False, reason="Layer 3 MTF Mismatch: LONG signal in TREND_BEAR regime"
                )
            elif regime == "CHOP":
                return CheckResult(
                    passed=False, reason="Layer 3 MTF Mismatch: Signal rejected in CHOP regime"
                )
            return CheckResult(passed=True)
        except Exception:
            return CheckResult(passed=False, reason="Layer 3 MTF Mismatch: Internal error (fail-safe BLOCK)")

    # ------------------------------------------------------------------
    # Check 0.5: Global Slot Cap & Burst Limiter
    # ------------------------------------------------------------------

    async def _check_max_active_positions(self, signal: object) -> CheckResult:
        """Hard constraint: Max 3 concurrent open positions across portfolio."""
        try:
            positions = await self._position_store.list_all()  # type: ignore[attr-defined]
            if len(positions) >= 3:
                return CheckResult(
                    passed=False,
                    reason=f"Maximum portfolio slots full ({len(positions)}/3 active positions)",
                )
            return CheckResult(passed=True)
        except Exception:
            logger.exception("PRM: max active positions check failed — BLOCKING")
            return CheckResult(passed=False, reason="position store unavailable")

    async def _check_entry_burst_limiter(self, signal: object) -> CheckResult:
        """Limit new entries to max 2 in any rolling 15-minute window to stop flash churn."""
        if self._redis is None:
            return CheckResult(passed=True)
        try:
            import time
            now_ts = time.time()
            window_start = now_ts - 900.0  # 15 minutes
            key = "karsa:risk:recent_entry_timestamps"

            # Purge entries older than 15 minutes
            await self._redis.zremrangebyscore(key, "-inf", window_start)  # type: ignore[attr-defined]
            recent_count = await self._redis.zcard(key)  # type: ignore[attr-defined]

            if recent_count >= 2:
                return CheckResult(
                    passed=False,
                    reason=f"Entry burst rate limit: {recent_count}/2 positions opened in last 15m",
                )
            return CheckResult(passed=True)
        except Exception:
            return CheckResult(passed=True)

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
                # Publish correlation data for downstream scoring
                if self._redis:
                    try:
                        import json as _json
                        await self._redis.set(f"karsa:correlation:{symbol}", _json.dumps({
                            "max_correlation": round(max_corr, 4),
                            "correlated_count": high_corr_count,
                        }), ex=300)
                    except Exception:
                        pass
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
                # Publish correlation data for downstream scoring
                if self._redis:
                    try:
                        import json as _json
                        await self._redis.set(f"karsa:correlation:{symbol}", _json.dumps({
                            "max_correlation": round(max_corr, 4),
                            "correlated_count": high_corr_count,
                        }), ex=300)
                    except Exception:
                        pass

            return CheckResult(passed=True)
        except Exception:
            logger.debug(f"PRM rolling correlation check fallback for {symbol}")
            return CheckResult(passed=True)

    # ------------------------------------------------------------------
    # Check 1.2: Multi-Leg Spread Detection
    # ------------------------------------------------------------------

    async def _check_spread_legs(self, signal: object) -> CheckResult:
        """Detect multi-leg spread trades (e.g. LONG ETH + SHORT SOL).

        If the incoming signal would create a spread pair with an existing position,
        evaluate the combined risk as a single unit rather than two independent positions.
        Spread pairs with correlated assets (>0.7 correlation) share directional risk.
        """
        symbol = getattr(signal, "symbol", None)
        direction = getattr(signal, "direction", "LONG")
        if not symbol:
            return CheckResult(passed=True)

        try:
            positions = await self._position_store.list_all()  # type: ignore[attr-defined]
            if not positions:
                return CheckResult(passed=True)

            # Find potential spread legs: same sector, opposite direction
            spread_legs = []
            for pos in positions:
                p_sym = pos.get("symbol", "")
                p_side = pos.get("side", "LONG")
                if p_sym == symbol or p_sym in ANCHOR_SYMBOLS:
                    continue

                # Opposite direction in same sector = potential spread
                if p_side != direction:
                    p_sector = await self._sector_mapping.get_sector(p_sym)  # type: ignore[attr-defined]
                    sig_sector = await self._sector_mapping.get_sector(symbol)  # type: ignore[attr-defined]
                    if p_sector == sig_sector and p_sector != "UNKNOWN":
                        spread_legs.append(pos)

            if not spread_legs:
                return CheckResult(passed=True)

            # Check if this creates a concentrated spread (3+ legs in same sector)
            total_sector_exposure = len(spread_legs) + 1  # +1 for incoming signal
            if total_sector_exposure >= 3:
                return CheckResult(
                    passed=False,
                    reason=f"Multi-leg spread: {total_sector_exposure} positions in sector {sig_sector} (max 2)",
                )

            # Spread with 1 existing position: log warning but allow
            if spread_legs:
                leg_sym = spread_legs[0].get("symbol", "?")
                logger.warning(
                    f"PRM Spread Detection: {symbol} {direction} forms spread with {leg_sym} "
                    f"{spread_legs[0].get('side', '?')} in sector {sig_sector}. "
                    f"Combined exposure monitored as single risk unit."
                )

            return CheckResult(passed=True)
        except Exception:
            logger.debug(f"PRM spread leg check fallback for {symbol}")
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
            if equity <= Decimal("0"):
                logger.warning("PRM: Wallet equity is 0 or unavailable — BLOCKING trade (fail-safe)")
                return CheckResult(passed=False, reason="Wallet equity unavailable (fail-safe BLOCK)")

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
    # Check 6: Drawdown Velocity Breaker (Sprint 1)
    # ------------------------------------------------------------------

    async def _check_drawdown_velocity(self) -> CheckResult:
        """Sprint 1: Block if rolling 1-hour PnL loss exceeds velocity threshold.

        Detects HOW FAST the account is losing — losing $5 over 24h is variance,
        losing $5 in 30 minutes means the regime has fundamentally shifted.

        Reads/writes Redis: system:velocity_pause
        Config: velocity_1h_loss_pct (default -1.5%), velocity_pause_seconds (default 7200s)
        """
        try:
            if self._redis is None:
                return CheckResult(passed=True)

            import json as _json

            # Check if already in velocity pause
            raw_pause = await self._redis.get("system:velocity_pause")
            if raw_pause:
                pause_data = _json.loads(raw_pause)
                pause_until = pause_data.get("pause_until", 0)
                now_ts = datetime.now(timezone.utc).timestamp()
                if now_ts < pause_until:
                    remaining_min = (pause_until - now_ts) / 60
                    logger.warning(
                        "PRM: velocity pause ACTIVE — %.0f min remaining", remaining_min
                    )
                    return CheckResult(
                        passed=False,
                        reason=f"velocity pause: {remaining_min:.0f}min remaining",
                    )
                else:
                    # Pause expired — clear it
                    await self._redis.delete("system:velocity_pause")
                    logger.info("PRM: velocity pause expired — resuming trading")

            # Calculate rolling 1-hour PnL from recent trades
            if not self._trade_store:
                return CheckResult(passed=True)

            trades = await self._trade_store.get_recent_trades(limit=50)  # type: ignore
            if not trades:
                return CheckResult(passed=True)

            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)
            rolling_1h_pnl = Decimal("0")

            for t in trades:
                try:
                    exit_time_str = t.get("exit_time", "")
                    if not exit_time_str:
                        continue
                    exit_time = datetime.fromisoformat(exit_time_str)
                    if exit_time.tzinfo is None:
                        exit_time = exit_time.replace(tzinfo=timezone.utc)
                    if exit_time >= one_hour_ago:
                        pnl = Decimal(str(t.get("realized_pnl", "0")))
                        rolling_1h_pnl += pnl
                except (ValueError, TypeError):
                    continue

            # Get current equity for percentage calculation
            try:
                wallet = await self._bybit_client.get_wallet_balance()  # type: ignore
                equity = Decimal(str(wallet.get("balance", wallet.get("available", "0"))))
            except Exception:
                equity = Decimal("0")

            if equity <= 0:
                return CheckResult(passed=True)

            # Check velocity threshold
            loss_pct = rolling_1h_pnl / equity
            threshold = Decimal(self._velocity_threshold)

            if loss_pct < threshold:
                # VELOCITY PAUSE triggered
                pause_seconds = self._velocity_pause_seconds
                pause_until_ts = now.timestamp() + pause_seconds
                pause_data = _json.dumps({
                    "status": "TRIGGERED",
                    "reason": f"1h PnL {rolling_1h_pnl:.2f} ({loss_pct:.2%}) exceeds {threshold:.2%}",
                    "pause_until": pause_until_ts,
                    "triggered_at": now.isoformat(),
                    "rolling_1h_pnl": str(rolling_1h_pnl),
                })
                await self._redis.set("system:velocity_pause", pause_data)

                logger.warning(
                    "PRM VELOCITY BREAKER: 1h PnL %.2f (%.2%%) exceeds threshold %.2%% — "
                    "pausing for %d seconds",
                    float(rolling_1h_pnl), float(loss_pct * 100),
                    float(threshold * 100), pause_seconds,
                )
                return CheckResult(
                    passed=False,
                    reason=f"velocity breaker: 1h PnL {rolling_1h_pnl:.2f} ({loss_pct:.2%}) < {threshold:.2%}",
                )

            return CheckResult(passed=True)

        except Exception:
            logger.exception("PRM: drawdown velocity check failed — fail-safe PASS")
            return CheckResult(passed=True)  # fail-open for velocity check (non-critical)
