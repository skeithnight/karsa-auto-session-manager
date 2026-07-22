import asyncio
import json
import logging
from decimal import Decimal
from app.core.database import DatabaseEngine
from app.core.redis_client import RedisClient
from app.core.position_store import PositionStore
from app.core.config import get_settings

logger = logging.getLogger(__name__)

async def main():
    settings = get_settings()
    
    # Init Redis
    redis_client = RedisClient()
    await redis_client.connect()
    
    position_store = PositionStore(redis_client)
    
    # Get all open positions
    open_positions = await position_store.list_all()
    print("=== OPEN POSITIONS ===")
    if not open_positions:
        print("No open positions.")
    else:
        for p in open_positions:
            print(f"Symbol: {p.get('symbol')} | Side: {p.get('side')} | Entry: {p.get('entry_price')} | Size: {p.get('size')} | Live: {p.get('live_price')} | PnL: {p.get('unrealized_pnl')}")
            
    print("\n=== PERFORMANCE METRICS ===")
    
    # Init Postgres
    db = DatabaseEngine()
    await db.connect(settings.postgres_url)
    
    query = """
    SELECT 
        COUNT(*) as total_trades,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
        SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losing_trades,
        SUM(realized_pnl) as net_pnl
    FROM trades
    WHERE status = 'CLOSED'
    """
    
    try:
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(query)
            total = row['total_trades'] or 0
            wins = row['winning_trades'] or 0
            losses = row['losing_trades'] or 0
            net_pnl = row['net_pnl'] or 0
            
            if total > 0:
                win_rate = (wins / total) * 100
                print(f"Total Trades: {total}")
                print(f"Wins: {wins} | Losses: {losses}")
                print(f"Win Rate: {win_rate:.2f}%")
                print(f"Net PnL: ${net_pnl:.2f}")
            else:
                print("No closed trades yet.")
    except Exception as e:
        print(f"Failed to fetch trade stats: {e}")
        
    await db.dispose()
    await redis_client.close()

if __name__ == "__main__":
    asyncio.run(main())
