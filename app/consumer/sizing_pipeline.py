"""Sizing Pipeline — Standalone position sizing.

Extracted from DecisionEngine._build_signal() to:
1. Be independently testable
2. Be reusable across live/shadow/backtest
3. Allow easy replacement of individual sizing steps

The pipeline composes:
- Kelly fraction (from trade history)
- Drawdown adaptation (anti-martingale)
- Conviction scaling (regime confidence)
- Macro narrator multiplier
- Uncertainty adjustment (half-kelly)
- GARCH volatility targeting
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SizingResult:
    """Result from the sizing pipeline.

    Attributes:
        amount: Final position size in base currency.
        risk_pct: Final risk percentage.
        kelly_fraction: Kelly fraction from trade history.
        drawdown_mult: Drawdown adaptation multiplier.
        conviction_mult: Conviction scaling multiplier.
        macro_mult: Macro narrator multiplier.
        session_mult: Session volatility multiplier.
        uncertainty_factor: Half-kelly uncertainty adjustment.
        garch_factor: GARCH volatility targeting factor.
    """

    amount: Decimal
    risk_pct: Decimal
    kelly_fraction: float
    drawdown_mult: float
    conviction_mult: float
    macro_mult: float
    session_mult: float
    uncertainty_factor: float
    garch_factor: float


class SizingPipeline:
    """Standalone sizing pipeline.

    This class encapsulates all position sizing logic, making it:
    - Independently testable
    - Reusable across live/shadow/backtest
    - Easy to modify individual sizing steps
    """

    def __init__(
        self,
        trade_memory: Any = None,
        redis_client: Any = None,
    ) -> None:
        """Initialize the sizing pipeline.

        Args:
            trade_memory: TradeMemory instance for historical trade data.
            redis_client: Redis client for external data.
        """
        self._trade_memory = trade_memory
        self._redis = redis_client

    async def calculate(
        self,
        symbol: str,
        wallet_balance: Decimal,
        entry_price: Decimal,
        sl_price: Decimal,
        score: float,
        profile: Any,
        session_mult: float = 1.0,
    ) -> SizingResult:
        """Run full sizing pipeline.

        Args:
            symbol: Trading pair.
            wallet_balance: Current wallet balance.
            entry_price: Entry price.
            sl_price: Stop loss price.
            score: Signal score (0-100).
            profile: Risk profile from DynamicRiskGate.
            session_mult: Session volatility multiplier.

        Returns:
            SizingResult with final position size and components.
        """
        # 1. Kelly fraction
        kelly_fraction = await self._kelly_fraction(symbol, score)

        # 2. Drawdown adaptation
        drawdown_mult = await self._drawdown_multiplier()

        # 3. Conviction scaling
        conviction_mult = await self._conviction_multiplier()

        # 4. Macro narrator
        macro_mult = await self._macro_multiplier()

        # 5. Uncertainty adjustment
        uncertainty_factor = await self._uncertainty_adjustment(symbol)

        # 6. GARCH volatility targeting
        garch_factor = await self._garch_targeting()

        # Compose final risk_pct
        base_risk = Decimal("0.10") * Decimal(str(kelly_fraction))
        scaled = base_risk * drawdown_mult * conviction_mult * macro_mult
        scaled *= uncertainty_factor * garch_factor
        scaled *= profile.size_multiplier * Decimal(str(session_mult))

        # Cap at 2%
        scaled = max(Decimal("0.005"), min(Decimal("0.020"), scaled))

        # Calculate amount
        risk_distance = abs(entry_price - sl_price)
        if risk_distance > Decimal("0") and wallet_balance > 0:
            amount = wallet_balance * scaled / risk_distance
            # Cap at 40% notional
            max_amount = (wallet_balance * Decimal("0.40")) / entry_price
            amount = min(amount, max_amount)
        else:
            amount = Decimal("0.001")

        logger.info(
            "SizingPipeline: %s amount=%.6f risk_pct=%.6f "
            "(kelly=%.3f dd=%.2f conv=%.2f macro=%.2f uncer=%.2f garch=%.2f)",
            symbol, float(amount), float(scaled),
            kelly_fraction, float(drawdown_mult), float(conviction_mult),
            macro_mult, float(uncertainty_factor), float(garch_factor),
        )

        return SizingResult(
            amount=amount,
            risk_pct=scaled,
            kelly_fraction=kelly_fraction,
            drawdown_mult=float(drawdown_mult),
            conviction_mult=float(conviction_mult),
            macro_mult=macro_mult,
            session_mult=session_mult,
            uncertainty_factor=float(uncertainty_factor),
            garch_factor=float(garch_factor),
        )

    async def _kelly_fraction(self, symbol: str, score: float) -> float:
        """Calculate Kelly fraction from trade history."""
        from app.risk.kelly_sizer import KellySizer
        kelly = KellySizer()

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
                logger.debug("SizingPipeline: Kelly trade memory read failed for %s", symbol)

        return kelly.calculate_risk_pct(
            wins=kelly_wins,
            losses=kelly_losses,
            avg_win_usd=kelly_avg_win,
            avg_loss_usd=kelly_avg_loss,
            fallback_score=score,
        )

    async def _drawdown_multiplier(self) -> Decimal:
        """Calculate drawdown-adaptive multiplier."""
        if self._redis is None:
            return Decimal("1.0")

        try:
            from app.core.config import get_settings
            settings = get_settings()

            # Read/update equity peak
            raw_peak = await self._redis.get("global:state:equity_peak")
            equity_peak = Decimal(str(raw_peak)) if raw_peak else Decimal("0")

            # Read current balance (will be updated by caller)
            current_equity = Decimal("0")  # Placeholder

            # Update peak if current equity is higher
            if current_equity > equity_peak:
                equity_peak = current_equity
                await self._redis.set("global:state:equity_peak", str(equity_peak))

            if equity_peak > 0 and current_equity > 0:
                drawdown = (equity_peak - current_equity) / equity_peak

                dd_severe = Decimal(settings.dd_severe_threshold)
                dd_moderate = Decimal(settings.dd_moderate_threshold)
                dd_near_peak = Decimal(settings.dd_near_peak_threshold)
                dd_severe_mult = Decimal(settings.dd_severe_mult)
                dd_moderate_mult = Decimal(settings.dd_moderate_mult)
                dd_near_peak_mult = Decimal(settings.dd_near_peak_mult)

                if drawdown > dd_severe:
                    return dd_severe_mult
                elif drawdown > dd_moderate:
                    return dd_moderate_mult
                elif drawdown < dd_near_peak:
                    return dd_near_peak_mult
        except Exception as e:
            logger.debug("SizingPipeline: drawdown check failed: %s", e)

        return Decimal("1.0")

    async def _conviction_multiplier(self) -> Decimal:
        """Calculate conviction scaling multiplier."""
        if self._redis is None:
            return Decimal("1.0")

        try:
            import json as _json
            regime_raw = await self._redis.get("system:config:regime")
            if regime_raw:
                regime_data = _json.loads(regime_raw)
                conviction = regime_data.get("conviction", 1.0)
                return Decimal(str(conviction))
        except Exception:
            logger.debug("SizingPipeline: conviction read failed")

        return Decimal("1.0")

    async def _macro_multiplier(self) -> float:
        """Calculate macro narrator multiplier."""
        try:
            from app.alpha.macro_narrator import get_macro_multiplier
            return await get_macro_multiplier(self._redis)
        except Exception as e:
            logger.debug("SizingPipeline: macro narrator failed: %s", e)
        return 1.0

    async def _uncertainty_adjustment(self, symbol: str) -> Decimal:
        """Calculate half-kelly uncertainty adjustment."""
        from app.risk.kelly_sizer import KellySizer
        kelly = KellySizer()

        win_rate_history = []
        kelly_wins = 0
        kelly_losses = 0

        if self._trade_memory is not None:
            try:
                recent = await self._trade_memory.get_recent(symbol, count=20)
                if recent:
                    wins_so_far = 0
                    for i, t in enumerate(recent):
                        if t.get("pnl_pct", 0) > 0:
                            wins_so_far += 1
                        win_rate_history.append(wins_so_far / (i + 1))
                    kelly_wins = sum(1 for t in recent if t.get("pnl_pct", 0) > 0)
                    kelly_losses = len(recent) - kelly_wins
            except Exception:
                pass

        base_risk = Decimal("0.01")  # Placeholder
        return kelly.apply_uncertainty_adjustment(
            base_risk_pct=base_risk,
            wins=kelly_wins,
            losses=kelly_losses,
            win_rate_history=win_rate_history,
        )

    async def _garch_targeting(self) -> Decimal:
        """Calculate GARCH volatility targeting factor."""
        if self._redis is None:
            return Decimal("1.0")

        try:
            import json as _json
            garch_raw = await self._redis.get("system:garch:forecast")
            if garch_raw:
                garch_data = _json.loads(garch_raw)
                forecasted_vol = garch_data.get("forecasted_vol")
                historical_vol = garch_data.get("historical_vol")

                if forecasted_vol and historical_vol and historical_vol > 0:
                    # Target vol / realized vol
                    ratio = historical_vol / forecasted_vol
                    return Decimal(str(max(0.5, min(2.0, ratio))))
        except Exception as e:
            logger.debug("SizingPipeline: GARCH targeting failed: %s", e)

        return Decimal("1.0")
