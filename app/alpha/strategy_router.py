"""Strategy Router — Phase 6 regime-aware signal scoring.

Scores signals 0-100 based on market regime. Each regime has its own
scoring sub-strategy. Gate threshold: 65 (neither CHOP component alone passes).

TREND scoring (max 100):
  +30  breakout: price > 20-period high (long) / < 20-period low (short)
  +30  volume surge: current bar > 1.5x 20-bar volume SMA
  +40  global sync: Binance AND OKX confirm same direction

RANGE scoring (max 100):
  +40  BB edge: price pierced Bollinger Band at 2.5 std dev
  +40  wick rejection: candle closed back inside range (pin bar)
  +20  RSI exhaustion: RSI > 75 (shorts) or RSI < 25 (longs)

CHOP scoring — granular confluence (max 100, gate 65):
  +20  orderbook absorption: contrarian delta vs price direction
  +20  price wick snap-back: candle reversed back inside range
  +30  funding confluence: rate skewed against crowd + price refuses to drop
  +30  OI drop (capitulation): OI dropping during the move (liquidation-driven)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("karsa.strategy_router")  # type: ignore[assignment]

from app.alpha.evidence_collector import EvidenceCollector
from app.alpha.regime_classifier import MarketRegime

from app.core.decision_context import DecisionContext
from app.core.feature_extractor import FeatureVector
from app.learning.expected_edge import ExpectedEdgeCalculator

# --- Constants (cross-ref: docs/SYSTEM_CONSTANTS.md §15.2) ---
TREND_SCORE_BREAKOUT: int = 30
TREND_SCORE_VOLUME: int = 30
TREND_SCORE_GLOBAL_SYNC: int = 40

RANGE_SCORE_BB_EDGE: int = 40
RANGE_SCORE_WICK: int = 40
RANGE_SCORE_RSI: int = 20

CHOP_SCORE_ORDERBOOK_ABSORPTION: int = 20
CHOP_SCORE_WICK_SNAPBACK: int = 20
CHOP_SCORE_FUNDING_CONF: int = 30
CHOP_SCORE_OI_DROP: int = 30

STRATEGY_GATE_THRESHOLD: int = 65
# Cross-asset volatility normalization: ATR as % of price reference point
VOLATILITY_REFERENCE_ATR_PCT: float = 2.0  # typical BTC ATR_pct
VOLATILITY_FACTOR_MIN: float = 0.7  # low-vol bonus (lower gate)
VOLATILITY_FACTOR_MAX: float = 1.30  # high-vol penalty (higher gate)


class StrategyRouter:
    """Regime-aware signal scorer — no LLM, deterministic."""

    def __init__(
        self,
        volatility_scaling: bool = True,
        collector: EvidenceCollector | None = None,
        edge_calculator: ExpectedEdgeCalculator | None = None
    ) -> None:
        self.volatility_scaling = volatility_scaling
        self.collector = collector or EvidenceCollector()
        self.edge_calculator = edge_calculator

    # ─── Sprint 1: Funding Rate Carry Strategy ─────────────────────────

    async def evaluate_carry_signal(
        self,
        symbol: str,
        direction: str,
        redis_client: object,
        config: object | None = None,
    ) -> tuple[int, str]:
        """Sprint 1: Evaluate Funding Rate Carry as a primary alpha signal.

        When funding is extremely negative (< -0.03%) for 3+ consecutive periods
        AND price change is < 2%, shorts are paying longs. This is a mechanical
        edge — you earn carry just by holding, plus the squeeze move.

        Args:
            symbol: Trading pair
            direction: "LONG" or "SHORT"
            redis_client: Redis client for funding history
            config: Optional Settings object for thresholds

        Returns:
            Tuple of (score_bonus, signal_reason)
        """
        try:
            if redis_client is None:
                return 0, ""

            import json as _json

            # Read config thresholds
            if config:
                threshold = float(getattr(config, "carry_funding_threshold", "-0.0003"))
                min_periods = int(getattr(config, "carry_consecutive_periods", 3))
                max_price_change = float(getattr(config, "carry_max_price_change_pct", "0.02"))
                bonus = int(getattr(config, "carry_score_bonus", 25))
            else:
                threshold = -0.0003  # -0.03%
                min_periods = 3
                max_price_change = 0.02  # 2%
                bonus = 25

            # Fetch current funding rate
            raw_rate = await redis_client.get(f"karsa:market:{symbol}:funding_rate")
            if not raw_rate:
                return 0, ""
            current_rate = float(raw_rate)

            # Track funding history in Redis (rolling 8 periods = 24h of 8h funding)
            history_key = f"karsa:funding:history:{symbol}"
            raw_history = await redis_client.get(history_key)
            history = _json.loads(raw_history) if raw_history else []

            # Append current rate (keep last 8 periods)
            history.append({"rate": current_rate, "ts": datetime.now(timezone.utc).isoformat()})
            if len(history) > 8:
                history = history[-8:]
            await redis_client.set(history_key, _json.dumps(history), ex=86400)  # 24h TTL

            # Count consecutive negative periods from the end
            consecutive_negative = 0
            for entry in reversed(history):
                if entry["rate"] < threshold:
                    consecutive_negative += 1
                else:
                    break

            if consecutive_negative < min_periods:
                return 0, ""

            # Check price stability (price change < max_price_change during carry period)
            raw_price_start = await redis_client.get(f"karsa:market:{symbol}:price_start_carry")
            raw_price_now = await redis_client.get(f"shadow:price:{symbol}")

            if raw_price_start and raw_price_now:
                price_start = float(raw_price_start)
                price_now = float(raw_price_now)
                if price_start > 0:
                    price_change = abs(price_now - price_start) / price_start
                    if price_change > max_price_change:
                        # Price moved too much — carry opportunity passed
                        return 0, ""
            else:
                # First time seeing this — set baseline price
                if raw_price_now:
                    await redis_client.set(
                        f"karsa:market:{symbol}:price_start_carry",
                        raw_price_now,
                        ex=86400,
                    )

            # CARRY SIGNAL: Negative funding + stable price = mechanical edge
            reason = (
                f"CARRY: funding={current_rate:.6f} for {consecutive_negative} "
                f"consecutive periods, shorts paying longs"
            )
            logger.info("CARRY SIGNAL: %s %s (+%d pts) — %s", symbol, direction, bonus, reason)
            return bonus, reason

        except Exception as e:
            logger.debug(f"Carry signal evaluation failed for {symbol}: {e}")
            return 0, ""

    # ─── Sprint 2: Liquidation Heatmap ────────────────────────────────

    async def evaluate_liquidation_heatmap(
        self,
        symbol: str,
        direction: str,
        redis_client: object,
        config: object | None = None,
    ) -> tuple[int, str]:
        """Sprint 2: Detect liquidation cascade zones from OI delta patterns.

        When open interest drops sharply (>5% in one period) while price moves
        in one direction, it signals forced liquidations. If multiple OI drops
        cluster within a price range, that zone is a liquidation magnet — price
        tends to revisit it to trigger more liquidations.

        Args:
            symbol: Trading pair
            direction: "LONG" or "SHORT"
            redis_client: Redis client for OI history
            config: Optional Settings object for thresholds

        Returns:
            Tuple of (score_bonus, signal_reason)
        """
        try:
            if redis_client is None:
                return 0, ""

            import json as _json

            # Read config
            if config:
                confluence_threshold = int(getattr(config, "liq_heatmap_confluence_threshold", 3))
                range_pct = float(getattr(config, "liq_heatmap_range_pct", "0.02"))
                bonus = int(getattr(config, "liq_heatmap_score_bonus", 20))
            else:
                confluence_threshold = 3
                range_pct = 0.02
                bonus = 20

            # Read OI change history from Redis (stored by market_data_ingestor)
            history_key = f"karsa:oi:history:{symbol}"
            raw_history = await redis_client.get(history_key)
            if not raw_history:
                return 0, ""

            oi_history = _json.loads(raw_history)
            if len(oi_history) < 3:
                return 0, ""

            # Get current price
            raw_price = await redis_client.get(f"shadow:price:{symbol}")
            if not raw_price:
                return 0, ""
            current_price = float(raw_price)

            # Find liquidation events: OI drops > 5% in a single period
            liq_events = []
            for entry in oi_history:
                oi_change = entry.get("oi_change", 0)
                price = entry.get("price", current_price)
                if oi_change < -0.05:  # >5% OI drop = liquidation cascade
                    liq_events.append({"price": price, "oi_drop": oi_change})

            if len(liq_events) < confluence_threshold:
                return 0, ""

            # Check if current price is near a liquidation cluster
            range_size = current_price * range_pct
            clusters_near_price = 0
            for event in liq_events:
                if abs(event["price"] - current_price) < range_size:
                    clusters_near_price += 1

            if clusters_near_price >= confluence_threshold:
                reason = (
                    f"LIQ_HEATMAP: {clusters_near_price} liquidation events "
                    f"within {range_pct*100:.1f}% of current price — cascade zone detected"
                )
                logger.info("LIQ_HEATMAP: %s %s (+%d pts) — %s", symbol, direction, bonus, reason)
                return bonus, reason

            return 0, ""

        except Exception as e:
            logger.debug(f"Liquidation heatmap evaluation failed for {symbol}: {e}")
            return 0, ""

    # ─── Sprint 2: Cross-Asset Momentum ───────────────────────────────

    async def evaluate_cross_asset_momentum(
        self,
        symbol: str,
        direction: str,
        redis_client: object,
        config: object | None = None,
    ) -> tuple[int, str]:
        """Sprint 2: Cross-asset momentum alignment check.

        When BTC, ETH, and altcoins all show the same directional momentum,
        it signals a market-wide move rather than isolated pump/dump.
        Aligned momentum across assets increases signal confidence.

        Args:
            symbol: Trading pair
            direction: "LONG" or "SHORT"
            redis_client: Redis client for price history
            config: Optional Settings object for weights

        Returns:
            Tuple of (score_bonus, signal_reason)
        """
        try:
            if redis_client is None:
                return 0, ""

            # Read config
            if config:
                btc_weight = float(getattr(config, "cross_asset_btc_weight", "0.60"))
                eth_weight = float(getattr(config, "cross_asset_eth_weight", "0.30"))
                alt_weight = float(getattr(config, "cross_asset_alt_weight", "0.10"))
                lookback = int(getattr(config, "cross_asset_lookback_bars", 6))
                bonus = int(getattr(config, "cross_asset_score_bonus", 15))
            else:
                btc_weight = 0.60
                eth_weight = 0.30
                alt_weight = 0.10
                lookback = 6
                bonus = 15

            # Get BTC, ETH, and altcoin momentum from Redis
            # Momentum = price change over lookback period
            btc_momentum = await self._get_asset_momentum(redis_client, "BTC/USDT", lookback)
            eth_momentum = await self._get_asset_momentum(redis_client, "ETH/USDT", lookback)
            alt_momentum = await self._get_asset_momentum(redis_client, symbol, lookback)

            if btc_momentum is None or eth_momentum is None or alt_momentum is None:
                return 0, ""

            # Calculate weighted momentum score
            weighted_score = (
                btc_momentum * btc_weight +
                eth_momentum * eth_weight +
                alt_momentum * alt_weight
            )

            # Check alignment: all assets moving in same direction
            aligned = (
                (btc_momentum > 0 and eth_momentum > 0 and alt_momentum > 0 and direction == "LONG") or
                (btc_momentum < 0 and eth_momentum < 0 and alt_momentum < 0 and direction == "SHORT")
            )

            if aligned and abs(weighted_score) > 0.005:  # >0.5% weighted move
                reason = (
                    f"CROSS_ASSET: BTC={btc_momentum:+.3f} ETH={eth_momentum:+.3f} "
                    f"ALT={alt_momentum:+.3f} — aligned {direction} momentum"
                )
                logger.info("CROSS_ASSET: %s %s (+%d pts) — %s", symbol, direction, bonus, reason)
                return bonus, reason

            return 0, ""

        except Exception as e:
            logger.debug(f"Cross-asset momentum evaluation failed for {symbol}: {e}")
            return 0, ""

    async def _get_asset_momentum(
        self,
        redis_client: object,
        symbol: str,
        lookback: int,
    ) -> float | None:
        """Helper: Get price momentum (return) over lookback period."""
        import json as _json

        history_key = f"karsa:price:history:{symbol}"
        raw_history = await redis_client.get(history_key)
        if not raw_history:
            return None

        history = _json.loads(raw_history)
        if len(history) < lookback:
            return None

        # Calculate return over lookback period
        price_now = history[-1].get("price", 0)
        price_then = history[-lookback].get("price", 0)
        if price_then <= 0:
            return None

        return (price_now - price_then) / price_then

    async def evaluate_signal(
        self,
        features: FeatureVector,
        regime: MarketRegime,
        direction: str,
        symbol: str = "UNKNOWN",
        conviction: float = 0.5,
    ) -> tuple[DecisionContext, float]:
        """Score a signal probabilistically using EvidenceCollector.

        Args:
            features: FeatureVector with technical indicators
            regime: current market regime
            direction: "LONG" or "SHORT"
            symbol: Trading pair
            conviction: regime conviction 0.0-1.0 from Redis

        Returns:
            Tuple of (DecisionContext, vol_factor)
        """
        context = DecisionContext(
            symbol=symbol,
            direction=direction,
            regime=regime,
            features=features,
            regime_conviction=conviction,
        )

        # ─── PROFITABILITY FIX: EXCLUDE MASSIVE-CAPS FROM HYPER ───
        if regime in (MarketRegime.HYPER_BULL, MarketRegime.HYPER_BEAR):
            if symbol in ("BTC/USDT", "ETH/USDT"):
                logger.info(f"StrategyRouter: {symbol} rejected from HYPER (massive-cap)")
                return context, 1.0

        if regime == MarketRegime.SNIPER:
            # Sniper bypasses deterministic collection, relies heavily on AI Pre-Approval
            context.total_confidence = 100.0  # Trap must be scored explicitly in live loop or pre-approved
            return context, 1.0

        # HARD BLOCK: Reject Breakout signals in RANGE regime to prevent fakeout losses
        if regime == MarketRegime.RANGE:
            rsi = features.rsi_14 or 50.0
            # Buying top of range (LONG + RSI > 50) or selling bottom of range (SHORT + RSI < 50)
            if (direction == "LONG" and rsi > 50.0) or (direction == "SHORT" and rsi < 50.0):
                logger.info(f"StrategyRouter RANGE Hard Block: Rejecting {direction} breakout in RANGE regime for {symbol} (rsi={rsi:.1f})")
                context.total_confidence = 0.0
                return context, 1.0

        context = self.collector.collect(context)

        # Apply Adaptive Strategy Ranking based on Expected Edge
        if self.edge_calculator:
            edge_profile = await self.edge_calculator.calculate(context)
            if edge_profile.sample_size > 5:
                if edge_profile.expectancy > 1.0:
                    context.add_evidence("expected_edge", 1.0, 15.0, f"High historical expectancy: {edge_profile.expectancy:.2f}%")
                elif edge_profile.expectancy < -0.5:
                    context.add_evidence("expected_edge", -1.0, 20.0, f"Negative historical expectancy: {edge_profile.expectancy:.2f}%")

        # Wire regime conviction to score (prevents boundary whipsaw)
        # Conviction is 0.0-1.0 from classify_with_conviction(), written to Redis
        if context.regime_conviction < 1.0:
            context.total_confidence *= context.regime_conviction
            logger.debug(f"StrategyRouter: score weighted by conviction={context.regime_conviction:.3f}")

        atr_pct = features.atr_pct or VOLATILITY_REFERENCE_ATR_PCT
        vol_factor = (
            self._volatility_factor(atr_pct) if self.volatility_scaling else 1.0
        )

        logger.info(
            f"StrategyRouter: regime={regime.value} dir={direction} "
            f"confidence={context.total_confidence:.1f} atr_pct={atr_pct:.2f} vol_factor={vol_factor:.2f}"
        )

        from app.core import metrics as m

        score = context.total_confidence
        if score < 50:
            bucket = "0-50"
        elif score < 65:
            bucket = "50-65"
        elif score < 85:
            bucket = "65-85"
        else:
            bucket = "85-100"

        m.strategy_scored_total.labels(regime=regime.value, score_bucket=bucket).inc()

        return context, float(vol_factor)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_rsi(closes: np.ndarray[Any, Any], period: int = 14) -> float:
        """RSI via Wilder smoothing. Returns 50.0 on insufficient data."""
        if len(closes) < period + 1:
            return 50.0

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = float(np.mean(gains[:period]))
        avg_loss = float(np.mean(losses[:period]))

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    @staticmethod
    def _volatility_factor(atr_pct: float) -> float:
        """Scale factor based on asset volatility relative to reference.

        High-vol assets (e.g. altcoins with ATR_pct=4%) get a factor > 1.0,
        effectively raising the gate threshold. Low-vol assets get < 1.0.
        """
        if VOLATILITY_REFERENCE_ATR_PCT <= 0:
            return 1.0
        raw = atr_pct / VOLATILITY_REFERENCE_ATR_PCT
        return max(VOLATILITY_FACTOR_MIN, min(VOLATILITY_FACTOR_MAX, raw))
