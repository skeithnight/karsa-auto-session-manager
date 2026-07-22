"""Parameter Sweeper — Grid Search Optimizer for BacktestEngine."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from loguru import logger

from app.backtest.engine import BacktestEngine
from app.alpha.regime_classifier import RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.risk.dynamic_risk_gate import DynamicRiskGate


@dataclass
class OptimizationResult:
    sl_atr_buffer: Decimal
    trail_atr_mult: Decimal
    total_trades: int
    total_wins: int
    win_rate: float
    net_pnl: Decimal


class GridSearchOptimizer:
    """Runs backtest engine over a grid of parameters, streaming one symbol at a time."""

    def __init__(self, sl_buffers: list[Decimal], trail_mults: list[Decimal]):
        self.sl_buffers = sl_buffers
        self.trail_mults = trail_mults
        self.results: dict[tuple[Decimal, Decimal], OptimizationResult] = {}
        
        for sl in sl_buffers:
            for trail in trail_mults:
                self.results[(sl, trail)] = OptimizationResult(
                    sl_atr_buffer=sl,
                    trail_atr_mult=trail,
                    total_trades=0,
                    total_wins=0,
                    win_rate=0.0,
                    net_pnl=Decimal("0")
                )
                
        self.classifier = RegimeClassifier()
        self.router = StrategyRouter()

    async def process_symbol(self, symbol: str, candles: list[list]) -> None:
        """Process a single symbol against all parameter combinations."""
        if not candles:
            return
            
        logger.debug(f"Optimizing {symbol} across {len(self.sl_buffers) * len(self.trail_mults)} combinations...")
        
        for sl in self.sl_buffers:
            for trail in self.trail_mults:
                gate = DynamicRiskGate(override_sl_buffer=sl, override_trail_mult=trail)
                engine = BacktestEngine(self.classifier, self.router, gate)
                
                reports = await engine.run(symbol, candles)
                
                res = self.results[(sl, trail)]
                for r in reports:
                    if r.trade_taken:
                        res.total_trades += 1
                        res.net_pnl += r.pnl_net
                        if r.pnl_net > 0:
                            res.total_wins += 1

    def get_results(self) -> list[OptimizationResult]:
        """Finalize win rates and return sorted results."""
        final_results = []
        for res in self.results.values():
            if res.total_trades > 0:
                res.win_rate = (res.total_wins / res.total_trades) * 100
            final_results.append(res)
            
        final_results.sort(key=lambda x: x.net_pnl, reverse=True)
        return final_results
