"""Experiment Registry — Manages reproducible research experiments.

Parses YAML manifests, tracks Git commits/seeds, and saves results
(metrics.json, trades.parquet, report.md) into structured directories:
experiments/YYYY/MM/EXP_NAME_ID/
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
import pandas as pd


class ExperimentManifest:
    """Parses and validates an experiment YAML manifest."""

    def __init__(self, filepath: Path) -> None:
        if not filepath.exists():
            raise FileNotFoundError(f"Manifest not found: {filepath}")
        
        with open(filepath, "r") as f:
            self.data = yaml.safe_load(f)

        self.name = self.data.get("experiment", {}).get("name", "unnamed_experiment")
        self.control_config = self.data.get("control", {})
        self.variant_config = self.data.get("variant", {})
        self.dataset = self.data.get("dataset", {})
        self.parent_experiment_id = self.data.get("experiment", {}).get("parent_experiment_id")

        # Reproducibility tracking
        self.exp_id = f"{self.name}_{str(uuid.uuid4())[:8]}"
        self.timestamp = datetime.now(timezone.utc)
        self.git_commit = self._get_git_commit()
        
    def _get_git_commit(self) -> str:
        try:
            import subprocess
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True
            )
            return result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            return "unknown"


class ExperimentRegistry:
    """Saves and tracks experiment results."""

    def __init__(self, base_dir: str = "experiments") -> None:
        self.base_dir = Path(base_dir)

    def _get_experiment_dir(self, manifest: ExperimentManifest) -> Path:
        dt = manifest.timestamp
        path = self.base_dir / str(dt.year) / f"{dt.month:02d}" / manifest.exp_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def register_run(
        self,
        manifest: ExperimentManifest,
        control_trades: list[dict[str, Any]],
        variant_trades: list[dict[str, Any]],
        control_metrics: dict[str, float],
        variant_metrics: dict[str, float],
        stats: dict[str, Any],
        report_md: str
    ) -> Path:
        """Save all experiment artifacts and return the directory path."""
        exp_dir = self._get_experiment_dir(manifest)
        
        # Save manifest
        with open(exp_dir / "config.yaml", "w") as f:
            yaml.dump(manifest.data, f)
            
        # Save metrics
        metrics = {
            "control": control_metrics,
            "variant": variant_metrics,
            "metadata": {
                "id": manifest.exp_id,
                "timestamp": manifest.timestamp.isoformat(),
                "commit": manifest.git_commit
            }
        }
        with open(exp_dir / "metrics.json", "w") as f:
            json.dump(metrics, f, indent=2)
            
        # Save trades and equity (using pandas)
        def _save_equity(trades: list[dict[str, Any]], prefix: str) -> None:
            if not trades: return
            df = pd.DataFrame(trades)
            df.to_parquet(exp_dir / f"{prefix}_trades.parquet")
            
            # Reconstruct equity curve for graphing
            if "pnl_pct" in df.columns:
                returns = df["pnl_pct"].values
                equity = (1 + returns).cumprod()
                eq_df = pd.DataFrame({
                    "trade_id": df.get("trade_id", range(len(equity))),
                    "equity": equity
                })
                eq_df.to_csv(exp_dir / f"{prefix}_equity.csv", index=False)

        _save_equity(control_trades, "control")
        _save_equity(variant_trades, "variant")
            
        # Save statistical validation
        with open(exp_dir / "validation.json", "w") as f:
            json.dump(stats, f, indent=2)

        # Save report
        with open(exp_dir / "report.md", "w") as f:
            f.write(report_md)
            
        logger.info("Experiment %s registered at %s", manifest.exp_id, exp_dir)
        return exp_dir
