"""Backtest Engine — deterministic candle-by-candle strategy replay.

Reuses RegimeClassifier, StrategyRouter, DynamicRiskGate directly.
No AI layer. No Redis dependency. All state is in-memory.
Mirrors ShadowAPM's worst_price_seen / funding / fee logic exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
from loguru import logger

from app.alpha.regime_classifier import MarketRegime, RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.risk.dynamic_risk_gate import DynamicRiskGate, RiskProfile

FUNDING_INTERVAL_BARS: int = 8  # every 8 candles (1h candles)


@dataclass(frozen=True)
class BacktestReport:
    """Result of a single backtest run for one symbol + direction."""

    symbol: str
    direction: str
    regime: MarketRegime
    score: float
    entry_price: Decimal
    exit_price: Decimal | None
    exit_reason: str | None
    sl_price: Decimal | None
    tp_price: Decimal | None
    amount: Decimal
    size_multiplier: Decimal
    pnl_gross: Decimal
    pnl_net: Decimal
    total_fees: Decimal
    total_funding: Decimal
    bars_held: int
    entry_time: datetime | None
    exit_time: datetime | None
    risk_profile: RiskProfile
    trade_taken: bool


class BacktestEngine:
    """Deterministic backtest engine.

    Candle-by-candle replay with APM simulation (worst_price_seen,
    funding drag, time exits). Reuses StrategyRouter, RegimeClassifier,
    DynamicRiskGate directly.
    """

    def __init__(
        self,
        regime_classifier: RegimeClassifier,
        strategy_router: StrategyRouter,
        risk_gate: DynamicRiskGate,
        base_size: Decimal = Decimal("0.001"),
        slippage_pct: Decimal = Decimal("0.0005"),
        taker_fee_pct: Decimal = Decimal("0.00055"),
        maker_fee_pct: Decimal = Decimal("0.0002"),
        funding_rate: Decimal = Decimal("0"),
        funding_interval_bars: int = FUNDING_INTERVAL_BARS,
        strategy_gate_threshold: float = 65.0,
    ) -> None:
        self._classifier = regime_classifier
        self._router = strategy_router
        self._risk_gate = risk_gate
        self._base_size = base_size
        self._slippage_pct = slippage_pct
        self._taker_fee = taker_fee_pct
        self._maker_fee = maker_fee_pct
        self._funding_rate = funding_rate
        self._funding_interval = funding_interval_bars
        self._gate = strategy_gate_threshold

    async def run(
        self,
        symbol: str,
        candles: list[list],
        job_id: str = "",
        global_prices: dict | None = None,
        orderbook_delta: float | None = None,
        funding_rate: float | None = None,
        oi_change: float | None = None,
    ) -> list[BacktestReport]:
        """Run backtest for a single symbol over historical candles.

        Returns list of BacktestReport — one per direction tested.
        """
        if len(candles) < 50:
            logger.debug(f"BacktestEngine: {symbol} — only {len(candles)} candles, need 50")
            return []

        arr = np.array(candles, dtype=float)
        reports: list[BacktestReport] = []

        # Lower gate when microstructure data is missing — candle-only scoring
        # cannot reach the normal 65 threshold (CHOP max=20, TREND max=60).
        has_microstructure = orderbook_delta is not None or funding_rate is not None or oi_change is not None
        effective_gate = self._gate if has_microstructure else self._gate * 0.45  # 65 → 29.25
        if not has_microstructure:
            logger.debug(f"BacktestEngine: {symbol} — no microstructure data, lowered gate to {effective_gate:.1f}")

        idx = 50  # minimum candles for RegimeClassifier

        while idx < len(arr):
            context = arr[: idx + 1]
            regime = self._classifier.classify(context)
            directions = self._determine_directions(regime)

            for direction in directions:
                score, vol_factor = self._router.evaluate_signal(
                    context, regime, direction,
                    global_prices=global_prices,
                    orderbook_delta=orderbook_delta,
                    funding_rate=funding_rate,
                    oi_change=oi_change,
                )

                if score >= effective_gate:
                    report = self._simulate_trade(
                        symbol=symbol,
                        direction=direction,
                        regime=regime,
                        score=score,
                        entry_candle_idx=idx,
                        candles=arr,
                    )
                    reports.append(report)
                    idx += max(report.bars_held, 1) + 1
                    break
                else:
                    reports.append(
                        BacktestReport(
                            symbol=symbol,
                            direction=direction,
                            regime=regime,
                            score=score,
                            entry_price=Decimal("0"),
                            exit_price=None,
                            exit_reason=None,
                            sl_price=None,
                            tp_price=None,
                            amount=Decimal("0"),
                            size_multiplier=Decimal("0"),
                            pnl_gross=Decimal("0"),
                            pnl_net=Decimal("0"),
                            total_fees=Decimal("0"),
                            total_funding=Decimal("0"),
                            bars_held=0,
                            entry_time=None,
                            exit_time=None,
                            risk_profile=self._risk_gate.get_profile(regime),
                            trade_taken=False,
                        )
                    )

            # If no trade was taken, advance 1 bar
            # "not scored" means zero directions were tested (empty directions list)
            # "no trade" means directions were tested but all scored below gate
            trade_taken = any(
                r.trade_taken
                for r in reports[-len(directions):]
            ) if directions else False
            if not trade_taken:
                idx += 1

        return reports

    def _determine_directions(self, regime: MarketRegime) -> list[str]:
        if regime in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR):
            return ["LONG"] if regime == MarketRegime.TREND_BULL else ["SHORT"]
        return ["LONG", "SHORT"]

    def _simulate_trade(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        score: float,
        entry_candle_idx: int,
        candles: np.ndarray,
    ) -> BacktestReport:
        """Simulate a single trade candle-by-candle from entry to exit."""
        profile = self._risk_gate.get_profile(regime)
        entry_candle = candles[entry_candle_idx]
        entry_price = self._compute_entry_price(entry_candle, direction)

        atr = self._calculate_atr(candles[: entry_candle_idx + 1])
        sl_price = self._compute_sl_price(entry_price, direction, atr, profile.sl_atr_buffer)
        tp_price = self._compute_tp_price(entry_price, direction, atr, profile.trail_atr_mult, profile.take_profit_type)

        worst_price = entry_price
        peak_price = entry_price
        accumulated_funding = Decimal("0")
        last_funding_bar = entry_candle_idx
        bars_held = 0
        ts_ms = entry_candle[0]
        entry_time = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

        entry_fee_rate = self._maker_fee if profile.use_post_only else self._taker_fee

        risk_distance = abs(entry_price - sl_price)
        amount = self._base_size if risk_distance <= 0 else (self._base_size * Decimal("100") * profile.size_multiplier / risk_distance)

        for j in range(entry_candle_idx + 1, len(candles)):
            bars_held += 1
            candle_high = Decimal(str(candles[j, 2]))
            candle_low = Decimal(str(candles[j, 3]))
            candle_close = Decimal(str(candles[j, 4]))

            # Update worst price (wick detection)
            if direction == "LONG":
                worst_price = min(worst_price, candle_low)
            else:
                worst_price = max(worst_price, candle_high)

            # Update peak (trailing TP)
            if direction == "LONG":
                peak_price = max(peak_price, candle_high)
            else:
                peak_price = min(peak_price, candle_low)

            # SL hit via worst_price
            sl_hit = (direction == "LONG" and worst_price <= sl_price) or (
                direction == "SHORT" and worst_price >= sl_price
            )
            if sl_hit:
                return self._build_report(
                    symbol, direction, regime, score,
                    entry_price, sl_price, "sl_hit", sl_price,
                    amount, profile, entry_fee_rate,
                    entry_time, candles[j, 0], bars_held,
                    accumulated_funding,
                )

            # TP hit
            current_tp = tp_price
            if current_tp is not None:
                tp_hit = (direction == "LONG" and candle_high >= current_tp) or (
                    direction == "SHORT" and candle_low <= current_tp
                )
                if tp_hit:
                    return self._build_report(
                        symbol, direction, regime, score,
                        entry_price, current_tp, "tp_hit", sl_price,
                        amount, profile, entry_fee_rate,
                        entry_time, candles[j, 0], bars_held,
                        accumulated_funding,
                    )

            # Funding (every 8 bars)
            if j - last_funding_bar >= self._funding_interval and self._funding_rate > 0:
                funding_fee = entry_price * amount * abs(self._funding_rate)
                accumulated_funding += funding_fee
                last_funding_bar = j

            # Time exit
            max_hold_bars = profile.max_hold_time_mins // 60
            if max_hold_bars > 0 and bars_held >= max_hold_bars:
                return self._build_report(
                    symbol, direction, regime, score,
                    entry_price, candle_close, "time_exit", sl_price,
                    amount, profile, entry_fee_rate,
                    entry_time, candles[j, 0], bars_held,
                    accumulated_funding,
                )

            # End of data
            if j == len(candles) - 1:
                return self._build_report(
                    symbol, direction, regime, score,
                    entry_price, candle_close, "end_of_data", sl_price,
                    amount, profile, entry_fee_rate,
                    entry_time, candles[j, 0], bars_held,
                    accumulated_funding,
                )

        return self._build_report(
            symbol, direction, regime, score,
            entry_price, Decimal("0"), "end_of_data", sl_price,
            amount, profile, entry_fee_rate,
            entry_time, ts_ms, 0,
            accumulated_funding,
        )

    def _compute_entry_price(self, candle: np.ndarray, direction: str) -> Decimal:
        close = Decimal(str(candle[4]))
        if direction == "LONG":
            return close * (1 + self._slippage_pct)
        return close * (1 - self._slippage_pct)

    def _compute_sl_price(
        self, entry_price: Decimal, direction: str, atr: Decimal, sl_buffer: Decimal
    ) -> Decimal:
        if direction == "LONG":
            return entry_price - (atr * sl_buffer)
        return entry_price + (atr * sl_buffer)

    def _compute_tp_price(
        self, entry_price: Decimal, direction: str, atr: Decimal,
        trail_mult: Decimal, tp_type: str,
    ) -> Decimal | None:
        if tp_type == "TRAILING":
            return None
        offset = atr * trail_mult
        if direction == "LONG":
            return entry_price + offset
        return entry_price - offset

    def _build_report(
        self, symbol, direction, regime, score,
        entry_price, exit_price, exit_reason, sl_price,
        amount, profile, entry_fee_rate,
        entry_time, exit_ts_ms, bars_held,
        accumulated_funding,
    ) -> BacktestReport:
        exit_time = datetime.fromtimestamp(exit_ts_ms / 1000, tz=UTC)

        if direction == "LONG":
            pnl_gross = (exit_price - entry_price) * amount
        else:
            pnl_gross = (entry_price - exit_price) * amount

        entry_fee = entry_price * amount * entry_fee_rate
        exit_fee = exit_price * amount * self._taker_fee
        total_fees = entry_fee + exit_fee
        pnl_net = pnl_gross - total_fees - accumulated_funding

        return BacktestReport(
            symbol=symbol, direction=direction, regime=regime, score=score,
            entry_price=entry_price, exit_price=exit_price, exit_reason=exit_reason,
            sl_price=sl_price, tp_price=None, amount=amount,
            size_multiplier=profile.size_multiplier,
            pnl_gross=pnl_gross, pnl_net=pnl_net,
            total_fees=total_fees, total_funding=accumulated_funding,
            bars_held=bars_held, entry_time=entry_time, exit_time=exit_time,
            risk_profile=profile, trade_taken=True,
        )

    @staticmethod
    def _calculate_atr(candles: np.ndarray, period: int = 14) -> Decimal:
        n = len(candles)
        if n < period + 1:
            return Decimal("0")
        highs, lows, closes = candles[:, 2], candles[:, 3], candles[:, 4]
        trs = []
        for i in range(1, n):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period:
            return Decimal("0")
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return Decimal(str(round(atr, 8)))
