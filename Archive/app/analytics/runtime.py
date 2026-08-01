"""Runtime Analytics Module.

Calculates the Decision Intelligence Health Score (Epic 0 / Sprint 8).
The Health Score is a unified metric aggregating infrastructure, pipeline, 
execution safety, shadow reliability, observability, and portfolio integrity.
"""

from __future__ import annotations

import logging
from prometheus_client import REGISTRY

logger = logging.getLogger("karsa.analytics.runtime")


class RuntimeAnalyzer:
    """Calculates runtime reliability and health scores."""

    @staticmethod
    def get_metric_value(metric_name: str) -> float:
        """Helper to get current value of a prometheus metric."""
        val = REGISTRY.get_sample_value(metric_name)
        return float(val) if val is not None else 0.0

    @classmethod
    def calculate_health_score(cls) -> dict[str, float]:
        """
        Calculate the Decision Intelligence Health Score.
        Returns a dict of sub-scores and the overall score.
        """
        scores = {}

        # 1. Execution Safety
        sl_attempts = cls.get_metric_value("karsa_sl_place_attempts_total")
        sl_failures = cls.get_metric_value("karsa_sl_place_failure_total")
        scores["execution_safety"] = (
            (sl_attempts - sl_failures) / sl_attempts * 100.0 if sl_attempts > 0 else 100.0
        )

        # 2. Shadow Reliability
        shadow_attempts = cls.get_metric_value("karsa_shadow_execution_attempts_total")
        shadow_failures = cls.get_metric_value("karsa_shadow_execution_failure_total")
        scores["shadow_reliability"] = (
            (shadow_attempts - shadow_failures) / shadow_attempts * 100.0 if shadow_attempts > 0 else 100.0
        )

        # 3. Observability / Trace Integrity
        trace_total = cls.get_metric_value("karsa_decision_trace_complete_total")
        trace_invalid = cls.get_metric_value("karsa_decision_trace_invalid_total")
        scores["observability"] = (
            (trace_total - trace_invalid) / trace_total * 100.0 if trace_total > 0 else 100.0
        )

        # 4. Infrastructure (Memory stability proxies)
        restarts = cls.get_metric_value("karsa_restarts_total")
        scores["infrastructure"] = max(0.0, 100.0 - (restarts * 5.0))

        # 5. Reconciliation Integrity
        recon_total = cls.get_metric_value("karsa_reconciliation_success_total")
        phantom_trades = cls.get_metric_value("karsa_phantom_trade_detected_total")
        scores["reconciliation"] = (
            (recon_total - phantom_trades) / recon_total * 100.0 if recon_total > 0 else 100.0
        )

        # Overall Health Score is the average of sub-scores
        if scores:
            scores["overall_health"] = sum(scores.values()) / len(scores)
        else:
            scores["overall_health"] = 100.0

        return scores
