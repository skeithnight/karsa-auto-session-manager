"""Statistical Validator — Validates if alpha is real or luck.

Uses Mann-Whitney U tests and Bootstrapping on trade return distributions
between the Control and Variant runs to generate p-values.
"""

from __future__ import annotations

from typing import Any
import numpy as np
from loguru import logger

try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger.warning("scipy not installed. Statistical validation will be skipped.")


class StatisticalValidator:
    """Validates if the Variant outperforms the Control statistically."""

    @staticmethod
    def validate(
        control_trades: list[dict[str, Any]],
        variant_trades: list[dict[str, Any]],
        metric_key: str = "pnl_pct"
    ) -> dict[str, Any]:
        """Run statistical tests on the return distributions."""
        if not SCIPY_AVAILABLE:
            return {"error": "scipy required for validation"}

        if not control_trades or not variant_trades:
            return {"error": "insufficient trades for validation"}

        control_returns = np.array([t.get(metric_key, 0.0) for t in control_trades])
        variant_returns = np.array([t.get(metric_key, 0.0) for t in variant_trades])

        if len(control_returns) < 5 or len(variant_returns) < 5:
            return {"error": "need at least 5 trades for significance testing"}

        # Mann-Whitney U test (non-parametric, robust to outliers)
        # H0: Distribution of Variant is stochastic equality with Control
        # H1: Variant > Control (alternative='greater')
        stat, p_value = stats.mannwhitneyu(variant_returns, control_returns, alternative='greater')
        
        # Bootstrap difference in mean EV
        try:
            diff_in_means = np.mean(variant_returns) - np.mean(control_returns)
            
            # Simple bootstrap
            n_iterations = 1000
            n_size = min(len(control_returns), len(variant_returns))
            boot_diffs = np.zeros(n_iterations)
            
            for i in range(n_iterations):
                c_boot = np.random.choice(control_returns, size=n_size, replace=True)
                v_boot = np.random.choice(variant_returns, size=n_size, replace=True)
                boot_diffs[i] = np.mean(v_boot) - np.mean(c_boot)
                
            confidence_interval = np.percentile(boot_diffs, [2.5, 97.5])
            prob_positive_diff = np.mean(boot_diffs > 0)
        except Exception as e:
            logger.debug(f"Bootstrap failed: {e}")
            prob_positive_diff = 0.0
            confidence_interval = [0.0, 0.0]
            diff_in_means = 0.0

        return {
            "Mann-Whitney p-value": float(p_value),
            "Significant (alpha=0.05)": bool(p_value < 0.05),
            "Mean EV Delta": float(diff_in_means),
            "Probability Variant beats Control": float(prob_positive_diff),
            "Bootstrap 95% CI": [float(c) for c in confidence_interval]
        }
