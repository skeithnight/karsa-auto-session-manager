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

_GATE_THRESHOLD = 40.0  # ponytail: raised from 25, restore to 65 when confident
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
            logger.debug("evaluate: %s — only %d candles (need %d)", symbol, len(candles), _MIN_CANDLES)
            return None

        metrics.signals_entered_pipeline.labels(symbol=symbol).inc()

        # Convert to numpy if needed
        if isinstance(candles, list):
            arr = np.array(candles, dtype=np.float64)
        else:
            arr = candles

        # Step 1: Regime classification
        regime = self._classifier.classify(arr)
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
            effective_gate = float(self._gate) * vol_factor
            logger.debug("evaluate: %s %s score=%.1f (gate=%.1f vol=%.2f)", symbol, direction, score, effective_gate, vol_factor)

            if score >= effective_gate:
                metrics.signal_confidence_passed_total.labels(regime=regime.value).inc()
                return await self._build_signal(symbol, direction, regime, score, arr)

        return None

    async def check_consecutive_losses(self, symbol: str, regime: MarketRegime) -> bool:
        """Check if symbol has 3+ consecutive losses in the same regime.

        Returns True if signal should be REJECTED (too many consecutive losses).
        """
        if self._trade_memory is None:
            return False

        try:
            trades = await self._trade_memory.get_recent(symbol, count=5)
            if not trades:
                return False

            regime_str = regime.value
            consecutive = 0
            for t in trades:
                pnl = t.get("pnl_pct", 0)
                t_regime = t.get("regime", "")
                if pnl < 0 and t_regime == regime_str:
                    consecutive += 1
                else:
                    break

            if consecutive >= self._CONSECUTIVE_LOSS_THRESHOLD:
                logger.warning(
                    "consecutive_loss_block: %s %d losses in %s — REJECTING",
                    symbol, consecutive, regime_str,
                )
                from app.core import metrics
                metrics.signal_confidence_passed_total.labels(regime=regime_str)
                return True
            return False

        except Exception:
            logger.exception("consecutive_loss_check failed for %s", symbol)
            return False

    def _determine_directions(self, regime: MarketRegime) -> list[str]:
        """Determine which directions to evaluate based on regime."""
        if regime == MarketRegime.TREND_BULL:
            return ["LONG"]
        if regime == MarketRegime.TREND_BEAR:
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
            # amount = (balance * risk_pct * size_multiplier) / risk_distance
            risk_pct = await self._get_risk_pct()
            amount = self._wallet_balance * risk_pct * profile.size_multiplier / risk_distance
            # Cap notional to 40% of equity (PRM single position limit)
            max_notional = self._wallet_balance * Decimal("0.40")
            if entry_price > 0:
                max_amount = max_notional / entry_price
                if amount > max_amount:
                    amount = max_amount
        else:
            # Fallback: fixed base_size when balance unknown
            amount = self._base_size * Decimal("100") * profile.size_multiplier / risk_distance

        # Entry fee rate (maker for post_only, taker otherwise)
        entry_fee_rate = self._maker_fee if profile.use_post_only else self._taker_fee

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
