"""Sector & Narrative Rotation Filter.

Ranks sectors by average 4H performance return.
Restricts LONG signals to top-performing sectors and SHORT signals to bottom-performing sectors.
Prevents buying "falling knives" in cold/dying crypto narratives.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from app.data.sector_mapping import get_sector

logger = logger = logging.getLogger(__name__)


class SectorRotationFilter:
    """Ranks crypto sectors and filters signals by narrative momentum."""

    def __init__(self) -> None:
        self._sector_returns: dict[str, float] = {}

    def update_sector_returns(self, symbol_returns_4h: dict[str, float]) -> None:
        """Calculate average 4H return per sector from individual symbol returns.

        Args:
            symbol_returns_4h: Dict mapping unified symbol (e.g., "FET/USDT") to 4H % return.
        """
        sector_totals: dict[str, float] = defaultdict(float)
        sector_counts: dict[str, int] = defaultdict(int)

        for symbol, ret in symbol_returns_4h.items():
            sector = get_sector(symbol)
            if sector != "UNKNOWN" and sector != "MAJORS":
                sector_totals[sector] += ret
                sector_counts[sector] += 1

        self._sector_returns = {
            sector: sector_totals[sector] / sector_counts[sector]
            for sector in sector_totals
            if sector_counts[sector] > 0
        }

    def check_sector_alignment(self, symbol: str, direction: str) -> dict:
        """Check if symbol's sector performance aligns with the signal direction.

        Returns:
            dict with: approved (bool), sector (str), percentile (float), reason (str)
        """
        sector = get_sector(symbol)
        # MAJORS (BTC/ETH) and UNKNOWN symbols bypass sector rotation check
        if sector in ("MAJORS", "UNKNOWN") or not self._sector_returns:
            return {"approved": True, "sector": sector, "percentile": 0.5, "reason": "bypass"}

        sorted_sectors = sorted(
            self._sector_returns.items(), key=lambda x: x[1], reverse=True
        )
        total_sectors = len(sorted_sectors)
        if total_sectors < 3:
            return {"approved": True, "sector": sector, "percentile": 0.5, "reason": "insufficient_sectors"}

        # Find rank of target sector (0-indexed, 0 = top performing)
        rank = next(
            (i for i, (s, _) in enumerate(sorted_sectors) if s == sector), None
        )
        if rank is None:
            return {"approved": True, "sector": sector, "percentile": 0.5, "reason": "unranked"}

        # Percentile: 1.0 = top, 0.0 = bottom
        percentile = 1.0 - (rank / (total_sectors - 1)) if total_sectors > 1 else 0.5

        # Rule: LONGs require top 70% sector performance (percentile >= 0.3)
        # SHORTs require bottom 70% sector performance (percentile <= 0.7)
        if direction == "LONG" and percentile < 0.3:
            logger.warning(
                f"SectorRotation: LONG {symbol} REJECTED — sector {sector} is in bottom 30% (percentile={percentile:.2f})"
            )
            return {
                "approved": False,
                "sector": sector,
                "percentile": percentile,
                "reason": f"cold_sector_{sector}_bottom_30pct",
            }

        if direction == "SHORT" and percentile > 0.7:
            logger.warning(
                f"SectorRotation: SHORT {symbol} REJECTED — sector {sector} is in top 30% (percentile={percentile:.2f})"
            )
            return {
                "approved": False,
                "sector": sector,
                "percentile": percentile,
                "reason": f"hot_sector_{sector}_top_30pct",
            }

        return {"approved": True, "sector": sector, "percentile": percentile, "reason": "ok"}
