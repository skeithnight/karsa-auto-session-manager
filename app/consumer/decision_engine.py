"""Shared decision pipeline — regime → strategy score → risk gate → TradeSignal.

Used identically by karsa-live and karsa-shadow. Both modes share this
module and only diverge at execution (SmartOrderRouter vs ShadowExecutor).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np

from app.alpha.ev_scorer import EVScorer
from app.alpha.ev_threshold import DynamicThreshold
from app.alpha.evidence_collector import EvidenceCollector
from app.alpha.market_analyzer import MarketAnalyzer
from app.alpha.regime_classifier import MarketRegime
from app.alpha.rejected_signal_tracker import RejectedSignalTracker
from app.alpha.sector_filter import SectorRotationFilter
from app.alpha.strategy_router import StrategyRouter
from app.core.decision_context import DecisionContext
from app.core.feature_extractor import FeatureExtractor
from app.core.feature_store import FeatureStore
from app.core.market_snapshot import MarketSnapshot
from app.core.observability import ObservabilityLogger
from app.learning.expected_edge import ExpectedEdgeCalculator
from app.learning.similarity_engine import SimilarityEngine
from app.learning.statistical_learning import StatisticalLearning
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
    expected_value: float = 0.0  # EV = P(win) * avg_win - P(loss) * avg_loss

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

        # Phase 1: EV Composite Scoring (parallel path for A/B comparison)
        self._ev_scorer = EVScorer()
        self._ev_threshold = DynamicThreshold()
        self._rejected_tracker = RejectedSignalTracker(redis_client)

        self._router = StrategyRouter(
            volatility_scaling=True,
            collector=self._evidence_collector,
            edge_calculator=self._edge_calculator
        )

    def set_wallet_balance(self, balance: Decimal) -> None:
        """Update wallet balance for position sizing."""
        self._wallet_balance = balance

    async def _check_ranking_gate(self) -> str:
        """Read cached ranking decision from Redis.

        Returns 'PROMOTE', 'NEEDS_MORE_EVIDENCE', or 'REJECT'.
        Fail-open: returns 'PROMOTE' if Redis is down or no ranking exists.
        """
        if self._redis is None:
            return "PROMOTE"
        try:
            raw = await self._redis.get("karsa:ranking:decision")
            if raw is None:
                return "PROMOTE"
            return raw.decode() if isinstance(raw, bytes) else str(raw)
        except Exception:
            return "PROMOTE"

    async def _get_dynamic_gate_threshold(self) -> float:
        """Read dynamic gate threshold calibrated from historical EV.

        Uses rolling average of winning trade EVs to set threshold.
        Falls back to static _GATE_THRESHOLD if no historical data.
        Formula: threshold = median(EV of winning trades) * 0.8
        Ensures threshold stays within [50.0, 90.0] bounds.
        """
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

    async def _get_strategy_elo(self, strategy: str) -> float:
        """Read ELO rating for a strategy from Redis.

        Returns ELO rating (default 1500.0 for new strategies).
        """
        if self._redis is None:
            return 1500.0
        try:
            import json as _json
            raw = await self._redis.get(f"karsa:elo:{strategy}")
            if raw:
                data = _json.loads(raw)
                return data.get("elo", 1500.0)
        except Exception:
            pass
        return 1500.0

    async def _get_risk_pct(self) -> Decimal:
        """Read risk_pct from Redis karsa:auto:config, karsa:settings:risk_pct, or Settings."""
        if self._redis is not None:
            try:
                import json as _json

                raw = await self._redis.get("karsa:auto:config")
                if raw:
                    cfg = _json.loads(raw)
                    if "risk_pct" in cfg:
                        return Decimal(str(cfg["risk_pct"])) / Decimal("100")

                settings_risk = await self._redis.get("karsa:settings:risk_pct")
                if settings_risk:
                    return Decimal(str(settings_risk)) / Decimal("100")
            except Exception:
                pass
        from app.core.config import get_settings

        _s = get_settings()
        return Decimal(str(getattr(_s, "risk_per_trade_pct", "0.10")))

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

        # ─── RANKING GATE (Strategy Promotion) ──────────────────────────
        # If RankingEngine has rejected this strategy, skip evaluation entirely.
        # Fail-open: PROMOTE if Redis is down or no ranking computed yet.
        ranking_decision = await self._check_ranking_gate()
        if ranking_decision == "REJECT":
            logger.debug("skip %s — RankingEngine REJECT", symbol)
            await self._track_rejection(symbol, "LONG", "ranking_reject", regime=regime if 'regime' in dir() else None)
            return None
        # ─────────────────────────────────────────────────────────────────

        # ─── VOLATILITY FLOOR (Dead Market Kill Switch) ──────────────────
        # Apply 0.5x penalty to EV/confidence when BTC 1H ATR is below the 10th percentile
        # of its 90-day rolling distribution.
        vol_floor_penalty = 1.0
        if self._redis is not None:
            try:
                import json as _json
                vol_floor_raw = await self._redis.get("system:vol:floor:threshold")
                if vol_floor_raw:
                    vol_floor = _json.loads(vol_floor_raw)
                    threshold = vol_floor.get("threshold", 0)
                    current_btc_atr = vol_floor.get("current_atr", 0)
                    if threshold > 0 and current_btc_atr > 0 and current_btc_atr < threshold:
                        logger.warning(
                            "ENVIRONMENTAL PENALTY: %s BTC volatility below 10th percentile "
                            "(current=%.4f < threshold=%.4f). Applying 0.5x EV penalty.",
                            symbol, current_btc_atr, threshold,
                        )
                        ObservabilityLogger.log_reject_reason(
                            symbol, "Volatility Floor Penalty (0.5x)",
                            {"current_btc_atr": current_btc_atr, "threshold": threshold}
                        )
                        vol_floor_penalty = 0.5
            except Exception as e:
                logger.debug("Volatility floor check failed for %s: %s", symbol, e)
        # ─────────────────────────────────────────────────────────────────

        # ─── SESSION HARD-BLOCK (Asian Dead Zone) ────────────────────────
        # Block altcoin entries during low-liquidity Asian session.
        # Cash is a position — the system should sleep during dead zones.
        now_utc = datetime.now(UTC)
        current_hour = now_utc.hour
        try:
            from app.core.config import get_settings
            _s = get_settings()
            if _s.session_block_enabled and _s.session_block_start_hour <= current_hour < _s.session_block_end_hour:
                # Allow BTC/ETH and screened dynamic universe candidates (ACCUMULATION / MOMENTUM_SQUEEZE)
                is_screened_candidate = False
                if self._redis:
                    try:
                        raw_univ = await self._redis.get("system:universe:symbols")
                        if raw_univ:
                            u_data = _json.loads(raw_univ)
                            is_screened_candidate = symbol in u_data.get("symbols", [])
                    except Exception:
                        pass

                if not is_screened_candidate and (not _s.session_block_allow_btc_eth or symbol not in ("BTC/USDT", "ETH/USDT")):
                    logger.warning(
                        "SESSION BLOCK: %s rejected — Asian session low liquidity (hour=%d, block=%d-%d timezone.utc). Altcoin entries blocked.",
                        symbol, current_hour, _s.session_block_start_hour, _s.session_block_end_hour,
                    )
                    ObservabilityLogger.log_reject_reason(
                        symbol, "Session Block",
                        {"hour": current_hour, "block_start": _s.session_block_start_hour, "block_end": _s.session_block_end_hour}
                    )
                    await self._track_rejection(symbol, "LONG", "session_block")
                    return None
        except Exception as e:
            logger.debug("Session block check failed for %s: %s", symbol, e)
        # ─────────────────────────────────────────────────────────────────

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
                            await self._track_rejection(symbol, "LONG", "spread_balloon", price=Decimal(str(arr[-1][4])) if 'arr' in dir() else None)
                            return None
            except Exception as e:
                logger.debug("Spread balloon check failed for %s: %s", symbol, e)

        # Feature extraction uses the snapshot built above (with all fields including cvd_slope, liquidity_walls)
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

        # ─── PHASE 1: EV COMPOSITE SCORING (primary decision path) ──────
        # Compute EV score for both directions — drives trade decisions
        ev_scores: dict[str, tuple[float, float]] = {}  # direction -> (ev_score, threshold)
        for dir_name in ["LONG", "SHORT"]:
            try:
                ev_score, ev_threshold = await self._compute_ev_score(
                    symbol=symbol,
                    direction=dir_name,
                    regime=regime,
                    features=features,
                    funding_rate=funding_rate,
                    oi_change=oi_change,
                    multi_tf_agrees=True,  # Will be refined per-direction
                    conviction=0.5,
                )
                ev_scores[dir_name] = (ev_score, ev_threshold)
            except Exception as e:
                logger.debug("EV scoring failed for %s %s: %s", symbol, dir_name, e)
        # ─────────────────────────────────────────────────────────────────

        # Update Sector Rotation returns (4H return)
        if len(arr) >= 4:
            close_now = float(arr[-1][4])
            close_4h = float(arr[-4][4])
            if close_4h > 0:
                self._sector_filter.update_sector_returns({symbol: (close_now - close_4h) / close_4h})

        # Step 2: Determine directions (regime-dependent)
        directions = self._determine_directions(regime)

        # Step 3: Score each direction, rank by EV (Phase 2: Find Edge)
        best_signal: TradeSignal | None = None
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

            # Sector Rotation Scoring: bonus/penalty based on sector momentum
            sector_score = self._sector_filter.get_sector_score(symbol, direction)
            if sector_score != 1.0:
                logger.info(
                    "evaluate: %s %s SECTOR_SCORE %.2fx (sector=%s)",
                    symbol, direction, sector_score, sec_res.get("sector"),
                )

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
                ev_sc, ev_th = ev_scores.get(direction, (0.0, 0.55))
                if ev_sc < ev_th:
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
            # Fetch regime conviction from Redis (written by classification loop)
            conviction = 1.0
            if self._redis is not None:
                try:
                    _conv_key = f"system:regime:{symbol.replace('/', ':')}:conviction"
                    raw_conv = await self._redis.get(_conv_key)
                    if raw_conv is not None:
                        conviction = float(raw_conv)
                except Exception:
                    pass
            context, vol_factor = await self._router.evaluate_signal(
                features=features,
                regime=regime,
                direction=direction,
                symbol=symbol,
                conviction=conviction,
            )
            stage_timings["strategy_scoring"] = time.perf_counter() - t_score
            metrics.pipeline_stage_latency_seconds.labels(stage="strategy_scoring").observe(stage_timings["strategy_scoring"])

            if context is None:
                continue

            # Apply Statistical Learning (Fatigue & Calibration)
            t_stat = time.perf_counter()
            await self._statistical_learning.calibrate(context)
            stage_timings["statistical_learning"] = time.perf_counter() - t_stat
            metrics.pipeline_stage_latency_seconds.labels(stage="statistical_learning").observe(stage_timings["statistical_learning"])

            score = context.total_confidence * vol_floor_penalty

            # P24: Adaptive Symbol Performance Multiplier from TradeMemory.
            # Penalizes toxic symbols (<30% win rate → 0.7x) and boosts golden symbols (>60% → 1.2x).
            # Requires 10+ historical trades for statistical significance.
            if self._trade_memory is not None:
                try:
                    symbol_mult = await self._trade_memory.get_symbol_performance_multiplier(symbol)
                    if symbol_mult != 1.0:
                        logger.info(
                            "evaluate: %s %s applying symbol performance multiplier %.1fx (score %.1f -> %.1f)",
                            symbol, direction, symbol_mult, score, score * symbol_mult,
                        )
                        score *= symbol_mult
                except Exception:
                    logger.debug("evaluate: symbol performance multiplier failed for %s", symbol)



            # Correlation-Based Sizing: read correlation data from PRM's rolling correlation check
            # and adjust score based on how many open positions are correlated
            corr_penalty = 1.0
            if self._redis is not None:
                try:
                    import json as _json
                    corr_raw = await self._redis.get(f"karsa:correlation:{symbol}")
                    if corr_raw:
                        corr_data = _json.loads(corr_raw)
                        max_corr = corr_data.get("max_correlation", 0.0)
                        corr_count = corr_data.get("correlated_count", 0)
                        if corr_count >= 2 and max_corr > 0.80:
                            corr_penalty = 0.5  # Hard penalty for 2+ correlated
                        elif corr_count == 1 and max_corr > 0.80:
                            corr_penalty = 0.75  # Mild penalty for 1 correlated
                        if corr_penalty < 1.0:
                            logger.info(
                                "evaluate: %s %s CORR_SIZING penalty %.2fx (corr=%.2f, count=%d) → score %.1f",
                                symbol, direction, corr_penalty, max_corr, corr_count, score * corr_penalty,
                            )
                            score *= corr_penalty
                except Exception:
                    pass

            # Momentum Exemption: Do not penalize explosive gainers for their volatility
            if momentum_exemption:
                logger.info(
                    "evaluate: %s %s has momentum exemption, bypassing volatility gate (vol_factor %.2f -> 1.0)",
                    symbol,
                    direction,
                    vol_factor,
                )
                vol_factor = 1.0

            # Apply Macro Penalty (e.g. 0.8x if fighting macro trend)
            score = score * macro_penalty

            # --- Sprint 1: Funding Rate Carry Strategy ---
            # If funding is extremely negative for 3+ periods, add carry bonus
            if self._redis is not None:
                try:
                    from app.core.config import get_settings
                    _s = get_settings()
                    carry_bonus, carry_reason = await self._router.evaluate_carry_signal(
                        symbol=symbol,
                        direction=direction,
                        redis_client=self._redis,
                        config=_s,
                    )
                    if carry_bonus > 0:
                        score += carry_bonus
                        logger.info(
                            "evaluate: %s %s CARRY BONUS +%d (%s) → score=%.1f",
                            symbol, direction, carry_bonus, carry_reason, score,
                        )
                except Exception as e:
                    logger.debug(f"evaluate: carry signal failed for {symbol}: {e}")

            # --- Sprint 1: Funding Term Structure Scoring ---
            # Squeeze imminent signals from term structure (current vs predicted funding)
            if self._redis is not None:
                try:
                    term_signal = await self._redis.get(f"karsa:market:{symbol}:funding_term_signal")
                    if term_signal:
                        term_signal = term_signal.decode() if isinstance(term_signal, bytes) else str(term_signal)
                        if term_signal == "SQUEEZE_IMMINENT_LONG" and direction == "LONG":
                            score += 25
                            logger.info(
                                "evaluate: %s %s FUNDING TERM STRUCTURE: SQUEEZE_IMMINENT_LONG +25 → score=%.1f",
                                symbol, direction, score,
                            )
                        elif term_signal == "SQUEEZE_IMMINENT_SHORT" and direction == "SHORT":
                            score += 25
                            logger.info(
                                "evaluate: %s %s FUNDING TERM STRUCTURE: SQUEEZE_IMMINENT_SHORT +25 → score=%.1f",
                                symbol, direction, score,
                            )
                except Exception as e:
                    logger.debug(f"evaluate: funding term structure check failed for {symbol}: {e}")

            # --- Sprint 2: Liquidation Heatmap Signal ---
            # Detect liquidation cascade zones from OI delta patterns
            if self._redis is not None:
                try:
                    from app.core.config import get_settings
                    _s2 = get_settings()
                    liq_bonus, liq_reason = await self._router.evaluate_liquidation_heatmap(
                        symbol=symbol,
                        direction=direction,
                        redis_client=self._redis,
                        config=_s2,
                    )
                    if liq_bonus > 0:
                        score += liq_bonus
                        logger.info(
                            "evaluate: %s %s LIQ_HEATMAP BONUS +%d (%s) → score=%.1f",
                            symbol, direction, liq_bonus, liq_reason, score,
                        )
                except Exception as e:
                    logger.debug(f"evaluate: liquidation heatmap failed for {symbol}: {e}")

            # --- Sprint 2: Cross-Asset Momentum Signal ---
            # Check if BTC, ETH, and altcoins are aligned in the same direction
            if self._redis is not None:
                try:
                    xa_bonus, xa_reason = await self._router.evaluate_cross_asset_momentum(
                        symbol=symbol,
                        direction=direction,
                        redis_client=self._redis,
                        config=_s2 if '_s2' in dir() else None,
                    )
                    if xa_bonus > 0:
                        score += xa_bonus
                        logger.info(
                            "evaluate: %s %s CROSS_ASSET BONUS +%d (%s) → score=%.1f",
                            symbol, direction, xa_bonus, xa_reason, score,
                        )
                except Exception as e:
                    logger.debug(f"evaluate: cross-asset momentum failed for {symbol}: {e}")

            # --- Sprint 2: Token Unlock Calendar Filter ---
            # Penalize tokens with upcoming large token unlocks (sell pressure)
            if self._redis is not None:
                try:
                    unlock_penalty = await self._check_token_unlock(symbol, direction)
                    if unlock_penalty > 0:
                        score -= unlock_penalty
                        logger.info(
                            "evaluate: %s %s TOKEN_UNLOCK PENALTY -%d → score=%.1f",
                            symbol, direction, unlock_penalty, score,
                        )
                except Exception as e:
                    logger.debug(f"evaluate: token unlock check failed for {symbol}: {e}")

            # --- Sprint 3: HMM Regime Prediction Signal ---
            # Read HMM state + probabilities from Redis (published by HMMRegimeClassifier loop)
            if self._redis is not None:
                try:
                    from app.core.config import get_settings
                    _s3 = get_settings()
                    hmm_raw = await self._redis.get(_s3.hmm_redis_key)
                    if hmm_raw:
                        import json as _json
                        hmm_data = _json.loads(hmm_raw)
                        hmm_signal = hmm_data.get("signal")
                        hmm_confidence = hmm_data.get("confidence", 0.0)
                        hmm_probs = hmm_data.get("probabilities", {})

                        if hmm_signal == "HMM_BREAKOUT_IMMINENT" and direction == "LONG":
                            # Scale bonus by confidence: 100% conf = full bonus, 50% = half
                            scaled_bonus = _s3.hmm_score_breakout_bonus * hmm_confidence
                            score += scaled_bonus
                            logger.info(
                                "evaluate: %s %s HMM_BREAKOUT_IMMINENT +%d (conf=%.2f, scaled=%.1f) → score=%.1f",
                                symbol, direction, _s3.hmm_score_breakout_bonus,
                                hmm_confidence, scaled_bonus, score,
                            )
                        elif hmm_signal == "HMM_CHOP_IMMINENT":
                            scaled_penalty = _s3.hmm_score_chop_penalty * hmm_confidence
                            score -= scaled_penalty
                            logger.info(
                                "evaluate: %s %s HMM_CHOP_IMMINENT -%d (conf=%.2f, scaled=%.1f) → score=%.1f",
                                symbol, direction, _s3.hmm_score_chop_penalty,
                                hmm_confidence, scaled_penalty, score,
                            )

                        # Regime confidence bonus/penalty: HIGH_VOL with high confidence
                        # boosts volatile regime strategies, LOW_VOL with high confidence
                        # penalizes trend strategies
                        if hmm_probs and hmm_confidence > 0.6:
                            high_vol_prob = hmm_probs.get("HIGH_VOL", 0.0)
                            low_vol_prob = hmm_probs.get("LOW_VOL", 0.0)
                            if high_vol_prob > 0.7 and direction == "SHORT":
                                # High-confidence volatile regime = good for shorts
                                conf_bonus = 10.0 * high_vol_prob
                                score += conf_bonus
                                logger.info(
                                    "evaluate: %s %s HMM_CONF_BONUS (HIGH_VOL=%.2f) +%.1f → score=%.1f",
                                    symbol, direction, high_vol_prob, conf_bonus, score,
                                )
                            elif low_vol_prob > 0.7 and direction == "LONG":
                                # High-confidence low vol = accumulation, bullish
                                conf_bonus = 8.0 * low_vol_prob
                                score += conf_bonus
                                logger.info(
                                    "evaluate: %s %s HMM_CONF_BONUS (LOW_VOL=%.2f) +%.1f → score=%.1f",
                                    symbol, direction, low_vol_prob, conf_bonus, score,
                                )
                except Exception as e:
                    logger.debug(f"evaluate: HMM signal check failed for {symbol}: {e}")

            # Session / Time-of-Day Volatility Filtering
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

            # Use dynamic threshold calibrated from historical EV
            base_gate = await self._get_dynamic_gate_threshold()
            effective_gate = base_gate * vol_factor

            # Apply sector rotation score
            score *= sector_score

            # --- Dip Buyer Boost (Phase 3) ---
            # Strong performers (+15% in 48h) in dip zone (-5% to -10% from high)
            # get a 10% gate reduction (still passes all risk checks)
            dip_buy_boost = False
            if len(arr) >= 48:
                close_now = float(arr[-1][4])
                close_48h = float(arr[-48][4])
                if close_48h > 0:
                    move_48h = (close_now - close_48h) / close_48h
                    if move_48h > 0.15:  # +15% in 48h = strong performer
                        # Check if current price is in dip zone (-5% to -10% from 48h high)
                        high_48h = max(float(arr[i][2]) for i in range(-48, 0))  # high prices
                        dip_from_high = (high_48h - close_now) / high_48h
                        if 0.05 <= dip_from_high <= 0.10:
                            dip_buy_boost = True
                            effective_gate *= 0.90  # 10% gate reduction
                            logger.info(
                                "evaluate: %s DIP BUY candidate — %.1f%% pullback from 48h high, gate reduced to %.1f",
                                symbol, dip_from_high * 100, effective_gate,
                            )

            from app.data.filters import AssetQualityFilter
            if AssetQualityFilter().is_blacklisted(symbol):
                logger.warning("evaluate: %s REJECTED — blacklisted low asset quality", symbol)
                continue

            logger.debug(
                "evaluate: %s %s score=%.1f (gate=%.1f vol=%.2f dip_buy=%s)",
                symbol,
                direction,
                score,
                effective_gate,
                vol_factor,
                dip_buy_boost,
            )

            # ─── DUAL-GATE CONFLUENCE (StrategyRouter + EVScorer) ───────────
            # High-conviction entry requires BOTH:
            # 1. Deterministic StrategyRouter score >= effective_gate (>= 70 default)
            # 2. Statistical EV score >= ev_threshold
            ev_score, ev_threshold = ev_scores.get(direction, (0.0, 0.55))
            ev_passed = ev_score >= ev_threshold if ev_scores else True
            strategy_passed = score >= effective_gate

            # Overextension Guard: Reject parabolic chase at exhaustion
            rsi_val = features.rsi_14 if features.rsi_14 is not None else 50.0
            is_overextended = False
            if direction == "LONG" and rsi_val > 78.0 and regime == MarketRegime.TREND_BULL:
                is_overextended = True
                logger.warning("evaluate: %s LONG rejected — overextended RSI(14)=%.1f > 78.0", symbol, rsi_val)
            elif direction == "SHORT" and rsi_val < 22.0 and regime == MarketRegime.TREND_BEAR:
                is_overextended = True
                logger.warning("evaluate: %s SHORT rejected — overextended RSI(14)=%.1f < 22.0", symbol, rsi_val)

            if is_overextended:
                await self._track_rejection(symbol, direction, "overextended_rsi", regime=regime, price=Decimal(str(arr[-1][4])))
                continue

            if strategy_passed and ev_passed:
                decision_source = "dual_confluence"
                logger.info(
                    "evaluate: %s %s DUAL CONFLUENCE PASSED: score=%.1f >= gate=%.1f AND ev=%.3f >= threshold=%.3f",
                    symbol, direction, score, effective_gate, ev_score, ev_threshold,
                )
            else:
                decision_source = "rejected"
                logger.info(
                    "evaluate: %s %s REJECTED: score=%.1f (gate=%.1f passed=%s), ev=%.3f (threshold=%.3f passed=%s)",
                    symbol, direction, score, effective_gate, strategy_passed, ev_score, ev_threshold, ev_passed,
                )
                await self._track_rejection(
                    symbol, direction, "low_score",
                    regime=regime, price=Decimal(str(arr[-1][4])),
                )
                continue

            if decision_source != "rejected":
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

                # Track best EV signal instead of returning first pass
                if best_signal is None or signal.expected_value > best_signal.expected_value:
                    logger.info(
                        "evaluate: %s %s NEW BEST EV=%.4f (score=%.1f) — was %.4f",
                        symbol, direction, signal.expected_value, score,
                        best_signal.expected_value if best_signal else 0.0,
                    )
                    best_signal = signal
                else:
                    logger.info(
                        "evaluate: %s %s EV=%.4f < best=%.4f — skipped",
                        symbol, direction, signal.expected_value, best_signal.expected_value,
                    )

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

                # Don't return yet — continue to check other direction for higher EV

        # Return highest-EV signal across all directions (Phase 2: Find Edge)
        if best_signal is not None:
            logger.info(
                "evaluate: %s BEST SIGNAL %s EV=%.4f score=%.1f",
                symbol, best_signal.direction, best_signal.expected_value, best_signal.score,
            )
        return best_signal

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

    async def _check_token_unlock(self, symbol: str, direction: str) -> int:
        """Sprint 2: Check if token has upcoming large unlock (sell pressure).

        Reads unlock schedule from Redis (populated by data engine or external feed).
        If unlock is within window and impact is significant, returns penalty score.

        Returns:
            Penalty score (0 if no unlock concern).
        """
        if self._redis is None:
            return 0

        try:
            from datetime import datetime, timedelta

            from app.core.config import get_settings

            settings = get_settings()
            unlock_window = timedelta(hours=settings.unlock_window_hours)
            impact_threshold = float(settings.unlock_impact_threshold_pct)
            penalty = settings.unlock_penalty_score

            # Check Redis for unlock schedule
            unlock_key = f"karsa:unlock:{symbol}"
            raw_unlock = await self._redis.get(unlock_key)
            if not raw_unlock:
                return 0

            import json as _json
            unlock_data = _json.loads(raw_unlock)

            unlock_time_str = unlock_data.get("unlock_time")
            unlock_pct = unlock_data.get("unlock_pct_of_supply", 0)

            if not unlock_time_str or unlock_pct < impact_threshold:
                return 0

            unlock_time = datetime.fromisoformat(unlock_time_str)
            # Make timezone-aware if naive
            if unlock_time.tzinfo is None:
                unlock_time = unlock_time.replace(tzinfo=UTC)
            now = datetime.now(UTC)

            # Check if unlock is within window
            time_to_unlock = unlock_time - now
            if timedelta(0) <= time_to_unlock <= unlock_window:
                logger.info(
                    "TOKEN_UNLOCK: %s unlock in %.1fh (%.2f%% supply) — penalty %d",
                    symbol,
                    time_to_unlock.total_seconds() / 3600,
                    unlock_pct * 100,
                    penalty,
                )
                return penalty

            return 0

        except Exception as e:
            logger.debug(f"Token unlock check failed for {symbol}: {e}")
            return 0

    def _determine_directions(self, regime: MarketRegime) -> list[str]:
        """Determine which directions to evaluate based on regime."""
        if regime in (MarketRegime.TREND_BULL, MarketRegime.HYPER_BULL):
            return ["LONG"]
        if regime in (MarketRegime.TREND_BEAR, MarketRegime.HYPER_BEAR):
            return ["SHORT"]
        return ["LONG", "SHORT"]

    async def _compute_ev_score(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        features: object,
        funding_rate: float | None,
        oi_change: float | None,
        multi_tf_agrees: bool,
        conviction: float,
    ) -> tuple[float, str]:
        """Phase 1: Compute EV score using EVScorer.

        Returns:
            (ev_score, threshold) — both floats for comparison with existing score.
        """

        # Extract feature values
        rsi = float(features.rsi) if hasattr(features, 'rsi') and features.rsi is not None else 50.0
        macd_hist = float(features.macd_hist) if hasattr(features, 'macd_hist') and features.macd_hist is not None else 0.0
        ema_dist = float(features.ema_distance) if hasattr(features, 'ema_distance') and features.ema_distance is not None else 0.0
        skew = float(features.skew) if hasattr(features, 'skew') and features.skew is not None else 0.0
        cvd_slope = float(features.cvd_slope) if hasattr(features, 'cvd_slope') and features.cvd_slope is not None else 0.0
        depth_ratio = float(features.depth_ratio) if hasattr(features, 'depth_ratio') and features.depth_ratio is not None else 1.0
        spread_pct = float(features.spread_pct) if hasattr(features, 'spread_pct') and features.spread_pct is not None else 0.001
        close_price = float(features.close) if hasattr(features, 'close') and features.close is not None else 0.0

        # Get historical win rate from trade memory
        historical_win_rate = 0.5
        if self._trade_memory is not None:
            try:
                recent = await self._trade_memory.get_recent(symbol, count=20)
                if recent:
                    wins = sum(1 for t in recent if t.get("pnl_pct", 0) > 0)
                    historical_win_rate = wins / len(recent)
            except Exception:
                pass

        # Get dynamic threshold
        drawdown_pct = 0.0
        if self._redis is not None:
            try:
                raw_peak = await self._redis.get("global:state:equity_peak")
                if raw_peak and self._wallet_balance > 0:
                    equity_peak = float(raw_peak)
                    if equity_peak > 0:
                        drawdown_pct = (equity_peak - float(self._wallet_balance)) / equity_peak
            except Exception:
                pass

        hour_utc = datetime.now(UTC).hour
        threshold = await self._ev_threshold.get_threshold(
            redis_client=self._redis,
            drawdown_pct=drawdown_pct,
            recent_win_rate=historical_win_rate,
            hour_utc=hour_utc,
            regime=regime.value if hasattr(regime, "value") else str(regime),
        )

        # Compute EV score
        ev_score, components = self._ev_scorer.score(
            regime=regime.value,
            direction=direction,
            spread_pct=spread_pct,
            rsi=rsi,
            macd_hist=macd_hist,
            ema_dist=ema_dist,
            skew=skew,
            cvd_slope=cvd_slope,
            depth_ratio=depth_ratio,
            funding_rate=funding_rate if funding_rate is not None else 0.0,
            multi_tf_agrees=multi_tf_agrees,
            historical_win_rate=historical_win_rate,
            regime_conviction=conviction,
            oi_change_pct=oi_change if oi_change is not None else 0.0,
            hour_utc=hour_utc,
        )

        # Apply CEX vs DEX price divergence adjustment (Phase A on-chain alpha signal)
        if self._redis is not None and close_price > 0:
            try:
                raw_onchain = await self._redis.get(f"onchain:symbol:{symbol}")
                if raw_onchain:
                    onchain_data = json.loads(raw_onchain)
                    dex_price = float(onchain_data.get("dex_price", 0))
                    if dex_price > 0:
                        ev_score = self._ev_scorer.apply_dex_divergence_adjustment(
                            raw_ev=ev_score,
                            cex_price=close_price,
                            dex_price=dex_price,
                            direction=direction,
                        )
            except Exception as e:
                logger.warning(f"DecisionEngine: DEX divergence check failed for {symbol}: {e}")

        logger.debug(
            "EVScorer: %s %s ev=%.3f threshold=%.3f components=%s",
            symbol, direction, ev_score, threshold, components,
        )

        return ev_score, threshold

    async def _track_rejection(
        self,
        symbol: str,
        direction: str,
        reject_reason: str,
        regime: MarketRegime | None = None,
        price: object = None,
    ) -> None:
        """Track rejected signal with hypothetical EV for post-hoc analysis."""
        ev_scores_local = getattr(self, '_ev_scores_cache', {})
        ev_score = ev_scores_local.get(direction, (0.0, 0.0))[0]
        try:
            await self._rejected_tracker.track(
                symbol=symbol,
                direction=direction,
                reject_reason=reject_reason,
                hypothetical_ev=ev_score,
                regime=regime.value if regime else None,
                price=price,
            )
        except Exception:
            pass

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
                walls = context.features.liquidity_walls or {}
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
            # P16: Feed real trade history to KellySizer for adaptive position sizing.
            # Previously wins=0/losses=0 was always passed, making Kelly dead code.
            kelly_wins = 0
            kelly_losses = 0
            kelly_avg_win = 0.0
            kelly_avg_loss = 0.0
            if self._trade_memory is not None:
                try:
                    recent = await self._trade_memory.get_recent(symbol, count=30)
                    if recent:
                        win_pnls = [t["pnl_pct"] for t in recent if t.get("pnl_pct", 0) > 0]
                        loss_pnls = [abs(t["pnl_pct"]) for t in recent if t.get("pnl_pct", 0) < 0]
                        kelly_wins = len(win_pnls)
                        kelly_losses = len(loss_pnls)
                        kelly_avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
                        kelly_avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
                except Exception:
                    logger.debug("_build_signal: Kelly trade memory read failed for %s", symbol)
            scaled_risk_pct = kelly_sizer.calculate_risk_pct(
                wins=kelly_wins, losses=kelly_losses,
                avg_win_usd=kelly_avg_win, avg_loss_usd=kelly_avg_loss,
                fallback_score=score,
            )

            # --- Sprint 1: Drawdown-Adaptive Sizing (Anti-Martingale) ---
            # Track equity peak and adjust Kelly based on drawdown state
            try:
                if self._redis is not None:
                    from app.core.config import get_settings
                    _s = get_settings()

                    # Read/update equity peak
                    raw_peak = await self._redis.get("global:state:equity_peak")
                    equity_peak = Decimal(str(raw_peak)) if raw_peak else Decimal("0")

                    current_equity = Decimal(str(self._wallet_balance))

                    # Update peak if current equity is higher
                    if current_equity > equity_peak:
                        equity_peak = current_equity
                        await self._redis.set("global:state:equity_peak", str(equity_peak))

                    if equity_peak > 0 and current_equity > 0:
                        # Apply drawdown-adaptive multiplier
                        dd_severe = Decimal(_s.dd_severe_threshold)
                        dd_moderate = Decimal(_s.dd_moderate_threshold)
                        dd_near_peak = Decimal(_s.dd_near_peak_threshold)
                        dd_severe_mult = Decimal(_s.dd_severe_mult)
                        dd_moderate_mult = Decimal(_s.dd_moderate_mult)
                        dd_near_peak_mult = Decimal(_s.dd_near_peak_mult)

                        drawdown = (equity_peak - current_equity) / equity_peak

                        if drawdown > dd_severe:
                            multiplier = dd_severe_mult
                            logger.warning(
                                "DD-ADAPTIVE: SEVERE dd=%.1f%% → %.2fx sizing",
                                float(drawdown * 100), float(multiplier),
                            )
                        elif drawdown > dd_moderate:
                            multiplier = dd_moderate_mult
                            logger.info(
                                "DD-ADAPTIVE: MODERATE dd=%.1f%% → %.2fx sizing",
                                float(drawdown * 100), float(multiplier),
                            )
                        elif drawdown < dd_near_peak:
                            multiplier = dd_near_peak_mult
                            logger.info(
                                "DD-ADAPTIVE: NEAR PEAK dd=%.1f%% → %.2fx sizing (house money)",
                                float(drawdown * 100), float(multiplier),
                            )
                        else:
                            multiplier = Decimal("1.0")

                        scaled_risk_pct = (scaled_risk_pct * multiplier).quantize(Decimal("0.0001"))
                        scaled_risk_pct = max(Decimal("0.005"), min(Decimal("0.020"), scaled_risk_pct))
            except Exception as e:
                logger.debug(f"DD-ADAPTIVE: failed for {symbol}: {e}")

            # --- Sprint 2: Half-Kelly with Uncertainty ---
            # Reduce position size when Kelly estimate is unreliable
            try:
                win_rate_history = []
                if self._trade_memory is not None:
                    try:
                        recent = await self._trade_memory.get_recent(symbol, count=20)
                        if recent:
                            # Calculate rolling win rates
                            wins_so_far = 0
                            for i, t in enumerate(recent):
                                if t.get("pnl_pct", 0) > 0:
                                    wins_so_far += 1
                                win_rate_history.append(wins_so_far / (i + 1))
                    except Exception:
                        pass

                scaled_risk_pct = kelly_sizer.apply_uncertainty_adjustment(
                    base_risk_pct=scaled_risk_pct,
                    wins=kelly_wins,
                    losses=kelly_losses,
                    win_rate_history=win_rate_history,
                )
            except Exception as e:
                logger.debug(f"UNCERTAINTY: failed for {symbol}: {e}")

            # --- Sprint 3: GARCH Volatility Targeting ---
            # Adjust position size based on GARCH volatility forecast vs historical
            try:
                forecasted_vol = None
                historical_vol = None
                if self._redis is not None:
                    garch_raw = await self._redis.get("system:garch:forecast")
                    if garch_raw:
                        import json as _json
                        garch_data = _json.loads(garch_raw)
                        forecasted_vol = garch_data.get("forecasted_vol")
                        historical_vol = garch_data.get("historical_vol")

                scaled_risk_pct = kelly_sizer.apply_volatility_targeting(
                    base_risk_pct=scaled_risk_pct,
                    forecasted_vol=forecasted_vol,
                    historical_vol=historical_vol,
                )
            except Exception as e:
                logger.debug(f"GARCH VOL TARGETING: failed for {symbol}: {e}")

            # --- Conviction Scaling (Phase 1) ---
            # Read conviction from Redis and multiply with Kelly result
            # Weak regimes → smaller positions, strong regimes → full size
            conviction = 1.0
            if self._redis is not None:
                try:
                    import json as _json
                    regime_raw = await self._redis.get("system:config:regime")
                    if regime_raw:
                        regime_data = _json.loads(regime_raw)
                        conviction = regime_data.get("conviction", 1.0)
                except Exception:
                    logger.debug("_build_signal: conviction read failed, using 1.0")

            # CONVICTION FLOOR: Reject signals in weak/ambiguous regimes entirely.
            # Low conviction = choppy market = noise trades = losses.
            # This is the #1 win rate improvement lever.
            CONVICTION_FLOOR = 0.35
            if conviction < CONVICTION_FLOOR:
                logger.info(
                    "evaluate: %s REJECTED — conviction=%.3f < floor %.3f (regime too weak)",
                    symbol, conviction, CONVICTION_FLOOR,
                )
                return None

            scaled_risk_pct *= Decimal(str(conviction))
            logger.info(
                "evaluate: %s conviction=%.3f → scaled_risk_pct=%.6f (after conviction)",
                symbol, conviction, float(scaled_risk_pct),
            )

            # ─── MACRO NARRATOR SIZING ─────────────────────────────────────
            # Apply global macro multiplier from AI Macro Narrator (4-hour cycle).
            # CHOP=0.5x, RISK_OFF=0.25x, RISK_ON=1.0x.
            try:
                from app.alpha.macro_narrator import get_macro_multiplier
                macro_mult = await get_macro_multiplier(self._redis)
                if macro_mult != 1.0:
                    scaled_risk_pct *= Decimal(str(macro_mult))
                    logger.info(
                        "evaluate: %s MACRO NARRATOR %.2fx → scaled_risk_pct=%.6f",
                        symbol, macro_mult, float(scaled_risk_pct),
                    )
            except Exception as e:
                logger.debug("Macro narrator multiplier failed for %s: %s", symbol, e)
            # ───────────────────────────────────────────────────────────────

            amount = (
                self._wallet_balance
                * scaled_risk_pct
                * profile.size_multiplier
                * Decimal(str(session_mult))
                / risk_distance
            )
            # Rational minimum position floor ($45 USDT) for small accounts so fee drag does not dominate
            min_notional_floor = min(Decimal("45.0"), self._wallet_balance * Decimal("0.55"))
            if entry_price > 0:
                min_amount = min_notional_floor / entry_price
                amount = max(amount, min_amount)

            # Cap notional to max_single_position_pct of equity (PRM single position limit)
            from app.core.config import get_settings

            _cfg = get_settings()
            max_single_pct = Decimal(str(getattr(_cfg, "max_single_position_pct", "0.60")))
            max_notional = self._wallet_balance * max_single_pct
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

        # --- EV Computation (Phase 2: Find Edge) ---
        # EV = P(win) * avg_win - P(loss) * avg_loss
        # P(win) proxy: score / 100 (confidence as probability)
        # Reward/Risk: use SL/TP distances from ATR-based levels
        try:
            p_win = min(score / 100.0, 0.95)  # cap at 95%
            p_loss = 1.0 - p_win
            risk_dist = abs(float(entry_price - sl_price)) if sl_price else float(atr)
            if tp_price is not None:
                reward_dist = abs(float(tp_price - entry_price))
            else:
                reward_dist = float(atr) * 2.0  # TRAILING: assume 2x ATR target
            avg_win = reward_dist / float(entry_price) if entry_price > 0 else 0.0
            avg_loss = risk_dist / float(entry_price) if entry_price > 0 else 0.0
            ev = (p_win * avg_win) - (p_loss * avg_loss)
        except Exception as e:
            logger.debug(f"_build_signal EV calculation error: {e}")
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
            expected_value=ev,
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
