import numpy as np

from app.alpha.regime_classifier import MarketRegime
from app.alpha.strategy_router import StrategyRouter


def _make_trending_candles(n=50, direction=1.0):
    candles = np.zeros((n, 6))
    for i in range(n):
        c = 100.0 + direction * i * 2.0
        candles[i] = [1000 + i * 3600, c - 0.5, c + 0.5, c - 0.5, c, 1000 + i * 50]
    return candles

router = StrategyRouter(volatility_scaling=False)
candles = _make_trending_candles(50, direction=1.0)
last_close = candles[-1, 4]
result, vol_factor = router.evaluate_signal(
    candles,
    regime=MarketRegime.TREND_BULL,
    direction="LONG",
    global_prices={"binance": last_close + 5, "okx": last_close + 3}
)
print("Result:", result)
