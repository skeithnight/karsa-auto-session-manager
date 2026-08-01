"""Trade Lifecycle Analytics Module.

Calculates aggregates for Maximum Adverse Excursion (MAE),
Maximum Favorable Excursion (MFE), and Peak R-Multiples.
"""

from __future__ import annotations

import logging
from sqlalchemy import text
from app.core.database import DatabaseEngine

logger = logging.getLogger("karsa.analytics.lifecycle")


class LifecycleAnalyzer:
    """Analyze trade lifecycle flow metrics."""

    def __init__(self, db: DatabaseEngine) -> None:
        self.db = db

    async def analyze_lifecycle(self) -> dict[str, float]:
        """
        Calculate avg MAE, avg MFE, and avg Peak R-Multiple across all closed trades.
        """
        results = {}
        try:
            async with self.db.engine.connect() as conn:
                query = text("""
                    SELECT 
                        AVG(mae) as avg_mae,
                        AVG(mfe) as avg_mfe,
                        AVG(peak_r_multiple) as avg_peak_r
                    FROM trades 
                    WHERE exit_time IS NOT NULL
                """)
                row = (await conn.execute(query)).fetchone()
                
                if row:
                    results["avg_mae"] = float(row[0]) if row[0] is not None else 0.0
                    results["avg_mfe"] = float(row[1]) if row[1] is not None else 0.0
                    results["avg_peak_r_multiple"] = float(row[2]) if row[2] is not None else 0.0
                else:
                    results["avg_mae"] = 0.0
                    results["avg_mfe"] = 0.0
                    results["avg_peak_r_multiple"] = 0.0

                # Analyze exit reasons
                query_reasons = text("""
                    SELECT exit_reason, COUNT(*) as count
                    FROM trades
                    WHERE exit_time IS NOT NULL
                    GROUP BY exit_reason
                """)
                rows_reasons = (await conn.execute(query_reasons)).fetchall()
                
                for r in rows_reasons:
                    reason = r[0] if r[0] else "unknown"
                    count = r[1]
                    results[f"exit_reason_{reason}_count"] = count

        except Exception as e:
            logger.error(f"Failed to analyze lifecycle: {e}")
            
        return results
