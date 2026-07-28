"""Metrics Engine — Computes advanced quant metrics for research experiments.

Calculates:
- Performance: CAGR, Profit Factor, Sharpe, Sortino, Calmar, Omega
- Risk: Max Drawdown, Ulcer Index, VaR (95%)
- Trade Quality: Expectancy, Win Rate, Profit Factor, Median R
"""

from __future__ import annotations

import math
from typing import Any
import numpy as np
import pandas as pd


class MetricsEngine:
    """Computes advanced performance and risk metrics from trade history."""

    @staticmethod
    def compute(trades: list[dict[str, Any]], initial_capital: float = 10000.0) -> dict[str, float]:
        """Compute all metrics from a list of trades.
        
        Assumes each trade dict has 'pnl_pct' or 'pnl_net' and 'entry_time'.
        """
        if not trades:
            return MetricsEngine._empty_metrics()

        df = pd.DataFrame(trades)
        
        # Ensure we have a return series
        if "pnl_pct" in df.columns:
            returns = df["pnl_pct"].values
        elif "pnl_net" in df.columns:
            returns = (df["pnl_net"] / initial_capital).values
        else:
            return MetricsEngine._empty_metrics()

        # Cumulative Equity and Drawdown
        equity_curve = np.cumprod(1 + returns)
        peak_equity = np.maximum.accumulate(equity_curve)
        drawdowns = (equity_curve - peak_equity) / peak_equity
        max_dd = np.min(drawdowns) if len(drawdowns) > 0 else 0.0

        # Basic Stats
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.0
        gross_profit = np.sum(wins) if len(wins) > 0 else 0.0
        gross_loss = np.abs(np.sum(losses)) if len(losses) > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        expectancy = np.mean(returns) if len(returns) > 0 else 0.0
        median_r = np.median(returns) if len(returns) > 0 else 0.0

        # Annualization (assume trades span T days, default to 365 if unknown)
        # For simplicity in cross-sectional research, we'll use trade-based Sharpe
        # or daily Sharpe if timestamps exist.
        if "entry_time" in df.columns:
            try:
                df["entry_time"] = pd.to_datetime(df["entry_time"])
                days = (df["entry_time"].max() - df["entry_time"].min()).days
                days = max(days, 1)
            except Exception:
                days = 365
        else:
            days = 365
            
        trades_per_year = len(returns) / (days / 365) if days > 0 else 0
        
        # Sharpe & Sortino (Annualized)
        mean_ret = np.mean(returns)
        std_ret = np.std(returns) if len(returns) > 1 else 0.0
        ann_factor = math.sqrt(max(trades_per_year, 1))
        sharpe = (mean_ret / std_ret * ann_factor) if std_ret > 0 else 0.0
        
        downside_returns = returns[returns < 0]
        downside_std = np.std(downside_returns) if len(downside_returns) > 1 else 0.0
        sortino = (mean_ret / downside_std * ann_factor) if downside_std > 0 else 0.0

        # Calmar Ratio
        cagr = (equity_curve[-1] ** (365 / days)) - 1 if len(equity_curve) > 0 and equity_curve[-1] > 0 else 0.0
        calmar = cagr / abs(max_dd) if max_dd < 0 else float("inf")

        # Value at Risk (95% Historical)
        var_95 = np.percentile(returns, 5) if len(returns) > 0 else 0.0

        return {
            "Total Trades": len(returns),
            "Win Rate": float(win_rate),
            "Profit Factor": float(profit_factor),
            "Expectancy": float(expectancy),
            "Median Return": float(median_r),
            "Sharpe Ratio": float(sharpe),
            "Sortino Ratio": float(sortino),
            "Calmar Ratio": float(calmar),
            "Max Drawdown": float(max_dd),
            "VaR 95%": float(var_95),
            "CAGR": float(cagr),
        }

    @staticmethod
    def _empty_metrics() -> dict[str, float]:
        return {
            "Total Trades": 0.0,
            "Win Rate": 0.0,
            "Profit Factor": 0.0,
            "Expectancy": 0.0,
            "Median Return": 0.0,
            "Sharpe Ratio": 0.0,
            "Sortino Ratio": 0.0,
            "Calmar Ratio": 0.0,
            "Max Drawdown": 0.0,
            "VaR 95%": 0.0,
            "CAGR": 0.0,
        }
