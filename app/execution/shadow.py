"""Shadow Mode — simulated execution on live market data.

Intercepts SOR calls, records virtual entries/exits with fees/slippage.
Zero real orders placed. Separate Redis namespace (shadow:position:*)
and separate DB table (shadow_trades).

Refinements applied (from docs/review/refinement_shadom_plan.md):
  1. Fee asymmetry: maker (0.02%) vs taker (0.055%) based on is_post_only
  2. Wick miss prevention: worst_price_seen tracking in Redis position state
  3. Funding rate drag: 8h funding deduction on held positions
  4. Pending limit orders: PENDING_VIRTUAL_FILL state for post-only entries

Components:
  ShadowExecutor       — same interface as SmartOrderRouter, no inheritance
  ShadowExchangeClient — wraps Redis for APM, same interface as BybitClient
  ShadowAPM            — wraps real APM, adds SL hit + wick + funding logic
"""

from __future__ import annotations

import asyncio
import json as _json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Any

from loguru import logger

from app.core import metrics
from app.core.config import get_settings
from app.core.redis_client import RedisClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SHADOW_PENDING_TTL_SECS: int = 600  # limit orders expire after 10 min
SHADOW_FUNDING_INTERVAL_HOURS: int = 8


# ---------------------------------------------------------------------------
# ShadowExecutor — drop-in for SmartOrderRouter
# ---------------------------------------------------------------------------

class ShadowExecutor:
    """Simulated order routing. Same execute/execute_exit interface as SOR.

    Refinement 1: is_post_only param routes to maker vs taker fee.
    """

    def __init__(
        self,
        redis_client: RedisClient,
        position_store: object,
        trade_store: object,
        alert_service: object | None = None,
    ) -> None:
        self._redis = redis_client
        self._pos_store = position_store
        self._trade_store = trade_store
        self._alert = alert_service
        self._counter = 0
        settings = get_settings()
        self._slippage = Decimal(settings.shadow_slippage_pct)
        self._taker_fee = Decimal(settings.shadow_taker_fee_pct)
        self._maker_fee = Decimal(settings.shadow_maker_fee_pct)

    def _next_id(self) -> str:
        self._counter += 1
        return f"SHADOW-{uuid.uuid4().hex[:8]}"

    def _pick_fee(self, is_post_only: bool) -> Decimal:
        """Fee asymmetry: maker (post-only) vs taker (market/IOC)."""
        return self._maker_fee if is_post_only else self._taker_fee

    async def _get_mid_price(self, symbol: str) -> Decimal:
        """Read live mid price from Redis system:state:{symbol}."""
        raw = await self._redis.get(f"system:state:{symbol}")
        if raw:
            try:
                data = _json.loads(raw)
                bid = Decimal(str(data.get("bid", "0")))
                ask = Decimal(str(data.get("ask", "0")))
                if bid > 0 and ask > 0:
                    return (bid + ask) / 2
                last = Decimal(str(data.get("last", "0")))
                if last > 0:
                    return last
            except Exception:
                pass
        raw2 = await self._redis.get(f"ticker:{symbol}")
        if raw2:
            try:
                data = _json.loads(raw2)
                last = Decimal(str(data.get("last", "0")))
                if last > 0:
                    return last
            except Exception:
                pass
        raise ValueError(f"ShadowExecutor: no price for {symbol}")

    def _apply_slippage(self, price: Decimal, side: str) -> Decimal:
        """Apply slippage — worse fill for the trader."""
        if side == "buy":
            return (price * (1 + self._slippage)).quantize(
                Decimal("0.00000001"), rounding=ROUND_DOWN
            )
        return (price * (1 - self._slippage)).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        )

    async def execute(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal | None = None,
        price_tick: Decimal | None = None,
        max_loss_usd: Decimal | None = None,
        is_post_only: bool = False,
    ) -> dict | None:
        """Virtual entry. Returns fake order dict matching SOR output.

        Refinement 4: When is_post_only=True, returns status=PENDING
        so ShadowAPM can simulate limit fill timing.
        """
        if amount <= 0:
            return None

        mid = await self._get_mid_price(symbol)
        fill_price = self._apply_slippage(mid, side)

        fee_rate = self._pick_fee(is_post_only)
        fee = (fill_price * amount * fee_rate).quantize(Decimal("0.01"))

        order_id = self._next_id()
        sl_id = f"SHADOW-SL-{uuid.uuid4().hex[:8]}"

        # Refinement 4: pending state for limit orders
        status = "PENDING_VIRTUAL_FILL" if is_post_only else "filled"

        logger.info(
            f"SHADOW ENTRY: {symbol} {side} {amount} @ {fill_price} "
            f"(mid={mid}, fee={fee}, fee_type={'maker' if is_post_only else 'taker'}, "
            f"status={status})"
        )

        metrics.karsa_shadow_orders_placed_total.labels(
            symbol=symbol, side=side
        ).inc()

        result: dict[str, Any] = {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "price": fill_price,
            "amount": amount,
            "status": status,
            "sl_order_id": sl_id,
            "fee": str(fee),
            "fee_type": "maker" if is_post_only else "taker",
            "is_shadow": True,
        }

        if is_post_only:
            result["pending_since"] = datetime.now(timezone.utc).isoformat()

        return result

    async def execute_exit(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal | None = None,
        reason: str = "manual",
    ) -> dict | None:
        """Virtual exit. Returns fake fill dict. Exits always taker."""
        if amount <= 0:
            return None

        mid = await self._get_mid_price(symbol)
        exit_side = "sell" if side == "buy" else "buy"
        fill_price = self._apply_slippage(mid, exit_side)

        fee = (fill_price * amount * self._taker_fee).quantize(Decimal("0.01"))

        logger.info(
            f"SHADOW EXIT: {symbol} {exit_side} {amount} @ {fill_price} "
            f"(reason={reason}, fee={fee})"
        )

        metrics.karsa_shadow_exits_placed_total.labels(
            symbol=symbol, reason=reason
        ).inc()

        return {
            "id": self._next_id(),
            "symbol": symbol,
            "side": exit_side,
            "price": fill_price,
            "amount": amount,
            "status": "filled",
            "fee": str(fee),
            "reason": reason,
            "is_shadow": True,
        }

    async def cancel_all(self, symbol: str) -> None:
        """No-op — no real orders to cancel."""
        pass

    async def cancel_all_positions(self) -> None:
        """No-op — no real orders to cancel."""
        pass


# ---------------------------------------------------------------------------
# ShadowExchangeClient — wraps Redis for APM
# ---------------------------------------------------------------------------

class ShadowExchangeClient:
    """Same interface as BybitClient for APM, reads live prices from Redis."""

    def __init__(self, redis_client: RedisClient) -> None:
        self._redis = redis_client

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def fetch_tickers(self) -> dict:
        """Read live prices from Redis system:state:* keys."""
        result: dict[str, dict] = {}
        try:
            settings = get_settings()
            for symbol in settings.symbols[:10]:
                raw = await self._redis.get(f"system:state:{symbol}")
                if raw:
                    data = _json.loads(raw)
                    bid = Decimal(str(data.get("bid", "0")))
                    ask = Decimal(str(data.get("ask", "0")))
                    last = Decimal(str(data.get("last", "0")))
                    if bid > 0 and ask > 0:
                        result[symbol] = {"bid": bid, "ask": ask, "last": last}
        except Exception as e:
            logger.warning(f"ShadowExchangeClient: fetch_tickers error: {e}")
        return result

    async def fetch_positions(self) -> list:
        """Return empty — no exchange positions in shadow mode."""
        return []

    async def fetch_open_orders(self) -> list:
        """Return empty — no exchange orders in shadow mode."""
        return []

    async def place_stop_loss(
        self, symbol: str, side: str, price: Decimal, amount: Decimal
    ) -> dict | None:
        """No-op — shadow positions use virtual SL."""
        return None

    async def amend_stop_loss(
        self, symbol: str, side: str, price: Decimal
    ) -> dict | None:
        """No-op."""
        return None

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """No-op."""
        return True

    async def create_market_order(
        self, symbol: str, side: str, amount: Decimal
    ) -> dict | None:
        """Virtual market order — returns fake fill."""
        return {
            "id": f"SHADOW-MKT-{uuid.uuid4().hex[:8]}",
            "status": "filled",
            "is_shadow": True,
        }


# ---------------------------------------------------------------------------
# ShadowAPM — monitors shadow positions, detects SL hits + wicks + funding
# ---------------------------------------------------------------------------

class ShadowAPM:
    """Shadow ActivePositionManager.

    Refinements applied:
      - Wick miss prevention: tracks worst_price_seen in Redis position state
      - Funding rate drag: deducts 8h funding fee on held positions
      - Pending limit fill: activates PENDING orders when price crosses entry
    """

    def __init__(
        self,
        real_apm: object,
        shadow_executor: ShadowExecutor,
        redis_client: RedisClient,
        position_store: object,
        trade_store: object,
    ) -> None:
        self._apm = real_apm
        self._executor = shadow_executor
        self._redis = redis_client
        self._pos_store = position_store
        self._trade_store = trade_store

    async def run(self) -> None:
        """Main monitoring loop — 2s interval."""
        logger.info("ShadowAPM: starting monitoring loop")
        while True:
            try:
                positions = await self._pos_store.list_all()
                for pos in positions:
                    await self._manage_shadow_position(pos)
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ShadowAPM: error in monitoring loop")
                await asyncio.sleep(5)

    async def _manage_shadow_position(self, pos: dict) -> None:
        """Dispatch: pending fills first, then open position management."""
        status = pos.get("status", "OPEN")

        if status == "PENDING_VIRTUAL_FILL":
            await self._check_pending_fill(pos)
            return

        if status == "OPEN":
            await self._manage_open_position(pos)

    # --- Refinement 4: Pending limit fill detection ---

    async def _check_pending_fill(self, pos: dict) -> None:
        """Check if live price crossed virtual entry to fill pending limit."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "")
        entry_price = Decimal(pos.get("entry_price", "0"))

        if not symbol or not side or entry_price <= 0:
            return

        # Expire stale pending orders
        pending_since = pos.get("pending_since", "")
        if pending_since:
            try:
                since = datetime.fromisoformat(pending_since)
                age = (datetime.now(timezone.utc) - since).total_seconds()
                if age > SHADOW_PENDING_TTL_SECS:
                    logger.info(
                        f"SHADOW PENDING EXPIRED: {symbol} {side} after {age:.0f}s"
                    )
                    metrics.karsa_shadow_limit_orders_unfilled_total.labels(
                        symbol=symbol
                    ).inc()
                    await self._pos_store.remove(symbol, side)
                    return
            except Exception:
                pass

        try:
            mid = await self._executor._get_mid_price(symbol)
        except ValueError:
            return

        # Long fills when price dips to entry; short fills when price rises
        filled = False
        if side == "buy" and mid <= entry_price:
            filled = True
        elif side == "sell" and mid >= entry_price:
            filled = True

        if filled:
            logger.info(
                f"SHADOW PENDING FILLED: {symbol} {side} @ {entry_price} (mid={mid})"
            )
            pos["status"] = "OPEN"
            pos["worst_price_seen"] = str(entry_price)
            pos["last_funding_ts"] = datetime.now(timezone.utc).isoformat()
            key = self._pos_store._key(symbol, side)
            await self._redis.redis.set(key, _json.dumps(pos))

    # --- Refinement 2: Wick miss prevention + SL detection ---

    async def _manage_open_position(self, pos: dict) -> None:
        """Check live price against virtual SL, using worst_price_seen for wicks."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "")
        entry_price = Decimal(pos.get("entry_price", "0"))
        virtual_sl = Decimal(pos.get("virtual_sl", "0"))

        if not symbol or not side or entry_price <= 0:
            return

        try:
            mid = await self._executor._get_mid_price(symbol)
        except ValueError:
            return

        # Refinement 2: update worst_price_seen
        worst = Decimal(pos.get("worst_price_seen", str(mid)))
        if side == "buy" and mid < worst:
            worst = mid
        elif side == "sell" and mid > worst:
            worst = mid
        pos["worst_price_seen"] = str(worst)

        # Persist worst_price_seen back to Redis
        key = self._pos_store._key(symbol, side)
        await self._redis.redis.set(key, _json.dumps(pos))

        # Update peak for trailing stop logic
        await self._pos_store.update_peak(symbol, side, mid)

        # Refinement 3: funding rate drag
        await self._apply_funding_if_due(pos, symbol, side)

        # SL hit detection using worst_price_seen (catches wicks)
        if virtual_sl > 0:
            sl_hit = False
            if side == "buy" and worst <= virtual_sl:
                sl_hit = True
            elif side == "sell" and worst >= virtual_sl:
                sl_hit = True

            if sl_hit:
                logger.warning(
                    f"SHADOW SL HIT: {symbol} {side} worst={worst} <= sl={virtual_sl}"
                )
                metrics.karsa_shadow_sl_hits_total.labels(
                    symbol=symbol, side=side
                ).inc()
                await self._close_shadow_position(pos, virtual_sl, "sl_hit")

    # --- Refinement 3: Funding rate drag ---

    async def _apply_funding_if_due(
        self, pos: dict, symbol: str, side: str
    ) -> None:
        """Deduct 8h funding fee from virtual PnL if funding interval elapsed."""
        last_funding_ts_str = pos.get("last_funding_ts", "")
        if not last_funding_ts_str:
            return

        try:
            last_funding = datetime.fromisoformat(last_funding_ts_str)
        except Exception:
            return

        now = datetime.now(timezone.utc)
        elapsed = now - last_funding

        if elapsed < timedelta(hours=SHADOW_FUNDING_INTERVAL_HOURS):
            return

        # Fetch current funding rate from Redis
        funding_rate = await self._get_funding_rate(symbol)
        if funding_rate <= 0:
            pos["last_funding_ts"] = now.isoformat()
            return

        # Funding fee = position_notional * funding_rate
        amount = Decimal(pos.get("amount", "0"))
        entry_price = Decimal(pos.get("entry_price", "0"))
        notional = entry_price * amount

        if side == "buy":
            funding_fee = notional * funding_rate
        else:
            funding_fee = notional * abs(funding_rate) * Decimal("-1")

        existing_fees = Decimal(pos.get("total_funding_fees", "0"))
        pos["total_funding_fees"] = str(existing_fees + funding_fee)
        pos["last_funding_ts"] = now.isoformat()

        key = self._pos_store._key(symbol, side)
        await self._redis.redis.set(key, _json.dumps(pos))

        if funding_fee > 0:
            logger.info(
                f"SHADOW FUNDING: {symbol} {side} fee={funding_fee:.4f} "
                f"(rate={funding_rate}, notional={notional:.2f})"
            )
            metrics.karsa_shadow_funding_fees_total_usdt.inc(float(funding_fee))

    async def _get_funding_rate(self, symbol: str) -> Decimal:
        """Read funding rate from Redis. Returns 0 if unavailable."""
        raw = await self._redis.get(f"funding:{symbol}")
        if raw:
            try:
                data = _json.loads(raw)
                return Decimal(str(data.get("funding_rate", "0")))
            except Exception:
                pass
        return Decimal("0")

    # --- Close shadow position ---

    async def _close_shadow_position(
        self, pos: dict, exit_price: Decimal, reason: str
    ) -> None:
        """Record virtual close trade in shadow_trades table."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "")
        entry_price = Decimal(pos.get("entry_price", "0"))
        amount = Decimal(pos.get("amount", "0"))

        if side == "buy":
            pnl = (exit_price - entry_price) * amount
        else:
            pnl = (entry_price - exit_price) * amount

        # Fee asymmetry: entry fee depends on order type
        settings = get_settings()
        entry_fee_rate = (
            Decimal(settings.shadow_maker_fee_pct)
            if pos.get("fee_type") == "maker"
            else Decimal(settings.shadow_taker_fee_pct)
        )
        exit_fee_rate = Decimal(settings.shadow_taker_fee_pct)

        entry_fee = entry_price * amount * entry_fee_rate
        exit_fee = exit_price * amount * exit_fee_rate
        total_fees = entry_fee + exit_fee

        # Refinement 3: add accumulated funding fees
        total_funding = Decimal(pos.get("total_funding_fees", "0"))
        net_pnl = pnl - total_fees - total_funding

        logger.info(
            f"SHADOW CLOSE: {symbol} {side} entry={entry_price} "
            f"exit={exit_price} pnl={pnl:.2f} fees={total_fees:.2f} "
            f"funding={total_funding:.4f} net={net_pnl:.2f} reason={reason}"
        )

        metrics.karsa_shadow_pnl_usdt.observe(float(net_pnl))
        metrics.karsa_shadow_fees_total_usdt.inc(float(total_fees))

        try:
            await self._trade_store.record_entry(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                amount=amount,
                confidence=float(pos.get("entry_confidence", 0)),
                regime=pos.get("regime", ""),
                strategy=pos.get("strategy", ""),
                sl_price=Decimal(pos.get("virtual_sl", "0")),
                tp_price=Decimal(pos.get("virtual_tp", "0")),
                risk_profile=pos.get("risk_profile_json", ""),
            )
            await self._trade_store.close_trade(
                symbol=symbol,
                side=side,
                exit_price=exit_price,
                exit_reason=reason,
                pnl=net_pnl,
            )
        except Exception as e:
            logger.error(f"ShadowAPM: failed to record shadow trade: {e}")

        await self._pos_store.remove(symbol, side)
