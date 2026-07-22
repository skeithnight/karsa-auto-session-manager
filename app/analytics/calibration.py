"""Confidence Calibration Analytics Module.

Analyzes trade outcomes grouped by confidence buckets (0-10, 11-20, etc.)
to determine if the system is well-calibrated (i.e. high confidence -> high win rate).
"""

from __future__ import annotations

import logging
from sqlalchemy import text
from app.core.database import DatabaseEngine

logger = logging.getLogger("karsa.analytics.calibration")


class CalibrationAnalyzer:
    """Analyze confidence score calibration."""

    def __init__(self, db: DatabaseEngine) -> None:
        self.db = db

    async def analyze_calibration(self) -> dict[str, dict[str, float]]:
        """
        Calculate win rate and avg PnL for each 10-point confidence bucket.
        """
        results = {}
        try:
            async with self.db.engine.connect() as conn:
                # Group by 10-point buckets: FLOOR(ai_confidence_before / 10) * 10
                query = text("""
                    SELECT 
                        FLOOR(ai_confidence_before / 10.0) * 10 as bucket,
                        COUNT(*) as total,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                        AVG(pnl) as avg_pnl
                    FROM trades 
                    WHERE ai_confidence_before IS NOT NULL AND exit_time IS NOT NULL
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """)
                rows = (await conn.execute(query)).fetchall()
                
                for row in rows:
                    bucket = int(row[0]) if row[0] is not None else 0
                    total = row[1]
                    wins = row[2]
                    avg_pnl = float(row[3]) if row[3] else 0.0
                    
                    win_rate = (wins / total * 100.0) if total > 0 else 0.0
                    bucket_name = f"{bucket}-{bucket+9}"
                    
                    results[bucket_name] = {
                        "total_trades": total,
                        "win_rate": win_rate,
                        "avg_pnl": avg_pnl
                    }

        except Exception as e:
            logger.error(f"Failed to analyze calibration: {e}")
            
        return results
