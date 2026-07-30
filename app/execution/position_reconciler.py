"""Position Reconciler — extracted from ActivePositionManager.

Handles position reconciliation (ghost detection), individual position
field repair from Bybit + candle data, ATR computation, and the health
check loop.
"""

from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from app.execution.constants import (
    APM_ERROR_BACKOFF_S,
    APM_RECONCILE_INTERVAL_S,
    ORPHAN_RE_ENTRY_GRACE_S,
    _safe_dec,
)


class PositionReconciler:
    """Reconciles internal position state against Bybit exchange data."""

    def __init__(
        self,
        bybit_client: object,
        position_store: object,
        regime_classifier: object,
        alert_service: object,
        recently_force_closed: dict[str, float],
        logger_: Any | None = None,
    ) -> None:
        self._client = bybit_client
        self._store = position_store
        self._regime = regime_classifier
        self._alert = alert_service
        self._recently_force_closed = recently_force_closed
        self._log = logger_ or logger

    # ------------------------------------------------------------------
    # Bulk reconciliation (ghost detection)
    # ------------------------------------------------------------------

    async def _reconcile_positions(self) -> None:
        """Compare internal state vs Bybit -- fix ghost positions.

        Also verifies SL order still exists on exchange; re-places if missing.
        """
        try:
            internal = await self._store.list_all()  # type: ignore[attr-defined]
            external = await self._client.fetch_positions()  # type: ignore[attr-defined]
            external_symbols = {p.get("symbol", "").replace("/", "") for p in external}

            for pos in internal:
                symbol = pos.get("symbol", "")
                if symbol.replace("/", "") not in external_symbols:
                    self._log.warning(f"APM: ghost position detected -- {symbol} not on Bybit, removing")
                    raw_side = pos.get("side", "buy")
                    api_side = "buy" if raw_side in ("buy", "LONG") else "sell"
                    await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]
                    continue

                # Verify SL is attached to the position -- ONLY re-place if missing
                # NEVER overwrite an existing SL (breakeven/trailing would be lost)
                entry_price = Decimal(str(pos.get("entry_price", "0")))
                raw_side = pos.get("side", "buy")
                api_side = "buy" if raw_side in ("buy", "LONG") else "sell"
                current_sl = Decimal(str(pos.get("current_sl", pos.get("stop_loss", "0")) or "0"))
                if entry_price > 0 and current_sl <= 0:
                    # SL is missing -- re-place it from initial_risk_per_unit
                    try:
                        sl_distance = Decimal(str(pos.get("initial_risk_per_unit", "0")))
                        if sl_distance > 0:
                            sl_price = entry_price - sl_distance if api_side == "buy" else entry_price + sl_distance
                        else:
                            # Fallback: 2% of entry
                            sl_price = (
                                entry_price * Decimal("0.98") if api_side == "buy" else entry_price * Decimal("1.02")
                            )
                        await self._client.set_trading_stop(  # type: ignore[attr-defined]
                            symbol, api_side, stop_loss=sl_price
                        )
                        self._log.info(f"APM: SL missing -- re-placed for {symbol} at {sl_price}")
                    except Exception:
                        self._log.warning(f"APM: SL reconciliation failed for {symbol}")
                elif current_sl > 0:
                    self._log.debug(f"APM: SL exists for {symbol} at {current_sl} -- skipping reconciliation re-place")

        except Exception:
            self._log.exception("APM: reconciliation failed")

    # ------------------------------------------------------------------
    # Per-position field reconciliation
    # ------------------------------------------------------------------

    async def _reconcile_position(self, pos: dict[str, Any]) -> bool:
        """Fill ALL missing fields from Bybit + candle data. Returns True if any field was updated.

        Critical: ensures no empty data in Redis. Runs once per position when fields are missing.
        """
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")
        changed = False

        # 1. Fetch Bybit position data for entry_price, SL, TP, amount
        try:
            bybit_symbol = symbol.replace("/", "")
            exchange_positions = await self._client.fetch_positions()
            exchange_pos = None
            for p in exchange_positions:
                p_sym = (p.get("symbol") or "").replace("/", "")
                p_side = "LONG" if p.get("side") == "buy" else "SHORT"
                if p_sym == bybit_symbol and p_side == side:
                    exchange_pos = p
                    break

            if exchange_pos is None:
                from app.core import metrics
                metrics.phantom_trade_detected_total.labels(symbol=symbol).inc()
                self._log.critical(f"APM reconcile: {symbol} is a phantom trade (does not exist on Bybit). PURGING from Redis.")
                # Immediate cleanup -- don't wait for SL hit. Phantom positions are stale state.
                try:
                    api_side = "buy" if side == "LONG" else "sell"
                    await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]
                    # FIX: Mark as recently force-closed to prevent orphan re-sync loop.
                    self._recently_force_closed[symbol] = datetime.now(timezone.utc).timestamp()
                    self._log.warning(f"APM: phantom {symbol} {side} purged from Redis (grace={ORPHAN_RE_ENTRY_GRACE_S}s)")
                except Exception as e:
                    self._log.error(f"APM: failed to purge phantom {symbol}: {e}")
                return False

            if exchange_pos:
                from app.core import metrics
                metrics.reconciliation_success_total.inc()
                # Entry price
                if not pos.get("entry_price") or pos.get("entry_price") == "0":
                    entry = exchange_pos.get("entry_price", 0)
                    if entry and float(entry) > 0:
                        pos["entry_price"] = str(entry)
                        changed = True
                        self._log.info(f"APM reconcile: {symbol} entry_price={entry}")

                # Amount (contracts)
                if not pos.get("amount") or pos.get("amount") == "0":
                    amount = exchange_pos.get("contracts", 0)
                    if amount and float(amount) > 0:
                        pos["amount"] = str(amount)
                        changed = True

                # SL from exchange -- validate direction
                exch_sl = exchange_pos.get("stopLoss")
                if exch_sl and str(exch_sl) not in ("0", "None", ""):
                    sl_val = Decimal(str(exch_sl))
                    entry_val = Decimal(str(pos.get("entry_price", 0)))
                    # SL must be below entry for LONG, above for SHORT
                    if entry_val > 0:
                        if side == "LONG" and sl_val >= entry_val:
                            self._log.warning(
                                f"APM reconcile: {symbol} SL {sl_val} >= entry {entry_val} for LONG -- skipping"
                            )
                        elif side == "SHORT" and sl_val <= entry_val:
                            self._log.warning(
                                f"APM reconcile: {symbol} SL {sl_val} <= entry {entry_val} for SHORT -- skipping"
                            )
                        else:
                            pos["current_sl"] = str(exch_sl)
                            pos["stop_loss"] = str(exch_sl)
                else:
                    if pos.get("current_sl") and str(pos.get("current_sl")) != "0":
                        self._log.critical(f"APM reconcile: {symbol} SL missing on exchange! Clearing local SL to trigger emergency replacement.")
                        pos["current_sl"] = "0"
                        pos["stop_loss"] = "0"
                        changed = True

                # TP from exchange
                exch_tp = exchange_pos.get("takeProfit")
                if exch_tp and str(exch_tp) not in ("0", "None", ""):
                    pos["take_profit"] = str(exch_tp)
        except Exception:
            self._log.debug(f"APM reconcile: failed to fetch Bybit data for {symbol}")

        # 2. ATR from candles
        atr = Decimal(str(pos.get("atr", "0") or "0"))
        if atr <= 0:
            atr = await self._compute_atr(symbol)
            if atr > 0:
                pos["atr"] = str(atr)
                changed = True
                self._log.info(f"APM reconcile: {symbol} atr={atr}")

        # 3. Regime from classifier
        entry_regime = pos.get("entry_regime", "")
        if not entry_regime and symbol:
            try:
                import numpy as np

                candles = []
                if hasattr(self._client, "session") and self._client.session:
                    bybit_symbol = symbol.replace("/", "")
                    raw = await self._client._execute(
                        self._client.session.get_kline,
                        category="linear",
                        symbol=bybit_symbol,
                        interval="60",
                        limit=60,
                    )
                    candle_data = raw.get("list", [])
                    if len(candle_data) >= 50:
                        candle_data.reverse()
                        candles = [[float(x) for x in c] for c in candle_data]
                if candles and hasattr(self._regime, "classify"):
                    arr = np.array(candles, dtype=np.float64)
                    regime = self._regime.classify(arr)
                    entry_regime = regime.value
                    pos["entry_regime"] = entry_regime
                    pos["regime"] = entry_regime
                    changed = True
                    self._log.info(f"APM reconcile: {symbol} regime={entry_regime}")
            except Exception:
                self._log.debug(f"APM reconcile: regime classification failed for {symbol}")

        # 4. initial_risk_per_unit from ATR or Entry Price Fallback
        # CAP: maximum 5% of entry price -- prevents catastrophic losses
        MAX_RISK_PCT = Decimal("0.05")
        initial_risk = Decimal(str(pos.get("initial_risk_per_unit", "0") or "0"))
        if initial_risk <= 0:
            if atr > 0:
                regime = entry_regime or "RANGE"
                sl_buffer = Decimal("1.0") if "RANGE" in regime else Decimal("1.5")
                initial_risk = atr * sl_buffer
            else:
                # Absolute fallback if ATR is completely missing for legacy positions
                entry_price_dec = Decimal(str(pos.get("entry_price", "0") or "0"))
                if entry_price_dec > 0:
                    initial_risk = entry_price_dec * Decimal("0.01")

            # Enforce 5% cap on initial risk
            entry_price_dec = Decimal(str(pos.get("entry_price", "0") or "0"))
            if entry_price_dec > 0:
                max_risk = entry_price_dec * MAX_RISK_PCT
                if initial_risk > max_risk:
                    self._log.warning(
                        f"APM: {symbol} initial_risk {initial_risk} exceeds 5% cap ({max_risk}) -- clamping"
                    )
                    initial_risk = max_risk

            if initial_risk > 0:
                pos["initial_risk_per_unit"] = str(initial_risk)
                changed = True
                self._log.info(f"APM reconcile: {symbol} initial_risk={initial_risk}")

        # 5. entry_time -- use entered_at if missing
        if not pos.get("entry_time") and pos.get("entered_at"):
            pos["entry_time"] = pos["entered_at"]

        # 6. Set exchange-side SL if missing
        current_sl = Decimal(str(pos.get("current_sl", pos.get("stop_loss", "0")) or "0"))
        entry_price = Decimal(str(pos.get("entry_price", "0") or "0"))
        if current_sl <= 0 and entry_price > 0 and initial_risk > 0:
            if side == "LONG":
                sl_price = entry_price - initial_risk
            else:
                sl_price = entry_price + initial_risk
            try:
                api_side = "buy" if side == "LONG" else "sell"
                await self._client.set_trading_stop(symbol, api_side, stop_loss=sl_price)
                pos["current_sl"] = str(sl_price)
                pos["stop_loss"] = str(sl_price)
                changed = True
                self._log.warning(f"APM reconcile: {symbol} exchange SL set to {sl_price}")
            except Exception as e:
                if "10001" in str(e):
                    # SL above/below price -- wrong direction, compute opposite
                    if side == "LONG":
                        sl_price = entry_price - initial_risk
                    else:
                        sl_price = entry_price + initial_risk
                    try:
                        await self._client.set_trading_stop(symbol, api_side, stop_loss=sl_price)
                        pos["current_sl"] = str(sl_price)
                        pos["stop_loss"] = str(sl_price)
                        changed = True
                        self._log.warning(f"APM reconcile: {symbol} exchange SL corrected to {sl_price}")
                    except Exception:
                        self._log.debug(f"APM reconcile: SL placement failed for {symbol}")
                else:
                    self._log.debug(f"APM reconcile: SL placement failed for {symbol}: {e}")

        # 7. Set exchange-side TP if missing and TREND regime
        if entry_regime and "TREND" in entry_regime:
            current_tp = pos.get("take_profit", "")
            if not current_tp or str(current_tp) in ("0", "None", ""):
                # For TREND, use 2x ATR as TP
                if atr > 0 and entry_price > 0:
                    if side == "LONG":
                        tp_price = entry_price + (atr * Decimal("2.0"))
                    else:
                        tp_price = entry_price - (atr * Decimal("2.0"))
                    try:
                        api_side = "buy" if side == "LONG" else "sell"
                        await self._client.set_trading_stop(symbol, api_side, take_profit=tp_price)
                        pos["take_profit"] = str(tp_price)
                        changed = True
                        self._log.warning(f"APM reconcile: {symbol} exchange TP set to {tp_price}")
                    except Exception:
                        self._log.debug(f"APM reconcile: TP placement failed for {symbol}")

        # 8. Persist changes
        if changed:
            try:
                # Use canonical side key (LONG/SHORT) to match position_store._key()
                from app.core.position_store import _normalize_side

                side_key = _normalize_side(side)
                redis_key = f"karsa:position:{symbol}:{side_key}"
                changed_fields = [
                    f
                    for f in [
                        "entry_price",
                        "amount",
                        "current_sl",
                        "stop_loss",
                        "take_profit",
                        "atr",
                        "entry_regime",
                        "regime",
                        "initial_risk_per_unit",
                        "entry_time",
                    ]
                    if pos.get(f)
                ]
                await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                self._log.warning(
                    "APM reconcile: %s updated %d field(s): %s",
                    symbol,
                    len(changed_fields),
                    changed_fields,
                )
            except Exception:
                self._log.exception(f"APM reconcile: persist failed for {symbol}")

        return changed

    # ------------------------------------------------------------------
    # ATR computation
    # ------------------------------------------------------------------

    async def _compute_atr(self, symbol: str, period: int = 14) -> Decimal:
        """Fetch 1h candles from Bybit and compute ATR(period) via Wilder smoothing."""
        try:
            import numpy as np

            bybit_symbol = symbol.replace("/", "")
            if hasattr(self._client, "session") and self._client.session:
                raw = await self._client._execute(
                    self._client.session.get_kline,
                    category="linear",
                    symbol=bybit_symbol,
                    interval="60",
                    limit=60,
                )
                candles = raw.get("list", [])
                if len(candles) < period + 1:
                    return Decimal("0")
                candles.reverse()
                arr = np.array([[float(x) for x in c] for c in candles], dtype=np.float64)
                highs, lows, closes = arr[:, 2], arr[:, 3], arr[:, 4]
                prev_closes = np.roll(closes, 1)
                prev_closes[0] = closes[0]
                tr = np.maximum(
                    highs - lows,
                    np.maximum(np.abs(highs - prev_closes), np.abs(lows - prev_closes)),
                )
                tr = tr[1:]
                atr = np.mean(tr[:period])
                for i in range(period, len(tr)):
                    atr = (atr * (period - 1) + tr[i]) / period
                result = Decimal(str(atr))
                if result > 0:
                    self._log.info(f"APM: computed ATR for {symbol} = {result}")
                return result
        except Exception:
            self._log.debug(f"APM: ATR computation failed for {symbol}")
        return Decimal("0")

    # ------------------------------------------------------------------
    # Health check loop
    # ------------------------------------------------------------------

    async def start_health_check_loop(self, interval_s: int = 60) -> None:
        """Scheduled position health check -- runs every `interval_s` seconds.

        Detects positions with missing critical fields and auto-repairs them
        from Bybit REST API + ATR computation. Runs as a separate asyncio task,
        independent of the main 2s monitoring loop.

        Critical fields checked every cycle:
          - initial_risk_per_unit  (APM won't protect position without this)
          - entry_regime           (controls trailing/TP strategy)
          - entry_price / amount   (needed for R-multiple calculation)
          - atr                    (needed for breakeven and trailing)
          - current_sl / stop_loss (exchange-side SL must exist)
        """
        _REQUIRED_FIELDS = [
            "initial_risk_per_unit",
            "entry_regime",
            "entry_price",
            "amount",
            "atr",
        ]
        while True:
            try:
                await asyncio.sleep(interval_s)
                positions = await self._store.list_all()  # type: ignore[attr-defined]
                if not positions:
                    continue

                repaired = 0
                for pos in positions:
                    symbol = pos.get("symbol", "")
                    side = pos.get("side", "LONG")
                    if not symbol:
                        continue

                    missing = await self._store.get_missing_fields(  # type: ignore[attr-defined]
                        symbol, side, _REQUIRED_FIELDS
                    )
                    # Always reconcile to ensure exchange matches Redis state (e.g. dropped SL)
                    self._log.warning(
                        "HEALTH_CHECK: %s %s missing fields %s -- auto-repairing",
                        symbol,
                        side,
                        missing,
                    )
                    changed = await self._reconcile_position(pos)

                    # Also verify SL exists on exchange after repair
                    entry_price = _safe_dec(pos.get("entry_price", "0"))
                    current_sl = _safe_dec(pos.get("current_sl", pos.get("stop_loss", "0")))
                    initial_risk = _safe_dec(pos.get("initial_risk_per_unit", "0"))
                    if current_sl <= 0 and entry_price > 0 and initial_risk > 0:
                        # SL still missing after repair -- place emergency SL
                        api_side = "buy" if side == "LONG" else "sell"
                        if side == "LONG":
                            sl_price = entry_price - initial_risk
                        else:
                            sl_price = entry_price + initial_risk
                        try:
                            await self._client.set_trading_stop(
                                symbol, api_side, stop_loss=sl_price
                            )  # type: ignore[attr-defined]
                            await self._store.update_fields(
                                symbol,
                                side,
                                {  # type: ignore[attr-defined]
                                    "current_sl": str(sl_price),
                                    "stop_loss": str(sl_price),
                                },
                            )
                            self._log.warning(
                                "HEALTH_CHECK: emergency SL placed for %s %s @ %s",
                                symbol,
                                side,
                                sl_price,
                            )
                            changed = True
                        except Exception as e:
                            self._log.error(
                                "HEALTH_CHECK: emergency SL FAILED for %s: %s -- POSITION UNPROTECTED",
                                symbol,
                                e,
                            )
                            if self._alert:
                                await self._alert.send(  # type: ignore[attr-defined]
                                    f"HEALTH CHECK: SL missing & placement FAILED for {symbol} {side}. MANUAL INTERVENTION NEEDED."
                                )

                    if changed:
                        repaired += 1

                if repaired:
                    self._log.warning("HEALTH_CHECK: repaired %d positions", repaired)
                    if self._alert:
                        await self._alert.send(  # type: ignore[attr-defined]
                            f"APM health check: auto-repaired {repaired} position(s) with missing fields."
                        )

            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("APM: health check loop error")
                await asyncio.sleep(APM_ERROR_BACKOFF_S)
