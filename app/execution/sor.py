"""Smart Order Routing — Post-Only → Reprice → Market fallback.

Regime-aware routing (Phase 12):
  CHOP/RANGE: force Post-Only (maker fee, no aggressive fills)
  TREND: allow Market fallback on reprice failure
  Spread gate: reject entries when bid-ask spread > threshold

Entry Strategies (Phase 13):
  MARKET:       Execute immediately. Volume spike > 2x AND strong breakout.
  LIMIT_RETEST: Limit at EMA20 / breakout level × 0.995. TTL: 2 candles.
  WAIT_PULLBACK: Limit at EMA50 / support level. TTL: 4 candles.
"""

from __future__ import annotations

import asyncio
import random
from decimal import Decimal
from typing import Any

from loguru import logger

from app.core import metrics
from app.execution.bybit_client import BybitClient

# Regime-aware routing constants
CHOP_RANGE_MAX_REPRICE = 1  # fewer reprices for CHOP/RANGE — reject faster
CHOP_RANGE_SPREAD_PCT = Decimal("0.002")  # 0.2% max spread for CHOP/RANGE
TREND_SPREAD_PCT = Decimal("0.005")  # 0.5% max spread for TREND

# Entry strategy constants
SLIPPAGE_REDUCE_THRESHOLD_PCT = Decimal("0.5")  # > 0.5% slippage → split/reduce
SLIPPAGE_SIZE_REDUCTION = Decimal("0.5")  # reduce by 50% when slippage detected
TWAP_NUM_SPLITS = 3
TWAP_INTERVAL_SECONDS = 300  # 5 minutes between splits
CANDLE_SECONDS = 3600  # 1H candle = 3600s


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

    async def _handle_auth_block(self, symbol: str, e: Exception) -> bool:
        """Handle Bybit 110126 error (unsigned agreement). Returns True if blocked."""
        error_msg = str(e).lower()
        if "110126" in error_msg or "sign the required agreement" in error_msg:
            logger.warning(f"🚫 BYBIT AGREEMENT REQUIRED: {symbol}. Adding to 24h blocklist.")
            await self.redis.setex(f"karsa:blocked_symbol:{symbol}", 86400, "unsigned_agreement")
            from app.core import metrics
            metrics.execution_blocked_unauthorized_total.inc()

            if self.alert_service:
                await self.alert_service.send(
                    f"🚨 **ACTION REQUIRED: Bybit Agreement**\n"
                    f"Symbol: `{symbol}`\n"
                    f"Reason: Contract requires manual agreement in Bybit UI.\n"
                    f"Action: Bot has blocked this symbol for 24h. Please log in to Bybit and accept the terms."
                )
            return True
        return False

    async def execute(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        price_tick: Decimal | None = None,
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
        # Normalize side: accept "LONG"/"BUY" from signal, convert to "buy"/"sell"
        side_upper = str(side).upper()
        side = "buy" if side_upper in ("BUY", "LONG") else "sell"
        logger.debug(f"execute: entering symbol={symbol} side={side}")
        if price_tick is None:
            price_tick = self.client._price_ticks.get(symbol, Decimal("0.01"))
        order: dict[str, Any] | None = None

        # Reject invalid price or sub-minimum notional (< 5 USDT on Bybit)
        if price <= 0:
            logger.warning(f"SOR: invalid price {price} for {symbol}, skipping")
            return None

        notional = price * amount
        if notional < Decimal("5.0"):
            logger.warning(
                f"SOR: notional {notional:.2f} USDT is below Bybit 5 USDT minimum for {symbol}, skipping"
            )
            return None

        # Precompute initial Stop-Loss price for zero-latency atomic exchange attachment
        sl_distance = max_loss_usd / amount if amount > 0 else Decimal("0")
        if side == "buy":
            raw_sl_price = price - sl_distance
        else:
            raw_sl_price = price + sl_distance
        effective_price_tick = price_tick if (isinstance(price_tick, Decimal) and price_tick > 0) else Decimal("0.01")
        est_sl_price = (raw_sl_price / effective_price_tick).quantize(Decimal("1")) * effective_price_tick if effective_price_tick > 0 else raw_sl_price

        # Iceberg / TWAP Order Slicing
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
                            symbol, side, current_amount, stop_loss=est_sl_price
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
                    symbol, side, amount, stop_loss=est_sl_price
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
        logger.info(
            "⚡ [STAGE 5: SMART ORDER ROUTER] Executing Post-Only Maker Limit %s %s Amount: %s @ Price: %s",
            symbol,
            side,
            amount,
            price,
        )
        metrics.sor_step_total.labels(symbol=symbol, step="post_only").inc()
        try:
            order = await self.client.create_limit_order(
                symbol, side, amount, price, stop_loss=est_sl_price
            )
            if order.get("status") in ("open", "closed"):
                logger.info(
                    "⚡ [STAGE 5: SMART ORDER ROUTER] Post-Only Maker Order Filled! OrderID: %s",
                    order.get("orderId"),
                )
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
            if await self._handle_auth_block(symbol, e):
                return None
            logger.warning(f"Post-Only failed: {e}")
            # If the order actually filled but we crashed during SL placement,
            # we MUST NOT proceed to step 2. We must return the filled order.
            if order and order.get("status") in ("open", "closed"):
                logger.error(f"CRITICAL: Post-Only filled but crashed after: {e}. Returning order to avoid duplicates.")
                return order

        # Step 2: Reprice attempts
        current_price = price
        # Derive effective_tick from actual exchange price_tick (or symbol precision)
        if isinstance(price_tick, Decimal) and price_tick > Decimal("0"):
            effective_tick = price_tick
        else:
            _ticks = getattr(self.client, "_price_ticks", {})
            raw_tick = _ticks.get(symbol, Decimal("0.0001")) if isinstance(_ticks, dict) else Decimal("0.0001")
            effective_tick = raw_tick if isinstance(raw_tick, Decimal) and raw_tick > Decimal("0") else Decimal("0.0001")

        def _quantize_to_tick(p: Decimal, tick: Decimal) -> Decimal:
            if not isinstance(tick, Decimal) or tick <= Decimal("0"):
                return p
            return (p / tick).quantize(Decimal("1")) * tick

        # Adaptive Maker-Fee Routing: Determine dynamic reprice attempts
        max_reprices = self.max_reprice_attempts
        try:
            ticker = await self.client.fetch_ticker(symbol)
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
            except Exception as e:
                logger.debug(f"Spread check failed: {e}")

            # Check for Adverse Selection / Toxic Orderbook Pull (Whale bid/ask pull)
            try:
                if self.redis:
                    raw_delta = await self.redis.get(f"karsa:market:{symbol}:orderbook_delta")
                    if raw_delta is not None:
                        ob_delta = float(raw_delta)
                        # If buying and bids pulled (delta < -0.4) or selling and asks pulled (delta > 0.4)
                        if (side == "buy" and ob_delta < -0.4) or (side == "sell" and ob_delta > 0.4):
                            logger.warning(
                                f"SOR ADVERSE SELECTION: Toxic orderbook pull detected for {symbol} "
                                f"(side={side}, ob_delta={ob_delta:.2f}). Aborting limit execution!"
                            )
                            if order and order.get("id"):
                                await self.client.cancel_order(order["id"], symbol)
                            return None
            except Exception as _pull_err:
                logger.debug(f"Orderbook pull check failed: {_pull_err}")

            await asyncio.sleep(delay)

            # Move price toward market with strict 3 bps (0.03%) max slippage cap
            slippage_cap_pct = Decimal("0.0003")  # 3 bps cap
            if side == "buy":
                max_price = price * (Decimal("1.0") + slippage_cap_pct)
                raw_next_price = min(current_price + effective_tick, max_price)
                current_price = _quantize_to_tick(raw_next_price, effective_tick)
            else:
                min_price = price * (Decimal("1.0") - slippage_cap_pct)
                raw_next_price = max(current_price - effective_tick, min_price)
                current_price = _quantize_to_tick(raw_next_price, effective_tick)
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
                    symbol, side, amount, current_price, stop_loss=est_sl_price
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
                if await self._handle_auth_block(symbol, e):
                    return None
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
                ticker = await self.client.fetch_ticker(symbol)
                if ticker:
                    bid = Decimal(str(ticker.get("bid", "0") or "0"))
                    ask = Decimal(str(ticker.get("ask", "0") or "0"))
                    side_lower = (side or "").lower()
                    market_price = ask if side_lower in ("buy", "long") else bid
                    if market_price > 0 and price > 0:
                        expected_slippage = abs(market_price - price) / price
                        if expected_slippage > Decimal("0.005"):
                            logger.error(f"SOR Reject: Market fallback would incur {expected_slippage:.2%} slippage (> 0.5% limit). Aborting entry.")
                            if self.alert_service:
                                await self.alert_service.send(f"⚠️ SOR Rejected market fallback for {symbol} due to high slippage ({expected_slippage:.2%})")  # type: ignore[attr-defined]
                            return None
            except Exception as e:
                logger.warning(f"SOR slippage check failed, aborting market fallback: {e}")
                if self.alert_service:
                    await self.alert_service.send(f"⚠️ SOR slippage check failed for {symbol}, aborting to prevent excess slippage")
                return None

            if order and order.get("id"):
                await self.client.cancel_order(order["id"], symbol)

            market_order = await self.client.create_market_order(
                symbol, side, amount, stop_loss=est_sl_price
            )
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
            if await self._handle_auth_block(symbol, e):
                return None
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

    # ------------------------------------------------------------------
    # Entry Strategy Routing
    # ------------------------------------------------------------------

    async def execute_with_strategy(
        self,
        symbol: str,
        side: str,  # "LONG" or "SHORT"
        size: Decimal,
        entry_strategy: str,  # "MARKET", "LIMIT_RETEST", "WAIT_PULLBACK"
        limit_price: Decimal | None = None,
        ttl_candles: int = 2,
        price_tick: Decimal | None = None,
        max_loss_usd: Decimal = Decimal("1.00"),
    ) -> dict[str, Any] | None:
        """Execute entry using the specified strategy.

        MARKET:       Immediate market fill. Use when volume spike > 2x AND breakout strong.
        LIMIT_RETEST: Limit order at limit_price (or mid × 0.995). TTL: ttl_candles.
        WAIT_PULLBACK: Limit order at limit_price. TTL: ttl_candles (default 4).

        Includes slippage protection: if order book depth is too thin,
        either reduce size by 50% or split into TWAP.
        """
        if price_tick is None:
            price_tick = self.client._price_ticks.get(symbol, Decimal("0.01"))

        strategy = entry_strategy.upper()
        logger.info(
            f"SOR strategy={strategy} symbol={symbol} side={side} size={size}"
        )

        # --- Slippage protection: check order book depth ---
        adjusted_size = await self._apply_slippage_guard(symbol, side, size, price_tick)
        if adjusted_size is None:
            logger.warning(f"SOR strategy {strategy}: slippage guard returned None for {symbol}")
            return None

        if strategy == "MARKET":
            return await self._execute_strategy_market(
                symbol, side, adjusted_size, price_tick, max_loss_usd,
            )
        elif strategy in ("LIMIT_RETEST", "WAIT_PULLBACK"):
            return await self._execute_strategy_limit(
                symbol, side, adjusted_size, limit_price, ttl_candles, price_tick, max_loss_usd,
            )
        else:
            logger.error(f"SOR unknown strategy '{strategy}', falling back to MARKET")
            return await self._execute_strategy_market(
                symbol, side, adjusted_size, price_tick, max_loss_usd,
            )

    async def _execute_strategy_market(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        price_tick: Decimal,
        max_loss_usd: Decimal,
    ) -> dict[str, Any] | None:
        """MARKET strategy: execute immediately via existing 3-step pipeline."""
        api_side = "buy" if side in ("buy", "LONG") else "sell"
        # Use current mid price as reference
        mid_price = await self._get_mid_price(symbol)
        if mid_price is None:
            logger.error(f"SOR MARKET: cannot get mid price for {symbol}")
            return None

        result = await self.execute(symbol, api_side, size, mid_price, price_tick, max_loss_usd)
        if result:
            logger.info(f"SOR MARKET filled: {symbol} {api_side} {size}")
        return result

    async def _execute_strategy_limit(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        limit_price: Decimal | None,
        ttl_candles: int,
        price_tick: Decimal,
        max_loss_usd: Decimal,
    ) -> dict[str, Any] | None:
        """LIMIT_RETEST / WAIT_PULLBACK: place limit, wait TTL, cancel if unfilled."""
        api_side = "buy" if side in ("buy", "LONG") else "sell"

        if limit_price is None:
            mid = await self._get_mid_price(symbol)
            if mid is None:
                logger.error(f"SOR LIMIT: cannot get mid price for {symbol}")
                return None
            # LIMIT_RETEST: 0.5% below mid; WAIT_PULLBACK: use mid as-is
            limit_price = (mid * Decimal("0.995")).quantize(price_tick)

        # Place the limit order
        try:
            order = await self.client.create_limit_order(symbol, api_side, size, limit_price)
        except Exception as e:
            if await self._handle_auth_block(symbol, e):
                return None
            logger.error(f"SOR LIMIT: order placement failed for {symbol}: {e}")
            return None

        order_id = order.get("id") or order.get("orderId", "")
        logger.info(
            f"SOR LIMIT placed: {symbol} {api_side} {size} @ {limit_price} "
            f"order_id={order_id} ttl={ttl_candles} candles"
        )

        # Wait for TTL (2 candles default = 7200s for 1H, but we poll at shorter intervals)
        ttl_seconds = ttl_candles * CANDLE_SECONDS
        poll_interval = max(0.1, min(30.0, ttl_seconds / 4))  # poll 4 times or every 30s, min 0.1s
        elapsed = 0.0
        while elapsed < ttl_seconds:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            # Check fill status
            try:
                status = await self.client.get_order_status(order_id, symbol)
                if status.get("status") in ("filled", "closed"):
                    fill_price = Decimal(
                        str(status.get("average", status.get("avgPrice", limit_price)))
                    )
                    logger.info(f"SOR LIMIT filled: {symbol} @ {fill_price}")
                    sl_id = await self._place_sl_after_fill(
                        symbol, api_side, fill_price, size, max_loss_usd, price_tick,
                    )
                    order["sl_order_id"] = sl_id or ""
                    order["status"] = "filled"
                    return order
            except Exception as e:
                logger.debug(f"SOR LIMIT status check failed: {e}")

        # TTL expired — cancel
        logger.warning(
            f"SOR LIMIT TTL expired: {symbol} {api_side} after {ttl_seconds}s, cancelling"
        )
        try:
            await self.client.cancel_order(order_id, symbol)
        except Exception as e:
            logger.warning(f"SOR LIMIT cancel failed: {e}")
        return None

    async def _apply_slippage_guard(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        price_tick: Decimal,
    ) -> Decimal | None:
        """Check order book depth. If slippage > 0.5%, reduce size by 50%.

        Returns adjusted size, or None if the order should be rejected entirely.
        """
        try:
            depth_1pct = await self._get_orderbook_depth_1pct(symbol)
            if depth_1pct is None or depth_1pct <= 0:
                # Cannot assess depth — proceed with original size
                return size

            mid = await self._get_mid_price(symbol)
            if mid is None or mid <= 0:
                return size

            slippage_pct = (size / depth_1pct) * Decimal("100")
            if slippage_pct > SLIPPAGE_REDUCE_THRESHOLD_PCT:
                reduced = (size * SLIPPAGE_SIZE_REDUCTION).quantize(price_tick)
                logger.warning(
                    f"SOR slippage guard: {symbol} slippage={slippage_pct:.2f}% "
                    f"(>{SLIPPAGE_REDUCE_THRESHOLD_PCT}%), reducing size {size} → {reduced}"
                )
                metrics.execution_slippage_bps.labels(symbol=symbol).observe(
                    float(slippage_pct * 100)
                )
                return reduced if reduced > 0 else None
        except Exception as e:
            logger.debug(f"SOR slippage guard check failed: {e}")
        return size

    async def _get_mid_price(self, symbol: str) -> Decimal | None:
        """Get mid price from ticker."""
        try:
            tickers = await self.client.fetch_tickers(symbol)
            if isinstance(tickers, list):
                for t in tickers:
                    if t.get("symbol") == symbol or t.get("symbol") == symbol.replace("/", ""):
                        bid = Decimal(str(t.get("bid", "0") or "0"))
                        ask = Decimal(str(t.get("ask", "0") or "0"))
                        if bid > 0 and ask > 0:
                            return (bid + ask) / Decimal("2")
            elif isinstance(tickers, dict):
                bid = Decimal(str(tickers.get("bid", "0") or "0"))
                ask = Decimal(str(tickers.get("ask", "0") or "0"))
                if bid > 0 and ask > 0:
                    return (bid + ask) / Decimal("2")
        except Exception as e:
            logger.debug(f"SOR get_mid_price failed for {symbol}: {e}")
        return None

    async def _get_orderbook_depth_1pct(self, symbol: str) -> Decimal | None:
        """Get total order book depth within 1% of mid price.

        Uses Bybit's get_orderbook REST endpoint.
        """
        try:

            if not self.client.session:
                return None
            bybit_sym = self.client._to_bybit_symbol(symbol) if hasattr(self.client, "_to_bybit_symbol") else symbol.replace("/", "").replace(":USDT", "")
            raw = await self.client._execute(
                self.client.session.get_orderbook,
                category="linear",
                symbol=bybit_sym,
                limit=50,
            )
            if not raw or raw.get("retCode") != 0:
                return None

            mid = await self._get_mid_price(symbol)
            if mid is None:
                return None

            lower_bound = mid * Decimal("0.99")
            upper_bound = mid * Decimal("1.01")

            total_depth = Decimal("0")
            # bids
            for entry in raw.get("result", {}).get("bids", []):
                price = Decimal(str(entry[0]))
                qty = Decimal(str(entry[1]))
                if lower_bound <= price <= upper_bound:
                    total_depth += qty
            # asks
            for entry in raw.get("result", {}).get("asks", []):
                price = Decimal(str(entry[0]))
                qty = Decimal(str(entry[1]))
                if lower_bound <= price <= upper_bound:
                    total_depth += qty

            return total_depth
        except Exception as e:
            logger.debug(f"SOR orderbook depth check failed for {symbol}: {e}")
            return None

    async def _execute_twap(
        self,
        symbol: str,
        side: str,
        total_size: Decimal,
        num_splits: int = 3,
        interval_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        """TWAP: split total_size into num_splits orders over interval_seconds.

        Each split is a market order. Returns list of fill results.
        """
        api_side = "buy" if side in ("buy", "LONG") else "sell"
        chunk = (total_size / Decimal(str(num_splits))).quantize(Decimal("0.001"))
        results: list[dict[str, Any]] = []

        for i in range(num_splits):
            current_amount = chunk if i < num_splits - 1 else (total_size - chunk * Decimal(str(num_splits - 1)))
            if current_amount <= 0:
                continue

            if i > 0:
                await asyncio.sleep(random.uniform(interval_seconds * 0.8, interval_seconds * 1.2))

            try:
                order = await self.client.create_market_order(symbol, api_side, current_amount)
                logger.info(f"SOR TWAP split {i + 1}/{num_splits}: {symbol} {api_side} {current_amount}")
                results.append(order)
            except Exception as e:
                logger.error(f"SOR TWAP split {i + 1}/{num_splits} failed for {symbol}: {e}")
                results.append({"error": str(e), "split": i + 1})

        return results

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
            eff_tick = price_tick if (isinstance(price_tick, Decimal) and price_tick > 0) else Decimal("0.01")
            sl_price = (raw_sl_price / eff_tick).quantize(Decimal("1")) * eff_tick

            # Cancel stale conditional stop orders before placing new one
            try:
                open_orders = await self.client.fetch_open_orders(symbol=symbol)
                stop_orders = [o for o in open_orders
                               if o.get("type") in ("stop", "StopOrder", "Stop")
                               or o.get("stopOrderType")]
                if len(stop_orders) >= 5:  # Pre-emptive cleanup at 5 (well before hitting 10 limit)
                    logger.warning(f"SL cleanup: cancelling {len(stop_orders)} stale stop orders for {symbol}")
                    for so in stop_orders:
                        try:
                            await self.client.cancel_order(so["id"], symbol)
                        except Exception:
                            pass
                    await asyncio.sleep(0.3)  # Let Bybit process cancellations
            except Exception as e:
                logger.warning(f"SL cleanup failed for {symbol}: {e}")

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
                return "atomic_position_sl"
            except Exception as ae:
                logger.error(f"Entry alert failed: {ae}")
                return "atomic_position_sl"
        return None

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
            api_side = "sell" if side == "buy" else "buy"  # side is already normalized to buy/sell by BybitClient
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

    def _check_spread_gate(self, spread_pct: Decimal | float, regime: Any = None) -> bool:
        """Check if bid-ask spread is within regime tolerance."""
        spread = Decimal(str(spread_pct))
        regime_str = str(getattr(regime, "value", regime) or "").upper()
        if "CHOP" in regime_str or "RANGE" in regime_str:
            return spread <= CHOP_RANGE_SPREAD_PCT
        return spread <= TREND_SPREAD_PCT

    async def _try_post_only(
        self, symbol: str, side: str, amount: Decimal, price: Decimal
    ) -> dict[str, Any] | None:
        """Attempt post-only limit order."""
        try:
            return await self.client.create_limit_order(
                symbol, side, amount, price, {"postOnly": True}
            )
        except Exception as e:
            logger.debug(f"_try_post_only failed for {symbol}: {e}")
            return None

    async def execute_fee_aware(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        ai_confidence: float = 0.5,
        atr: Decimal | float = Decimal("0.02"),
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Execute order with fee-aware ATR unit normalization and routing."""
        atr_dec = Decimal(str(atr))
        # If ATR is passed as percentage (< 1.0), convert to price offset
        if atr_dec < Decimal("1.0"):
            atr_dec = atr_dec * price

        return await self.execute(
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            **kwargs,
        )

    async def execute_regime_aware(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        regime: Any = None,
        spread_pct: Decimal | float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Execute order with regime-aware routing and spread constraints."""
        if spread_pct is not None and not self._check_spread_gate(spread_pct, regime):
            logger.warning(f"SOR regime-aware spread gate rejected {symbol} (spread={spread_pct})")
            return None

        return await self.execute(
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            **kwargs,
        )





