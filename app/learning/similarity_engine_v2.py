"""Similarity Engine V2 — Enhanced similarity matching.

Upgraded from V1 to include:
- Edge family in similarity matching
- Direction (LONG/SHORT) in similarity
- Holding time bucket
- Spread/liquidity state
- Minimum sample thresholds

This addresses quant trader review recommendation #3:
"Tighten the definition of expected value"
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class SimilarityEngineV2:
    """Enhanced similarity engine with family-aware matching.

    Computes similarity between market contexts including:
    - Technical features (ADX, Hurst, ATR%, RSI)
    - Edge family (trend, mean_reversion, carry, etc.)
    - Direction (LONG/SHORT)
    - Holding time bucket (SCALP, SHORT, SWING, POSITIONAL)
    - Spread/liquidity state
    """

    # Feature weights for similarity computation
    FEATURE_WEIGHTS = {
        "adx_14": 1.0,
        "hurst": 1.0,
        "atr_pct": 1.0,
        "rsi_14": 0.5,
        "spread_pct": 0.3,
        "funding_rate": 0.3,
    }

    # Family encoding for similarity
    FAMILY_ENCODING = {
        "trend_continuation": 0,
        "mean_reversion": 1,
        "carry_dislocation": 2,
        "liquidation_squeeze": 3,
        "event_breakout": 4,
        None: 2,  # Default to carry (neutral)
    }

    # Direction encoding
    DIRECTION_ENCODING = {
        "LONG": 0,
        "SHORT": 1,
    }

    # Holding time bucket encoding
    HOLDING_ENCODING = {
        "SCALP": 0,
        "SHORT": 1,
        "SWING": 2,
        "POSITIONAL": 3,
        "OPEN": 2,  # Default to SWING
    }

    def __init__(self, min_samples: int = 10) -> None:
        """Initialize the similarity engine.

        Args:
            min_samples: Minimum number of similar trades required
                        for reliable EV estimation.
        """
        self._min_samples = min_samples

    def compute_similarity(
        self,
        current: dict[str, Any],
        historical: dict[str, Any],
        include_family: bool = True,
        include_direction: bool = True,
        include_holding: bool = True,
    ) -> float:
        """Compute similarity between current and historical contexts.

        Args:
            current: Current market context with features.
            historical: Historical trade context with features.
            include_family: Whether to include edge family in similarity.
            include_direction: Whether to include direction in similarity.
            include_holding: Whether to include holding time bucket.

        Returns:
            Similarity score (0.0 to 1.0).
        """
        # Technical features (weighted Euclidean distance)
        tech_features = []
        tech_weights = []
        for feature, weight in self.FEATURE_WEIGHTS.items():
            v1 = current.get(feature) or 0.0
            v2 = historical.get(feature) or 0.0
            tech_features.append((v1, v2))
            tech_weights.append(weight)

        if tech_features:
            tech_dist = self._weighted_euclidean(tech_features, tech_weights)
            tech_similarity = np.exp(-tech_dist / 50.0)
        else:
            tech_similarity = 1.0

        # Family similarity (exact match = 1.0, different = 0.3)
        family_sim = 1.0
        if include_family:
            f1 = self.FAMILY_ENCODING.get(current.get("edge_family"), 2)
            f2 = self.FAMILY_ENCODING.get(historical.get("edge_family"), 2)
            family_sim = 1.0 if f1 == f2 else 0.3

        # Direction similarity (exact match = 1.0, different = 0.5)
        dir_sim = 1.0
        if include_direction:
            d1 = self.DIRECTION_ENCODING.get(current.get("direction"), 0)
            d2 = self.DIRECTION_ENCODING.get(historical.get("direction"), 0)
            dir_sim = 1.0 if d1 == d2 else 0.5

        # Holding time similarity (closer buckets = more similar)
        hold_sim = 1.0
        if include_holding:
            h1 = self.HOLDING_ENCODING.get(current.get("holding_bucket"), 2)
            h2 = self.HOLDING_ENCODING.get(historical.get("holding_bucket"), 2)
            hold_sim = 1.0 - abs(h1 - h2) * 0.25

        # Combined similarity (weighted average)
        combined = (
            tech_similarity * 0.5
            + family_sim * 0.2
            + dir_sim * 0.15
            + hold_sim * 0.15
        )

        return float(max(0.0, min(1.0, combined)))

    def _weighted_euclidean(
        self,
        features: list[tuple[float, float]],
        weights: list[float],
    ) -> float:
        """Compute weighted Euclidean distance."""
        if not features:
            return 0.0

        total = 0.0
        for (v1, v2), w in zip(features, weights):
            total += w * (v1 - v2) ** 2

        return np.sqrt(total)

    def find_similar(
        self,
        current: dict[str, Any],
        historical_trades: list[dict[str, Any]],
        top_k: int = 10,
        min_similarity: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Find similar historical trades.

        Args:
            current: Current market context.
            historical_trades: List of historical trades.
            top_k: Number of top similar trades to return.
            min_similarity: Minimum similarity threshold.

        Returns:
            List of similar trades with similarity scores.
        """
        if not historical_trades:
            return []

        scored = []
        for trade in historical_trades:
            sim = self.compute_similarity(current, trade)
            if sim >= min_similarity:
                scored.append({**trade, "similarity": sim})

        # Sort by similarity (descending)
        scored.sort(key=lambda x: x["similarity"], reverse=True)

        return scored[:top_k]

    def estimate_ev(
        self,
        similar_trades: list[dict[str, Any]],
        min_samples: int | None = None,
    ) -> dict[str, Any]:
        """Estimate expected value from similar trades.

        Args:
            similar_trades: List of similar trades with similarity scores.
            min_samples: Minimum samples required (overrides default).

        Returns:
            Dict with ev, confidence, sample_count, win_rate, avg_pnl.
        """
        min_req = min_samples or self._min_samples

        if len(similar_trades) < min_req:
            return {
                "ev": 0.0,
                "confidence": 0.0,
                "sample_count": len(similar_trades),
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "insufficient_samples": True,
            }

        # Weight by similarity
        total_weight = sum(t.get("similarity", 0.5) for t in similar_trades)
        if total_weight == 0:
            return {
                "ev": 0.0,
                "confidence": 0.0,
                "sample_count": len(similar_trades),
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "insufficient_samples": True,
            }

        # Weighted average PnL
        weighted_pnl = sum(
            t.get("pnl_pct", 0) * t.get("similarity", 0.5)
            for t in similar_trades
        )
        avg_pnl = weighted_pnl / total_weight

        # Win rate
        wins = sum(1 for t in similar_trades if t.get("pnl_pct", 0) > 0)
        win_rate = wins / len(similar_trades)

        # EV = P(win) * avg_win - P(loss) * avg_loss
        win_pnls = [t["pnl_pct"] for t in similar_trades if t.get("pnl_pct", 0) > 0]
        loss_pnls = [abs(t["pnl_pct"]) for t in similar_trades if t.get("pnl_pct", 0) < 0]

        avg_win = np.mean(win_pnls) if win_pnls else 0.0
        avg_loss = np.mean(loss_pnls) if loss_pnls else 0.0

        ev = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # Confidence based on sample size and consistency
        sample_confidence = min(1.0, len(similar_trades) / (min_req * 2))
        consistency = 1.0 - np.std([t.get("pnl_pct", 0) for t in similar_trades]) / max(1.0, abs(avg_pnl))
        confidence = sample_confidence * max(0.5, consistency)

        return {
            "ev": float(ev),
            "confidence": float(confidence),
            "sample_count": len(similar_trades),
            "win_rate": float(win_rate),
            "avg_pnl": float(avg_pnl),
            "avg_win": float(avg_win),
            "avg_loss": float(avg_loss),
            "insufficient_samples": False,
        }
