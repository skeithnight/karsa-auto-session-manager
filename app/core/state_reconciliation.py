"""State Reconciliation — sync exchange positions/orders with internal state on boot.

On startup, fetches all open positions and active orders from Bybit API,
compares with internal PositionStore and TradeStore, then:
  - Updates Redis position keys for any drift
  - Records orphaned positions (exchange has, we don't)
  - Logs discrepancies for operator visibility

Design: fail-safe. Any exception during reconciliation blocks startup —
operator must fix state before trading resumes.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from loguru import logger
class StateReconciler:
    """Reconcile exchange state with internal stores on startup."""

    def __init__(
        self,
        bybit_client: Any,
        position_store: Any,
        trade_store: Any | None,
        db_engine: Any,
    ) -> None:
        self._bybit = bybit_client
        self._pos_store = position_store
        self._trade_store = trade_store
        self._db = db_engine
        self._results: dict[str, Any] = {}

    async def reconcile(self) -> dict[str, Any]:
        """Run full reconciliation. Returns summary dict.

        Raises on critical failure — caller must handle.
        """
        logger.info("StateReconciler: starting reconciliation")
        start = asyncio.get_event_loop().time()

        # 1. Fetch exchange state
        exchange_positions = await self._fetch_exchange_positions()
        exchange_orders = await self._fetch_exchange_orders()

        # 2. Fetch internal state
        internal_positions = await self._fetch_internal_positions()

        # 3. Diff positions
        orphaned, missing, updated = self._diff_positions(
            exchange_positions, internal_positions,
        )

        # 4. Update internal store for any drift
        for sym, side in updated:
            logger.info("StateReconciler: updating position %s %s", sym, side)

        # 5. Record orphaned positions in trade store
        for pos in orphaned:
            await self._record_orphaned(pos)

        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        self._results = {
            "exchange_positions": len(exchange_positions),
            "internal_positions": len(internal_positions),
            "orphaned": len(orphaned),
            "missing": len(missing),
            "updated": len(updated),
            "active_orders": len(exchange_orders),
            "elapsed_ms": round(elapsed),
        }

        logger.info(
            "StateReconciler: done — exchange=%d internal=%d orphaned=%d "
            "missing=%d orders=%d elapsed=%.0fms",
            self._results["exchange_positions"],
            self._results["internal_positions"],
            self._results["orphaned"],
            self._results["missing"],
            self._results["active_orders"],
            elapsed,
        )
        return self._results

    async def _fetch_exchange_positions(self) -> list[dict]:
        """Fetch all open positions from Bybit."""
        try:
            positions = await self._bybit.fetch_positions()
            return [
                {
                    "symbol": p.get("symbol", ""),
                    "side": p.get("side", ""),
                    "contracts": Decimal(str(p.get("contracts", 0))),
                    "entry_price": Decimal(str(p.get("entry_price", 0))),
                    "unrealized_pnl": Decimal(str(p.get("unrealized_pnl", 0))),
                }
                for p in (positions or [])
            ]
        except Exception:
            logger.exception("StateReconciler: failed to fetch exchange positions")
            return []

    async def _fetch_exchange_orders(self) -> list[dict]:
        """Fetch all active orders from Bybit."""
        try:
            orders = await self._bybit.fetch_open_orders()
            return [
                {
                    "id": o.get("id", ""),
                    "symbol": o.get("symbol", ""),
                    "side": o.get("side", ""),
                    "price": Decimal(str(o.get("price", 0))),
                    "amount": Decimal(str(o.get("amount", 0))),
                }
                for o in (orders or [])
            ]
        except Exception:
            logger.exception("StateReconciler: failed to fetch exchange orders")
            return []

    async def _fetch_internal_positions(self) -> list[dict]:
        """Fetch positions from internal Redis store."""
        try:
            raw = await self._pos_store.list_all()
            return raw or []
        except Exception:
            logger.exception("StateReconciler: failed to fetch internal positions")
            return []

    def _diff_positions(
        self,
        exchange: list[dict],
        internal: list[dict],
    ) -> tuple[list[dict], list[str], list[tuple[str, str]]]:
        """Compare exchange vs internal positions.

        Returns:
            orphaned: positions on exchange but not internal
            missing: symbols in internal but not on exchange
            updated: (symbol, side) pairs that need internal state refresh
        """
        exchange_map: dict[str, dict] = {}
        for p in exchange:
            key = f"{p['symbol']}:{p['side']}"
            exchange_map[key] = p

        internal_map: dict[str, dict] = {}
        for p in internal:
            sym = p.get("symbol", "")
            side = p.get("side", "")
            key = f"{sym}:{side}"
            internal_map[key] = p

        orphaned = [p for k, p in exchange_map.items() if k not in internal_map]
        missing = [
            p.get("symbol", "")
            for k, p in internal_map.items()
            if k not in exchange_map
        ]
        updated = []
        for key, ex_p in exchange_map.items():
            in_p = internal_map.get(key)
            if in_p is not None:
                ex_amt = ex_p.get("contracts", Decimal("0"))
                in_amt = Decimal(str(in_p.get("amount", in_p.get("contracts", 0))))
                if ex_amt != in_amt:
                    updated.append((ex_p["symbol"], ex_p["side"]))

        return orphaned, missing, updated

    async def _record_orphaned(self, pos: dict) -> None:
        """Record orphaned exchange position in trade store for visibility."""
        if self._trade_store is None:
            return
        try:
            async with self._db.acquire() as conn:
                await conn.execute(
                    """
                        INSERT INTO trades (
                            symbol, side, amount, entry_price, regime,
                            entry_time, exit_time, pnl
                        ) VALUES (
                            $1, $2, $3, $4, 'RECONCILED',
                            NOW(), NULL, $5
                        )
                    """,
                    pos["symbol"],
                    pos["side"],
                    str(pos["contracts"]),
                    str(pos["entry_price"]),
                    str(pos.get("unrealized_pnl", 0)),
                )
            logger.info(
                "StateReconciler: recorded orphaned position %s %s",
                pos["symbol"], pos["side"],
            )
        except Exception:
            logger.exception("StateReconciler: failed to record orphaned position")

    def get_results(self) -> dict[str, Any]:
        """Return last reconciliation results."""
        return dict(self._results)
