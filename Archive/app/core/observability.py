"""Observability layer for Decision Intelligence (Sprint 0).

Handles structured JSON logging for:
- Decision Traces
- Feature Snapshots
- Regime Transitions
- Reject Reasons

All observability logs are printed via loguru with a specific [OBSERVABILITY] prefix.
"""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger


class ObservabilityLogger:
    """Centralized observability logging for the Decision Engine pipeline."""

    @staticmethod
    def log_decision_trace(
        strategy: str,
        confidence: float,
        regime: str,
        evidence: list[dict[str, Any]],
        entry_decision: str,
        exit_decision: str | None = None,
        decision_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        symbol: str | None = None,
        features_schema_version: str | None = None,
        ai_model_version: str | None = None,
        experiment_id: str | None = None,
        stage_timings: dict[str, float] | None = None,
        feature_vector: dict[str, Any] | None = None,
    ) -> None:
        """Log a complete decision trace."""
        payload = {
            "type": "decision_trace",
            "timestamp": time.time(),
            "decision_id": decision_id,
            "trace_id": trace_id,
            "session_id": session_id,
            "request_id": request_id,
            "symbol": symbol,
            "strategy": strategy,
            "confidence": confidence,
            "regime": regime,
            "evidence": evidence,
            "entry_decision": entry_decision,
            "exit_decision": exit_decision,
            "features_schema_version": features_schema_version,
            "ai_model_version": ai_model_version,
            "experiment_id": experiment_id,
            "stage_timings": stage_timings or {},
            "feature_vector": feature_vector or {},
        }
        logger.info(f"[OBSERVABILITY] {json.dumps(payload)}")

    @staticmethod
    def log_feature_snapshot(
        symbol: str,
        feature_vector: dict[str, Any],
        market_snapshot: dict[str, Any],
    ) -> None:
        """Log a snapshot of features and raw market data at the time of a signal."""
        payload = {
            "type": "feature_snapshot",
            "timestamp": time.time(),
            "symbol": symbol,
            "feature_vector": feature_vector,
            "market_snapshot": market_snapshot,
        }
        logger.info(f"[OBSERVABILITY] {json.dumps(payload)}")

    @staticmethod
    def log_regime_transition(
        old_regime: str,
        new_regime: str,
        duration_minutes: float,
    ) -> None:
        """Log a transition between market regimes."""
        payload = {
            "type": "regime_transition",
            "timestamp": time.time(),
            "old_regime": old_regime,
            "new_regime": new_regime,
            "duration_minutes": duration_minutes,
        }
        logger.info(f"[OBSERVABILITY] {json.dumps(payload)}")

    @staticmethod
    def log_reject_reason(
        symbol: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log why a trade or signal was rejected."""
        payload = {
            "type": "reject_reason",
            "timestamp": time.time(),
            "symbol": symbol,
            "reason": reason,
            "details": details or {},
        }
        logger.info(f"[OBSERVABILITY] {json.dumps(payload)}")
