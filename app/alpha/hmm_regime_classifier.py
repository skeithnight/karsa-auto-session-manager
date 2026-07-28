"""HMM Regime Prediction — Sprint 3 predictive regime detection.

Uses a 3-state Gaussian Hidden Markov Model to detect regime transitions
*before* they happen, replacing reactive ADX/Hurst with forward-looking signals.

States:
  0: LOW_VOL    — calm market, accumulating energy
  1: TRANSITION — regime shifting, breakout imminent
  2: HIGH_VOL   — volatile, trending or crashing

Redis signals published:
  - HMM_BREAKOUT_IMMINENT: LOW_VOL → TRANSITION (trend starting)
  - HMM_CHOP_IMMINENT: HIGH_VOL → LOW_VOL (volatility dying, chop ahead)

Fail-closed: if HMM fails to load/fit, returns None (no signal published).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# State indices
STATE_LOW_VOL = 0
STATE_TRANSITION = 1
STATE_HIGH_VOL = 2

# Minimum returns required to fit HMM
_MIN_RETURNS = 100  # ~4 days of 1H candles


class HMMRegimeClassifier:
    """3-state Gaussian HMM for predictive regime detection.

    Trained on rolling window of 1H returns. Publishes regime transition
    signals to Redis for the decision engine to consume.
    """

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client
        self._model: Any = None
        self._last_state: int | None = None
        self._last_fit_time: float = 0.0
        self._state_order: list[int] = []  # maps raw state → canonical (0=LOW,1=TRANS,2=HIGH)

    async def fit(self, returns: np.ndarray) -> bool:
        """Fit the HMM on a window of 1H returns.

        Args:
            returns: Array of log returns (1H candles).

        Returns:
            True if model was fitted successfully, False otherwise.
        """
        try:
            from hmmlearn.hmm import GaussianHMM
            from app.core.config import get_settings

            settings = get_settings()
            n_states = settings.hmm_n_states

            if len(returns) < _MIN_RETURNS:
                logger.warning(
                    "HMM: insufficient returns (%d < %d), skipping fit",
                    len(returns), _MIN_RETURNS,
                )
                return False

            # Reshape for hmmlearn: (n_samples, 1 feature)
            X = returns.reshape(-1, 1)

            # Remove any NaN/Inf
            mask = np.isfinite(X.ravel())
            X = X[mask]
            if len(X) < _MIN_RETURNS:
                logger.warning("HMM: too few finite returns after filtering (%d)", len(X))
                return False

            model = GaussianHMM(
                n_components=n_states,
                covariance_type="diag",
                n_iter=200,
                random_state=42,
                tol=1e-4,
            )
            model.fit(X)

            # Map states to canonical order: 0=LOW_VOL, 1=TRANSITION, 2=HIGH_VOL
            # by sorting states by their variance (lowest = LOW_VOL, highest = HIGH_VOL)
            # We store the mapping so classify() can translate.
            variances = np.array([np.sum(model.covars_[i]) for i in range(n_states)])
            self._state_order = np.argsort(variances).tolist()

            self._model = model
            self._last_fit_time = time.time()

            logger.info(
                "HMM: fitted %d-state model on %d returns, "
                "means=%s, variances=%s",
                n_states, len(X),
                [round(float(m), 6) for m in model.means_.ravel()],
                [round(float(v), 8) for v in model.covars_.ravel()],
            )
            return True

        except ImportError:
            logger.error("HMM: hmmlearn not installed, cannot fit model")
            return False
        except Exception:
            logger.exception("HMM: fit failed")
            return False

    async def classify(self, returns: np.ndarray) -> int | None:
        """Classify current regime using the fitted HMM.

        Args:
            returns: Recent 1H returns (at least 10 for prediction).

        Returns:
            State index (0=LOW_VOL, 1=TRANSITION, 2=HIGH_VOL) or None if no model.
        """
        if self._model is None:
            return None

        try:
            if len(returns) < 10:
                return None

            X = returns[-10:].reshape(-1, 1)
            mask = np.isfinite(X.ravel())
            if mask.sum() < 5:
                return None

            state = self._model.predict(X)
            raw_state = int(state[-1])
            # Map raw state to canonical order using state_order
            if self._state_order and raw_state < len(self._state_order):
                # state_order[i] = raw_state for canonical position i
                # We need inverse: given raw_state, find canonical position
                canonical = self._state_order.index(raw_state)
                return canonical
            return raw_state

        except Exception:
            logger.exception("HMM: classify failed")
            return None

    async def classify_with_confidence(self, returns: np.ndarray) -> dict[str, Any] | None:
        """Classify regime with probability distribution.

        Returns:
            Dict with 'state', 'state_name', 'probabilities' (per-state probs),
            'confidence' (max probability), or None if no model.
        """
        if self._model is None:
            return None

        try:
            if len(returns) < 10:
                return None

            X = returns[-10:].reshape(-1, 1)
            mask = np.isfinite(X.ravel())
            if mask.sum() < 5:
                return None

            # Use predict_proba for probability distribution
            probs = self._model.predict_proba(X)
            # Take the last timestep's probabilities
            last_probs = probs[-1]

            # Map raw states to canonical order
            state_names = {0: "LOW_VOL", 1: "TRANSITION", 2: "HIGH_VOL"}
            canonical_probs = [0.0, 0.0, 0.0]
            if self._state_order:
                for i, raw_idx in enumerate(self._state_order):
                    if raw_idx < len(last_probs):
                        canonical_probs[i] = float(last_probs[raw_idx])
            else:
                canonical_probs = [float(p) for p in last_probs]

            # Most likely canonical state
            best_state = int(np.argmax(canonical_probs))
            confidence = max(canonical_probs)

            return {
                "state": best_state,
                "state_name": state_names.get(best_state, "UNKNOWN"),
                "probabilities": {
                    "LOW_VOL": round(canonical_probs[0], 4),
                    "TRANSITION": round(canonical_probs[1], 4),
                    "HIGH_VOL": round(canonical_probs[2], 4),
                },
                "confidence": round(confidence, 4),
            }

        except Exception:
            logger.exception("HMM: classify_with_confidence failed")
            return None

    async def detect_transition(
        self, returns: np.ndarray, symbol: str = "BTC/USDT"
    ) -> str | None:
        """Detect regime transitions and publish signals to Redis.

        Args:
            returns: Recent 1H returns.
            symbol: Symbol for logging context.

        Returns:
            Signal string if transition detected, else None.
        """
        current_state = await self.classify(returns)
        if current_state is None:
            return None

        signal = None

        if self._last_state is not None and current_state != self._last_state:
            # Transition detected
            if self._last_state == STATE_LOW_VOL and current_state == STATE_TRANSITION:
                signal = "HMM_BREAKOUT_IMMINENT"
                logger.info(
                    "HMM TRANSITION: %s LOW_VOL → TRANSITION — %s",
                    symbol, signal,
                )
            elif self._last_state == STATE_HIGH_VOL and current_state == STATE_LOW_VOL:
                signal = "HMM_CHOP_IMMINENT"
                logger.info(
                    "HMM TRANSITION: %s HIGH_VOL → LOW_VOL — %s",
                    symbol, signal,
                )
            else:
                logger.debug(
                    "HMM TRANSITION: %s state %d → %d (no signal)",
                    symbol, self._last_state, current_state,
                )

        self._last_state = current_state

        # Publish current state + probabilities to Redis
        if self._redis is not None:
            try:
                from app.core.config import get_settings
                settings = get_settings()
                state_names = {0: "LOW_VOL", 1: "TRANSITION", 2: "HIGH_VOL"}

                # Get probability distribution for richer downstream use
                confidence_data = await self.classify_with_confidence(returns)
                probabilities = confidence_data["probabilities"] if confidence_data else {
                    "LOW_VOL": 0.0, "TRANSITION": 0.0, "HIGH_VOL": 0.0,
                }
                regime_confidence = confidence_data["confidence"] if confidence_data else 0.0

                payload = json.dumps({
                    "state": current_state,
                    "state_name": state_names.get(current_state, "UNKNOWN"),
                    "signal": signal,
                    "probabilities": probabilities,
                    "confidence": regime_confidence,
                    "timestamp": time.time(),
                })
                await self._redis.set(settings.hmm_redis_key, payload, ex=3600)
            except Exception:
                logger.debug("HMM: Redis publish failed for %s", symbol)

        return signal

    async def run_classification_loop(
        self,
        ohlcv_fetcher: object | None = None,
        symbol: str = "BTC/USDT",
        interval_seconds: int = 3600,
    ) -> None:
        """Background task: periodically refit and classify.

        Args:
            ohlcv_fetcher: Object with async fetch(symbol, interval, limit) method.
            symbol: Symbol to fetch candles for.
            interval_seconds: How often to run (default 1h).
        """
        import asyncio

        while True:
            try:
                if ohlcv_fetcher is not None:
                    # Fetch 1H candles for the rolling window
                    from app.core.config import get_settings
                    settings = get_settings()
                    limit = settings.hmm_rolling_window_days * 24 + 50  # +50 buffer

                    candles_raw = await ohlcv_fetcher.fetch(symbol, "1h", limit=limit)  # type: ignore[attr-defined]
                    if candles_raw and len(candles_raw) >= _MIN_RETURNS:
                        closes = np.array([c[4] for c in candles_raw], dtype=float)
                        # Log returns
                        returns = np.diff(np.log(closes[closes > 0]))

                        # Refit every 6 hours
                        if time.time() - self._last_fit_time > 6 * 3600:
                            await self.fit(returns)

                        # Classify and detect transitions
                        signal = await self.detect_transition(returns, symbol)
                        if signal:
                            logger.info("HMM LOOP: %s signal=%s", symbol, signal)

                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("HMM: classification loop error")
                await asyncio.sleep(30)
