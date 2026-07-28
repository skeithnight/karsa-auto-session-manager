"""Ranking Engine — CI/CD Promotion Gate for Alpha.

Enforces a strict Promotion Policy rather than arbitrary scoring.
Returns an explicit decision (PROMOTE, NEEDS_MORE_EVIDENCE, REJECT)
with a detailed list of reasons for explainability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from loguru import logger


@dataclass
class PromotionPolicy:
    """Strict quantitative thresholds required for promotion."""
    min_trades: int = 50
    min_pf: float = 1.25
    min_sharpe: float = 1.0
    max_dd: float = 0.15      # 15% Max Drawdown
    p_value_threshold: float = 0.05


class RankingEngine:
    """Evaluates experiments against the Promotion Policy."""

    @staticmethod
    def evaluate(
        metrics: dict[str, float], 
        stats: dict[str, Any] | None = None,
        policy: PromotionPolicy | None = None
    ) -> dict[str, Any]:
        """Generate a Promotion decision with explicit reasons."""
        if policy is None:
            policy = PromotionPolicy()
            
        reasons = []
        passes = True
        
        # Performance checks
        trades = metrics.get("Total Trades", 0)
        if trades >= policy.min_trades:
            reasons.append(f"Trade count ({trades}) meets minimum ({policy.min_trades})")
        else:
            reasons.append(f"Trade count ({trades}) failed minimum ({policy.min_trades})")
            passes = False
            
        pf = metrics.get("Profit Factor", 0.0)
        if pf >= policy.min_pf:
            reasons.append(f"Profit Factor ({pf:.2f}) meets minimum ({policy.min_pf})")
        else:
            reasons.append(f"Profit Factor ({pf:.2f}) failed minimum ({policy.min_pf})")
            passes = False
            
        sharpe = metrics.get("Sharpe Ratio", 0.0)
        if sharpe >= policy.min_sharpe:
            reasons.append(f"Sharpe Ratio ({sharpe:.2f}) meets minimum ({policy.min_sharpe})")
        else:
            reasons.append(f"Sharpe Ratio ({sharpe:.2f}) failed minimum ({policy.min_sharpe})")
            passes = False
            
        max_dd = abs(metrics.get("Max Drawdown", 0.0))
        if max_dd <= policy.max_dd:
            reasons.append(f"Max Drawdown ({max_dd:.1%}) within limit ({policy.max_dd:.1%})")
        else:
            reasons.append(f"Max Drawdown ({max_dd:.1%}) exceeded limit ({policy.max_dd:.1%})")
            passes = False
            
        # Statistical Confidence Check
        if stats:
            p_val = stats.get("Mann-Whitney p-value", 1.0)
            prob_beat = stats.get("Bootstrap Prob(Variant > Control)", 0.0)

            if p_val <= policy.p_value_threshold:
                reasons.append(f"Statistically significant (p={p_val:.4f} <= {policy.p_value_threshold})")
                reasons.append(f"Probability Variant beats Control = {prob_beat:.1%}")
            else:
                reasons.append(f"Failed statistical significance (p={p_val:.4f} > {policy.p_value_threshold})")
                reasons.append(f"Probability Variant beats Control = {prob_beat:.1%}")
                passes = False
        else:
            # No stats available — allow promotion if all performance metrics pass
            # (statistical validation is optional for live trading promotion)
            reasons.append("Statistical validation skipped (no stats provided — promoting on performance metrics only)")

        # Promotion Decision
        if passes:
            decision = "✅ PROMOTE"
        elif pf >= 1.0 and max_dd <= 0.20 and trades >= 10:
            decision = "⚠ NEEDS_MORE_EVIDENCE"
            reasons.append("Strategy is marginally profitable but failed strict promotion policy.")
        else:
            decision = "❌ REJECT"
            
        logger.info("Ranking Engine Decision: %s", decision)
            
        return {
            "decision": decision,
            "reasons": reasons
        }
