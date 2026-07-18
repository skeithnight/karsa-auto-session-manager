"""Shadow Store — Redis position tracking + Postgres trade recording for shadow mode.

ShadowPositionStore: extends PositionStore with shadow:position:* key prefix.
ShadowTradeStore: extends TradeStore targeting shadow_trades table.

Separate namespace from live stores — zero collision risk.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy import text

from app.core import metrics
from app.core.position_store import PositionStore
from app.core.trade_store import TradeStore


class ShadowPositionStore(PositionStore):
    """Redis-backed position tracking with shadow: prefix."""

    def _key(self, symbol: str, side: str) -> str:
        return f"shadow:position:{symbol}:{side}"

    async def cleanup_stale(self, exchange_symbols: set[str]) -> int:
        """No exchange truth in shadow mode — return 0."""
        return 0


class ShadowTradeStore(TradeStore):
    """Postgres CRUD targeting shadow_trades table."""

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
        """Record shadow trade entry. Returns trade id."""
        now = datetime.now(UTC)
        try:
            async with self.db.engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """INSERT INTO shadow_trades (symbol, side, amount, entry_price,
                        regime, entry_time, ai_confidence, entry_regime,
                        initial_risk_per_unit, risk_profile_json, is_shadow)
                        VALUES (:symbol, :side, :amount, :entry_price, :regime,
                        :entry_time, :ai_confidence, :entry_regime,
                        :initial_risk_per_unit, :risk_profile_json, TRUE)
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
                        "initial_risk_per_unit": str(initial_risk_per_unit) if initial_risk_per_unit is not None else None,
                        "risk_profile_json": risk_profile_json,
                    },
                )
                row = result.fetchone()
                await conn.commit()
                trade_id = row[0] if row else 0
                logger.info(f"Shadow trade recorded: {symbol} {side} id={trade_id}")
                return trade_id
        except Exception as e:
            metrics.postgres_write_errors.labels(table="shadow_trades").inc()
            logger.error(f"Shadow record_entry failed: {e}")
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
        """Close shadow trade. Updates most recent open shadow trade for symbol."""
        now = datetime.now(UTC)
        regime_clause = ", regime = :regime" if regime else ""
        try:
            async with self.db.engine.connect() as conn:
                if trade_id is not None:
                    result = await conn.execute(
                        text(f"""UPDATE shadow_trades SET exit_price = :exit_price,
                            pnl = :pnl, exit_reason = :exit_reason,
                            exit_time = :exit_time{regime_clause}
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
                        text(f"""UPDATE shadow_trades SET exit_price = :exit_price,
                            pnl = :pnl, exit_reason = :exit_reason,
                            exit_time = :exit_time{regime_clause}
                            WHERE symbol = :symbol AND exit_time IS NULL
                            ORDER BY entry_time DESC LIMIT 1"""),
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
                rows = result.rowcount or 0
                logger.info(f"Shadow trade closed: {symbol} rows={rows} pnl={pnl}")
                return rows
        except Exception as e:
            metrics.postgres_write_errors.labels(table="shadow_trades").inc()
            logger.error(f"Shadow close_trade failed: {e}")
            raise
