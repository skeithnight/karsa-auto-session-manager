"""ML Prefilter — EXPERIMENTAL OVERLAY.

⚠️  EXPERIMENTAL: This module is classified as an experimental overlay
per the quant trader persona review recommendation #6:
"Make the ML prefilter earn the right to exist"

Status: Shadow evaluation mode only
Promotes to production ONLY when it demonstrates:
1. Calibration across time windows
2. Stability across market regimes
3. Incremental value over deterministic scoring
4. Non-trivial contribution after fees and slippage

Current behavior:
- Loads local joblib model if present
- Fail-opens to 1.0 on failure
- Predicts from short feature vector
- Default prediction: 1.0 (no filtering)

To enable in shadow mode:
    export ML_PREFILTER_SHADOW=true

To promote to production:
    1. Run calibration study across 30+ days
    2. Verify precision > 0.6 and recall > 0.5
    3. Confirm incremental EV > 0 after fees
    4. Set ML_PREFILTER_PRODUCTION=true
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

MODEL_PATH = "/app/models/xgb_shadow_model.joblib"


class MLPrefilterExperimental:
    """Experimental ML prefilter for shadow evaluation.

    This is a reclassified version of the original MLPrefilter,
    marked as experimental until it earns production status.
    """

    def __init__(self) -> None:
        self.model = None
        self._shadow_mode = os.getenv("ML_PREFILTER_SHADOW", "false").lower() == "true"
        self._production_mode = os.getenv("ML_PREFILTER_PRODUCTION", "false").lower() == "true"

        if self._production_mode and os.path.exists(MODEL_PATH):
            try:
                import joblib
                self.model = joblib.load(MODEL_PATH)
                logger.info(f"ML Prefilter (PRODUCTION) loaded from {MODEL_PATH}")
            except Exception as e:
                logger.warning(f"ML Prefilter load failed: {e}")
        elif self._shadow_mode and os.path.exists(MODEL_PATH):
            try:
                import joblib
                self.model = joblib.load(MODEL_PATH)
                logger.info(f"ML Prefilter (SHADOW) loaded from {MODEL_PATH}")
            except Exception as e:
                logger.warning(f"ML Prefilter load failed: {e}")
        else:
            logger.debug("ML Prefilter: model not loaded (shadow=%s, production=%s)",
                        self._shadow_mode, self._production_mode)

    @property
    def is_active(self) -> bool:
        """Check if ML prefilter is active."""
        return self.model is not None and (self._shadow_mode or self._production_mode)

    @property
    def mode(self) -> str:
        """Return current mode."""
        if self._production_mode:
            return "production"
        elif self._shadow_mode:
            return "shadow"
        return "disabled"

    def predict_probability(self, features: dict[str, Any]) -> float:
        """Predict probability of profitable trade.

        Args:
            features: Feature dict with technical indicators.

        Returns:
            Probability (0.0 to 1.0). Returns 1.0 on failure (fail-open).
        """
        if not self.is_active:
            return 1.0

        try:
            import numpy as np
            feature_vector = np.array([
                features.get("adx_14", 0.0),
                features.get("hurst", 0.5),
                features.get("atr_pct", 50.0),
                features.get("rsi_14", 50.0),
                features.get("spread_pct", 0.0),
                features.get("funding_rate", 0.0),
            ]).reshape(1, -1)

            prob = self.model.predict_proba(feature_vector)[0][1]

            if self._shadow_mode:
                logger.debug("ML Prefilter (shadow): prob=%.3f", prob)

            return float(prob)

        except Exception as e:
            logger.warning("ML Prefilter prediction failed: %s", e)
            return 1.0  # Fail-open

    def should_filter(self, features: dict[str, Any], threshold: float = 0.3) -> bool:
        """Check if signal should be filtered.

        Args:
            features: Feature dict.
            threshold: Probability threshold below which to filter.

        Returns:
            True if signal should be filtered (rejected).
        """
        if not self.is_active:
            return False

        prob = self.predict_probability(features)
        if prob < threshold:
            logger.info("ML Prefilter: filtering signal (prob=%.3f < %.3f)", prob, threshold)
            return True
        return False
