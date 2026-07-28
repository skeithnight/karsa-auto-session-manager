"""Decision Engine V2 — Refactored orchestrator.

This is the refactored DecisionEngine that uses:
- EdgeFamilies for per-strategy evaluation
- ScoreComposer for cross-family composition
- SizingPipeline for position sizing
- SignalBuilder for TradeSignal construction

This replaces the monolithic DecisionEngine with a thin orchestrator
that delegates to modular, testable components.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np

from app.alpha.regime_classifier import MarketRegime
from app.alpha.market_analyzer import MarketAnalyzer
from app.alpha.strategy_router import StrategyRouter
from app.consumer.edge_families.trend import TrendContinuation
from app.consumer.edge_families.mean_reversion import MeanReversion
from app.consumer.edge_families.carry import CarryDislocation
from app.consumer.edge_families.liquidation_squeeze import LiquidationSqueeze
from app.consumer.edge_families.event_breakout import EventBreakout
from app.consumer.score_composer import ScoreComposer
from app.consumer.sizing_pipeline import SizingPipeline
from app.core.decision_context import DecisionContext
from app.core.feature_extractor import FeatureExtractor
from app.core.feature_store import FeatureStore
from app.core.market_snapshot import MarketSnapshot
from app.core.observability import ObservabilityLogger
from app.risk.dynamic_risk_gate import DynamicRiskGate, RiskProfile

logger = logging.getLogger(__name__)

_MIN_CANDLES = 50
_SLIPPAGE_PCT = Decimal("0.0005")
_TAKER_FEE = Decimal("0.00055")
_MAKER_FEE = Decimal("0.0002")
_BASE_SIZE = Decimal("0.001")


@dataclass(frozen=True)
class TradeSignal:
    """A validated trade signal produced by the decision pipeline."""
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
    candles: list[list]
    trace_id: str | None = None
    vol_factor: float = 1.0
    session_mult: float = 1.0
    spread_bps: float = 0.0
    cvd_slope: float = 0.0
    atr_pct: float = 0.0
    regime_encoded: int = 0
    context: DecisionContext | None = None
    expected_value: float = 0.0
    winning_family: str | None = None
    stage_timings: dict[str, float] | None = None

    @property
    def confidence(self) -> float:
        """Normalized confidence score (0.0 - 1.0)."""
        return self.score / 100.0 if self.score > 1.0 else self.score


class DecisionEngineV2:
    """Refactored decision pipeline orchestrator.

    Uses modular components:
    - ScoreComposer for family-aware scoring
    - SizingPipeline for position sizing
    - SignalBuilder for TradeSignal construction
    """

    def __init__(
        self,
        analyzer: MarketAnalyzer,
        router: StrategyRouter,
        risk_gate: DynamicRiskGate,
        gate_threshold: float = 75.0,
        base_size: Decimal = _BASE_SIZE,
        slippage_pct: Decimal = _SLIPPAGE_PCT,
        taker_fee: Decimal = _TAKER_FEE,
        maker_fee: Decimal = _MAKER_FEE,
        trade_memory: object | None = None,
        redis_client: object | None = None,
        multi_tf: object | None = None,
        crypto_analyst: object | None = None,
        trade_store: object | None = None,
    ) -> None:
        self._analyzer = analyzer
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
        self._crypto_analyst = crypto_analyst
        self._trade_store = trade_store

        # Compose edge families
        self._score_composer = ScoreComposer(families=[
            TrendContinuation(),
            MeanReversion(),
            CarryDislocation(),
            LiquidationSqueeze(),
            EventBreakout(),
        ])

        # Sizing pipeline
        self._sizing = SizingPipeline(
            trade_memory=trade_memory,
            redis_client=redis_client,
        )

        # Regime hysteresis
        self._prev_regimes: dict[str, MarketRegime] = {}
        self._regime_transition_counts: dict[str, int] = {}

    def set_wallet_balance(self, balance: Decimal) -> None:
        """Update wallet balance for position sizing."""
        self._wallet_balance = balance

    async def evaluate(
        self,
        symbol: str,
        candles: list[list] | np.ndarray,
        global_prices: dict[str, float] | None = None,
        orderbook_delta: float | None = None,
        funding_rate: float | None = None,
        oi_change: float | None = None,
        cvd_slope: float | None = None,
        liquidity_walls: dict[str, float | None] | None = None,
    ) -> TradeSignal | None:
        """Run full decision pipeline on candle data.

        This is the refactored evaluate method that delegates to:
        1. Pre-checks (ranking gate, volatility floor, session block)
        2. Feature extraction
        3. Regime classification
        4. Score composition (via edge families)
        5. Gate check
        6. Sizing
        7. Signal building
        """
        from app.core import metrics

        metrics.signals_pipeline_attempted.labels(symbol=symbol).inc()
        metrics.funnel_universe_scanned.inc()
        t_start = time.perf_counter()
        stage_timings = {}

        # --- PRE-CHECKS ---
        if len(candles) < _MIN_CANDLES:
            return None

        metrics.signals_entered_pipeline.labels(symbol=symbol).inc()

        # Ranking gate
        ranking_decision = await self._check_ranking_gate()
        if ranking_decision == "REJECT":
            return None

        # Volatility floor
        vol_floor_penalty = await self._check_volatility_floor(symbol)

        # Session block
        if await self._check_session_block(symbol):
            return None

        # --- FEATURE EXTRACTION ---
        arr = np.array(candles, dtype=np.float64) if isinstance(candles, list) else candles

        snapshot = MarketSnapshot(
            symbol=symbol,
            timestamp_ms=int(arr[-1][0]),
            candles=arr,
            global_prices=global_prices,
            orderbook_delta=orderbook_delta,
            funding_rate=funding_rate,
            oi_change=oi_change,
            cvd_slope=cvd_slope,
            liquidity_walls=liquidity_walls,
        )

        store = FeatureStore(snapshot)
        features = FeatureExtractor.extract(store)

        stage_timings["feature_extraction"] = time.perf_counter() - t_start

        # --- REGIME CLASSIFICATION ---
        market_state = self._analyzer.current_state
        regime = MarketRegime(market_state.regime)

        # Regime hysteresis
        regime = self._apply_regime_hysteresis(symbol, regime)

        stage_timings["regime_classification"] = time.perf_counter() - t_start

        # --- DIRECTION DETERMINATION ---
        directions = self._determine_directions(regime)

        # --- SCORE COMPOSITION & GATE CHECK ---
        best_signal: TradeSignal | None = None

        for direction in directions:
            t_score = time.perf_counter()

            # Score composition via edge families
            composed = await self._score_composer.compose(
                symbol=symbol,
                direction=direction,
                regime=regime,
                features=features,
                snapshot=snapshot,
                redis_client=self._redis,
                router=self._router,
                multi_tf=self._multi_tf,
                arr=arr,
                funding_rate=funding_rate,
                oi_change=oi_change,
            )

            stage_timings["strategy_scoring"] = time.perf_counter() - t_score

            if composed.total_score == 0:
                continue

            # Apply volatility floor penalty
            score = composed.total_score * vol_floor_penalty

            # Get dynamic gate threshold
            base_gate = await self._get_dynamic_gate_threshold()
            effective_gate = base_gate

            # Dip buyer boost
            if len(arr) >= 48:
                close_now = float(arr[-1][4])
                close_48h = float(arr[-48][4])
                if close_48h > 0:
                    move_48h = (close_now - close_48h) / close_48h
                    if move_48h > 0.15:
                        high_48h = max(float(arr[i][2]) for i in range(-48, 0))
                        dip_from_high = (high_48h - close_now) / high_48h
                        if 0.05 <= dip_from_high <= 0.10:
                            effective_gate *= 0.90

            # Gate check
            if score < effective_gate:
                ObservabilityLogger.log_reject_reason(
                    symbol, "Low Score",
                    {"score": score, "gate": effective_gate, "direction": direction}
                )
                continue

            # --- SIZING ---
            profile = self._risk_gate.get_profile(regime)
            entry_price = self._calculate_entry_price(arr, direction)
            sl_price = self._calculate_sl_price(entry_price, arr, direction, profile)
            atr = self._calculate_atr(arr)

            sizing = await self._sizing.calculate(
                symbol=symbol,
                wallet_balance=self._wallet_balance,
                entry_price=entry_price,
                sl_price=sl_price,
                score=score,
                profile=profile,
                session_mult=composed.metadata.get("session_mult", 1.0),
            )

            # --- BUILD SIGNAL ---
            signal = self._build_signal(
                symbol=symbol,
                direction=direction,
                regime=regime,
                score=score,
                arr=arr,
                entry_price=entry_price,
                sl_price=sl_price,
                atr=atr,
                sizing=sizing,
                profile=profile,
                composed=composed,
                stage_timings=stage_timings,
            )

            # Track best EV
            if best_signal is None or signal.expected_value > best_signal.expected_value:
                best_signal = signal

        return best_signal

    async def _check_ranking_gate(self) -> str:
        """Read cached ranking decision from Redis.

        Returns plain string: 'PROMOTE', 'NEEDS_MORE_EVIDENCE', or 'REJECT'.
        Normalizes any emoji-decorated strings to plain values.
        """
        if self._redis is None:
            return "PROMOTE"
        try:
            raw = await self._redis.get("karsa:ranking:decision")
            if raw is None:
                return "PROMOTE"
            decision = raw.decode() if isinstance(raw, bytes) else str(raw)
            # Normalize: strip emoji and whitespace
            decision = decision.replace("✅", "").replace("❌", "").replace("⚠️", "").strip()
            # Map to standard values
            if "PROMOTE" in decision.upper():
                return "PROMOTE"
            elif "REJECT" in decision.upper():
                return "REJECT"
            else:
                return "NEEDS_MORE_EVIDENCE"
        except Exception:
            return "PROMOTE"

    async def _check_volatility_floor(self, symbol: str) -> float:
        """Check volatility floor and return penalty multiplier."""
        if self._redis is None:
            return 1.0
        try:
            import json as _json
            vol_floor_raw = await self._redis.get("system:vol:floor:threshold")
            if vol_floor_raw:
                vol_floor = _json.loads(vol_floor_raw)
                threshold = vol_floor.get("threshold", 0)
                current_btc_atr = vol_floor.get("current_atr", 0)
                if threshold > 0 and current_btc_atr > 0 and current_btc_atr < threshold:
                    return 0.5
        except Exception:
            pass
        return 1.0

    async def _check_session_block(self, symbol: str) -> bool:
        """Check session block and return True if blocked."""
        try:
            from app.core.config import get_settings
            settings = get_settings()
            now_utc = datetime.now(UTC)
            current_hour = now_utc.hour
            if settings.session_block_enabled:
                if settings.session_block_start_hour <= current_hour < settings.session_block_end_hour:
                    if not settings.session_block_allow_btc_eth or symbol not in ("BTC/USDT", "ETH/USDT"):
                        return True
        except Exception:
            pass
        return False

    def _apply_regime_hysteresis(self, symbol: str, regime: MarketRegime) -> MarketRegime:
        """Apply regime hysteresis (require 2 consecutive readings to switch)."""
        prev_regime = self._prev_regimes.get(symbol)
        if prev_regime is not None and regime != prev_regime:
            self._regime_transition_counts[symbol] = self._regime_transition_counts.get(symbol, 0) + 1
            if self._regime_transition_counts[symbol] < 2:
                regime = prev_regime
            else:
                self._regime_transition_counts.pop(symbol, None)
        else:
            self._regime_transition_counts.pop(symbol, None)
        self._prev_regimes[symbol] = regime
        return regime

    def _determine_directions(self, regime: MarketRegime) -> list[str]:
        """Determine which directions to evaluate based on regime."""
        if regime in (MarketRegime.TREND_BULL, MarketRegime.HYPER_BULL):
            return ["LONG"]
        if regime in (MarketRegime.TREND_BEAR, MarketRegime.HYPER_BEAR):
            return ["SHORT"]
        return ["LONG", "SHORT"]

    async def _get_dynamic_gate_threshold(self) -> float:
        """Read dynamic gate threshold calibrated from historical EV."""
        if self._redis is None:
            return float(self._gate)
        try:
            import json as _json
            raw = await self._redis.get("karsa:gate:dynamic_threshold")
            if raw:
                data = _json.loads(raw)
                dynamic = data.get("threshold", float(self._gate))
                return max(50.0, min(90.0, dynamic))
        except Exception:
            pass
        return float(self._gate)

    def _calculate_entry_price(self, arr: np.ndarray, direction: str) -> Decimal:
        """Calculate entry price with slippage."""
        close = Decimal(str(arr[-1][4]))
        if direction == "LONG":
            return close * (Decimal("1") + self._slippage)
        else:
            return close * (Decimal("1") - self._slippage)

    def _calculate_sl_price(
        self,
        entry_price: Decimal,
        arr: np.ndarray,
        direction: str,
        profile: RiskProfile,
    ) -> Decimal:
        """Calculate stop loss price."""
        atr = self._calculate_atr(arr)
        sl_buffer = profile.sl_atr_buffer
        if direction == "LONG":
            return entry_price - (atr * sl_buffer)
        else:
            return entry_price + (atr * sl_buffer)

    @staticmethod
    def _calculate_atr(arr: np.ndarray, period: int = 14) -> Decimal:
        """ATR(14) via Wilder smoothing."""
        highs = arr[:, 2]
        lows = arr[:, 3]
        closes = arr[:, 4]

        if len(highs) < period + 1:
            return Decimal("0")

        prev_closes = np.roll(closes, 1)
        prev_closes[0] = closes[0]

        tr = np.maximum(
            highs - lows,
            np.maximum(
                np.abs(highs - prev_closes),
                np.abs(lows - prev_closes),
            ),
        )
        tr = tr[1:]

        atr = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr = (atr * (period - 1) + tr[i]) / period

        return Decimal(str(atr))

    def _build_signal(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        score: float,
        arr: np.ndarray,
        entry_price: Decimal,
        sl_price: Decimal,
        atr: Decimal,
        sizing: Any,
        profile: RiskProfile,
        composed: Any,
        stage_timings: dict[str, float],
    ) -> TradeSignal:
        """Build TradeSignal from pipeline outputs."""
        close = Decimal(str(arr[-1][4]))
        ts_ms = int(arr[-1][0])

        # TP price
        if profile.take_profit_type == "TRAILING":
            tp_price: Decimal | None = None
        else:
            offset = atr * profile.trail_atr_mult
            if direction == "LONG":
                tp_price = entry_price + offset
            else:
                tp_price = entry_price - offset

        # Entry fee rate
        entry_fee_rate = self._maker_fee if profile.use_post_only else self._taker_fee

        # Extract features for signal
        cvd_slope = 0.0
        spread_bps = 0.0
        atr_pct = 0.0

        # EV computation
        try:
            p_win = min(score / 100.0, 0.95)
            p_loss = 1.0 - p_win
            risk_dist = abs(float(entry_price - sl_price))
            if tp_price is not None:
                reward_dist = abs(float(tp_price - entry_price))
            else:
                reward_dist = float(atr) * 2.0
            avg_win = reward_dist / entry_price if entry_price > 0 else 0.0
            avg_loss = risk_dist / entry_price if entry_price > 0 else 0.0
            ev = (p_win * avg_win) - (p_loss * avg_loss)
        except Exception:
            ev = 0.0

        return TradeSignal(
            symbol=symbol,
            direction=direction,
            regime=regime,
            score=score,
            risk_profile=profile,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            amount=sizing.amount,
            entry_fee_rate=entry_fee_rate,
            atr=atr,
            timestamp_ms=ts_ms,
            candles=arr.tolist(),
            trace_id=uuid.uuid4().hex,
            vol_factor=composed.metadata.get("vol_factor", 1.0),
            session_mult=sizing.session_mult,
            spread_bps=spread_bps,
            cvd_slope=cvd_slope,
            atr_pct=atr_pct,
            regime_encoded=regime.encode(),
            expected_value=ev,
            winning_family=composed.winning_family,
            stage_timings=stage_timings,
        )
