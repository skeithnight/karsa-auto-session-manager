"""Static sector classification for all configured symbols."""

from __future__ import annotations

from loguru import logger

# Static mapping: symbol → sector
# Stablecoins intentionally excluded (not tradeable)
SECTOR_MAP: dict[str, str] = {
    # Tier 1 — Majors (own sector)
    "BTC/USDT": "MAJORS",
    "ETH/USDT": "MAJORS",
    # Tier 2 — L1 chains
    "SOL/USDT": "L1",
    "BNB/USDT": "L1",
    "AVAX/USDT": "L1",
    "NEAR/USDT": "L1",
    "APT/USDT": "L1",
    "ATOM/USDT": "L1",
    "TON/USDT": "L1",
    "KAS/USDT": "L1",
    "MNT/USDT": "L1",
    "STRK/USDT": "L1",
    "VET/USDT": "L1",
    # Tier 2 — L2 chains
    "ARB/USDT": "L2",
    "OP/USDT": "L2",
    "MATIC/USDT": "L2",
    "IMX/USDT": "L2",
    "ZK/USDT": "L2",
    "DYDX/USDT": "L2",
    "W/USDT": "L2",
    "BLUR/USDT": "L2",
    # Tier 2 — DeFi
    "UNI/USDT": "DEFI",
    "AAVE/USDT": "DEFI",
    "MKR/USDT": "DEFI",
    "CRV/USDT": "DEFI",
    "RUNE/USDT": "DEFI",
    "PENDLE/USDT": "DEFI",
    "LDO/USDT": "DEFI",
    "SNX/USDT": "DEFI",
    "ONDO/USDT": "DEFI",
    # Tier 2 — AI / Compute
    "FET/USDT": "AI",
    "RNDR/USDT": "AI",
    "TAO/USDT": "AI",
    "WLD/USDT": "AI",
    "IO/USDT": "AI",
    "GRT/USDT": "AI",
    # Tier 2 — DePIN / Infra
    "INJ/USDT": "INFRA",
    "TIA/USDT": "INFRA",
    "SEI/USDT": "INFRA",
    "STX/USDT": "INFRA",
    # Tier 3 — Payments / Legacy
    "XRP/USDT": "PAYMENTS",
    "LTC/USDT": "PAYMENTS",
    "BCH/USDT": "PAYMENTS",
    "XLM/USDT": "PAYMENTS",
    "HBAR/USDT": "PAYMENTS",
    # Tier 3 — Other L1 / Utility
    "LINK/USDT": "ORACLE",
    "SUI/USDT": "L1",
    "DOT/USDT": "L1",
    "ICP/USDT": "L1",
    "ADA/USDT": "L1",
    "TRX/USDT": "L1",
    "FIL/USDT": "INFRA",
    "ETC/USDT": "L1",
    "MANA/USDT": "METAVERSE",
    "SAND/USDT": "METAVERSE",
    "GALA/USDT": "METAVERSE",
    "ORDI/USDT": "BTCFI",
    "TRB/USDT": "ORACLE",
    "CFX/USDT": "L1",
    "YGG/USDT": "GAMING",
    "NOT/USDT": "MEME",
    # Tier 4 — Meme / Trending
    "DOGE/USDT": "MEME",
    "SHIB/USDT": "MEME",
    "PEPE/USDT": "MEME",
    "WIF/USDT": "MEME",
    "BONK/USDT": "MEME",
    "FLOKI/USDT": "MEME",
    "FTM/USDT": "L1",
    "BOME/USDT": "MEME",
    "ENA/USDT": "DEFI",
    "JUP/USDT": "DEFI",
    "PYTH/USDT": "ORACLE",
}


def get_sector(symbol: str) -> str:
    """Get sector for a symbol. Returns 'UNKNOWN' if not classified."""
    sector = SECTOR_MAP.get(symbol)
    if sector is None:
        logger.warning(f"Sector mapping: unknown symbol {symbol}")
        return "UNKNOWN"
    return sector


def all_classified(symbols: list[str]) -> bool:
    """Check that all symbols have a sector classification."""
    unclassified = [s for s in symbols if s not in SECTOR_MAP]
    if unclassified:
        logger.warning(f"Unclassified symbols: {unclassified}")
        return False
    return True
