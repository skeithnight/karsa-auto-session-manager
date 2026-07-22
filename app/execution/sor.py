"""Smart Order Routing — Post-Only → Reprice → Market fallback.

Regime-aware routing (Phase 12):
  CHOP/RANGE: force Post-Only (maker fee, no aggressive fills)
  TREND: allow Market fallback on reprice failure
  Spread gate: reject entries when bid-ask spread > threshold
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from loguru import logger

from app.core import metrics
from app.execution.bybit_client import BybitClient

# Regime-aware routing constants
CHOP_RANGE_MAX_REPRICE = 1  # fewer reprices for CHOP/RANGE — reject faster
CHOP_RANGE_SPREAD_PCT = Decimal("0.002")  # 0.2% max spread for CHOP/RANGE
TREND_SPREAD_PCT = Decimal("0.005")  # 0.5% max spread for TREND


class SmartOrderRouter:
    """3-step order routing: Post-Only → Reprice → Market/IOC.

    Regime-aware: enforces Post-Only + tighter spread gate for CHOP/RANGE.
    """

    def __init__(
        self,
        bybit_client: BybitClient,
        max_reprice_attempts: int = 2,
        reprice_delay_seconds: float = 2.0,
        alert_service: object | None = None,
        redis_client: RedisClient | None = None,
    ) -> None:
        logger.debug("SmartOrderRouter.__init__: entering")
        self.client = bybit_client
        self.max_reprice_attempts = max_reprice_attempts
        self.reprice_delay_seconds = reprice_delay_seconds
        self.skip_to_market = False
        self.alert_service = alert_service
        self.redis = redis_client
        logger.debug("SmartOrderRouter.__init__: returning")

    async def execute(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        price_tick: Decimal = Decimal("0.01"),
        max_loss_usd: Decimal = Decimal("1.00"),
    ) -> dict[str, Any] | None:
        """Execute order with 3-step fallback + exchange-side SL on fill.
        Callers: executor_task in main.py.
        API change: sl_distance_pct replaced with max_loss_usd (absolute USD loss cap).
        SL price = fill_price - (max_loss_usd / amount) for LONG, + for SHORT.
        1. Post-Only Limit
        2. Reprice (up to max_reprice_attempts)
        3. Market/IOC fallback
        4. Place exchange-side Stop-Loss immediately on fill (CLAUDE.md Rule 5)
        """
        # Normalize side: accept "LONG"/"SHORT" from signal, convert to "buy"/"sell"
        side = "buy" if side in ("buy", "LONG") else "sell"
        logger.debug(f"execute: entering symbol={symbol} side={side}")
        order: dict[str, Any] | None = None

        # Reject invalid price
        if price <= 0:
            logger.warning(f"SOR: invalid price {price} for {symbol}, skipping")
            return None

        # Iceberg / TWAP Order Slicing
        notional = price * amount
        if notional > Decimal("2000"):
            logger.info(
                f"SOR Iceberg Mode: {symbol} notional > $2000, slicing into 4 chunks"
            )
            import random

            chunk_amount = (amount / Decimal("4")).quantize(Decimal("0.001"))
            if chunk_amount > 0:
                filled_amount = Decimal("0")
                last_order = None
                for i in range(4):
                    current_amount = (
                        chunk_amount if i < 3 else (amount - (chunk_amount * 3))
                    )
                    if current_amount <= 0:
                        continue
                    if i > 0:
                        await asyncio.sleep(random.uniform(1.5, 3.5))
                    try:
                        last_order = await self.client.create_market_order(
                            symbol, side, current_amount
                        )
                        if last_order:
                            filled_amount += current_amount
                    except Exception as e:
                        logger.error(f"SOR Iceberg chunk {i} failed: {e}")
                        # Place SL on what we have so far — don't leave position unprotected
                        if filled_amount > 0 and last_order:
                            break

                if last_order and filled_amount > 0:
                    metrics.orders_placed.labels(symbol=symbol, side=side).inc()
                    # SL on actual filled amount, not the theoretical full amount
                    sl_id = await self._place_sl_after_fill(
                        symbol, side, price, filled_amount, max_loss_usd, price_tick
                    )
                    last_order["sl_order_id"] = sl_id or ""
                    last_order["amount"] = str(
                        filled_amount
                    )  # Return actual filled for downstream
                    return last_order
                return None

        # High latency mode — skip to market directly
        if self.skip_to_market:
            logger.info(f"SOR: latency mode — market order {side} {amount}")
            try:
                market_order = await self.client.create_market_order(
                    symbol, side, amount
                )
                sl_id = await self._place_sl_after_fill(
                    symbol, side, price, amount, max_loss_usd, price_tick
                )
                market_order["sl_order_id"] = sl_id
                return market_order
            except Exception as e:
                logger.error(f"SOR market fallback failed: {e}")
                return None

        order = None
        # Step 1: Post-Only Limit
        logger.info(f"SOR Step 1: Post-Only Limit {side} {amount} @ {price}")
        metrics.sor_step_total.labels(symbol=symbol, step="post_only").inc()
        try:
            order = await self.client.create_limit_order(symbol, side, amount, price)
            if order.get("status") in ("open", "closed"):
                logger.info(f"Post-Only filled: {order['orderId']}")
                metrics.orders_placed.labels(symbol=symbol, side=side).inc()
                fill_price = Decimal(
                    str(order.get("average", order.get("avgPrice", price)))
                )
                slippage_bps = abs(fill_price - price) / price * Decimal("10000")
                metrics.execution_slippage_bps.labels(symbol=symbol).observe(
                    float(slippage_bps)
                )
                sl_id = await self._place_sl_after_fill(
                    symbol, side, price, amount, max_loss_usd, price_tick
                )
                order["sl_order_id"] = (
                    sl_id or ""
                )  # atomic SL has no order ID; normalize to ""
                logger.debug("execute: returning dict (Post-Only filled)")
                return order
            elif order.get("status") == "rejected":
                logger.warning(f"Post-Only rejected: {order.get('rejectReason', 'unknown')}")
        except Exception as e:
            logger.warning(f"Post-Only failed: {e}")
            # If the order actually filled but we crashed during SL placement,
            # we MUST NOT proceed to step 2. We must return the filled order.
            if order and order.get("status") in ("open", "closed"):
                logger.error(f"CRITICAL: Post-Only filled but crashed after: {e}. Returning order to avoid duplicates.")
                return order

        # Step 2: Reprice attempts
        current_price = price
        # Derive tick from price (0.02% of price, min 0.01) — prevents negative prices on low-value tokens
        effective_tick = max(price * Decimal("0.0002"), Decimal("0.01"))

        # Adaptive Maker-Fee Routing: Determine dynamic reprice attempts
        max_reprices = self.max_reprice_attempts
        try:
            tickers = await self.client.fetch_tickers()
            if isinstance(tickers, dict):
                ticker = tickers.get(symbol) or tickers.get(symbol.replace("/", ""))
                if ticker:
                    bid_vol = Decimal(str(ticker.get("bidVolume", "0") or "0"))
                    ask_vol = Decimal(str(ticker.get("askVolume", "0") or "0"))
                    # If orderbook imbalance heavily favors our side (strong support),
                    # we have time to wait for a maker fill rather than crossing the spread immediately.
                    if side == "buy" and bid_vol > 0 and ask_vol > 0 and bid_vol >= ask_vol * Decimal("2.0"):
                        max_reprices = max(max_reprices, 4)
                        logger.info(f"SOR: Strong bid support detected for {symbol}, extending reprice attempts to {max_reprices} to secure maker fee.")
                    elif side == "sell" and ask_vol > 0 and bid_vol > 0 and ask_vol >= bid_vol * Decimal("2.0"):
                        max_reprices = max(max_reprices, 4)
                        logger.info(f"SOR: Strong ask resistance detected for {symbol}, extending reprice attempts to {max_reprices} to secure maker fee.")
        except Exception as e:
            logger.debug(f"SOR adaptive routing check failed: {e}")

        for attempt in range(max_reprices):
            delay = self.reprice_delay_seconds
            try:
                # Spread-adaptive repricing: fast delay if spread is wide
                if self.redis:
                    state = await self.redis.get_global_state(symbol)
                    if state and state.get("best_bid") and state.get("best_ask"):
                        bid = Decimal(str(state["best_bid"]))
                        ask = Decimal(str(state["best_ask"]))
                        if bid > 0 and ask > 0:
                            spread = (ask - bid) / bid
                            if spread > Decimal("0.002"):
                                delay = 0.1
                else:
                    # Fallback to ccxt fetch_tickers if redis missing
                    tickers = await self.client.fetch_tickers()
                    if isinstance(tickers, dict):
                        ticker = tickers.get(symbol) or tickers.get(symbol.replace("/", ""))
                        if ticker:
                            bid = Decimal(str(ticker.get("bid", "0") or "0"))
                            ask = Decimal(str(ticker.get("ask", "0") or "0"))
                            if bid > 0 and ask > 0:
                                spread = (ask - bid) / bid
                                if spread > Decimal("0.002"):
                                    delay = 0.1
            except Exception as e:
                logger.debug(f"Spread check failed: {e}")
            await asyncio.sleep(delay)

            # Move price toward market (buy: higher, sell: lower)
            if side == "buy":
                current_price += effective_tick
            else:
                current_price -= effective_tick
                # Guard: never go negative or zero
                if current_price <= 0:
                    logger.warning(
                        f"Reprice would go negative ({current_price}), falling back to market"
                    )
                    break

            logger.info(f"SOR Step 2: Reprice attempt {attempt + 1} @ {current_price}")
            metrics.sor_step_total.labels(symbol=symbol, step="reprice").inc()
            try:
                # Cancel unfilled order if exists
                if order and order.get("id"):
                    await self.client.cancel_order(order["id"], symbol)

                order = await self.client.create_limit_order(
                    symbol, side, amount, current_price
                )
                if order.get("status") in ("open", "closed"):
                    logger.info(f"Reprice filled: {order['orderId']}")
                    fill_price = Decimal(
                        str(order.get("average", order.get("avgPrice", current_price)))
                    )
                    slippage_bps = abs(fill_price - price) / price * Decimal("10000")
                    metrics.execution_slippage_bps.labels(symbol=symbol).observe(
                        float(slippage_bps)
                    )
                    sl_id = await self._place_sl_after_fill(
                        symbol, side, current_price, amount, max_loss_usd, price_tick
                    )
                    order["sl_order_id"] = sl_id or ""
                    logger.debug("execute: returning dict (Reprice filled)")
                    return order
                elif order.get("status") == "rejected":
                    logger.warning(f"Reprice rejected: {order.get('rejectReason', 'unknown')}")
            except Exception as e:
                logger.warning(f"Reprice failed: {e}")
                if order and order.get("status") in ("open", "closed"):
                    logger.error(f"CRITICAL: Reprice filled but crashed after: {e}. Returning order to avoid duplicates.")
                    return order

        # Step 3: Market/IOC fallback
        logger.info("SOR Step 3: Market/IOC fallback")
        metrics.sor_step_total.labels(symbol=symbol, step="market").inc()
        try:
            # Hard Slippage Limit Check
            try:
                tickers = await self.client.fetch_tickers()
                if isinstance(tickers, dict):
                    ticker = tickers.get(symbol) or tickers.get(symbol.replace("/", ""))
                    if ticker:
                        bid = Decimal(str(ticker.get("bid", "0") or "0"))
                        ask = Decimal(str(ticker.get("ask", "0") or "0"))
                        market_price = ask if side == "buy" else bid
                        if market_price > 0 and price > 0:
                            expected_slippage = abs(market_price - price) / price
                            if expected_slippage > Decimal("0.005"):
                                logger.error(f"SOR Reject: Market fallback would incur {expected_slippage:.2%} slippage (> 0.5% limit). Aborting entry.")
                                if self.alert_service:
                                    await self.alert_service.send(f"⚠️ SOR Rejected market fallback for {symbol} due to high slippage ({expected_slippage:.2%})")  # type: ignore[attr-defined]
                                return None
            except Exception as e:
                logger.debug(f"SOR slippage check failed, proceeding to market: {e}")

            if order and order.get("id"):
                await self.client.cancel_order(order["id"], symbol)

            market_order = await self.client.create_market_order(symbol, side, amount)
            logger.info(f"Market fallback filled: {market_order['orderId']}")
            metrics.orders_placed.labels(symbol=symbol, side=side).inc()
            fill_price = Decimal(
                str(market_order.get("average", market_order.get("avgPrice", price)))
            )
            slippage_bps = abs(fill_price - price) / price * Decimal("10000")
            metrics.execution_slippage_bps.labels(symbol=symbol).observe(
                float(slippage_bps)
            )
            sl_id = await self._place_sl_after_fill(
                symbol, side, price, amount, max_loss_usd, price_tick
            )
            market_order["sl_order_id"] = sl_id or ""
            logger.debug("execute: returning dict (Market fallback)")
            return market_order
        except Exception as e:
            metrics.orders_failed.labels(
                symbol=symbol, error_type=type(e).__name__
            ).inc()
            logger.error(f"Market fallback failed: {e}")
            logger.debug(f"execute: error={e}")
            return None

    async def execute_exit(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        price_tick: Decimal = Decimal("0.01"),
    ) -> dict[str, Any] | None:
        """Execute profitable exit via Post-Only → Reprice → Market.

        No SL placement (position is closing). For loss exits / hard fails,
        callers should use create_market_order directly to guarantee fill.

        Handles partial fills: tracks cumExecQty across repricing attempts
        and adjusts remaining amount to avoid orphaned partial positions.
        """
        logger.debug(f"execute_exit: entering symbol={symbol} side={side}")

        if price <= 0:
            logger.warning(
                f"SOR exit: invalid price {price} for {symbol}, falling back to market"
            )
            return await self.client.create_market_order(
                symbol, side, amount, params={"reduceOnly": True}
            )

        remaining = amount
        total_filled = Decimal("0")

        # Step 1: Post-Only Limit
        logger.info(f"SOR exit Step 1: Post-Only {side} {remaining} @ {price}")
        try:
            order = await self.client.create_limit_order(
                symbol, side, remaining, price, params={"reduceOnly": True}
            )
            if order.get("status") in ("open", "closed"):
                logger.info(f"Exit Post-Only filled: {order['orderId']}")
                return order
        except Exception as e:
            logger.warning(f"Exit Post-Only failed: {e}")

        # Step 2: Reprice attempts (track partial fills across attempts)
        current_price = price
        effective_tick = max(price * Decimal("0.001"), Decimal("0.01"))
        for attempt in range(self.max_reprice_attempts):
            await asyncio.sleep(self.reprice_delay_seconds)

            # Check partial fill before cancelling
            if order and order.get("id"):
                try:
                    status = await self.client.get_order_status(order["id"], symbol)
                    filled_qty = Decimal(str(status.get("cumExecQty", "0")))
                    if filled_qty > 0:
                        total_filled += filled_qty
                        remaining -= filled_qty
                        logger.info(
                            f"SOR exit: partial fill detected — {filled_qty} filled, "
                            f"{remaining} remaining (total_filled={total_filled})"
                        )
                except Exception:
                    pass  # proceed with cancel anyway

            if side == "buy":
                current_price += effective_tick
            else:
                current_price -= effective_tick
                if current_price <= 0:
                    logger.warning(
                        "Exit reprice would go negative, falling back to market"
                    )
                    break

            logger.info(
                f"SOR exit Step 2: Reprice attempt {attempt + 1} @ {current_price} (remaining={remaining})"
            )
            try:
                if order and order.get("id"):
                    await self.client.cancel_order(order["id"], symbol)

                if remaining <= 0:
                    break  # fully filled across attempts
                order = await self.client.create_limit_order(
                    symbol, side, remaining, current_price, params={"reduceOnly": True}
                )
                if order.get("status") in ("open", "closed"):
                    logger.info(f"Exit reprice filled: {order['orderId']}")
                    return order
            except Exception as e:
                logger.warning(f"Exit reprice failed: {e}")

        # Step 3: Market fallback for any remaining
        if remaining > 0:
            logger.info(f"SOR exit Step 3: Market fallback (remaining={remaining})")
            try:
                if order and order.get("id"):
                    await self.client.cancel_order(order["id"], symbol)
                market_order = await self.client.create_market_order(
                    symbol, side, remaining, params={"reduceOnly": True}
                )
                logger.info(f"Exit market fallback filled: {market_order['orderId']}")
                return market_order
            except Exception as e:
                logger.error(f"Exit market fallback failed: {e}")
                return None
        else:
            logger.info(
                f"SOR exit: fully filled across repricing (total_filled={total_filled})"
            )
            return order

    async def _place_sl_after_fill(
        self,
        symbol: str,
        side: str,
        fill_price: Decimal,
        amount: Decimal,
        max_loss_usd: Decimal,
        price_tick: Decimal,
    ) -> str | None:
        """Place exchange-side SL immediately after fill. CLAUDE.md Rule 5.

        Uses Bybit V5 set_trading_stop to attach SL atomically to the position.
        Falls back to conditional order if atomic placement fails after 1 retry.
        """
        sl_price = Decimal("0")
        try:
            sl_distance = max_loss_usd / amount if amount > 0 else Decimal("0")
            if side == "buy":
                raw_sl_price = fill_price - sl_distance
            else:
                raw_sl_price = fill_price + sl_distance

            # Round sl_price to price_tick to avoid Bybit precision errors
            sl_price = (raw_sl_price / price_tick).quantize(Decimal("1")) * price_tick

            # Cancel stale conditional stop orders before placing new one
            try:
                open_orders = await self.client.fetch_open_orders(symbol=symbol)
                stop_orders = [o for o in open_orders
                               if o.get("type") in ("stop", "StopOrder", "Stop")
                               or o.get("stopOrderType")]
                if len(stop_orders) >= 8:  # Pre-emptive cleanup at 8 (before hitting 10 limit)
                    self._log.warning(f"SL cleanup: cancelling {len(stop_orders)} stale stop orders for {symbol}")
                    for so in stop_orders:
                        try:
                            await self.client.cancel_order(so["id"], symbol)
                        except Exception:
                            pass
                    import asyncio
                    await asyncio.sleep(0.3)  # Let Bybit process cancellations
            except Exception as e:
                self._log.warning(f"SL cleanup failed for {symbol}: {e}")

            # Primary: atomic SL via set_trading_stop (exchange attaches to position)
            try:
                await self.client.set_trading_stop(symbol, side, stop_loss=sl_price)
                metrics.stop_loss_placement.labels(
                    symbol=symbol, result="success"
                ).inc()
                logger.info(
                    f"Atomic SL placed via set_trading_stop: {symbol} @ {sl_price}"
                )
            except Exception as primary_err:
                # Retry once after 2s
                logger.warning(
                    f"set_trading_stop failed for {symbol}, retrying: {primary_err}"
                )
                await asyncio.sleep(2)
                try:
                    await self.client.set_trading_stop(symbol, side, stop_loss=sl_price)
                    metrics.stop_loss_placement.labels(
                        symbol=symbol, result="success"
                    ).inc()
                    logger.info(f"Atomic SL placed on retry: {symbol} @ {sl_price}")
                except Exception as retry_err:
                    # Fallback: legacy conditional order
                    metrics.stop_loss_placement.labels(
                        symbol=symbol, result="fallback"
                    ).inc()
                    logger.critical(
                        f"set_trading_stop RETRY FAILED for {symbol}: {retry_err} — falling back to conditional order"
                    )
                    sl_order = await self.client.place_stop_loss(
                        symbol, side, sl_price, amount
                    )
                    if sl_order:
                        logger.info(
                            f"Fallback conditional SL placed: {sl_order.get('id')} @ {sl_price}"
                        )
                    else:
                        logger.critical(
                            f"FALLBACK SL ALSO RETURNED NONE for {symbol} {side} — position unprotected!"
                        )

        except Exception as e:
            metrics.stop_loss_placement.labels(symbol=symbol, result="failed").inc()
            logger.critical(
                f"SL PLACEMENT FAILED for {symbol} {side}: {e} — position UNPROTECTED!"
            )
            logger.debug(f"_place_sl_after_fill: error={e}")
        # Entry alert fires on fill regardless of SL outcome
        if self.alert_service:
            try:
                from app.bot.utils.formatters import format_entry_alert

                await self.alert_service.send(
                    format_entry_alert(symbol, side, fill_price, amount, sl_price)
                )
            except Exception as ae:
                logger.error(f"Entry alert failed: {ae}")
        return None  # atomic SL has no order ID — lives on the position

    async def cancel_all(self, symbol: str) -> None:
        """Cancel all open orders for a symbol."""
        logger.debug(f"cancel_all: entering symbol={symbol}")
        try:
            orders = await self.client.fetch_open_orders()
            for order in orders:
                if order["symbol"] == symbol:
                    await self.client.cancel_order(order["id"], symbol)
                    logger.info(f"Cancelled order: {order['orderId']}")
        except Exception as e:
            logger.error(f"Cancel all failed: {e}")
            logger.debug(f"cancel_all: error={e}")
        logger.debug("cancel_all: returning None")

    async def cancel_all_positions(self) -> None:
        """Cancel all open orders across all symbols — used by emergency kill/sell-all."""
        logger.debug("cancel_all_positions: entering")
        try:
            orders = await self.client.fetch_open_orders()
            for order in orders:
                await self.client.cancel_order(order["id"], order["symbol"])
                logger.info(f"Cancelled order: {order['orderId']} ({order['symbol']})")
        except Exception as e:
            logger.error(f"Cancel all positions failed: {e}")
            logger.debug(f"cancel_all_positions: error={e}")
        logger.debug("cancel_all_positions: returning None")

    async def flatten_all_positions(self) -> None:
        """Emergency flatten: cancel all orders then market-close every open position.

        Called by kill switch, watchdog, and Telegram /kill /sellall commands.
        Errors on individual positions do NOT stop the loop — every position gets
        attempted. Per RISK_AND_RUNBOOK §1: ignore all errors during kill.
        """
        logger.critical("FLATTEN ALL: starting emergency position close")
        # Step 1: cancel all pending orders
        await self.cancel_all_positions()

        # Step 2: market-close every open position
        try:
            positions = await self.client.fetch_positions()
        except Exception as e:
            logger.critical(f"FLATTEN ALL: fetch_positions failed — cannot close: {e}")
            return

        closed = 0
        for pos in positions:
            symbol = pos.get("symbol", "")
            side = pos.get("side", "")
            qty = Decimal(str(pos.get("amount", "0")))
            if qty <= 0 or not symbol:
                continue
            api_side = "sell" if side == "LONG" else "buy"
            try:
                await self.client.create_market_order(
                    symbol, api_side, qty, {"reduceOnly": True}
                )
                closed += 1
                logger.critical(f"FLATTEN ALL: market-closed {symbol} {side} qty={qty}")
            except Exception as e:
                logger.critical(f"FLATTEN ALL: FAILED to close {symbol} {side}: {e}")

        metrics.positions_flattened_total.labels(reason="kill_switch").inc(closed)
        logger.critical(
            f"FLATTEN ALL: complete — {closed}/{len(positions)} positions closed"
        )

    # ------------------------------------------------------------------
    # Regime-aware execution (Phase 12)
    # ------------------------------------------------------------------

    async def execute_regime_aware(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        use_post_only: bool = False,
        regime: str = "",
        price_tick: Decimal = Decimal("0.01"),
        max_loss_usd: Decimal = Decimal("1.00"),
    ) -> dict[str, Any] | None:
        """Regime-aware order execution.

        CHOP/RANGE: force Post-Only only — reject if not filled, no market fallback.
        TREND: allow full Post-Only → Reprice → Market pipeline.
        Spread gate: reject if bid-ask spread exceeds regime threshold.
        """
        if price <= 0:
            logger.warning("SOR: invalid price %s for %s, skipping", price, symbol)
            return None

        # Spread gate
        spread_ok = await self._check_spread_gate(symbol, regime)
        if not spread_ok:
            metrics.orders_rejected.labels(symbol=symbol, reason="spread_gate").inc()
            return None

        # CHOP/RANGE: Post-Only only — no market fallback
        if use_post_only:
            order = await self._try_post_only(symbol, side, amount, price)
            if order is not None:
                sl_id = await self._place_sl_after_fill(
                    symbol, side, price, amount, max_loss_usd, price_tick
                )
                order["sl_order_id"] = sl_id or ""
                return order
            logger.info(
                "SOR: Post-Only rejected for %s (CHOP/RANGE) — no market fallback",
                symbol,
            )
            metrics.orders_rejected.labels(
                symbol=symbol, reason="chop_range_post_only"
            ).inc()
            return None

        # TREND: full pipeline with regime-adjusted reprices
        max_reprices = (
            self.max_reprice_attempts
            if regime not in ("CHOP", "RANGE")
            else CHOP_RANGE_MAX_REPRICE
        )
        return await self._execute_full_pipeline(
            symbol,
            side,
            amount,
            price,
            price_tick,
            max_loss_usd,
            max_reprices,
        )

    async def _check_spread_gate(self, symbol: str, regime: str) -> bool:
        """Reject if bid-ask spread exceeds regime threshold."""
        try:
            if self.redis:
                state = await self.redis.get_global_state(symbol)
                if state and state.get("best_bid") and state.get("best_ask"):
                    bid = Decimal(str(state["best_bid"]))
                    ask = Decimal(str(state["best_ask"]))
                    if bid <= 0 or ask <= 0:
                        return True
                    spread = (ask - bid) / bid
                    threshold = (
                        CHOP_RANGE_SPREAD_PCT
                        if regime in ("CHOP", "RANGE")
                        else TREND_SPREAD_PCT
                    )
                    return spread <= threshold

            # Fallback
            tickers = await self.client.fetch_tickers(symbol=symbol)
            if not tickers:
                return True
            ticker = tickers[0] if isinstance(tickers, list) else tickers
            bid = Decimal(str(ticker.get("bid", 0)))
            ask = Decimal(str(ticker.get("ask", 0)))
            if bid <= 0 or ask <= 0:
                return True
            spread = (ask - bid) / bid
            threshold = (
                CHOP_RANGE_SPREAD_PCT
                if regime in ("CHOP", "RANGE")
                else TREND_SPREAD_PCT
            )
            if spread > threshold:
                logger.warning(
                    "SOR: spread %.4f exceeds threshold %.4f for %s (%s)",
                    float(spread),
                    float(threshold),
                    symbol,
                    regime,
                )
                return False
            return True
        except Exception:
            logger.warning(
                "SOR: spread check failed for %s, rejecting (fail-closed)", symbol
            )
            return False

    async def _try_post_only(
        self, symbol: str, side: str, amount: Decimal, price: Decimal
    ) -> dict[str, Any] | None:
        """Attempt a single Post-Only limit order."""
        metrics.sor_step_total.labels(symbol=symbol, step="post_only").inc()
        try:
            order = await self.client.create_limit_order(symbol, side, amount, price)
            if order.get("status") in ("open", "closed"):
                metrics.orders_placed.labels(symbol=symbol, side=side).inc()
                fill_price = Decimal(
                    str(order.get("average", order.get("avgPrice", price)))
                )
                slippage_bps = abs(fill_price - price) / price * Decimal("10000")
                metrics.execution_slippage_bps.labels(symbol=symbol).observe(
                    float(slippage_bps)
                )
                return order
        except Exception as exc:
            logger.warning("SOR: Post-Only failed: %s", exc)
        return None

    async def _execute_full_pipeline(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        price_tick: Decimal,
        max_loss_usd: Decimal,
        max_reprices: int,
    ) -> dict[str, Any] | None:
        """Post-Only → Reprice → Market pipeline with configurable reprices."""
        # Step 1: Post-Only
        order = await self._try_post_only(symbol, side, amount, price)
        if order is not None:
            sl_id = await self._place_sl_after_fill(
                symbol, side, price, amount, max_loss_usd, price_tick
            )
            order["sl_order_id"] = sl_id
            return order

        # Step 2: Reprice attempts
        current_price = price
        effective_tick = max(price * Decimal("0.001"), Decimal("0.01"))
        for attempt in range(max_reprices):
            await asyncio.sleep(self.reprice_delay_seconds)
            if side == "buy":
                current_price += effective_tick
            else:
                current_price -= effective_tick
            if current_price <= 0:
                break
            metrics.sor_step_total.labels(symbol=symbol, step="reprice").inc()
            if order and order.get("id"):
                await self.client.cancel_order(order["id"], symbol)
            try:
                order = await self.client.create_limit_order(
                    symbol, side, amount, current_price
                )
                if order.get("status") in ("open", "closed"):
                    fill_price = Decimal(
                        str(order.get("average", order.get("avgPrice", current_price)))
                    )
                    slippage_bps = abs(fill_price - price) / price * Decimal("10000")
                    metrics.execution_slippage_bps.labels(symbol=symbol).observe(
                        float(slippage_bps)
                    )
                    sl_id = await self._place_sl_after_fill(
                        symbol,
                        side,
                        current_price,
                        amount,
                        max_loss_usd,
                        price_tick,
                    )
                    order["sl_order_id"] = sl_id
                    return order
            except Exception:
                logger.debug("SOR: reprice %d failed", attempt + 1)

        # Step 3: Market fallback
        if order and order.get("id"):
            await self.client.cancel_order(order["id"], symbol)
        metrics.sor_step_total.labels(symbol=symbol, step="market").inc()
        try:
            market_order = await self.client.create_market_order(symbol, side, amount)
            metrics.orders_placed.labels(symbol=symbol, side=side).inc()
            fill_price = Decimal(
                str(market_order.get("average", market_order.get("avgPrice", price)))
            )
            slippage_bps = abs(fill_price - price) / price * Decimal("10000")
            metrics.execution_slippage_bps.labels(symbol=symbol).observe(
                float(slippage_bps)
            )
            sl_id = await self._place_sl_after_fill(
                symbol, side, price, amount, max_loss_usd, price_tick
            )
            market_order["sl_order_id"] = sl_id
            return market_order
        except Exception as exc:
            metrics.orders_failed.labels(
                symbol=symbol, error_type=type(exc).__name__
            ).inc()
            logger.error("SOR: market fallback failed: %s", exc)
            return None
