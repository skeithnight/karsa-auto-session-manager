import asyncio
import json
from decimal import Decimal
from loguru import logger
import ccxt.async_support as ccxt

from app.core.redis_client import RedisClient
from app.data.ohlcv_fetcher import OHLCVFetcher
from app.backtest.optimizer import GridSearchOptimizer

async def main():
    logger.info("Starting Parameter Sweeper...")
    redis = RedisClient()
    await redis.connect()
    
    raw = await redis.redis.get("system:universe:symbols")
    if not raw:
        logger.error("No universe found in Redis")
        return
        
    data = json.loads(raw)
    symbols = data.get("symbols", [])
    
    exchange = ccxt.bybit({"enableRateLimit": True})
    fetcher = OHLCVFetcher(exchange)
    
    sl_grid = [Decimal("0.5"), Decimal("1.0"), Decimal("1.5"), Decimal("2.0")]
    trail_grid = [Decimal("0.5"), Decimal("1.0"), Decimal("1.5"), Decimal("2.0")]
    optimizer = GridSearchOptimizer(sl_grid, trail_grid)
    
    logger.info(f"Processing {len(symbols)} symbols sequentially (Laptop-Safe Batch Mode)...")
    for sym in symbols:
        try:
            logger.info(f"Fetching 500 candles for {sym}...")
            c = await fetcher.fetch(sym, "15m", 500)
            if c:
                await optimizer.process_symbol(sym, c)
                
            # Cool down CPU for laptops
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed processing {sym}: {e}")
            
    results = optimizer.get_results()
    
    logger.info("--- TOP 5 OPTIMIZATION RESULTS ---")
    for idx, r in enumerate(results[:5]):
        logger.info(f"#{idx+1}: SL={r.sl_atr_buffer} Trail={r.trail_atr_mult} | Win={r.win_rate:.1f}% PnL=${r.net_pnl:.2f} Trades={r.total_trades}")
        
    await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
