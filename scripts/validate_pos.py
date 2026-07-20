import asyncio
import json
import redis.asyncio as redis
from datetime import datetime, timezone

async def main():
    r = redis.Redis(host="karsa-redis", port=6379, db=0, decode_responses=True)
    keys = await r.keys("karsa:position:*")
    
    if not keys:
        print("No active positions found in Redis.")
        return
        
    for key in keys:
        data = await r.get(key)
        if not data:
            continue
        pos = json.loads(data)
        
        symbol = pos.get("symbol", "UNKNOWN")
        side = pos.get("side", "UNKNOWN")
        entry_price = float(pos.get("entry_price", "0"))
        amount = float(pos.get("amount", "0"))
        entered_at_str = pos.get("entered_at")
        
        if not entered_at_str:
            print(f"[{symbol}] Missing entered_at")
            continue
            
        entered_at = datetime.fromisoformat(entered_at_str)
        if entered_at.tzinfo is None:
            entered_at = entered_at.replace(tzinfo=timezone.utc)
            
        now = datetime.now(timezone.utc)
        held_mins = (now - entered_at).total_seconds() / 60.0
        
        # Calculate fees
        fee_cost = (entry_price * amount) * 0.0011
        
        print(f"=== {symbol} ({side}) ===")
        print(f"Entry Price: {entry_price}")
        print(f"Amount: {amount}")
        print(f"Entered At: {entered_at_str} ({held_mins:.1f} mins ago)")
        print(f"Estimated Exit Fee Cost: ${fee_cost:.4f}")
        
        # Checking thresholds
        print(f"-> Quick Profit eligible (R>2.0)? {'YES' if held_mins <= 5 else 'NO (Too old)'}")
        print(f"-> Stagnation eligible (R<0.2)? {'YES' if held_mins >= 10 else 'NO (Too young)'}")
        print(f"-> Stale Exit eligible? {'YES' if held_mins >= 15 else 'NO (Too young)'}")
        print("")

if __name__ == "__main__":
    asyncio.run(main())
