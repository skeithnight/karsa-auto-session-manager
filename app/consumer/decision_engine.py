"""Shared decision pipeline — regime → strategy score → risk gate → TradeSignal.

Used identically by karsa-live and karsa-shadow. Both modes share this
module and only diverge at execution (SmartOrderRouter vs ShadowExecutor).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from app.alpha.regime_classifier import MarketRegime, RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.risk.dynamic_risk_gate import DynamicRiskGate, RiskProfile

logger = logging.getLogger(__name__)

_GATE_THRESHOLD = 75.0  # Increased for higher confidence entries
_MIN_CANDLES = 50
_SLIPPAGE_PCT = Decimal("0.0005")
_TAKER_FEE = Decimal("0.00055")
_MAKER_FEE = Decimal("0.0002")
_BASE_SIZE = Decimal("0.001")
_FUNDING_INTERVAL_BARS = 8


@dataclass(frozen=True)
class TradeSignal:
    """A validated trade signal produced by the decision pipeline.

    Attributes:
        symbol: Unified trading pair.
        direction: LONG or SHORT.
        regime: Current market regime.
        score: Strategy router score (0-100).
        risk_profile: Risk profile used for sizing.
        entry_price: Calculated entry with slippage.
        sl_price: Stop loss price.
        tp_price: Take profit price (None for TRAILING).
        amount: Position size in base currency.
        entry_fee_rate: Maker or taker rate for entry.
        candles: Candle context used for evaluation (for position tracking).
        expires_at: Absolute timestamp (time.time()) after which signal is stale.
    """

    symbol: str
    direction: str
    regime: MarketRegime
    score: float
    risk_profile: RiskProfile
    entry_price: Decimal
    sl_price: Decimal
    tp_price: Decimal | None
    amount: Decimal
    entry_fee_rate: Decimal
    atr: Decimal
    timestamp_ms: int
    candles: list[list] = field(repr=False)
    expires_at: float | None = None
    trace_id: str | None = None


class DecisionEngine:
    """Shared decision pipeline — evaluates candle data into TradeSignals.

    Composes RegimeClassifier, StrategyRouter, and DynamicRiskGate.
    Modes (live/shadow) use identical pipeline; execution layer handles divergence.
    """

    _CONSECUTIVE_LOSS_THRESHOLD: int = 3

    def __init__(
        self,
        classifier: RegimeClassifier,
        router: StrategyRouter,
        risk_gate: DynamicRiskGate,
        gate_threshold: float = _GATE_THRESHOLD,
        base_size: Decimal = _BASE_SIZE,
        slippage_pct: Decimal = _SLIPPAGE_PCT,
        taker_fee: Decimal = _TAKER_FEE,
        maker_fee: Decimal = _MAKER_FEE,
        trade_memory: object | None = None,
        redis_client: object | None = None,
        multi_tf: object | None = None,
    ) -> None:
        self._classifier = classifier
        self._router = router
        self._risk_gate = risk_gate
        self._gate = Decimal(str(gate_threshold))
        self._base_size = base_size
        self._slippage = slippage_pct
        self._taker_fee = taker_fee
        self._maker_fee = maker_fee
        self._wallet_balance: Decimal = Decimal("0")
        self._trade_memory = trade_memory
        self._redis = redis_client
        self._multi_tf = multi_tf
        self._prev_regimes: dict[str, MarketRegime] = {}
        self._regime_transition_counts: dict[str, int] = {}

    def set_wallet_balance(self, balance: Decimal) -> None:
        """Update wallet balance for position sizing."""
        self._wallet_balance = balance

    async def _get_risk_pct(self) -> Decimal:
        """Read risk_pct from Redis karsa:auto:config. Default10%."""
        if self._redis is None:
            return Decimal("0.10")
        try:
            import json as _json

            raw = await self._redis.get("karsa:auto:config")
            if raw:
                cfg = _json.loads(raw)
                pct = cfg.get("risk_pct", 10)
                return Decimal(str(pct)) / Decimal("100")
        except Exception:
            pass
        return Decimal("0.10")

    async def evaluate(
        self,
        symbol: str,
        candles: list[list] | np.ndarray,
        global_prices: dict[str, float] | None = None,
        orderbook_delta: float | None = None,
        funding_rate: float | None = None,
        oi_change: float | None = None,
    ) -> TradeSignal | None:
        """Run full decision pipeline on candle data.

        Args:
            symbol: Unified trading pair.
            candles: OHLCV data, oldest first. Minimum 50 rows.
            global_prices: Cross-exchange prices {binance: float, okx: float}.
            orderbook_delta: Orderbook imbalance (CHOP scoring).
            funding_rate: Current funding rate (CHOP scoring).
            oi_change: Open interest change (CHOP scoring).

        Returns:
            TradeSignal if score >= gate threshold, else None.
        """
        from app.core import metrics

        metrics.signals_pipeline_attempted.labels(symbol=symbol).inc()

        if len(candles) < _MIN_CANDLES:
            logger.debug(
                "evaluate: %s — only %d candles (need %d)",
                symbol,
                len(candles),
                _MIN_CANDLES,
            )
            return None

        metrics.signals_entered_pipeline.labels(symbol=symbol).inc()

        # Convert to numpy if needed
        if isinstance(candles, list):
            arr = np.array(candles, dtype=np.float64)
        else:
            arr = candles

        # HFT Spread Balloon Gate
        # Fetch live microsecond best_bid and best_ask from Data Engine state
        if self._redis:
            try:
                import json as _json
                state_raw = await self._redis.get(f"global:state:{symbol}")
                if state_raw:
                    state = _json.loads(state_raw)
                    best_bid = Decimal(str(state.get("best_bid", "0")))
                    best_ask = Decimal(str(state.get("best_ask", "0")))
                    if best_bid > 0 and best_ask > 0:
                        spread = (best_ask - best_bid) / best_bid
                        if spread > Decimal("0.005"):
                            logger.warning(
                                "evaluate: %s SPREAD BALLOON REJECTION (spread=%.2f%% > 0.5%%) — rejecting to prevent slippage",
                                symbol, float(spread * 100)
                            )
                            return None
            except Exception as e:
                logger.debug("Spread balloon check failed for %s: %s", symbol, e)

        # Step 1: Regime classification
        regime = self._classifier.classify(arr)

        # Regime hysteresis: require 2 consecutive readings to switch regime
        _prev_regime = self._prev_regimes.get(symbol)
        if _prev_regime is not None and regime != _prev_regime:
            self._regime_transition_counts[symbol] = self._regime_transition_counts.get(symbol, 0) + 1
            if self._regime_transition_counts[symbol] < 2:
                regime = _prev_regime  # Stick with previous until confirmed
            else:
                self._regime_transition_counts.pop(symbol, None)  # Transition confirmed
        else:
            self._regime_transition_counts.pop(symbol, None)  # No transition or same regime
        self._prev_regimes[symbol] = regime

        logger.debug("evaluate: %s regime=%s", symbol, regime.value)
        if self._redis:
            try:
                key = f"system:regime:{symbol.replace('/', ':')}"
                await self._redis.set(key, regime.value)
            except Exception as e:
                logger.warning("Failed to save regime to redis for %s: %s", symbol, e)

        # Step 2: Determine directions (regime-dependent)
        directions = self._determine_directions(regime)

        # Step 3: Score each direction, take first pass
        for direction in directions:
            # Extreme Funding Rate Block
            if funding_rate is not None:
                if direction == "LONG" and funding_rate > 0.0005:
                    logger.info(
                        "evaluate: %s LONG blocked due to extreme positive funding %.5f",
                        symbol,
                        funding_rate,
                    )
                    continue
                if direction == "SHORT" and funding_rate < -0.0005:
                    logger.info(
                        "evaluate: %s SHORT blocked due to extreme negative funding %.5f",
                        symbol,
                        funding_rate,
                    )
                    continue
            momentum_exemption = False
            macro_penalty = 1.0

            # Multi-Timeframe Trend Alignment Block
            if self._multi_tf:
                mtf_res = await self._multi_tf.check(symbol, direction)
                if mtf_res.get("blocked"):
                    logger.info(
                        "evaluate: %s %s blocked by 4H Multi-Timeframe filter",
                        symbol,
                        direction,
                    )
                    continue

                # Momentum Exemption: if the token is up/down > 15% in 24h, it has detached from the macro trend.
                if len(arr) >= 24:
                    close_now = arr[-1][4]
                    close_24h_ago = arr[-24][4]
                    pct_change = (close_now - close_24h_ago) / close_24h_ago
                    if direction == "LONG" and pct_change > 0.15 or direction == "SHORT" and pct_change < -0.15:
                        momentum_exemption = True

                # Macro Anchor (Lead-Lag) Penalty
                if symbol not in ["BTC/USDT", "ETH/USDT"] and not momentum_exemption:
                    macro_penalty = await self._multi_tf.get_macro_anchor_penalty(
                        direction
                    )

            metrics.signals_generated.labels(symbol=symbol, direction=direction).inc()

            score, vol_factor = self._router.evaluate_signal(
                arr,
                regime,
                direction,
                global_prices=global_prices,
                orderbook_delta=orderbook_delta,
                funding_rate=funding_rate,
                oi_change=oi_change,
            )

            # Momentum Exemption: Do not penalize explosive gainers for their volatility
            if momentum_exemption:
                logger.info(
                    "evaluate: %s %s has momentum exemption, bypassing volatility gate (vol_factor %.2f -> 1.0)",
                    symbol,
                    direction,
                    vol_factor,
                )
                vol_factor = 1.0

                # If StrategyRouter scored it low (e.g. 0) because the explosive move happened a few hours ago,
                # we force the score up to the base gate so it reaches the AI Analyst.
                if score < float(self._gate):
                    logger.info(
                        "evaluate: %s %s momentum exemption forcing score %.1f -> %.1f for AI Analyst review",
                        symbol,
                        direction,
                        score,
                        float(self._gate),
                    )
                    score = float(self._gate)

            # Apply Macro Penalty (e.g. 0.8x if fighting macro trend)
            score = score * macro_penalty
            effective_gate = float(self._gate) * vol_factor
            logger.debug(
                "evaluate: %s %s score=%.1f (gate=%.1f vol=%.2f)",
                symbol,
                direction,
                score,
                effective_gate,
                vol_factor,
            )

            if score >= effective_gate:
                if await self.check_consecutive_losses(symbol, regime):
                    return None

                metrics.signal_confidence_passed_total.labels(regime=regime.value).inc()
                return await self._build_signal(symbol, direction, regime, score, arr)

        return None

    async def check_consecutive_losses(self, symbol: str, regime: MarketRegime) -> bool:
        """Check if symbol has 3+ consecutive losses or 4+ breakevens in the same regime.

        Returns True if signal should be REJECTED (too many consecutive losses/choppiness).
        """
        if self._trade_memory is None:
            return False

        try:
            trades = await self._trade_memory.get_recent(symbol, count=7)  # Look deeper (was 5)
            if not trades:
                return False

            regime_str = regime.value
            consecutive = 0
            breakeven_streak = 0
            for t in trades:
                pnl = t.get("pnl_pct", 0)
                reason = t.get("exit_reason", "")
                if pnl < 0:
                    consecutive += 1
                    if "breakeven" in reason or "stagnation" in reason:
                        breakeven_streak += 1
                else:
                    break

            if consecutive >= self._CONSECUTIVE_LOSS_THRESHOLD:
                logger.warning(
                    "consecutive_loss_block: %s %d consecutive losses — REJECTING (cooldown)",
                    symbol,
                    consecutive,
                )
                from app.core import metrics
                metrics.signal_confidence_passed_total.labels(regime=regime_str)
                return True

            if breakeven_streak >= 4:
                logger.warning(
                    "breakeven_streak_block: %s %d consecutive breakevens/stagnation — REJECTING (choppy)",
                    symbol,
                    breakeven_streak,
                )
                return True

            return False

        except Exception:
            logger.exception("consecutive_loss_check failed for %s", symbol)
            return False

    def _determine_directions(self, regime: MarketRegime) -> list[str]:
        """Determine which directions to evaluate based on regime."""
        if regime in (MarketRegime.TREND_BULL, MarketRegime.HYPER_BULL):
            return ["LONG"]
        if regime in (MarketRegime.TREND_BEAR, MarketRegime.HYPER_BEAR):
            return ["SHORT"]
        return ["LONG", "SHORT"]

    async def _build_signal(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        score: float,
        arr: np.ndarray,
    ) -> TradeSignal:
        """Build a TradeSignal from pipeline outputs.

        Computes entry price (with slippage), ATR, SL/TP, position size.
        """
        close = Decimal(str(arr[-1][4]))
        ts_ms = int(arr[-1][0])

        # Risk profile
        profile = self._risk_gate.get_profile(regime)

        # Entry price with slippage
        if direction == "LONG":
            entry_price = close * (Decimal("1") + self._slippage)
        else:
            entry_price = close * (Decimal("1") - self._slippage)

        # ATR via Wilder smoothing
        atr = self._calculate_atr(arr)

        # SL price
        sl_buffer = profile.sl_atr_buffer
        if direction == "LONG":
            sl_price = entry_price - (atr * sl_buffer)
        else:
            sl_price = entry_price + (atr * sl_buffer)

        # TP price (TRAILING → None)
        if profile.take_profit_type == "TRAILING":
            tp_price: Decimal | None = None
        else:
            offset = atr * profile.trail_atr_mult
            if direction == "LONG":
                tp_price = entry_price + offset
            else:
                tp_price = entry_price - offset

        # Position size (balance-based risk allocation)
        risk_distance = abs(entry_price - sl_price)
        if risk_distance <= Decimal("0"):
            amount = self._base_size
        elif self._wallet_balance > 0:
            # Kelly Scaling: Multiply risk_pct by (score/100)
            base_risk_pct = await self._get_risk_pct()
            scaled_risk_pct = base_risk_pct * Decimal(str(score / 100.0))

            amount = (
                self._wallet_balance
                * scaled_risk_pct
                * profile.size_multiplier
                / risk_distance
            )
            # Cap notional to 40% of equity (PRM single position limit)
            max_notional = self._wallet_balance * Decimal("0.40")
            if entry_price > 0:
                max_amount = max_notional / entry_price
                amount = min(amount, max_amount)
        else:
            # Fallback: fixed base_size when balance unknown
            amount = (
                self._base_size
                * Decimal("100")
                * profile.size_multiplier
                / risk_distance
            )

        # Entry fee rate (maker for post_only, taker otherwise)
        entry_fee_rate = self._maker_fee if profile.use_post_only else self._taker_fee

        import uuid

        return TradeSignal(
            symbol=symbol,
            direction=direction,
            regime=regime,
            score=score,
            risk_profile=profile,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            amount=amount,
            entry_fee_rate=entry_fee_rate,
            atr=atr,
            timestamp_ms=ts_ms,
            candles=arr.tolist(),
            trace_id=uuid.uuid4().hex,
        )

    @staticmethod
    def _calculate_atr(arr: np.ndarray, period: int = 14) -> Decimal:
        """ATR(14) via Wilder smoothing — matches backtest engine.

        Args:
            arr: Numpy array with columns [ts, open, high, low, close, volume].
            period: Lookback (default 14).

        Returns:
            Decimal ATR value. Returns Decimal("0") if insufficient data.
        """
        highs = arr[:, 2]
        lows = arr[:, 3]
        closes = arr[:, 4]

        if len(highs) < period + 1:
            return Decimal("0")

        # True Range per bar
        prev_closes = np.roll(closes, 1)
        prev_closes[0] = closes[0]

        tr = np.maximum(
            highs - lows,
            np.maximum(
                np.abs(highs - prev_closes),
                np.abs(lows - prev_closes),
            ),
        )
        tr = tr[1:]  # drop first NaN-equivalent row

        # Wilder smoothing
        atr = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr = (atr * (period - 1) + tr[i]) / period

        return Decimal(str(atr))
