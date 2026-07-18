"""Data Normalizer — convert exchange-specific schemas to unified GlobalState."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from loguru import logger
from pydantic import BaseModel, Field


class ExchangeData(BaseModel):
    """Normalized data from a single exchange."""

    exchange: str
    symbol: str
    bids: list[tuple[Decimal, Decimal]] = Field(default_factory=list)
    asks: list[tuple[Decimal, Decimal]] = Field(default_factory=list)
    last_price: Decimal | None = None
    timestamp: datetime
    is_stale: bool = False


class GlobalState(BaseModel):
    """Unified market state across all read exchanges."""

    symbol: str
    exchanges: list[ExchangeData] = Field(default_factory=list)
    global_vwap: Decimal | None = None
    aggregate_skew: Decimal | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    total_volume: Decimal | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Normalizer:
    """ONLY place raw exchange dicts get touched directly."""

    def normalize_orderbook(
        self, raw_data: dict, exchange_id: str, symbol: str
    ) -> ExchangeData:
        """Convert raw orderbook from any exchange to ExchangeData."""
        logger.debug(f"normalize_orderbook: entering exchange_id={exchange_id} symbol={symbol}")
        try:
            bids = [
                (Decimal(str(price)), Decimal(str(size)))
                for price, size in raw_data.get("bids", [])
            ]
            asks = [
                (Decimal(str(price)), Decimal(str(size)))
                for price, size in raw_data.get("asks", [])
            ]

            result = ExchangeData(
                exchange=exchange_id,
                symbol=symbol,
                bids=bids,
                asks=asks,
                timestamp=datetime.now(UTC),
            )
            logger.debug("normalize_orderbook: returning ExchangeData")
            return result
        except (InvalidOperation, TypeError) as e:
            logger.error(f"Failed to normalize orderbook from {exchange_id}: {e}")
            logger.debug(f"normalize_orderbook: error={e}")
            raise

    def normalize_trade(self, raw_trade: dict, exchange_id: str, symbol: str) -> ExchangeData:
        """Convert raw trade from any exchange to ExchangeData."""
        logger.debug(f"normalize_trade: entering exchange_id={exchange_id} symbol={symbol}")
        try:
            price = Decimal(str(raw_trade.get("price", 0)))
            result = ExchangeData(
                exchange=exchange_id,
                symbol=symbol,
                last_price=price,
                timestamp=datetime.now(UTC),
            )
            logger.debug("normalize_trade: returning ExchangeData")
            return result
        except (InvalidOperation, TypeError) as e:
            logger.error(f"Failed to normalize trade from {exchange_id}: {e}")
            logger.debug(f"normalize_trade: error={e}")
            raise

    def build_global_state(self, symbol: str, exchanges: list[ExchangeData]) -> GlobalState:
        """Aggregate exchange data into a single GlobalState."""
        logger.debug(f"build_global_state: entering symbol={symbol}")
        active_exchanges = [e for e in exchanges if not e.is_stale]

        # Calculate VWAP from best bid/ask across exchanges
        global_vwap = None
        aggregate_skew = None
        best_bid = None
        best_ask = None
        total_volume = None
        if active_exchanges:
            total_bid_vol = Decimal("0")
            total_ask_vol = Decimal("0")
            weighted_sum = Decimal("0")
            all_bids: list[Decimal] = []
            all_asks: list[Decimal] = []
            for ex in active_exchanges:
                if ex.bids and ex.asks:
                    best_bid_price = ex.bids[0][0]
                    best_ask_price = ex.asks[0][0]
                    bid_vol = ex.bids[0][1]
                    ask_vol = ex.asks[0][1]
                    all_bids.append(best_bid_price)
                    all_asks.append(best_ask_price)
                    mid = (best_bid_price + best_ask_price) / 2
                    vol = bid_vol + ask_vol
                    weighted_sum += mid * vol
                    total_bid_vol += bid_vol
                    total_ask_vol += ask_vol
            total_vol = total_bid_vol + total_ask_vol
            if total_vol > 0:
                global_vwap = weighted_sum / total_vol
                total_volume = total_vol

            # Skew: bid vs ask volume ratio
            if total_bid_vol + total_ask_vol > 0:
                aggregate_skew = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol)

            # Best bid/ask across all active exchanges
            if all_bids:
                best_bid = max(all_bids)
            if all_asks:
                best_ask = min(all_asks)

        result = GlobalState(
            symbol=symbol,
            exchanges=active_exchanges,
            global_vwap=global_vwap,
            aggregate_skew=aggregate_skew,
            best_bid=best_bid,
            best_ask=best_ask,
            total_volume=total_volume,
            updated_at=datetime.now(UTC),
        )
        logger.debug(f"build_global_state: vwap={global_vwap} skew={aggregate_skew}")
        logger.debug("build_global_state: returning GlobalState")
        return result
