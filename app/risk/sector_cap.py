"""Sector diversity cap — reject signals when sector already at max positions."""

from __future__ import annotations

from loguru import logger

from app.core.position_store import PositionStore
from app.data.sector_mapping import get_sector


class SectorCap:
    """Enforce max positions per sector to maintain portfolio diversity.

    ponytail: count from position_store, no separate Redis writer.
    Rebuilt on every check — position_store is the source of truth.
    """

    def __init__(self, position_store: PositionStore, max_per_sector: int = 2) -> None:
        self.position_store = position_store
        self.max_per_sector = max_per_sector

    async def _count_by_sector(self) -> dict[str, int]:
        """Count active positions per sector."""
        positions = await self.position_store.list_all()
        counts: dict[str, int] = {}
        for pos in positions:
            sector = get_sector(pos.get("symbol", ""))
            counts[sector] = counts.get(sector, 0) + 1
        return counts

    async def check(self, symbol: str) -> bool:
        """Check if a new position in `symbol` is allowed.

        Returns True if allowed, False if sector is at cap.
        """
        sector = get_sector(symbol)
        if sector == "UNKNOWN":
            logger.warning(f"Sector cap: unknown sector for {symbol}, allowing")
            return True

        counts = await self._count_by_sector()
        current = counts.get(sector, 0)

        if current >= self.max_per_sector:
            logger.warning(
                f"Sector cap: {sector} at {current}/{self.max_per_sector}, rejecting {symbol}"
            )
            return False

        logger.debug(
            f"Sector cap: {sector} at {current}/{self.max_per_sector}, allowing {symbol}"
        )
        return True

    async def get_status(self) -> dict[str, dict[str, int]]:
        """Get current sector allocation for status display."""
        counts = await self._count_by_sector()
        return {
            sector: {"current": count, "max": self.max_per_sector}
            for sector, count in counts.items()
        }
