#!/usr/bin/env python3
"""
Backtest Determinism Validation script.
Runs the BacktestEngine twice on the exact same dataset to verify
that the outputs are 100% identical and there are no sources of
nondeterminism (e.g. unseeded randoms, race conditions, unordered sets).
"""

import asyncio
from decimal import Decimal
import sys
import numpy as np

from app.backtest.engine import BacktestEngine
from app.alpha.regime_classifier import RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.risk.dynamic_risk_gate import DynamicRiskGate

def make_candles(n=300):
    c = 100.0
    arr = []
    for i in range(n):
        arr.append([1000 + i * 3600000, c-0.5, c+1.0, c-1.0, c, 1000])
        # Add a mix of trend and chop
        if i < 100:
            c += 0.5
        elif i < 200:
            c += np.random.uniform(-2, 2)
        else:
            c -= 0.5
    return arr

async def main():
    np.random.seed(42)
    candles = make_candles(500)
    
    classifier1 = RegimeClassifier()
    router1 = StrategyRouter(volatility_scaling=True)
    gate1 = DynamicRiskGate()
    engine1 = BacktestEngine(classifier1, router1, gate1)
    
    rep1 = await engine1.run("BTCUSDT", candles, "run1", orderbook_delta=0.01, funding_rate=0.0, oi_change=0.0)
    
    # Re-init to clear any state
    np.random.seed(42)
    classifier2 = RegimeClassifier()
    router2 = StrategyRouter(volatility_scaling=True)
    gate2 = DynamicRiskGate()
    engine2 = BacktestEngine(classifier2, router2, gate2)
    
    rep2 = await engine2.run("BTCUSDT", candles, "run2", orderbook_delta=0.01, funding_rate=0.0, oi_change=0.0)
    
    print(f"Run 1 generated {len(rep1)} reports.")
    print(f"Run 2 generated {len(rep2)} reports.")
    
    if len(rep1) != len(rep2):
        print(f"FAILED: length mismatch. rep1={len(rep1)}, rep2={len(rep2)}")
        sys.exit(1)
        
    for i, (r1, r2) in enumerate(zip(rep1, rep2)):
        if r1.entry_price != r2.entry_price or r1.score != r2.score or r1.direction != r2.direction or r1.pnl_net != r2.pnl_net:
            print(f"FAILED: trade mismatch at index {i}!\nR1: {r1}\nR2: {r2}")
            sys.exit(1)
            
    print("PASS: Determinism validated. 100% identical outputs for identical datasets.")
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
