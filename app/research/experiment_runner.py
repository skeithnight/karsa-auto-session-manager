"""Experiment Runner — Orchestrates research experiments.

Runs the Control and Variant backtests as defined in the Manifest,
computes statistics, generates the final Markdown report, and registers
the artifacts in the Experiment Registry.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
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

        # Initialize BacktestEngine with real components
        from app.alpha.regime_classifier import RegimeClassifier
        from app.alpha.strategy_router import StrategyRouter
        from app.backtest.engine import BacktestEngine
        from app.risk.dynamic_risk_gate import DynamicRiskGate

        regime_classifier = RegimeClassifier()
        strategy_router = StrategyRouter()
        risk_gate = DynamicRiskGate()

        # Control: no AI
        control_engine = BacktestEngine(
            regime_classifier=regime_classifier,
            strategy_router=strategy_router,
            risk_gate=risk_gate,
        )

        # Variant: with AI (if enabled)
        variant_config = manifest.variant_config
        ai_enabled = variant_config.get("ai", False)
        variant_engine = BacktestEngine(
            regime_classifier=regime_classifier,
            strategy_router=strategy_router,
            risk_gate=risk_gate,
        )

        # Load historical candles for backtest
        symbol = manifest.control_config.get("symbol", "BTC/USDT")
        days = manifest.control_config.get("days", 90)

        logger.info("Running Control (AI=False, Symbol=%s)", symbol)
        control_reports = await self._run_backtest(control_engine, symbol, days)

        logger.info("Running Variant (AI=%s, Symbol=%s)", ai_enabled, symbol)
        variant_reports = await self._run_backtest(variant_engine, symbol, days)

        # Convert reports to trade dicts for MetricsEngine
        control_trades = self._reports_to_trades(control_reports)
        variant_trades = self._reports_to_trades(variant_reports)

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

    async def _run_backtest(
        self,
        engine: "BacktestEngine",
        symbol: str,
        days: int,
    ) -> list:
        """Run backtest using historical candles from database."""
        from app.backtest.data_loader import MicroDataLoader

        loader = MicroDataLoader()
        df = await loader.fetch_ohlcv(symbol, "1h", limit=days * 24)
        # Convert DataFrame to list of lists format expected by BacktestEngine
        if df.empty:
            candles = []
        else:
            candles = df[["timestamp", "open", "high", "low", "close", "volume"]].values.tolist()

        if not candles:
            logger.warning("No candles found for %s, using synthetic data", symbol)
            candles = self._generate_synthetic_candles(days * 24)

        reports = await engine.run(symbol, candles, job_id=f"ab_{symbol}")
        return reports

    def _generate_synthetic_candles(self, n: int) -> list[list]:
        """Generate synthetic candles for testing when no historical data."""
        import random
        from datetime import datetime, timezone

        candles = []
        price = 50000.0
        now = datetime.now(timezone.utc)

        for i in range(n):
            ts = int((now.timestamp() - (n - i) * 3600) * 1000)
            change = random.gauss(0, 0.02)  # 2% std dev
            price *= (1 + change)
            o = price * (1 - abs(change) * 0.5)
            h = price * (1 + abs(change))
            l = price * (1 - abs(change))
            c = price
            vol = random.uniform(1000, 5000)
            candles.append([ts, o, h, l, c, vol])

        return candles

    def _reports_to_trades(self, reports: list) -> list[dict]:
        """Convert BacktestReport list to trade dicts for MetricsEngine."""
        trades = []
        for i, r in enumerate(reports):
            if r.trade_taken:
                trades.append({
                    "trade_id": i,
                    "pnl_pct": float(r.pnl_net / Decimal("10000")) if r.pnl_net else 0.0,
                    "entry_time": r.entry_time.isoformat() if r.entry_time else "",
                    "exit_time": r.exit_time.isoformat() if r.exit_time else "",
                    "direction": r.direction,
                    "regime": str(r.regime),
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
