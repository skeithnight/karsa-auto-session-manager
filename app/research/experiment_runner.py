"""⚠️  SCAFFOLD MODULE — Uses mock trades, not production-grade.

See app/research/SCAFFOLD_DISCLAIMER.md for status and promotion criteria.
Per quant trader persona review recommendation #9: Remove research theater.
"""

"""Experiment Runner — Orchestrates research experiments.

Runs the Control and Variant backtests as defined in the Manifest,
computes statistics, generates the final Markdown report, and registers
the artifacts in the Experiment Registry.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from app.research.experiment_registry import ExperimentManifest, ExperimentRegistry
from app.research.metrics_engine import MetricsEngine
from app.research.statistical_validator import StatisticalValidator
from app.research.ranking_engine import RankingEngine


class ExperimentRunner:
    """Runs a full A/B experiment via the BacktestEngine."""

    def __init__(self, registry_dir: str = "experiments") -> None:
        self.registry = ExperimentRegistry(base_dir=registry_dir)

    async def run_experiment(self, manifest_path: str) -> Path:
        """Run an experiment from a YAML manifest."""
        path = Path(manifest_path)
        manifest = ExperimentManifest(path)
        logger.info("Running Experiment: %s (%s)", manifest.name, manifest.exp_id)

        # In a real implementation, we would initialize the BacktestEngine here
        # and pass manifest.control_config vs manifest.variant_config.
        # For the scaffolding, we mock the trade output to demonstrate the pipeline.
        
        logger.info("Running Control (AI=%s, Micro=%s)", 
                    manifest.control_config.get("ai"), manifest.control_config.get("micro"))
        await asyncio.sleep(1) # Simulate backtest
        control_trades = self._mock_trades(base_return=0.001, variance=0.01)

        logger.info("Running Variant (AI=%s, Micro=%s)", 
                    manifest.variant_config.get("ai"), manifest.variant_config.get("micro"))
        await asyncio.sleep(1) # Simulate backtest
        variant_trades = self._mock_trades(base_return=0.003, variance=0.012)

        # 1. Compute Metrics
        control_metrics = MetricsEngine.compute(control_trades)
        variant_metrics = MetricsEngine.compute(variant_trades)

        # 2. Statistical Validation
        stats = StatisticalValidator.validate(control_trades, variant_trades)

        # 3. Ranking & Promotion Gate
        ranking = RankingEngine.evaluate(variant_metrics, stats)

        # 4. Generate Report
        report_md = self._generate_report(
            manifest, control_metrics, variant_metrics, stats, ranking
        )

        # 5. Register Artifacts
        exp_dir = self.registry.register_run(
            manifest=manifest,
            control_trades=control_trades,
            variant_trades=variant_trades,
            control_metrics=control_metrics,
            variant_metrics=variant_metrics,
            stats=stats,
            report_md=report_md
        )

        logger.info("Experiment %s complete. Decision: %s", 
                    manifest.name, ranking["decision"])
        
        return exp_dir

    def _mock_trades(self, base_return: float, variance: float) -> list[dict]:
        import numpy as np
        from datetime import datetime, timedelta
        
        np.random.seed(42) # For reproducibility in the mock
        trades = []
        now = datetime.now()
        for i in range(100):
            # Normal distribution of returns
            ret = np.random.normal(base_return, variance)
            trades.append({
                "trade_id": i,
                "pnl_pct": float(ret),
                "entry_time": (now + timedelta(days=i)).isoformat(),
            })
        return trades

    def _generate_report(
        self,
        manifest: ExperimentManifest,
        control: dict,
        variant: dict,
        stats: dict,
        ranking: dict
    ) -> str:
        """Render the Markdown ablation report."""
        md = [
            f"# Experiment Report: {manifest.name}",
            f"**ID:** `{manifest.exp_id}` | **Date:** `{manifest.timestamp.isoformat()}`",
            f"**Commit:** `{manifest.git_commit}`",
            "",
            "## Promotion Gate",
            f"- **Decision:** {ranking.get('decision')}",
            "- **Reasons:**",
        ]
        
        for reason in ranking.get("reasons", []):
            md.append(f"  - {reason}")
            
        md.extend([
            "",
            "## Statistical Validation",
        ])
        
        for k, v in stats.items():
            md.append(f"- **{k}**: {v}")
            
        md.extend([
            "",
            "## Performance Ablation",
            "| Metric | Control | Variant | Delta |",
            "|--------|---------|---------|-------|"
        ])
        
        for key in control.keys():
            c_val = control[key]
            v_val = variant[key]
            delta = v_val - c_val
            
            # Format nicely
            if isinstance(c_val, float):
                c_str = f"{c_val:.4f}"
                v_str = f"{v_val:.4f}"
                d_str = f"{delta:+.4f}"
            else:
                c_str = str(c_val)
                v_str = str(v_val)
                d_str = str(delta)
                
            md.append(f"| {key} | {c_str} | {v_str} | {d_str} |")
            
        return "\n".join(md)
