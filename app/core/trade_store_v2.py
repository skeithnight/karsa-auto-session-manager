"""Trade Store V2 — Extended trade history with family attribution.

Extends TradeStore to support:
- edge_family (which strategy family produced this trade)
- expected_value (EV at time of entry)
- holding_time_bucket (SCALP/SHORT/SWING/POSITIONAL)
- holding_time_minutes (precise tracking)

This addresses audit finding #4: family-aware ranking cannot work yet
because family attribution is not persisted in trade history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.core.database import DatabaseEngine


class TradeStoreV2:
    """Extended Postgres CRUD for trade history with family attribution."""

    def __init__(self, db: DatabaseEngine) -> None:
        self.db = db

    async def record_entry_v2(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        entry_price: Decimal,
        regime: str | None = None,
        edge_family: str | None = None,
        expected_value: float | None = None,
        win_rate_at_entry: float | None = None,
        ai_confidence: int | None = None,
        entry_regime: str | None = None,
        initial_risk_per_unit: Decimal | None = None,
        risk_profile_json: str | None = None,
        trace_id: str | None = None,
    ) -> int:
        """Record trade entry with family attribution. Returns trade id."""
        now = datetime.now(UTC)
        try:
            async with self.db.engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """INSERT INTO trades (
                            symbol, side, amount, entry_price, regime, entry_time,
                            ai_confidence, entry_regime, initial_risk_per_unit,
                            risk_profile_json, trace_id, edge_family, expected_value,
                            win_rate_at_entry
                        ) VALUES (
                            :symbol, :side, :amount, :entry_price, :regime, :entry_time,
                            :ai_confidence, :entry_regime, :initial_risk_per_unit,
                            :risk_profile_json, :trace_id, :edge_family, :expected_value,
                            :win_rate_at_entry
                        ) RETURNING id"""
                    ),
                    {
                        "symbol": symbol,
                        "side": side,
                        "amount": float(amount),
                        "entry_price": float(entry_price),
                        "regime": regime,
                        "entry_time": now,
                        "ai_confidence": ai_confidence,
                        "entry_regime": entry_regime,
                        "initial_risk_per_unit": float(initial_risk_per_unit) if initial_risk_per_unit else None,
                        "risk_profile_json": risk_profile_json,
                        "trace_id": trace_id,
                        "edge_family": edge_family,
                        "expected_value": expected_value,
                        "win_rate_at_entry": win_rate_at_entry,
                    },
                )
                await conn.commit()
                trade_id = result.scalar()
                logger.debug("record_entry_v2: trade %d recorded for %s %s", trade_id, symbol, side)
                return trade_id
        except Exception as e:
            logger.error("record_entry_v2 failed: %s", e)
            return 0

    async def close_trade_v2(
        self,
        trade_id: int,
        exit_price: Decimal,
        exit_reason: str,
        pnl: Decimal | None = None,
    ) -> None:
        """Close trade with holding time calculation."""
        try:
            async with self.db.engine.connect() as conn:
                # Get entry time
                result = await conn.execute(
                    text("SELECT entry_time FROM trades WHERE id = :id"),
                    {"id": trade_id},
                )
                row = result.fetchone()
                if not row:
                    return

                entry_time = row[0]
                exit_time = datetime.now(UTC)

                # Calculate holding time
                holding_minutes = int((exit_time - entry_time).total_seconds() / 60)

                # Calculate bucket
                if holding_minutes < 60:
                    bucket = "SCALP"
                elif holding_minutes < 240:
                    bucket = "SHORT"
                elif holding_minutes < 1440:
                    bucket = "SWING"
                else:
                    bucket = "POSITIONAL"

                # Update trade
                await conn.execute(
                    text("""UPDATE trades SET
                        exit_price = :exit_price,
                        exit_time = :exit_time,
                        exit_reason = :exit_reason,
                        pnl = :pnl,
                        holding_time_minutes = :holding_time_minutes,
                        holding_time_bucket = :holding_time_bucket
                    WHERE id = :id"""),
                    {
                        "exit_price": float(exit_price),
                        "exit_time": exit_time,
                        "exit_reason": exit_reason,
                        "pnl": float(pnl) if pnl else None,
                        "holding_time_minutes": holding_minutes,
                        "holding_time_bucket": bucket,
                        "id": trade_id,
                    },
                )
                await conn.commit()
                logger.debug(
                    "close_trade_v2: trade %d closed (holding=%d min, bucket=%s)",
                    trade_id, holding_minutes, bucket,
                )
        except Exception as e:
            logger.error("close_trade_v2 failed: %s", e)

    async def get_recent_trades_v2(
        self,
        limit: int = 50,
        edge_family: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent trades with family attribution.

        Args:
            limit: Maximum number of trades to return.
            edge_family: Filter by edge family (optional).

        Returns:
            List of trade dicts with all fields including family attribution.
        """
        try:
            async with self.db.engine.connect() as conn:
                query = """SELECT id, symbol, side, amount, entry_price, exit_price,
                    pnl, regime, entry_time, exit_time, exit_reason,
                    edge_family, expected_value, holding_time_minutes,
                    holding_time_bucket, win_rate_at_entry
                FROM trades
                WHERE exit_time IS NOT NULL"""

                params: dict[str, Any] = {"limit": limit}

                if edge_family:
                    query += " AND edge_family = :edge_family"
                    params["edge_family"] = edge_family

                query += " ORDER BY exit_time DESC LIMIT :limit"

                result = await conn.execute(text(query), params)
                rows = result.fetchall()

                return [
                    {
                        "id": row[0],
                        "symbol": row[1],
                        "side": row[2],
                        "amount": row[3],
                        "entry_price": row[4],
                        "exit_price": row[5],
                        "realized_pnl": row[6],
                        "regime": row[7],
                        "entry_time": row[8],
                        "exit_time": row[9],
                        "exit_reason": row[10],
                        "edge_family": row[11],
                        "expected_value": row[12],
                        "holding_time_minutes": row[13],
                        "holding_time_bucket": row[14],
                        "win_rate_at_entry": row[15],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error("get_recent_trades_v2 failed: %s", e)
            return []
