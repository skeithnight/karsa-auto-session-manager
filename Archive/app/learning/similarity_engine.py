"""Similarity Engine (Sprint 3).

Finds historically similar market contexts to inform current decisions.
"""
from __future__ import annotations

try:
    import numpy as np
except ImportError:
    np = None


class SimilarityEngine:
    """Computes similarity between feature vectors."""

    def compute_similarity(self, v1: dict, v2: dict) -> float:
        """Compute similarity between two feature vectors."""
        # Extract numerical features
        features1 = np.array([
            v1.get("adx_14") or 0.0,
            v1.get("hurst") or 0.5,
            v1.get("atr_pct") or 50.0,
            v1.get("rsi_14") or 50.0,
        ])
        features2 = np.array([
            v2.get("adx_14") or 0.0,
            v2.get("hurst") or 0.5,
            v2.get("atr_pct") or 50.0,
            v2.get("rsi_14") or 50.0,
        ])

        # Euclidean distance normalized
        dist = np.linalg.norm(features1 - features2)

        # Convert distance to similarity (0 to 1)
        # Using a simple exponential decay
        similarity = np.exp(-dist / 50.0)
        return float(similarity)
