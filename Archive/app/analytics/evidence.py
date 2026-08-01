"""Evidence Analytics Module.

Calculates win-rates, expectancies, and profit factors for 
each boolean evidence source (Trend, Momentum, Orderbook, Funding, AI).
"""

from __future__ import annotations

import logging
from sqlalchemy import text
from app.core.database import DatabaseEngine

logger = logging.getLogger("karsa.analytics.evidence")


class EvidenceAnalyzer:
    """Analyze decision evidence performance."""

    EVIDENCE_COLUMNS = [
        "evidence_trend",
        "evidence_momentum",
        "evidence_orderbook",
        "evidence_funding",
        "evidence_ai"
    ]

    def __init__(self, db: DatabaseEngine) -> None:
        self.db = db

    async def analyze_evidence_performance(self) -> dict[str, dict[str, float]]:
        """
        Calculates performance metrics (win_rate, avg_pnl) for each evidence source.
        Only considers closed trades.
        """
        results = {}
        try:
            async with self.db.engine.connect() as conn:
                for col in self.EVIDENCE_COLUMNS:
                    query = text(f"""
                        SELECT 
                            COUNT(*) as total_trades,
                            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                            AVG(pnl) as avg_pnl,
                            SUM(pnl) as total_pnl
                        FROM trades 
                        WHERE {col} = TRUE AND exit_time IS NOT NULL
                    """)
                    row = (await conn.execute(query)).fetchone()
                    
                    if row and row[0] > 0:
                        total = row[0]
                        wins = row[1]
                        avg_pnl = float(row[2]) if row[2] else 0.0
                        total_pnl = float(row[3]) if row[3] else 0.0
                        win_rate = (wins / total) * 100.0
                        
                        results[col] = {
                            "total_trades": total,
                            "win_rate": win_rate,
                            "avg_pnl": avg_pnl,
                            "total_pnl": total_pnl,
                        }
                    else:
                        results[col] = {
                            "total_trades": 0,
                            "win_rate": 0.0,
                            "avg_pnl": 0.0,
                            "total_pnl": 0.0,
                        }
                        
        except Exception as e:
            logger.error(f"Failed to analyze evidence performance: {e}")
            
        return results
