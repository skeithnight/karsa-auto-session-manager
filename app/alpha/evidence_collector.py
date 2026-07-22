"""Evidence Collector (Sprint 2).

Gathers evidence for a specific strategy and computes total confidence.
"""

from __future__ import annotations

import yaml
from loguru import logger

from app.core.decision_context import DecisionContext


class EvidenceCollector:
    """Collects evidence and applies weights based on confidence profiles."""

    def __init__(self, profile_path: str = "config/confidence_profiles/default.yaml") -> None:
        self.profile = self._load_profile(profile_path)

    def _load_profile(self, path: str) -> dict:
        try:
            with open(path) as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load confidence profile at {path}: {e}")
            return {"regimes": {}}

    def collect(self, context: DecisionContext) -> DecisionContext:
        """Evaluate features against the regime profile to gather evidence."""
        regime_str = context.regime.value
        regime_cfg = self.profile.get("regimes", {}).get(regime_str, {})

        context.base_confidence = regime_cfg.get("base_confidence", 0.0)
        context.total_confidence = context.base_confidence

        weights = regime_cfg.get("weights", {})

        # Trend / Momentum Evidence
        if "trend_alignment" in weights:
            rsi = context.features.rsi_14 or 50.0
            if context.direction == "LONG" and rsi > 50:
                context.add_evidence("trend_alignment", 1.0, weights["trend_alignment"], f"RSI > 50 ({rsi:.1f})")
            elif context.direction == "SHORT" and rsi < 50:
                context.add_evidence("trend_alignment", 1.0, weights["trend_alignment"], f"RSI < 50 ({rsi:.1f})")
            else:
                context.add_evidence("trend_alignment", 0.0, weights["trend_alignment"], "Counter-trend")

        if "momentum" in weights:
            adx = context.features.adx_14 or 0.0
            score = min(1.0, adx / 50.0) # Cap at 1.0
            context.add_evidence("momentum", score, weights["momentum"], f"ADX is {adx:.1f}")

        if "mean_reversion" in weights:
            rsi = context.features.rsi_14 or 50.0
            if context.direction == "LONG" and rsi < 40:
                context.add_evidence("mean_reversion", 1.0, weights["mean_reversion"], f"RSI Oversold ({rsi:.1f})")
            elif context.direction == "SHORT" and rsi > 60:
                context.add_evidence("mean_reversion", 1.0, weights["mean_reversion"], f"RSI Overbought ({rsi:.1f})")
            else:
                context.add_evidence("mean_reversion", 0.0, weights["mean_reversion"], "RSI Neutral")

        # Volatility expansion
        if "volatility_expansion" in weights:
            atr_pct = context.features.atr_pct or 50.0
            if atr_pct > 60.0:
                context.add_evidence("volatility_expansion", 1.0, weights["volatility_expansion"], f"ATR % > 60 ({atr_pct:.1f})")
            else:
                context.add_evidence("volatility_expansion", 0.0, weights["volatility_expansion"], "Volatility flat or contracting")

        # Range / Liquidity Sweep
        if "liquidity_sweep" in weights:
            # Placeholder for liquidity sweep (needs wick logic, simplify for now)
            close = context.features.close or 0.0
            sma = context.features.sma_20 or close
            if close != 0:
                diff_pct = abs(close - sma) / close
                if diff_pct > 0.02:
                    context.add_evidence("liquidity_sweep", 1.0, weights["liquidity_sweep"], "Far from SMA")
                else:
                    context.add_evidence("liquidity_sweep", 0.0, weights["liquidity_sweep"], "Near SMA")

        return context
