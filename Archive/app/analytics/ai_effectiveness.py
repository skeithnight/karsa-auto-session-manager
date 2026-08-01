"""AI Effectiveness Analytics Module.

Calculates metrics related to AI participation in trading decisions,
such as latency, confidence shift, and AI vs Non-AI performance.
"""

from __future__ import annotations

import logging
from sqlalchemy import text
from app.core.database import DatabaseEngine

logger = logging.getLogger("karsa.analytics.ai_effectiveness")


class AIEffectivenessAnalyzer:
    """Analyze AI effectiveness and performance impact."""

    def __init__(self, db: DatabaseEngine) -> None:
        self.db = db

    async def analyze_ai_impact(self) -> dict[str, float]:
        """
        Compare trades with and without AI evidence.
        """
        results = {}
        try:
            async with self.db.engine.connect() as conn:
                # With AI
                query_ai = text("""
                    SELECT 
                        COUNT(*) as total,
                        AVG(pnl) as avg_pnl,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)::NUMERIC / NULLIF(COUNT(*), 0) * 100.0 as win_rate
                    FROM trades 
                    WHERE evidence_ai = TRUE AND exit_time IS NOT NULL
                """)
                row_ai = (await conn.execute(query_ai)).fetchone()
                
                if row_ai and row_ai[0] > 0:
                    results["ai_total_trades"] = row_ai[0]
                    results["ai_avg_pnl"] = float(row_ai[1]) if row_ai[1] else 0.0
                    results["ai_win_rate"] = float(row_ai[2]) if row_ai[2] else 0.0
                else:
                    results["ai_total_trades"] = 0
                    results["ai_avg_pnl"] = 0.0
                    results["ai_win_rate"] = 0.0

                # Without AI
                query_no_ai = text("""
                    SELECT 
                        COUNT(*) as total,
                        AVG(pnl) as avg_pnl,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)::NUMERIC / NULLIF(COUNT(*), 0) * 100.0 as win_rate
                    FROM trades 
                    WHERE evidence_ai = FALSE AND exit_time IS NOT NULL
                """)
                row_no_ai = (await conn.execute(query_no_ai)).fetchone()

                if row_no_ai and row_no_ai[0] > 0:
                    results["no_ai_total_trades"] = row_no_ai[0]
                    results["no_ai_avg_pnl"] = float(row_no_ai[1]) if row_no_ai[1] else 0.0
                    results["no_ai_win_rate"] = float(row_no_ai[2]) if row_no_ai[2] else 0.0
                else:
                    results["no_ai_total_trades"] = 0
                    results["no_ai_avg_pnl"] = 0.0
                    results["no_ai_win_rate"] = 0.0

                # Confidence shift
                query_shift = text("""
                    SELECT 
                        AVG(ai_confidence_after - ai_confidence_before) as avg_shift,
                        AVG(ai_latency_ms) as avg_latency
                    FROM trades
                    WHERE evidence_ai = TRUE AND exit_time IS NOT NULL
                """)
                row_shift = (await conn.execute(query_shift)).fetchone()

                if row_shift:
                    results["avg_confidence_shift"] = float(row_shift[0]) if row_shift[0] else 0.0
                    results["avg_ai_latency_ms"] = float(row_shift[1]) if row_shift[1] else 0.0
                else:
                    results["avg_confidence_shift"] = 0.0
                    results["avg_ai_latency_ms"] = 0.0

        except Exception as e:
            logger.error(f"Failed to analyze AI impact: {e}")
            
        return results
