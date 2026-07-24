"""Shared decision pipeline — regime → strategy score → risk gate → TradeSignal.

Used identically by karsa-live and karsa-shadow. Both modes share this
module and only diverge at execution (SmartOrderRouter vs ShadowExecutor).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from app.alpha.evidence_collector import EvidenceCollector
from app.alpha.regime_classifier import MarketRegime, RegimeClassifier
from app.alpha.market_analyzer import MarketAnalyzer
from app.alpha.strategy_router import StrategyRouter
from app.core.decision_context import DecisionContext
from app.core.feature_extractor import FeatureExtractor
from app.core.feature_store import FeatureStore
from app.core.market_snapshot import MarketSnapshot
from app.core.observability import ObservabilityLogger
from app.learning.expected_edge import ExpectedEdgeCalculator
from app.learning.similarity_engine import SimilarityEngine
from app.learning.statistical_learning import StatisticalLearning
from app.alpha.sector_filter import SectorRotationFilter
from app.risk.dynamic_risk_gate import DynamicRiskGate, RiskProfile
from app.risk.kelly_sizer import KellySizer

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
    vol_factor: float = 1.0
    session_mult: float = 1.0
    spread_bps: float = 0.0
    cvd_slope: float = 0.0
    atr_pct: float = 0.0
    regime_encoded: int = 0
    context: DecisionContext | None = None

    @property
    def confidence(self) -> float:
        """Normalized confidence score (0.0 - 1.0)."""
        return self.score / 100.0 if self.score > 1.0 else self.score
    stage_timings: dict[str, float] | None = None


class DecisionEngine:
    """Shared decision pipeline — evaluates candle data into TradeSignals.

    Composes RegimeClassifier, StrategyRouter, and DynamicRiskGate.
    Modes (live/shadow) use identical pipeline; execution layer handles divergence.
    """

    _CONSECUTIVE_LOSS_THRESHOLD: int = 3

    def __init__(
        self,
        analyzer: MarketAnalyzer,
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
        crypto_analyst: object | None = None,
        trade_store: object | None = None,
    ) -> None:
        self._analyzer = analyzer
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
        self._prev_regimes: dict[str, MarketRegime] = {}
        self._regime_transition_counts: dict[str, int] = {}
        self._similarity = SimilarityEngine()
        self._evidence_collector = EvidenceCollector(
            profile_path="config/confidence_profiles/default.yaml"
        )
        self._edge_calculator = ExpectedEdgeCalculator(
            similarity=self._similarity,
            trade_memory=trade_memory
        )
        self._statistical_learning = StatisticalLearning(trade_memory=trade_memory)
        self._sector_filter = SectorRotationFilter()
        self._background_tasks = set()

        self._router = StrategyRouter(
            volatility_scaling=True,
            collector=self._evidence_collector,
            edge_calculator=self._edge_calculator
        )

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
        cvd_slope: float | None = None,
        liquidity_walls: dict[str, float | None] | None = None,
    ) -> TradeSignal | None:
        """Run full decision pipeline on candle data.

        Args:
            symbol: Unified trading pair.
            candles: OHLCV data, oldest first. Minimum 50 rows.
            global_prices: Cross-exchange prices {binance: float, okx: float}.
            orderbook_delta: Orderbook imbalance (CHOP scoring).
            funding_rate: Current funding rate (CHOP scoring).
            oi_change: Open interest change (CHOP scoring).
            cvd_slope: Optional slope of cumulative volume delta.
            liquidity_walls: Optional dict of liquidity levels.

        Returns:
            TradeSignal if score >= gate threshold, else None.
        """
        import time

        from app.core import metrics

        metrics.signals_pipeline_attempted.labels(symbol=symbol).inc()
        metrics.funnel_universe_scanned.inc()
        t_start = time.perf_counter()
        stage_timings = {}

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

        # Build standardized market snapshot and features
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
                            ObservabilityLogger.log_reject_reason(symbol, "Spread Balloon Rejection", {"spread_pct": float(spread * 100)})
                            return None
            except Exception as e:
                logger.debug("Spread balloon check failed for %s: %s", symbol, e)

        # Build standardized market snapshot and features
        snapshot = MarketSnapshot(
            symbol=symbol,
            timestamp_ms=int(arr[-1][0]),
            candles=arr,
            global_prices=global_prices,
            orderbook_delta=orderbook_delta,
            funding_rate=funding_rate,
            oi_change=oi_change,
        )
        store = FeatureStore(snapshot)
        features = FeatureExtractor.extract(store)

        t_feat = time.perf_counter()
        stage_timings["feature_extraction"] = t_feat - t_start
        metrics.pipeline_stage_latency_seconds.labels(stage="feature_extraction").observe(stage_timings["feature_extraction"])

        # Step 1: Regime classification (Read immutable state)
        market_state = self._analyzer.current_state
        regime = MarketRegime(market_state.regime)

        t_regime = time.perf_counter()
        stage_timings["regime_classification"] = t_regime - t_feat
        metrics.pipeline_stage_latency_seconds.labels(stage="regime_classification").observe(stage_timings["regime_classification"])

        # Regime hysteresis: require 2 consecutive readings to switch regime
        _prev_regime = self._prev_regimes.get(symbol)
        if _prev_regime is not None and regime != _prev_regime:
            self._regime_transition_counts[symbol] = self._regime_transition_counts.get(symbol, 0) + 1
            if self._regime_transition_counts[symbol] < 2:
                regime = _prev_regime  # Stick with previous until confirmed
            else:
                self._regime_transition_counts.pop(symbol, None)  # Transition confirmed
                ObservabilityLogger.log_regime_transition(
                    old_regime=_prev_regime.value,
                    new_regime=regime.value,
                    duration_minutes=0.0
                )
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

        # Update Sector Rotation returns (4H return)
        if len(arr) >= 4:
            close_now = float(arr[-1][4])
            close_4h = float(arr[-4][4])
            if close_4h > 0:
                self._sector_filter.update_sector_returns({symbol: (close_now - close_4h) / close_4h})

        # Step 2: Determine directions (regime-dependent)
        directions = self._determine_directions(regime)

        # Step 3: Score each direction, take first pass
        for direction in directions:
            # Sector & Narrative Rotation Filter
            sec_res = self._sector_filter.check_sector_alignment(symbol, direction)
            if not sec_res.get("approved"):
                logger.info(
                    "evaluate: %s %s BLOCKED by Sector Rotation filter (%s)",
                    symbol, direction, sec_res.get("reason")
                )
                ObservabilityLogger.log_reject_reason(symbol, "Sector Rotation Filter", {"direction": direction, "reason": sec_res.get("reason")})
                continue

            # Extreme Funding Rate Block
            if funding_rate is not None:
                if direction == "LONG" and funding_rate > 0.0005:
                    logger.info(
                        "evaluate: %s LONG blocked due to extreme positive funding %.5f",
                        symbol,
                        funding_rate,
                    )
                    ObservabilityLogger.log_reject_reason(symbol, "Extreme Positive Funding", {"funding_rate": funding_rate, "direction": direction})
                    continue
                if direction == "SHORT" and funding_rate < -0.0005:
                    logger.info(
                        "evaluate: %s SHORT blocked due to extreme negative funding %.5f",
                        symbol,
                        funding_rate,
                    )
                    ObservabilityLogger.log_reject_reason(symbol, "Extreme Negative Funding", {"funding_rate": funding_rate, "direction": direction})
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
                    ObservabilityLogger.log_reject_reason(symbol, "Multi-Timeframe Filter", {"direction": direction})
                    continue

                # Momentum Exemption: if the token is up/down > 8% in 24h, it has detached from the macro trend.
                if len(arr) >= 24:
                    close_now = arr[-1][4]
                    close_24h_ago = arr[-24][4]
                    pct_change = (close_now - close_24h_ago) / close_24h_ago
                    if direction == "LONG" and pct_change > 0.08 or direction == "SHORT" and pct_change < -0.08:
                        momentum_exemption = True

                # Macro Anchor (Lead-Lag) Hard Block & Penalty
                if symbol not in ["BTC/USDT", "ETH/USDT"] and not momentum_exemption:
                    mom_block = await self._multi_tf.check_macro_momentum_block(symbol, direction)
                    if mom_block.get("blocked"):
                        logger.warning(
                            "evaluate: %s %s HARD BLOCKED by BTC/ETH Macro Momentum filter (%s)",
                            symbol, direction, mom_block.get("reason")
                        )
                        ObservabilityLogger.log_reject_reason(symbol, "Macro Momentum Hard Block", {"direction": direction, "reason": mom_block.get("reason")})
                        continue

                    macro_penalty = await self._multi_tf.get_macro_anchor_penalty(
                        direction
                    )

            metrics.signals_generated.labels(symbol=symbol, direction=direction).inc()
            metrics.funnel_raw_signals.inc()

            t_score = time.perf_counter()
            context, vol_factor = await self._router.evaluate_signal(
                features=features,
                regime=regime,
                direction=direction,
                symbol=symbol,
            )
            stage_timings["strategy_scoring"] = time.perf_counter() - t_score
            metrics.pipeline_stage_latency_seconds.labels(stage="strategy_scoring").observe(stage_timings["strategy_scoring"])

            # Apply Statistical Learning (Fatigue & Calibration)
            t_stat = time.perf_counter()
            await self._statistical_learning.calibrate(context)
            stage_timings["statistical_learning"] = time.perf_counter() - t_stat
            metrics.pipeline_stage_latency_seconds.labels(stage="statistical_learning").observe(stage_timings["statistical_learning"])

            score = context.total_confidence

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

            # Session / Time-of-Day Volatility Filtering
            from datetime import UTC, datetime
            now_utc = datetime.now(UTC)
            hour = now_utc.hour
            if 0 <= hour < 7:
                session_mult, session_name = 0.7, "ASIA"
            elif 7 <= hour < 12:
                session_mult, session_name = 1.0, "LONDON"
            elif 12 <= hour < 16:
                session_mult, session_name = 1.2, "LDN_NY_OVERLAP"
            elif 16 <= hour < 21:
                session_mult, session_name = 1.0, "NEW_YORK"
            else:
                session_mult, session_name = 0.8, "PACIFIC"

            logger.info(
                f"evaluate: {symbol} {direction} Session ({session_name}): "
                f"sizing_multiplier={session_mult}x (score={score:.1f} unpenalized)"
            )

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
                # Fire AI Shadow Scoring ONLY for signals that passed local pre-filter
                if self._crypto_analyst is not None and self._trade_store is not None:
                    import asyncio

                    async def _shadow_score_and_record():
                        try:
                            spread_pct = float(features.spread) if getattr(features, 'spread', None) is not None else 0.0
                            funding_rate = float(features.funding_rate) if getattr(features, 'funding_rate', None) is not None else 0.0
                            oi_change = float(features.oi_change_1h) if getattr(features, 'oi_change_1h', None) is not None else 0.0

                            logger.info("shadow_score_and_record: starting AI analysis for %s", symbol)
                            ai_result = await self._crypto_analyst.analyze(
                                symbol=symbol,
                                direction=direction,
                                confidence=score,
                                regime=regime.value,
                                spread_pct=spread_pct,
                                funding_rate=funding_rate,
                                oi_change=oi_change,
                                price=Decimal(str(arr[-1][4]))
                            )
                            logger.info("shadow_score_and_record: AI analysis complete for %s (passed)", symbol)

                            await self._trade_store.record_signal(
                                symbol=symbol,
                                direction=direction,
                                confidence_score=score,
                                alpha_metrics={"stage_timings": stage_timings},
                                risk_passed=True,
                                strategy_type="SWING",
                                ai_confidence_score=ai_result.ai_confidence if ai_result else None,
                                ai_reasoning=ai_result.reasoning if ai_result else None,
                                macro_context=None
                            )
                        except Exception as e:
                            logger.error("shadow_score_and_record failed for %s: %s", symbol, e, exc_info=True)

                    task = asyncio.create_task(_shadow_score_and_record())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

                if await self.check_consecutive_losses(symbol, regime):
                    return None

                metrics.signal_confidence_passed_total.labels(regime=regime.value).inc()
                metrics.funnel_alpha_passed.inc()
                signal = await self._build_signal(symbol, direction, regime, score, arr, context, session_mult)
                # Unfreeze briefly to set stage_timings
                object.__setattr__(signal, "stage_timings", stage_timings)

                metrics.decision_latency_seconds.labels(symbol=symbol, regime=regime.value).observe(time.perf_counter() - t_start)

                import time
                ObservabilityLogger.log_decision_trace(
                    strategy=regime.value,
                    confidence=score,
                    regime=regime.value,
                    evidence=[{"type": "score", "value": score}],
                    entry_decision=f"BUY_{direction}",
                    stage_timings=stage_timings,
                    symbol=symbol,
                    decision_id=f"dec-{int(time.time()*1000)}",
                )
                ObservabilityLogger.log_feature_snapshot(
                    symbol=symbol,
                    feature_vector=context.to_dict(),
                    market_snapshot={"close": float(arr[-1][4])}
                )

                return signal
            else:
                ObservabilityLogger.log_reject_reason(
                    symbol,
                    "Low Score",
                    {"score": score, "gate": effective_gate, "direction": direction}
                )

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
                from app.core import metrics
                metrics.consecutive_loss_detected_total.labels(symbol=symbol, streak_count=str(consecutive), regime=regime_str).inc()
                logger.warning(
                    "consecutive_loss_block: %s %d consecutive losses — REJECTING (cooldown)",
                    symbol,
                    consecutive,
                )
                ObservabilityLogger.log_reject_reason(symbol, "Consecutive Loss Block", {"losses": consecutive})
                from app.core import metrics
                metrics.signal_confidence_passed_total.labels(regime=regime_str)
                return True

            if breakeven_streak >= 4:
                logger.warning(
                    "breakeven_streak_block: %s %d consecutive breakevens/stagnation — REJECTING (choppy)",
                    symbol,
                    breakeven_streak,
                )
                ObservabilityLogger.log_reject_reason(symbol, "Breakeven Streak Block", {"breakevens": breakeven_streak})
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
        context: DecisionContext | None = None,
        session_mult: float = 1.0,
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

            # Dynamic TP at Liquidity Walls: front-run large orderbook walls
            if context and hasattr(context, "features") and getattr(context.features, "liquidity_walls", None):
                walls = getattr(context.features, "liquidity_walls") or {}
                wall_above = walls.get("wall_above")
                wall_below = walls.get("wall_below")

                if direction == "LONG" and wall_above is not None:
                    wall_dec = Decimal(str(wall_above)) * Decimal("0.998")  # 0.2% in front of wall
                    if entry_price < wall_dec < tp_price:
                        logger.info("Dynamic TP: LONG TP adjusted from %s to %s (front-running ask wall at %s)", tp_price, wall_dec, wall_above)
                        tp_price = wall_dec
                elif direction == "SHORT" and wall_below is not None:
                    wall_dec = Decimal(str(wall_below)) * Decimal("1.002")  # 0.2% in front of wall
                    if tp_price < wall_dec < entry_price:
                        logger.info("Dynamic TP: SHORT TP adjusted from %s to %s (front-running bid wall at %s)", tp_price, wall_dec, wall_below)
                        tp_price = wall_dec

        # Position size (balance-based risk allocation via Fractional Kelly Criterion)
        risk_distance = abs(entry_price - sl_price)
        if risk_distance <= Decimal("0"):
            amount = self._base_size
        elif self._wallet_balance > 0:
            kelly_sizer = KellySizer()
            # Calculate Fractional Kelly (25%) risk percentage
            scaled_risk_pct = kelly_sizer.calculate_risk_pct(
                wins=0, losses=0, avg_win_usd=0.0, avg_loss_usd=0.0, fallback_score=score
            )

            amount = (
                self._wallet_balance
                * scaled_risk_pct
                * profile.size_multiplier
                * Decimal(str(session_mult))
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
                * Decimal(str(session_mult))
                / risk_distance
            )

        # Entry fee rate (maker for post_only, taker otherwise)
        entry_fee_rate = self._maker_fee if profile.use_post_only else self._taker_fee

        import uuid

        cvd_slope = float(context.features.cvd_slope) if context and context.features and context.features.cvd_slope is not None else 0.0
        spread_bps = float(context.features.spread_pct) * 10000.0 if context and context.features and context.features.spread_pct is not None else 0.0
        atr_pct = float(context.features.atr_pct) if context and context.features and context.features.atr_pct is not None else 0.0
        vol_factor = float(context.evidence[-1].value) if context and context.evidence else 1.0 # Volatility factor is not strictly required but handled if present

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
            vol_factor=vol_factor,
            session_mult=session_mult,
            spread_bps=spread_bps,
            cvd_slope=cvd_slope,
            atr_pct=atr_pct,
            regime_encoded=regime.encode(),
            context=context,
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
