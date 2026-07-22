import asyncio
from decimal import Decimal
from app.execution.bybit_client import BybitClient
from app.core.config import get_settings
from app.data.sector_mapping import get_sector

async def check_exposure():
    settings = get_settings()
    client = BybitClient(api_key=settings.BYBIT_API_KEY, api_secret=settings.BYBIT_API_SECRET, testnet=settings.USE_TESTNET)
    await client.initialize()

    wallet = await client.get_wallet_balance()
    equity = wallet.get("equity", Decimal("0"))
    print(f"Total Equity: ${equity:.2f}")

    positions = await client.fetch_positions()
    
    total_long_value = Decimal("0")
    total_short_value = Decimal("0")
    sector_counts = {}

    print("\nCurrent Positions:")
    for p in positions:
        sym = p["symbol"]
        # Convert Bybit symbol (e.g. ZECUSDT) back to CCXT format (ZEC/USDT) for sector lookup
        if not sym.endswith("/USDT"):
            sym = sym.replace("USDT", "/USDT")
        
        side = p["side"]
        size = Decimal(str(p["size"]))
        mark = Decimal(str(p["markPrice"]))
        notional = size * mark
        
        sector = get_sector(sym)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        
        print(f"- {sym} ({side}): {size} @ {mark} => ${notional:.2f} Notional (Sector: {sector})")

        if side == "buy":
            total_long_value += notional
        elif side == "sell":
            total_short_value += notional

    gross_exposure = total_long_value + total_short_value
    net_exposure = abs(total_long_value - total_short_value)
    
    gross_pct = (gross_exposure / equity) * 100 if equity > 0 else Decimal("0")
    net_pct = (net_exposure / equity) * 100 if equity > 0 else Decimal("0")

    print(f"\n--- Portfolio Exposure ---")
    print(f"Gross Exposure: ${gross_exposure:.2f} ({gross_pct:.2f}% of Equity) - Limit: {settings.max_gross_exposure_pct}")
    print(f"Net Exposure: ${net_exposure:.2f} ({net_pct:.2f}% of Equity) - Limit: {settings.max_net_exposure_pct}")
    
    print("\n--- Sector Distribution ---")
    for sec, count in sector_counts.items():
        print(f"{sec}: {count} / 2 allowed")

if __name__ == "__main__":
    asyncio.run(check_exposure())
