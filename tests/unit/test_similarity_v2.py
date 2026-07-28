"""Unit tests for SimilarityEngineV2."""

import pytest
from app.learning.similarity_engine_v2 import SimilarityEngineV2


class TestSimilarityEngineV2:
    """Test SimilarityEngineV2."""

    def setup_method(self):
        self.engine = SimilarityEngineV2(min_samples=5)

    def test_compute_similarity_identical(self):
        """Test similarity of identical contexts."""
        context = {
            "adx_14": 25.0,
            "hurst": 0.55,
            "atr_pct": 50.0,
            "rsi_14": 50.0,
            "edge_family": "trend_continuation",
            "direction": "LONG",
            "holding_bucket": "SWING",
        }
        sim = self.engine.compute_similarity(context, context)
        assert sim > 0.9

    def test_compute_similarity_different_family(self):
        """Test similarity with different families."""
        c1 = {"adx_14": 25.0, "edge_family": "trend_continuation", "direction": "LONG"}
        c2 = {"adx_14": 25.0, "edge_family": "mean_reversion", "direction": "LONG"}
        sim = self.engine.compute_similarity(c1, c2)
        # Family difference reduces similarity
        assert sim < 0.95

    def test_compute_similarity_different_direction(self):
        """Test similarity with different directions."""
        c1 = {"adx_14": 25.0, "direction": "LONG"}
        c2 = {"adx_14": 25.0, "direction": "SHORT"}
        sim = self.engine.compute_similarity(c1, c2)
        # Direction difference reduces similarity
        assert sim < 0.95

    def test_find_similar(self):
        """Test finding similar trades."""
        current = {"adx_14": 25.0, "hurst": 0.55, "edge_family": "trend_continuation"}
        historical = [
            {"adx_14": 25.0, "hurst": 0.55, "edge_family": "trend_continuation", "pnl_pct": 0.05},
            {"adx_14": 10.0, "hurst": 0.3, "edge_family": "mean_reversion", "pnl_pct": -0.02},
        ]
        similar = self.engine.find_similar(current, historical, top_k=1)
        assert len(similar) == 1
        assert similar[0]["pnl_pct"] == 0.05

    def test_estimate_ev_insufficient_samples(self):
        """Test EV estimation with insufficient samples."""
        result = self.engine.estimate_ev([], min_samples=5)
        assert result["insufficient_samples"] is True
        assert result["ev"] == 0.0

    def test_estimate_ev_with_samples(self):
        """Test EV estimation with sufficient samples."""
        trades = [
            {"pnl_pct": 0.05, "similarity": 0.8},
            {"pnl_pct": 0.03, "similarity": 0.7},
            {"pnl_pct": -0.02, "similarity": 0.6},
            {"pnl_pct": 0.04, "similarity": 0.9},
            {"pnl_pct": -0.01, "similarity": 0.5},
        ]
        result = self.engine.estimate_ev(trades, min_samples=3)
        assert result["insufficient_samples"] is False
        assert result["sample_count"] == 5
        assert result["win_rate"] == 0.6
        assert result["ev"] != 0.0
