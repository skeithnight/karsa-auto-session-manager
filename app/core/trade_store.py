"""Trade Store — Postgres-backed trade history and AI audit trail.

ponytail: raw SQL via SQLAlchemy text(), no ORM. Matches init_db.sql schema.
Callers: main.py (instantiates), CheckpointManager._exit_position (close_trade).
Schema: trades + ai_decisions tables from scripts/init_db.sql.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.core import metrics
from app.core.database import DatabaseEngine


class TradeStore:
    """Postgres CRUD for trade history + AI decisions."""

    def __init__(self, db: DatabaseEngine) -> None:
        self.db = db

    async def record_entry(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        entry_price: Decimal,
        regime: str | None = None,
        ai_confidence: int | None = None,
        entry_regime: str | None = None,
        initial_risk_per_unit: Decimal | None = None,
        risk_profile_json: str | None = None,
    ) -> int:
        """Record trade entry. Returns trade id."""
        now = datetime.now(UTC)
        try:
            async with self.db.engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """INSERT INTO trades (symbol, side, amount, entry_price, regime, entry_time, ai_confidence,
                        entry_regime, initial_risk_per_unit, risk_profile_json)
                        VALUES (:symbol, :side, :amount, :entry_price, :regime, :entry_time, :ai_confidence,
                        :entry_regime, :initial_risk_per_unit, :risk_profile_json)
                        RETURNING id"""
                    ),
                    {
                        "symbol": symbol,
                        "side": side,
                        "amount": str(amount),
                        "entry_price": str(entry_price),
                        "regime": regime,
                        "entry_time": now,
                        "ai_confidence": ai_confidence,
                        "entry_regime": entry_regime,
                        "initial_risk_per_unit": str(initial_risk_per_unit)
                        if initial_risk_per_unit is not None
                        else None,
                        "risk_profile_json": risk_profile_json,
                    },
                )
                row = result.fetchone()
                await conn.commit()
                trade_id = row[0] if row else 0
                logger.info(f"Trade recorded: {symbol} {side} id={trade_id}")
                return trade_id
        except Exception as e:
            metrics.postgres_write_errors.labels(table="trades").inc()
            logger.error(f"record_entry failed: {e}")
            raise

    async def close_trade(
        self,
        symbol: str,
        exit_price: Decimal,
        pnl: Decimal,
        exit_reason: str,
        trade_id: int | None = None,
        regime: str | None = None,
    ) -> int:
        """Close trade. If trade_id given, close that trade; else most recent open for symbol.
        Returns number of rows updated (0 means no matching open trade found).
        If regime is set, also updates the regime column.
        """
        now = datetime.now(UTC)
        regime_clause = ", regime = :regime" if regime else ""
        try:
            async with self.db.engine.connect() as conn:
                if trade_id is not None:
                    result = await conn.execute(
                        text(f"""UPDATE trades SET exit_price = :exit_price, pnl = :pnl,
                            exit_reason = :exit_reason, exit_time = :exit_time{regime_clause}
                            WHERE id = :trade_id AND exit_time IS NULL"""),
                        {
                            "trade_id": trade_id,
                            "exit_price": str(exit_price),
                            "pnl": str(pnl),
                            "exit_reason": exit_reason,
                            "exit_time": now,
                            **({"regime": regime} if regime else {}),
                        },
                    )
                else:
                    result = await conn.execute(
                        text(f"""UPDATE trades SET exit_price = :exit_price, pnl = :pnl,
                            exit_reason = :exit_reason, exit_time = :exit_time{regime_clause}
                            WHERE id = (
                                SELECT id FROM trades
                                WHERE symbol = :symbol AND exit_time IS NULL
                                ORDER BY entry_time DESC LIMIT 1
                            )"""),
                        {
                            "symbol": symbol,
                            "exit_price": str(exit_price),
                            "pnl": str(pnl),
                            "exit_reason": exit_reason,
                            "exit_time": now,
                            **({"regime": regime} if regime else {}),
                        },
                    )
                await conn.commit()
                updated = result.rowcount or 0
                if updated == 0:
                    logger.warning(
                        f"close_trade: no open trade updated — symbol={symbol} "
                        f"id={trade_id or 'latest'} (already closed or missing)"
                    )
                else:
                    logger.info(
                        f"Trade closed: {symbol} id={trade_id or 'latest'} pnl={pnl} reason={exit_reason}"
                    )
                return updated
        except Exception as e:
            metrics.postgres_write_errors.labels(table="trades").inc()
            logger.error(f"close_trade failed: {e}")
            raise

    async def record_full_trade(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        entry_price: Decimal,
        exit_price: Decimal,
        pnl: Decimal,
        regime: str,
        entry_time: datetime,
        exit_time: datetime,
        exit_reason: str,
    ) -> int:
        """Insert a complete trade (entry+exit) in one shot. Used by backfill."""
        try:
            async with self.db.engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """INSERT INTO trades (symbol, side, amount, entry_price, exit_price,
                        pnl, regime, entry_time, exit_time, exit_reason)
                        VALUES (:symbol, :side, :amount, :entry_price, :exit_price,
                        :pnl, :regime, :entry_time, :exit_time, :exit_reason)
                        RETURNING id"""
                    ),
                    {
                        "symbol": symbol,
                        "side": side,
                        "amount": str(amount),
                        "entry_price": str(entry_price),
                        "exit_price": str(exit_price),
                        "pnl": str(pnl),
                        "regime": regime,
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "exit_reason": exit_reason,
                    },
                )
                row = result.fetchone()
                await conn.commit()
                trade_id = row[0] if row else 0
                logger.info(
                    f"Full trade recorded: {symbol} {side} pnl={pnl} id={trade_id}"
                )
                return trade_id
        except Exception as e:
            metrics.postgres_write_errors.labels(table="trades").inc()
            logger.error(f"record_full_trade failed: {e}")
            raise

    async def get_history(
        self, page: int = 1, per_page: int = 20
    ) -> tuple[list[dict[str, Any]], int, int, int, Decimal]:
        """Get paginated trade history. Returns (trades, total, wins, losses, net_pnl)."""
        offset = (page - 1) * per_page
        async with self.db.engine.connect() as conn:
            count_result = await conn.execute(
                text("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL")
            )
            total = count_result.scalar() or 0

            rows = await conn.execute(
                text("""SELECT symbol, side, amount, entry_price, exit_price, pnl,
                    regime, entry_time, exit_time, exit_reason, ai_confidence
                    FROM trades WHERE exit_time IS NOT NULL
                    ORDER BY exit_time DESC LIMIT :limit OFFSET :offset"""),
                {"limit": per_page, "offset": offset},
            )
            trades = [
                {
                    "symbol": r[0],
                    "side": r[1],
                    "amount": r[2],
                    "entry_price": r[3],
                    "exit_price": r[4],
                    "pnl": r[5],
                    "regime": r[6],
                    "entry_time": r[7],
                    "exit_time": r[8],
                    "exit_reason": r[9],
                    "ai_confidence": r[10],
                }
                for r in rows.fetchall()
            ]

            stats = await conn.execute(
                text(
                    "SELECT COUNT(*) FILTER (WHERE pnl > 0), "
                    "COUNT(*) FILTER (WHERE pnl <= 0), "
                    "COALESCE(SUM(pnl), 0) FROM trades WHERE exit_time IS NOT NULL"
                )
            )
            row = stats.fetchone()
            wins = row[0] or 0
            losses = row[1] or 0
            net_pnl = Decimal(str(row[2])) if row[2] else Decimal("0")

        return trades, total, wins, losses, net_pnl

    async def get_trades_since(self, since: datetime) -> list[dict[str, Any]]:
        """Get all trades (open or closed) with entry_time >= since."""
        async with self.db.engine.connect() as conn:
            rows = await conn.execute(
                text("""SELECT id, symbol, side, amount, entry_price, exit_price,
                    pnl, regime, entry_time, exit_time, exit_reason
                    FROM trades WHERE entry_time >= :since
                    ORDER BY entry_time DESC"""),
                {"since": since},
            )
            return [
                {
                    "id": r[0],
                    "symbol": r[1],
                    "side": r[2],
                    "amount": r[3],
                    "entry_price": r[4],
                    "exit_price": r[5],
                    "pnl": r[6],
                    "regime": r[7],
                    "entry_time": r[8],
                    "exit_time": r[9],
                    "exit_reason": r[10],
                }
                for r in rows.fetchall()
            ]

    async def get_open_trade_by_symbol(self, symbol: str) -> dict[str, Any] | None:
        """Get the most recent open trade for a symbol (exit_time IS NULL)."""
        async with self.db.engine.connect() as conn:
            row = await conn.execute(
                text("""SELECT id, symbol, side, amount, entry_price, regime, entry_time
                    FROM trades WHERE symbol = :symbol AND exit_time IS NULL
                    ORDER BY entry_time DESC LIMIT 1"""),
                {"symbol": symbol},
            )
            r = row.fetchone()
            if not r:
                return None
            return {
                "id": r[0],
                "symbol": r[1],
                "side": r[2],
                "amount": r[3],
                "entry_price": r[4],
                "regime": r[5],
                "entry_time": r[6],
            }

    async def record_ai_decision(
        self,
        symbol: str,
        decision_type: str,
        model: str | None = None,
        input_hash: str | None = None,
        output_json: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Record AI decision for audit trail."""
        try:
            async with self.db.engine.begin() as conn:
                await conn.execute(
                    text(
                        """INSERT INTO ai_decisions (symbol, decision_type, model, input_hash, output_json, latency_ms)
                        VALUES (:symbol, :decision_type, :model, :input_hash, :output_json, :latency_ms)"""
                    ),
                    {
                        "symbol": symbol,
                        "decision_type": decision_type,
                        "model": model,
                        "input_hash": input_hash,
                        "output_json": output_json,
                        "latency_ms": latency_ms,
                    },
                )
        except Exception as e:
            metrics.postgres_write_errors.labels(table="ai_decisions").inc()
            logger.error(f"record_ai_decision failed: {e}")
            raise
