"""Active Position Manager — Phase 6 position lifecycle management.

Replaces TrailingStopManager and CheckpointManager when APM_ENABLED=True.
Runs on 2s interval. All errors → backoff, never crash loop.

Core responsibilities:
  - +1R breakeven lock (exchange-side SL amend)
  - Regime-aware trailing stop (3x ATR Chandelier for TREND)
  - Time-based exits (max_hold_time_mins from RiskProfile)
  - Regime Shift Kill Switch (with 3-check hysteresis)
  - Position reconciliation (ghost detection)
"""

from __future__ import annotations

import asyncio
import json as _json
from datetime import UTC, datetime
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from loguru import logger

# --- Constants (cross-ref: docs/SYSTEM_CONSTANTS.md §15.4) ---
APM_MONITOR_INTERVAL_S: int = 2
APM_ERROR_BACKOFF_S: int = 5
APM_RECONCILE_INTERVAL_S: int = 300
APM_BREAKEVEN_FEE_PCT = Decimal("0.0012")
APM_TREND_TRAIL_ATR_MULT = Decimal("2.5")
APM_TREND_TRAIL_ACTIVATE_R = Decimal("1.5")
APM_BREAKEVEN_LOCK_R = Decimal("1.0")  # fallback when ATR unavailable
APM_BREAKEVEN_ATR_MULT = Decimal("1.5")  # price must move > 1.5x ATR to trigger BE

# Regime shift hysteresis: require N consecutive shifted checks
REGIME_SHIFT_CONFIRM_COUNT: int = 3


def _safe_dec(value: object, default: str = "0") -> Decimal:
    """Convert any value safely to Decimal without raising."""
    try:
        return Decimal(str(value)) if value is not None else Decimal(default)
    except Exception:
        return Decimal(default)


class ActivePositionManager:
    """Manages open positions: breakeven, trailing, time exit, regime kill switch."""

    def __init__(
        self,
        bybit_client: object,
        position_store: object,
        redis_client: object,
        regime_classifier: object,
        alert_service: object,
        trade_memory: object | None = None,
        logger_: Any | None = None,
    ) -> None:
        self._client = bybit_client
        self._store = position_store
        self.redis_client = redis_client
        self._regime = regime_classifier
        self._alert = alert_service
        self._trade_memory = trade_memory
        self._log = logger_ or logger
        self._regime_shift_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def start_monitoring(self) -> None:
        """Main monitoring loop — runs forever with error backoff."""
        last_reconcile = 0.0
        _cached_exchange_positions: list = []
        _last_positions_fetch = 0.0
        _POSITIONS_CACHE_TTL = 30.0  # seconds — REST rate limit protection

        while True:
            try:
                now = datetime.now(UTC).timestamp()

                positions = await self._store.list_all()  # type: ignore[attr-defined]

                # Sync Bybit positions missing from Redis — refresh every 30s, not every 2s
                if now - _last_positions_fetch >= _POSITIONS_CACHE_TTL:
                    _cached_exchange_positions = await self._client.fetch_positions()  # type: ignore[attr-defined]
                    _last_positions_fetch = now

                exchange_positions = _cached_exchange_positions
                existing_syms = {(p.get("symbol", ""), p.get("side", "")) for p in positions}
                for ep in exchange_positions:
                    ep_sym = ep.get("symbol", "")
                    ep_side = "LONG" if ep.get("side") == "buy" else "SHORT"
                    ccxt_sym = ep_sym[:-4] + "/" + ep_sym[-4:] if len(ep_sym) > 4 else ep_sym
                    if (ccxt_sym, ep_side) not in existing_syms:
                        entry = Decimal(str(ep.get("entry_price", 0)))
                        amount = Decimal(str(ep.get("contracts", 0)))
                        if entry > 0 and amount > 0:
                            await self._store.save(
                                symbol=ccxt_sym,
                                side=ep_side,
                                entry_price=entry,
                                amount=amount,
                            )
                            # Re-read the saved pos to get full dict for reconciliation
                            saved = await self._store.get(ccxt_sym, ep_side)
                            if saved:
                                positions.append(saved)
                            self._log.warning(f"APM: synced orphan {ccxt_sym} {ep_side} from Bybit")

                # Fetch live mark prices for all held symbols
                live_prices: dict[str, Decimal] = {}
                missing_symbols = []
                try:
                    for pos in positions:
                        sym = pos.get("symbol", "")
                        if not sym:
                            continue
                        import json as _json
                        raw = await self.redis_client.get(f"global:state:{sym}")
                        state = _json.loads(raw) if raw else None
                        if state and state.get("best_bid") and state.get("best_ask"):
                            # Use mid price from orderbook
                            mid = (Decimal(str(state["best_bid"])) + Decimal(str(state["best_ask"]))) / Decimal("2")
                            live_prices[sym] = mid
                        else:
                            missing_symbols.append(sym)

                    if missing_symbols:
                        self._log.debug(f"APM: Redis missing live price for {missing_symbols}, falling back to Bybit REST")
                        # fallback to ccxt fetch_tickers
                        tickers = await self._client.fetch_tickers()  # type: ignore[attr-defined]
                        # Bybit fetch_tickers returns a list or dict depending on ccxt version.
                        # Handle both.
                        if isinstance(tickers, dict):
                            tickers_list = list(tickers.values())
                        else:
                            tickers_list = tickers

                        for t in tickers_list:
                            sym = t.get("symbol", "")
                            if sym in missing_symbols:
                                last = t.get("last") or t.get("close")
                                if last:
                                    live_prices[sym] = Decimal(str(last))
                except Exception as e:
                    self._log.warning(f"APM: failed to fetch live prices: {e}")

                for pos in positions:
                    symbol = pos.get("symbol", "")
                    if symbol in live_prices:
                        pos["live_price"] = str(live_prices[symbol])
                    await self._manage_single_position(pos)

                if now - last_reconcile > APM_RECONCILE_INTERVAL_S:
                    await self._reconcile_positions()
                    last_reconcile = now

                await asyncio.sleep(APM_MONITOR_INTERVAL_S)

            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("APM: error in monitoring loop")
                await asyncio.sleep(APM_ERROR_BACKOFF_S)

    async def start_health_check_loop(self, interval_s: int = 60) -> None:
        """Scheduled position health check — runs every `interval_s` seconds.

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
                        "HEALTH_CHECK: %s %s missing fields %s — auto-repairing",
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
                        # SL still missing after repair — place emergency SL
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
                                "HEALTH_CHECK: emergency SL FAILED for %s: %s — POSITION UNPROTECTED",
                                symbol,
                                e,
                            )
                            if self._alert:
                                await self._alert.send(  # type: ignore[attr-defined]
                                    f"🚨 HEALTH CHECK: SL missing & placement FAILED for {symbol} {side}. MANUAL INTERVENTION NEEDED."
                                )

                    if changed:
                        repaired += 1

                if repaired:
                    self._log.warning("HEALTH_CHECK: repaired %d positions", repaired)
                    if self._alert:
                        await self._alert.send(  # type: ignore[attr-defined]
                            f"⚠️ APM health check: auto-repaired {repaired} position(s) with missing fields."
                        )

            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("APM: health check loop error")
                await asyncio.sleep(APM_ERROR_BACKOFF_S)

    # ------------------------------------------------------------------
    # Per-position management
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
                self._log.critical(f"APM reconcile: {symbol} is a phantom trade (does not exist on Bybit). Ignoring reconciliation.")
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

                # SL from exchange — validate direction
                exch_sl = exchange_pos.get("stopLoss")
                if exch_sl and str(exch_sl) not in ("0", "None", ""):
                    sl_val = Decimal(str(exch_sl))
                    entry_val = Decimal(str(pos.get("entry_price", 0)))
                    # SL must be below entry for LONG, above for SHORT
                    if entry_val > 0:
                        if side == "LONG" and sl_val >= entry_val:
                            self._log.warning(
                                f"APM reconcile: {symbol} SL {sl_val} >= entry {entry_val} for LONG — skipping"
                            )
                        elif side == "SHORT" and sl_val <= entry_val:
                            self._log.warning(
                                f"APM reconcile: {symbol} SL {sl_val} <= entry {entry_val} for SHORT — skipping"
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

            if initial_risk > 0:
                pos["initial_risk_per_unit"] = str(initial_risk)
                changed = True
                self._log.info(f"APM reconcile: {symbol} initial_risk={initial_risk}")

        # 5. entry_time — use entered_at if missing
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
                    # SL above/below price — wrong direction, compute opposite
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

    async def _manage_single_position(self, pos: dict[str, Any]) -> None:
        """Run all position checks: breakeven, trailing, time, regime."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")

        # Skip if force-close failed recently — retry cooldown (5 min)
        retry_at = pos.get("force_close_retry_at", 0)
        if retry_at and datetime.now(UTC).timestamp() < retry_at:
            return

        # Reconcile ALL missing fields from Bybit + candles
        await self._reconcile_position(pos)

        _raw_entry = pos.get("entry_price", "0") or "0"
        entry_price = Decimal(str(_raw_entry))
        _raw_live = pos.get("live_price", pos.get("entry_price", "0")) or "0"
        live_price = Decimal(str(_raw_live))
        entry_regime = pos.get("entry_regime", "UNKNOWN")
        _raw_sl = pos.get("current_sl", pos.get("stop_loss", "0")) or "0"
        sl_price = Decimal(str(_raw_sl))
        _raw_risk = pos.get("initial_risk_per_unit", "0") or "0"
        initial_risk = Decimal(str(_raw_risk))
        moved_to_be = pos.get("moved_to_breakeven", False)

        # entry_time was recently fixed in position_store to write natively, but fallback to entered_at for existing/old positions
        _raw_time = pos.get("entry_time") or pos.get("entered_at")
        entry_time = _raw_time if _raw_time else None
        max_hold_mins = int(pos.get("max_hold_time_mins", 1440))

        if entry_price <= 0:
            return

        if initial_risk <= 0:
            return

        r_mult = self._calculate_r_multiple(side, entry_price, live_price, initial_risk)

        is_hyper = str(entry_regime).startswith("HYPER")
        wick_long = Decimal("-0.015") if is_hyper else Decimal("-0.03")
        wick_short = Decimal("0.015") if is_hyper else Decimal("0.03")
        be_lock_r = Decimal("0.3") if is_hyper else APM_BREAKEVEN_LOCK_R
        half_be_r = Decimal("0.1") if is_hyper else Decimal("0.5")

        # Flash-Crash Micro-Circuit Breaker (Wick Guard)
        _raw_last_tick = pos.get("last_tick_price", pos.get("live_price", "0")) or "0"
        last_tick_price = Decimal(str(_raw_last_tick))
        if last_tick_price > 0 and live_price > 0:
            tick_delta = (live_price - last_tick_price) / last_tick_price
            # If price moves > wick_threshold against us in a single check, trigger emergency exit
            if (side == "LONG" and tick_delta <= wick_long) or (side == "SHORT" and tick_delta >= wick_short):
                self._log.critical(
                    f"APM WICK GUARD: {symbol} {side} dropped/spiked {tick_delta:.2%} instantly! "
                    f"live={live_price} last={last_tick_price}. FRONT-RUNNING CASCADE!"
                )

                import time
                last_wick_ts = float(pos.get("last_wick_guard_ts", 0))
                if time.time() - last_wick_ts < 10.0:  # 10s debounce
                    pos["last_tick_price"] = str(live_price)  # Still update price reference
                    try:
                        from app.core.position_store import _normalize_side
                        side_key = _normalize_side(side)
                        redis_key = f"karsa:position:{symbol}:{side_key}"
                        await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    return
                pos["last_wick_guard_ts"] = str(time.time())

                if self._alert:
                    asyncio.create_task(self._alert.send(
                        f"🚨 WICK GUARD EMERGENCY EXIT: {symbol} {side} moved {tick_delta:.2%} instantly! Front-running slippage."
                    ))
                # Instead of market-selling into a thin book, tighten SL to 1.5% of current price
                if side == "LONG":
                    tight_sl = live_price * Decimal("0.985")
                else:
                    tight_sl = live_price * Decimal("1.015")
                try:
                    sl_order_id = pos.get("sl_order_id", "")
                    amount = Decimal(str(pos.get("amount", "0")))
                    api_side = "buy" if side == "LONG" else "sell"
                    await self._client.amend_stop_loss(sl_order_id, symbol, api_side, tight_sl, amount)  # type: ignore[attr-defined]
                    pos["current_sl"] = str(tight_sl)
                    pos["stop_loss"] = str(tight_sl)
                    sl_price = tight_sl
                    self._log.warning(f"APM WICK GUARD: tightened SL for {symbol} to {tight_sl}")
                except Exception:
                    self._log.exception(f"APM WICK GUARD: SL tighten failed for {symbol}")

                pos["last_tick_price"] = str(live_price)
                try:
                    from app.core.position_store import _normalize_side
                    side_key = _normalize_side(side)
                    redis_key = f"karsa:position:{symbol}:{side_key}"
                    await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                except Exception:
                    pass
                return

        pos["last_tick_price"] = str(live_price)

        # Track Peak Price for Chandelier Trailing
        peak_price = Decimal(str(pos.get("peak_price", entry_price)))
        peak_updated = False
        if side == "LONG" and live_price > peak_price or side == "SHORT" and live_price < peak_price:
            peak_price = live_price
            peak_updated = True

        if peak_updated:
            pos["peak_price"] = str(peak_price)

        # NOTE: $1 hard cap removed — ATR-based SL (via max_loss_usd) is the correct stop.
        # A $1 cap on a position sized by available_balance * risk_pct conflicts with
        # risk-proportional sizing and causes premature SL hits. See walkthrough_max_loss.md.

        # Exchange-side TP for RANGE/CHOP — place once on first loop
        if not pos.get("tp_placed") and entry_regime in ("RANGE", "CHOP"):
            await self._ensure_take_profit(pos, entry_price, initial_risk, side)

        # Multi-Tier Scale-Outs
        scale_tier = pos.get("scale_tier", 0)

        # Range/Chop/Hyper/Sniper logic
        if entry_regime == "SNIPER":
            # Asymmetric V-Shape Exits
            if scale_tier < 1 and r_mult >= Decimal("1.5"):
                # Exit 50% immediately at +1.5R and move SL to BE
                await self._scale_out_position(pos, Decimal("0.50"), entry_price, side)
                pos["scale_tier"] = 1
                if not moved_to_be:
                    await self._move_stop_to_breakeven(pos, entry_price, side)
                    moved_to_be = True
                    
            # Momentum Exhaustion Check (1m RSI > 80 or < 20)
            if r_mult > Decimal("0.2"):
                exhausted = await self._check_momentum_exhaustion(symbol, side)
                if exhausted:
                    self._log.warning(f"APM SNIPER: Momentum Exhaustion detected for {symbol}. Market closing remainder.")
                    if self._alert:
                        asyncio.create_task(self._alert.send(f"🚨 SNIPER MOMENTUM EXHAUSTION EXIT: {symbol} {side} closed at {live_price} to avoid dead-cat bounce!"))
                    await self._force_close_position(pos, symbol, side)
                    return
                    
        elif "TREND" not in entry_regime:
            if scale_tier < 1 and r_mult >= be_lock_r:
                await self._scale_out_position(pos, Decimal("0.50"), entry_price, side)
                pos["scale_tier"] = 1
        # Trend logic: 33% at 1.5R (Tier 1), 33% at 3.0R (Tier 2)
        elif scale_tier < 1 and r_mult >= Decimal("1.5"):
            await self._scale_out_position(pos, Decimal("0.33"), entry_price, side)
            pos["scale_tier"] = 1
            # Force breakeven lock upon Tier 1 scale-out to secure a free ride
            if not moved_to_be:
                await self._move_stop_to_breakeven(pos, entry_price, side)
                moved_to_be = True
        elif scale_tier < 2 and r_mult >= Decimal("3.0"):
            await self._scale_out_position(pos, Decimal("0.33"), entry_price, side)
            pos["scale_tier"] = 2

        # ATR-based BE trigger: price must move beyond noise threshold
        atr = Decimal(str(pos.get("atr", "0")))
        if atr > 0:
            price_move = abs(live_price - entry_price)
            be_triggered = price_move >= atr * APM_BREAKEVEN_ATR_MULT
        else:
            # Fallback to fixed 1R when ATR unavailable
            be_triggered = r_mult >= be_lock_r

        if not moved_to_be and be_triggered:
            await self._move_stop_to_breakeven(pos, entry_price, side)
            pos["moved_to_breakeven"] = True
            moved_to_be = True

        # ─── HALF-BREAKEVEN ─────────────────────────────────────────────
        moved_to_half_be = pos.get("moved_to_half_be", False)
        if not moved_to_be and not moved_to_half_be and r_mult >= half_be_r:
            half_trail_r = Decimal("-0.5")  # Move SL to -0.5R
            if side == "LONG":
                new_step_sl = entry_price + (initial_risk * half_trail_r)
            else:
                new_step_sl = entry_price - (initial_risk * half_trail_r)

            sl_tighter = (side == "LONG" and new_step_sl > sl_price) or (side == "SHORT" and new_step_sl < sl_price)
            if sl_tighter:
                try:
                    amount = Decimal(str(pos.get("amount", "0")))
                    api_side = "buy" if side == "LONG" else "sell"
                    sl_order_id = pos.get("sl_order_id", "")
                    await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_step_sl, amount)  # type: ignore[attr-defined]
                    pos["current_sl"] = str(new_step_sl)
                    pos["stop_loss"] = str(new_step_sl)
                    sl_price = new_step_sl
                    pos["moved_to_half_be"] = True
                    self._log.info(f"APM: Half-Breakeven locked for {symbol} to {new_step_sl}")
                except Exception as e:
                    self._log.debug(f"APM: Half-Breakeven amend failed for {symbol}: {e}")
        # ────────────────────────────────────────────────────────────────────────

        # ─── AGGRESSIVE PROFIT-PROTECT TRAILING ─────────────────────────────────
        # User request: "secure when position already in profit and then goes down then immedietly close profit"
        peak_r_mult = self._calculate_r_multiple(side, entry_price, peak_price, initial_risk)

        # Dynamic activation threshold based on regime
        is_hyper = "HYPER" in entry_regime
        activation_threshold = Decimal("0.5") if is_hyper else Decimal("1.5")

        if initial_risk > 0 and peak_r_mult >= activation_threshold:
            if is_hyper:
                # HYPER: Ultra-tight trail. We lock in peak - 0.2R.
                # If peak is 1.0R, we lock in 0.8R.
                trail_r = peak_r_mult - Decimal("0.2")
                # HYPER absolute floor is 0.3R
                trail_r = max(trail_r, Decimal("0.3"))
            elif peak_r_mult >= Decimal("2.5"):
                trail_r = peak_r_mult - Decimal("0.3")
            else:
                trail_r = peak_r_mult - Decimal("0.5")

            # Ensure we lock in at least 0.5R for non-hyper
            if not is_hyper:
                trail_r = max(trail_r, Decimal("0.5"))

            if side == "LONG":
                new_step_sl = entry_price + (initial_risk * trail_r)
            else:
                new_step_sl = entry_price - (initial_risk * trail_r)

            # Only amend if the new SL is tighter (more protective)
            sl_tighter = (side == "LONG" and new_step_sl > sl_price) or (side == "SHORT" and new_step_sl < sl_price)

            # Anti-spam: Only amend if the new SL is at least 0.15R better than current SL
            current_sl_r = self._calculate_r_multiple(side, entry_price, sl_price, initial_risk)
            significant_move = (trail_r - current_sl_r) >= Decimal("0.15")

            if sl_tighter and significant_move:
                try:
                    api_side = "buy" if side == "LONG" else "sell"
                    sl_order_id = pos.get("sl_order_id", "")
                    await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_step_sl, amount)  # type: ignore[attr-defined]
                    pos["current_sl"] = str(new_step_sl)
                    pos["stop_loss"] = str(new_step_sl)
                    sl_price = new_step_sl
                    self._log.info(f"APM: Profit-Protect SL amended for {symbol} to {new_step_sl} (Peak R: {peak_r_mult:.2f}, Locked: +{trail_r:.2f}R)")
                except Exception as e:
                    self._log.debug(f"APM: Profit-Protect amend failed for {symbol}: {e}")
        # ────────────────────────────────────────────────────────────────────────

        if "TREND" in entry_regime:
            await self._manage_trend_trailing_stop(pos, live_price, r_mult, side, sl_price)

        if entry_time is not None:
            closed = await self._manage_time_exit(pos, entry_time, max_hold_mins, live_price, entry_price, side, r_mult, entry_regime)
            if closed:
                return

        closed = await self._check_regime_shift(pos, symbol, entry_regime)
        if closed:
            return

        pos["last_check_at"] = datetime.now(UTC).isoformat()
        try:
            from app.core.position_store import _normalize_side

            side_key = _normalize_side(side)
            redis_key = f"karsa:position:{symbol}:{side_key}"
            await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
        except Exception:
            pass

    # ------------------------------------------------------------------
    # SNIPER Helpers
    # ------------------------------------------------------------------
    
    async def _check_momentum_exhaustion(self, symbol: str, side: str) -> bool:
        """Check if 1-minute RSI signals a V-shape top/bottom exhaustion."""
        try:
            bybit_symbol = symbol.replace("/", "")
            if hasattr(self._client, "session") and self._client.session:
                raw = await self._client._execute(
                    self._client.session.get_kline,
                    category="linear",
                    symbol=bybit_symbol,
                    interval="1",
                    limit=20
                )
                if raw and raw.get("retCode") == 0 and raw.get("result"):
                    # Bybit returns newest first, reverse it
                    kline_list = raw["result"].get("list", [])
                    if len(kline_list) >= 14:
                        kline_list.reverse()
                        closes = [float(k[4]) for k in kline_list]
                        
                        # Calculate simple RSI
                        import numpy as np
                        deltas = np.diff(closes)
                        seed = deltas[:14]
                        up = seed[seed >= 0].sum() / 14
                        down = -seed[seed < 0].sum() / 14
                        rs = up / down if down != 0 else 0
                        rsi = np.zeros_like(closes)
                        rsi[:14] = 100. - 100. / (1. + rs)
                        for i in range(14, len(closes)):
                            delta = deltas[i - 1]
                            upval = delta if delta > 0 else 0.
                            downval = -delta if delta < 0 else 0.
                            up = (up * 13 + upval) / 14
                            down = (down * 13 + downval) / 14
                            rs = up / down if down != 0 else 0
                            rsi[i] = 100. - 100. / (1. + rs)
                            
                        current_rsi = rsi[-1]
                        
                        if side == "LONG" and current_rsi > 80.0:
                            return True
                        elif side == "SHORT" and current_rsi < 20.0:
                            return True
        except Exception as e:
            self._log.debug(f"APM SNIPER: momentum exhaustion check failed for {symbol}: {e}")
        return False

    # ------------------------------------------------------------------
    # R-multiple calculation
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

    @staticmethod
    def _calculate_r_multiple(side: str, entry_price: Decimal, live_price: Decimal, initial_risk: Decimal) -> Decimal:
        """Calculate R-multiple. Zero-division guard."""
        try:
            if initial_risk <= 0:
                return Decimal("0")
            if side == "LONG":
                return (live_price - entry_price) / initial_risk
            else:
                return (entry_price - live_price) / initial_risk
        except (DivisionByZero, InvalidOperation):
            return Decimal("0")

    # ------------------------------------------------------------------
    # Exchange-side Take Profit
    # ------------------------------------------------------------------

    async def _ensure_take_profit(
        self,
        pos: dict[str, Any],
        entry_price: Decimal,
        initial_risk: Decimal,
        side: str,
    ) -> None:
        """Place exchange-side TP once for RANGE/CHOP regimes via atomic set_trading_stop."""
        symbol = pos.get("symbol", "")
        api_side = "buy" if side == "LONG" else "sell"
        # TP multiplier: RANGE=1.5:1, CHOP=2.0:1 to cover fees (0.11% round-trip) + slippage
        entry_regime_inner = pos.get("entry_regime", "RANGE")
        if "CHOP" in entry_regime_inner:
            tp_mult = Decimal("2.0")
        else:
            tp_mult = Decimal("1.5")  # RANGE default

        try:
            if side == "LONG":
                tp_price = entry_price + (initial_risk * tp_mult)
            else:
                tp_price = entry_price - (initial_risk * tp_mult)

            await self._client.set_trading_stop(symbol, api_side, take_profit=tp_price)  # type: ignore[attr-defined]
            pos["tp_placed"] = True
            self._log.info(f"APM: atomic TP placed for {symbol} @ {tp_price} ({tp_mult}:1 R/R)")
        except Exception:
            self._log.exception(f"APM: TP placement failed for {symbol}")

    # ------------------------------------------------------------------
    # Scale-out (partial close)
    # ------------------------------------------------------------------

    async def _scale_out_position(self, pos: dict[str, Any], pct: Decimal, entry_price: Decimal, side: str) -> None:
        """Partial close to lock profit. RANGE/CHOP: 50% at +1R. TREND: 30% at +2R."""
        symbol = pos.get("symbol", "")
        amount = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"
        try:
            close_qty = (amount * pct).quantize(Decimal("0.001"))
            if close_qty <= 0:
                return
            await self._client.reduce_position(symbol, api_side, close_qty)  # type: ignore[attr-defined]
            pos["scaled_out"] = True
            # Update amount in pos dict so subsequent calculations use reduced quantity
            new_amount = amount - close_qty
            pos["amount"] = str(new_amount)
        except Exception:
            self._log.exception(f"APM: scale-out failed for {symbol}")

    async def proactive_scale_out(
        self, symbol: str, side: str = "LONG", ratio: Decimal = Decimal("0.50")
    ) -> bool:
        """Execute proactive 50% scale-out for dynamic capital reallocation.

        Guarded by idempotency flag `proactive_scale_out_executed`.
        """
        pos = await self._store.get(symbol, side)  # type: ignore[attr-defined]
        if not pos:
            return False

        if pos.get("proactive_scale_out_executed") or str(pos.get("proactive_scale_out_executed")).lower() == "true":
            self._log.info(f"APM proactive_scale_out: {symbol} {side} already scaled out (idempotency guard). Skipping.")
            return False

        entry_price = Decimal(str(pos.get("entry_price", "0")))
        if entry_price <= 0:
            return False

        self._log.warning(
            f"APM CAPITAL REALLOCATION: Proactively scaling out {ratio * 100}% of {symbol} {side} to reallocate capital."
        )
        await self._scale_out_position(pos, ratio, entry_price, side)
        await self._move_stop_to_breakeven(pos, entry_price, side)

        pos["proactive_scale_out_executed"] = True
        try:
            from app.core.position_store import _normalize_side
            side_key = _normalize_side(side)
            redis_key = f"karsa:position:{symbol}:{side_key}"
            await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
        except Exception as e:
            self._log.error(f"APM proactive_scale_out state save failed for {symbol}: {e}")

        if self._alert:
            asyncio.create_task(
                self._alert.send(  # type: ignore[attr-defined]
                    f"🔄 CAPITAL REALLOCATION: Proactively scaled out {ratio * 100}% of {symbol} {side}. SL set to Breakeven."
                )
            )
        return True

    # ------------------------------------------------------------------
    # Breakeven
    # ------------------------------------------------------------------

    async def _move_stop_to_breakeven(self, pos: dict[str, Any], entry_price: Decimal, side: str) -> None:
        """Move SL to entry ± fee buffer. Exchange-side amend with retry."""
        symbol = pos.get("symbol", "")
        sl_order_id = pos.get("sl_order_id", "")
        amount = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"
        try:
            if side == "LONG":
                # LONG: SL moves to just above entry to cover fees (price must rise to profit)
                new_sl = entry_price + entry_price * APM_BREAKEVEN_FEE_PCT
            else:
                # SHORT: SL must be ABOVE entry (stop out if price rises back through entry)
                # Entry at 100 → SL at 100.25 (fee buffer above entry)
                new_sl = entry_price + entry_price * APM_BREAKEVEN_FEE_PCT

            new_sl_str = str(new_sl)
            try:
                await self._client.amend_stop_loss(
                    sl_order_id, symbol, api_side, new_sl, amount
                )  # type: ignore[attr-defined]
            except Exception:
                self._log.warning(f"APM: breakeven amend failed for {symbol}, retrying")
                await self._client.amend_stop_loss(
                    sl_order_id, symbol, api_side, new_sl, amount
                )  # type: ignore[attr-defined]

            # BUG-4 fix: persist breakeven flag + new SL price to Redis so this
            # does not re-trigger on every 2s cycle.
            pos["moved_to_breakeven"] = True
            pos["current_sl"] = new_sl_str
            pos["stop_loss"] = new_sl_str
            try:
                from app.core.position_store import _normalize_side

                side_key = _normalize_side(side)
                redis_key = f"karsa:position:{pos.get('symbol', '')}:{side_key}"
                await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
            except Exception:
                self._log.exception(f"APM: failed to persist breakeven flag for {symbol}")

            await self._store.update_sl(symbol, api_side, sl_order_id, new_sl)  # type: ignore[attr-defined]
            self._log.info(f"APM: breakeven locked for {symbol} at {new_sl_str}")

        except Exception:
            self._log.exception(f"APM: breakeven CRITICAL failure for {symbol}")
            if self._alert:
                await self._alert.send(f"⚠️ APM breakeven FAILED for {symbol}")  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Trend trailing stop
    # ------------------------------------------------------------------

    async def _manage_trend_trailing_stop(
        self,
        pos: dict[str, Any],
        live_price: Decimal,
        r_multiple: Decimal,
        side: str,
        current_sl: Decimal,
    ) -> None:
        """3x ATR Chandelier trailing — only amend if more protective."""
        if r_multiple < APM_TREND_TRAIL_ACTIVATE_R:
            return

        symbol = pos.get("symbol", "")
        atr = Decimal(str(pos.get("atr", "0")))
        if atr <= 0:
            return

        trail_distance = atr * APM_TREND_TRAIL_ATR_MULT

        # True Chandelier: trail from the peak price reached, not current live price
        peak = Decimal(str(pos.get("peak_price", live_price)))

        if side == "LONG":
            new_sl = peak - trail_distance
            if new_sl <= current_sl:
                return
        else:
            new_sl = peak + trail_distance
            if new_sl >= current_sl:
                return

        try:
            sl_order_id = pos.get("sl_order_id", "")
            amount = Decimal(str(pos.get("amount", "0")))
            api_side = "buy" if side == "LONG" else "sell"
            await self._client.amend_stop_loss(
                sl_order_id, symbol, api_side, new_sl, amount
            )  # type: ignore[attr-defined]
            # Update local pos dict with new SL so we don't spam requests
            new_sl_str = str(new_sl)
            pos["current_sl"] = new_sl_str
            pos["stop_loss"] = new_sl_str
            self._log.info(f"APM: trailing SL amended for {symbol} to {new_sl} (Peak={peak})")
        except Exception:
            self._log.exception(f"APM: trailing SL amend failed for {symbol}")

    # ------------------------------------------------------------------
    # Time exit
    # ------------------------------------------------------------------

    async def _manage_time_exit(
        self,
        pos: dict[str, Any],
        entry_time: object,
        max_minutes: int,
        live_price: Decimal,
        entry_price: Decimal,
        side: str,
        r_mult: Decimal,
        entry_regime: str,
    ) -> bool:
        """Force close if position held beyond max_hold_time_mins or if it is stale underwater.

        BUG-6 fix: entry_time from Redis is always an ISO string, not a datetime.
        Parse it here before the isinstance guard.
        Returns True if the position was closed, False otherwise.
        """
        # Parse string to timezone-aware datetime if needed
        if isinstance(entry_time, str):
            try:
                entry_time = datetime.fromisoformat(entry_time)
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=UTC)
            except Exception:
                self._log.debug("APM: could not parse entry_time=%r for time-exit", entry_time)
                return False

        if not isinstance(entry_time, datetime):
            return False

        now = datetime.now(UTC)
        held_mins = (now - entry_time).total_seconds() / 60.0

        # Volatility-Adjusted Time Stop (Dynamic Theta Decay)
        atr = float(pos.get("atr", 0))
        entry_price_f = float(entry_price)
        if atr > 0 and entry_price_f > 0:
            current_atr_pct = (atr / entry_price_f) * 100.0
            baseline_atr_pct = 2.0  # 2.0% baseline ATR reference
            vol_ratio = baseline_atr_pct / current_atr_pct
            vol_ratio = max(0.5, min(2.0, vol_ratio))
            max_minutes = int(max_minutes * vol_ratio)

        if held_mins > max_minutes:
            symbol = pos.get("symbol", "")
            self._log.warning(f"APM: volatility-adjusted time exit {symbol} after {held_mins:.0f}min (adj_max {max_minutes}m)")
            await self._force_close_position(pos, f"time_exit_{held_mins:.0f}min")
            return True

        is_hyper = str(entry_regime).startswith("HYPER")
        quick_profit_mins = 3 if is_hyper else 5
        quick_profit_r = Decimal("1.0") if is_hyper else Decimal("3.0")

        # Quick Profit Exit
        if held_mins <= quick_profit_mins and r_mult >= quick_profit_r:
            symbol = pos.get("symbol", "")
            self._log.warning(f"APM: QUICK PROFIT exit {symbol} after {held_mins:.0f}min (R={r_mult:.2f})")
            await self._force_close_position(pos, f"quick_profit_exit_R{r_mult:.1f}")
            return True

        # Stagnation Exit (Smart quick-win / cut loss)
        if is_hyper:
            stagnation_mins = 10     # Give HYPER more room (was 5)
            stagnation_r = Decimal("0.15")  # Only exit if barely moved (was 0.5)
        elif "TREND" in entry_regime:
            stagnation_mins = 60
            stagnation_r = Decimal("0.3")   # Trend needs time
        else:
            stagnation_mins = 30 if entry_regime == "RANGE" else 10
            stagnation_r = Decimal("0.5")   # RANGE/default: keep original

        # If held for a long time and R is still less than stagnation_r, take the quick win (if positive) or cut the small loss
        if held_mins >= stagnation_mins and r_mult < stagnation_r:
            symbol = pos.get("symbol", "")
            self._log.warning(f"APM: STAGNATION exit {symbol} after {held_mins:.0f}min (R={r_mult:.2f})")
            await self._force_close_position(pos, f"stagnation_exit_{held_mins:.0f}min")
            return True

        # Underwater Stale Exit
        underwater_mins = 15 if is_hyper else (120 if "TREND" in entry_regime else 60 if entry_regime == "RANGE" else 15)
        if held_mins >= underwater_mins:
            is_underwater = (side == "LONG" and live_price <= entry_price) or (side == "SHORT" and live_price >= entry_price)
            if is_underwater:
                symbol = pos.get("symbol", "")
                self._log.warning(f"APM: stale underwater exit {symbol} after {held_mins:.0f}min")
                await self._force_close_position(pos, f"stale_exit_{held_mins:.0f}min")
                return True
        return False

    # ------------------------------------------------------------------
    # Regime shift kill switch (with hysteresis)
    # ------------------------------------------------------------------

    async def _check_regime_shift(self, pos: dict[str, Any], symbol: str, entry_regime: str) -> bool:
        """Kill switch: force close if regime shifted N consecutive checks.
        Returns True if the position was closed, False otherwise.
        """
        try:
            current_regime = await self._regime.get_current_regime(symbol)  # type: ignore[attr-defined]
            current_value = current_regime.value if hasattr(current_regime, "value") else str(current_regime)

            if current_value != entry_regime:
                self._regime_shift_counts[symbol] = self._regime_shift_counts.get(symbol, 0) + 1
                if self._regime_shift_counts[symbol] >= REGIME_SHIFT_CONFIRM_COUNT:
                    self._log.warning(
                        f"APM: regime shift kill switch {symbol} — "
                        f"{entry_regime} → {current_value} ({self._regime_shift_counts[symbol]} checks)"
                    )
                    await self._force_close_position(pos, f"regime_shift_{entry_regime}_to_{current_value}")
                    self._regime_shift_counts.pop(symbol, None)
                    return True
            else:
                self._regime_shift_counts.pop(symbol, None)

        except Exception:
            self._log.exception(f"APM: regime check failed for {symbol}")
        return False

    # ------------------------------------------------------------------
    # Force close
    # ------------------------------------------------------------------

    async def _force_close_position(self, pos: dict[str, Any], reason: str) -> None:
        """Cancel all orders → market close → update state → alert."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")
        qty = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"

        exchange_closed = False
        try:
            # Cancel all open orders for this symbol (SL/TP/limit)
            orders = await self._client.fetch_open_orders()  # type: ignore[attr-defined]
            for order in orders:
                if order.get("symbol") == symbol:
                    await self._client.cancel_order(order["id"], symbol)  # type: ignore[attr-defined]
            # Market close with reduceOnly — capture fill price from response
            fill_price = Decimal("0")
            if qty > 0:
                close_side = "SELL" if side == "LONG" else "BUY"
                close_result = await self._client.create_market_order(
                    symbol, close_side, qty, {"reduceOnly": True}
                )  # type: ignore[attr-defined]
                # Extract fill price from Bybit response
                fill_price = Decimal(str(close_result.get("avgPrice", close_result.get("price", "0"))))

                # VERIFICATION: Ensure the position actually closed on Bybit
                import asyncio
                await asyncio.sleep(1.0)  # Wait for execution
                exchange_positions = await self._client.fetch_positions()  # type: ignore[attr-defined]
                still_open = False
                for p in exchange_positions:
                    if p["symbol"] == symbol and p["side"] == api_side and float(p.get("contracts", 0)) > 0:
                        still_open = True
                        break

                if still_open:
                    raise RuntimeError("Bybit accepted the order but position is still open (Price Protection or partial fill).")

            exchange_closed = True

        except Exception as e:
            err_str = str(e)
            if "110017" in err_str or "position is zero" in err_str:
                exchange_closed = True
                fill_price = Decimal("0")
                self._log.warning(f"APM: {symbol} already closed on exchange (handled in phase 1)")
            else:
                self._log.exception(f"APM: CRITICAL force close failed for {symbol}")
                # Set 5-min retry cooldown to prevent 2s spam-loop (same fail every cycle)
                pos["force_close_retry_at"] = datetime.now(UTC).timestamp() + 300
                try:
                    from app.core.position_store import _normalize_side
                    side_key = _normalize_side(side)
                    redis_key = f"karsa:position:{symbol}:{side_key}"
                    await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                except Exception:
                    pass
                if self._alert:
                    await self._alert.send(
                        f"🚨 APM FORCE CLOSE FAILED {symbol} — retry in 5min, MANUAL INTERVENTION NEEDED"
                    )  # type: ignore[attr-defined]
                return

        if exchange_closed:
            try:
                # Store exit price in Redis BEFORE removing so exit loop can read it
                if fill_price > 0:
                    pos["exit_price"] = str(fill_price)
                    pos["exit_reason"] = reason
                    pos["closed_at"] = datetime.now(UTC).isoformat()
                    try:
                        from app.core.position_store import _normalize_side
                        side_key = _normalize_side(side)
                        redis_key = f"karsa:position:{symbol}:{side_key}"
                        await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                    except Exception:
                        pass

                # Remove from local state (side needed for Redis key)
                try:
                    await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]
                except Exception as e:
                    self._log.warning(f"APM: failed to remove {symbol} from store: {e}")

                self._log.warning(f"APM: force closed {symbol} — {reason}")
                if self._alert:
                    await self._alert.send(f"🔴 APM force closed {symbol}: {reason}")  # type: ignore[attr-defined]

                # Record trade in memory for cooldown / AI context
                entry_price = Decimal(str(pos.get("entry_price", "0")))
                if self._trade_memory and fill_price > 0 and entry_price > 0:
                    try:
                        pnl = (
                            (fill_price - entry_price) * qty
                            if side == "LONG"
                            else (entry_price - fill_price) * qty
                        )
                        pnl_pct = (
                            pnl / (entry_price * qty) * 100
                            if entry_price * qty > 0
                            else Decimal("0")
                        )
                        hold_min = 0
                        entry_time_str = pos.get("entry_time", pos.get("entered_at", ""))
                        if entry_time_str:
                            try:
                                et = datetime.fromisoformat(entry_time_str)
                                if et.tzinfo is None:
                                    et = et.replace(tzinfo=UTC)
                                hold_min = int((datetime.now(UTC) - et).total_seconds() / 60)
                            except Exception:
                                pass
                        await self._trade_memory.store(
                            symbol=symbol,
                            pnl_pct=pnl_pct,
                            hold_duration_min=hold_min,
                            regime=pos.get("entry_regime", "UNKNOWN"),
                            exit_reason=reason,
                            entry_confidence=Decimal(str(pos.get("entry_confidence", "0"))),
                        )
                        self._log.info(f"APM: trade_memory stored {symbol} pnl={pnl_pct:.2f}% reason={reason}")
                    except Exception as e:
                        self._log.warning(f"APM: trade_memory store failed for {symbol}: {e}")

            except Exception as e:
                self._log.error(f"APM: post-close cleanup failed for {symbol}: {e}")
                # Ensure it's removed from local state to prevent orphan loop
                try:
                    await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    async def _reconcile_positions(self) -> None:
        """Compare internal state vs Bybit — fix ghost positions.

        Also verifies SL order still exists on exchange; re-places if missing.
        """
        try:
            internal = await self._store.list_all()  # type: ignore[attr-defined]
            external = await self._client.fetch_positions()  # type: ignore[attr-defined]
            external_symbols = {p.get("symbol", "").replace("/", "") for p in external}

            for pos in internal:
                symbol = pos.get("symbol", "")
                if symbol.replace("/", "") not in external_symbols:
                    self._log.warning(f"APM: ghost position detected — {symbol} not on Bybit, removing")
                    raw_side = pos.get("side", "buy")
                    api_side = "buy" if raw_side in ("buy", "LONG") else "sell"
                    await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]
                    continue

                # Verify SL is attached to the position — ONLY re-place if missing
                # NEVER overwrite an existing SL (breakeven/trailing would be lost)
                entry_price = Decimal(str(pos.get("entry_price", "0")))
                raw_side = pos.get("side", "buy")
                api_side = "buy" if raw_side in ("buy", "LONG") else "sell"
                current_sl = Decimal(str(pos.get("current_sl", pos.get("stop_loss", "0")) or "0"))
                if entry_price > 0 and current_sl <= 0:
                    # SL is missing — re-place it from initial_risk_per_unit
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
                        self._log.info(f"APM: SL missing — re-placed for {symbol} at {sl_price}")
                    except Exception:
                        self._log.warning(f"APM: SL reconciliation failed for {symbol}")
                elif current_sl > 0:
                    self._log.debug(f"APM: SL exists for {symbol} at {current_sl} — skipping reconciliation re-place")

        except Exception:
            self._log.exception("APM: reconciliation failed")
            self._log.exception("APM: reconciliation failed")
