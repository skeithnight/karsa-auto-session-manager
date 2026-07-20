"""Trade Reconciler — compare local Postgres trades against Bybit execution history.

Detects: missing entries (bot was down), missing exits, price mismatches.
Auto-repairs missing entries (regime="RECONCILED"). Alerts on CRITICAL gaps.
Backfills Postgres from Bybit closed PnL on startup.

ponytail: single class, no ABC/interface. Reuses TradeStore + BybitClient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException
from typing import Any

from loguru import logger

from app.bot.alert_service import AlertService
from app.core import metrics
from app.core.trade_store import TradeStore
from app.execution.bybit_client import BybitClient


def _safe_decimal(value: Any, default: str = "0") -> Decimal:
    """Convert value to Decimal safely."""
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, DecimalException):
        return Decimal(default)


@dataclass
class Discrepancy:
    """A mismatch between Bybit and local trade records."""

    kind: str  # missing_entry, missing_exit, price_mismatch, pnl_mismatch
    symbol: str
    side: str
    bybit_data: dict[str, Any]
    local_trade_id: int | None = None
    severity: str = "WARNING"  # WARNING or CRITICAL
    repaired: bool = False


@dataclass
class ReconcileReport:
    """Result of one reconciliation cycle."""

    timestamp: datetime
    bybit_fills_checked: int
    local_trades_checked: int
    discrepancies: list[Discrepancy] = field(default_factory=list)
    repairs_made: int = 0
    errors: list[str] = field(default_factory=list)


def _normalize_side(side: str) -> str:
    """Normalize side to canonical 'Buy'/'Sell'."""
    s = side.lower()
    if s in ("buy", "long"):
        return "Buy"
    if s in ("sell", "short"):
        return "Sell"
    return side


class TradeReconciler:
    """Compare local trade records against Bybit execution history.

    Runs periodically. Detects gaps, auto-repairs where safe, alerts on CRITICAL.
    """

    MAX_PAGES = 3  # 3 pages × 50 = 150 fills max per cycle
    PAGE_SIZE = 50
    GRACE_PERIOD_MINUTES = 5  # exclude very recent trades from comparison
    PRICE_TOLERANCE = Decimal("0.001")  # 0.1% price tolerance
    PNL_TOLERANCE = Decimal("0.05")  # 5% PnL tolerance
    MAX_REPAIRS_PER_CYCLE = 10
    TIME_WINDOW_MINUTES = 5  # match window for entry_time

    def __init__(
        self,
        bybit_client: BybitClient,
        trade_store: TradeStore,
        alert_service: AlertService,
        lookback_hours: int = 24,
    ) -> None:
        self.client = bybit_client
        self.store = trade_store
        self.alert = alert_service
        self.lookback_hours = lookback_hours
        self._backfill_done = False

    async def backfill_from_bybit(self, max_pages: int = 10) -> int:
        """One-time startup backfill: sync ALL Bybit closed PnL → Postgres.

        Fetches up to max_pages × 50 closed PnL records. For each Bybit record
        with no matching local trade, inserts a complete trade (entry+exit) with
        regime="BACKFILL". Returns count of trades inserted.
        """
        if self._backfill_done:
            return 0

        reverse_map = {v: k for k, v in self.client._symbol_map.items()}
        all_records: list[dict[str, Any]] = []
        cursor: str | None = None

        for _ in range(max_pages):
            result = await self.client.get_closed_pnl(limit=50, cursor=cursor)
            records = result.get("closed_pnl", [])
            if not records:
                break
            all_records.extend(records)
            cursor = result.get("cursor")
            if not cursor:
                break

        if not all_records:
            logger.info("Trade backfill: no Bybit closed PnL records found")
            self._backfill_done = True
            return 0

        # Fetch all existing local trades for dedup
        oldest_ts = min(int(r.get("createdTime", "0")) for r in all_records)
        oldest_dt = datetime.fromtimestamp(oldest_ts / 1000, tz=UTC)
        local_trades = await self.store.get_trades_since(oldest_dt)

        # Index local trades by (symbol, side, approximate entry_time) for dedup
        local_keys: set = set()
        for t in local_trades:
            entry_time = t.get("entry_time")
            if entry_time:
                # Round to minute for fuzzy matching
                minute_key = entry_time.strftime("%Y%m%d%H%M")
                local_keys.add(
                    f"{t['symbol']}:{_normalize_side(t['side'])}:{minute_key}"
                )

        inserted = 0
        for r in all_records:
            bybit_sym = r.get("symbol", "")
            ccxt_sym = reverse_map.get(bybit_sym, bybit_sym)
            if ccxt_sym.endswith("USDT") and "/" not in ccxt_sym:
                ccxt_sym = ccxt_sym[:-4] + "/USDT"

            # Bybit closedPnl side is the closing order's side. Invert to get position side.
            closing_side = _normalize_side(r.get("side", ""))
            side = "Buy" if closing_side == "Sell" else "Sell"

            entry_price = _safe_decimal(r.get("avgEntryPrice", "0"))
            exit_price = _safe_decimal(r.get("avgExitPrice", "0"))
            qty = _safe_decimal(r.get("qty", "0"))
            pnl = _safe_decimal(r.get("closedPnl", "0"))
            created_ts = int(r.get("createdTime", "0"))
            updated_ts = int(r.get("updatedTime", r.get("createdTime", "0")))
            entry_time = datetime.fromtimestamp(created_ts / 1000, tz=UTC)
            exit_time = datetime.fromtimestamp(updated_ts / 1000, tz=UTC)

            # Dedup check
            minute_key = entry_time.strftime("%Y%m%d%H%M")
            if f"{ccxt_sym}:{side}:{minute_key}" in local_keys:
                continue

            if entry_price <= 0 or qty <= 0:
                continue

            try:
                await self.store.record_full_trade(
                    symbol=ccxt_sym,
                    side=side,
                    amount=qty,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    pnl=pnl,
                    regime="BACKFILL",
                    entry_time=entry_time,
                    exit_time=exit_time,
                    exit_reason="bybit_sync",
                )
                local_keys.add(f"{ccxt_sym}:{side}:{minute_key}")
                inserted += 1
            except Exception as e:
                logger.warning(f"Trade backfill: failed for {ccxt_sym} {side}: {e}")

        self._backfill_done = True
        logger.info(
            f"Trade backfill complete: {inserted} trades inserted from "
            f"{len(all_records)} Bybit records"
        )
        return inserted

    async def reconcile(self) -> ReconcileReport:
        """Main entry. Fetch-compare-repair cycle."""
        now = datetime.now(UTC)
        since = now - timedelta(hours=self.lookback_hours)
        grace = now - timedelta(minutes=self.GRACE_PERIOD_MINUTES)
        report = ReconcileReport(
            timestamp=now, bybit_fills_checked=0, local_trades_checked=0
        )

        try:
            # 1. Fetch Bybit executions (paginated)
            bybit_fills = await self._fetch_bybit_fills(since)
            report.bybit_fills_checked = len(bybit_fills)

            # 2. Fetch Bybit closed PnL
            bybit_closed = await self._fetch_bybit_closed_pnl(since)

            # 3. Fetch local trades
            local_trades = await self.store.get_trades_since(since)
            report.local_trades_checked = len(local_trades)

            # 4. Pass 1: match fills → detect missing_entry, price_mismatch
            fill_discrepancies = self._compare_fills(bybit_fills, local_trades, grace)
            report.discrepancies.extend(fill_discrepancies)

            # 5. Pass 2: match closed PnL → detect missing_exit
            pnl_discrepancies = self._compare_closed_pnl(bybit_closed, local_trades)
            report.discrepancies.extend(pnl_discrepancies)

            # 6. Auto-repair missing entries
            repairs = await self._auto_repair(report.discrepancies)
            report.repairs_made = repairs

            # 7. Check unrealized PnL drift (exchange vs local)
            await self._check_unrealized_drift()

            # 8. Alert on critical discrepancies
            await self._alert_discrepancies(report.discrepancies)

        except Exception as e:
            report.errors.append(str(e))
            logger.error(f"Trade reconciler error: {e}")
            metrics.trade_reconcile_errors.labels(error_type=type(e).__name__).inc()

        if report.discrepancies:
            logger.warning(
                f"Trade reconciliation: {len(report.discrepancies)} discrepancies, "
                f"{report.repairs_made} repairs out of {report.bybit_fills_checked} fills checked"
            )
        else:
            logger.info(
                f"Trade reconciliation: all clean ({report.bybit_fills_checked} fills checked)"
            )

        return report

    async def _fetch_bybit_fills(self, since: datetime) -> list[dict[str, Any]]:
        """Fetch all executions since timestamp, with pagination."""
        all_fills: list[dict[str, Any]] = []
        cursor: str | None = None
        since_ms = str(int(since.timestamp() * 1000))

        for _ in range(self.MAX_PAGES):
            result = await self.client.get_executions(
                limit=self.PAGE_SIZE, cursor=cursor
            )
            fills = result.get("executions", [])
            if not fills:
                break

            # Filter by time — stop if we've gone past our window
            for f in fills:
                exec_time_ms = f.get("execTime", "0")
                if int(exec_time_ms) >= int(since_ms):
                    all_fills.append(f)

            # Check if oldest fill in this page is before our window
            oldest_ms = int(fills[-1].get("execTime", "0"))
            if oldest_ms < int(since_ms):
                break  # No need to fetch more pages

            cursor = result.get("cursor")
            if not cursor:
                break

        if cursor:
            logger.warning(
                "Trade reconciler: more pages available, lookback window may be too short"
            )
        return all_fills

    async def _fetch_bybit_closed_pnl(self, since: datetime) -> list[dict[str, Any]]:
        """Fetch closed PnL records since timestamp."""
        all_records: list[dict[str, Any]] = []
        cursor: str | None = None
        since_ms = str(int(since.timestamp() * 1000))

        for _ in range(self.MAX_PAGES):
            result = await self.client.get_closed_pnl(
                limit=self.PAGE_SIZE, cursor=cursor
            )
            records = result.get("closed_pnl", [])
            if not records:
                break

            for r in records:
                created_ms = r.get("createdTime", "0")
                if int(created_ms) >= int(since_ms):
                    all_records.append(r)

            oldest_ms = int(records[-1].get("createdTime", "0"))
            if oldest_ms < int(since_ms):
                break

            cursor = result.get("cursor")
            if not cursor:
                break

        return all_records

    def _compare_fills(
        self,
        bybit_fills: list[dict[str, Any]],
        local_trades: list[dict[str, Any]],
        grace: datetime,
    ) -> list[Discrepancy]:
        """Pass 1: match Bybit executions to local trades."""
        discrepancies: list[Discrepancy] = []
        window = timedelta(minutes=self.TIME_WINDOW_MINUTES)

        # Index local trades by (symbol, normalized_side) for fast lookup
        local_index: dict[str, list[dict[str, Any]]] = {}
        for t in local_trades:
            key = f"{t['symbol']}:{_normalize_side(t['side'])}"
            local_index.setdefault(key, []).append(t)

        # Group Bybit fills by (symbol, side, orderId) to handle partial fills
        grouped: dict[str, list[dict[str, Any]]] = {}
        for f in bybit_fills:
            # Skip closing fills (missing_exit handles them)
            if _safe_decimal(f.get("closedSize", "0")) > 0:
                continue

            bybit_sym = f.get("symbol", "")
            # Convert Bybit symbol to ccxt format
            reverse_map = {v: k for k, v in self.client._symbol_map.items()}
            ccxt_sym = reverse_map.get(bybit_sym, bybit_sym)
            if ccxt_sym.endswith("USDT") and "/" not in ccxt_sym:
                ccxt_sym = ccxt_sym[:-4] + "/USDT"

            side = _normalize_side(f.get("side", ""))
            order_id = f.get("orderId", "unknown")
            key = f"{ccxt_sym}:{side}:{order_id}"
            grouped.setdefault(key, []).append(f)

        for key, fills in grouped.items():
            parts = key.split(":")
            ccxt_sym = parts[0]
            side = parts[1]

            # Compute weighted average price for the order
            total_qty = Decimal("0")
            weighted_price = Decimal("0")
            for f in fills:
                qty = _safe_decimal(f.get("execQty", "0"))
                price = _safe_decimal(f.get("execPrice", "0"))
                weighted_price += price * qty
                total_qty += qty
            if total_qty > 0:
                weighted_price = weighted_price / total_qty

            exec_time_ms = int(fills[0].get("execTime", "0"))
            exec_time = datetime.fromtimestamp(exec_time_ms / 1000, tz=UTC)

            # Skip very recent fills (race with live trading)
            if exec_time > grace:
                continue

            # Find matching local trade
            match_key = f"{ccxt_sym}:{side}"
            local_candidates = local_index.get(match_key, [])
            matched = False
            for t in local_candidates:
                entry_time = t.get("entry_time")
                if entry_time and isinstance(entry_time, datetime):
                    if (
                        abs((entry_time - exec_time).total_seconds())
                        < window.total_seconds()
                    ):
                        matched = True
                        # Check price mismatch
                        local_price = _safe_decimal(t.get("entry_price", "0"))
                        if local_price > 0 and weighted_price > 0:
                            diff = abs(local_price - weighted_price) / weighted_price
                            if diff > self.PRICE_TOLERANCE:
                                discrepancies.append(
                                    Discrepancy(
                                        kind="price_mismatch",
                                        symbol=ccxt_sym,
                                        side=side,
                                        bybit_data={
                                            "bybit_price": str(weighted_price),
                                            "local_price": str(local_price),
                                        },
                                        local_trade_id=t.get("id"),
                                        severity="WARNING",
                                    )
                                )
                        break

            if not matched:
                discrepancies.append(
                    Discrepancy(
                        kind="missing_entry",
                        symbol=ccxt_sym,
                        side=side,
                        bybit_data={
                            "price": str(weighted_price),
                            "qty": str(total_qty),
                            "exec_time": exec_time.isoformat(),
                            "order_id": fills[0].get("orderId", ""),
                        },
                        severity="WARNING",
                    )
                )

        return discrepancies

    def _compare_closed_pnl(
        self,
        bybit_records: list[dict[str, Any]],
        local_trades: list[dict[str, Any]],
    ) -> list[Discrepancy]:
        """Pass 2: match Bybit closed PnL to local trades."""
        discrepancies: list[Discrepancy] = []
        reverse_map = {v: k for k, v in self.client._symbol_map.items()}

        # Index local open trades by symbol
        open_trades = [t for t in local_trades if t.get("exit_time") is None]
        open_index: dict[str, list[dict[str, Any]]] = {}
        for t in open_trades:
            open_index.setdefault(t["symbol"], []).append(t)

        # Index all closed local trades by symbol for PnL comparison
        closed_trades = [t for t in local_trades if t.get("exit_time") is not None]
        closed_index: dict[str, list[dict[str, Any]]] = {}
        for t in closed_trades:
            closed_index.setdefault(t["symbol"], []).append(t)

        # Process each closed PnL record independently
        matched_local_ids = set()

        for r in bybit_records:
            bybit_sym = r.get("symbol", "")
            ccxt_sym = reverse_map.get(bybit_sym, bybit_sym)
            if ccxt_sym.endswith("USDT") and "/" not in ccxt_sym:
                ccxt_sym = ccxt_sym[:-4] + "/USDT"

            # Bybit closedPnl side is the closing order's side. Invert to get position side.
            closing_side = _normalize_side(r.get("side", ""))
            side = "Buy" if closing_side == "Sell" else "Sell"

            pnl = _safe_decimal(r.get("closedPnl", "0"))
            qty = _safe_decimal(r.get("qty", "0"))
            entry = _safe_decimal(r.get("avgEntryPrice", "0"))
            exit_price = _safe_decimal(r.get("avgExitPrice", "0"))
            updated_time_ms = int(r.get("updatedTime", r.get("createdTime", "0")))

            matched = False

            # 1. Check if there's an open local trade to close
            open_candidates = open_index.get(ccxt_sym, [])
            for t in open_candidates:
                if (
                    _normalize_side(t["side"]) == side
                    and t.get("id") not in matched_local_ids
                ):
                    matched_local_ids.add(t.get("id"))
                    discrepancies.append(
                        Discrepancy(
                            kind="missing_exit",
                            symbol=ccxt_sym,
                            side=side,
                            bybit_data={
                                "closed_pnl": str(pnl),
                                "avg_entry_price": str(entry),
                                "avg_exit_price": str(exit_price),
                                "qty": str(qty),
                                "updated_time_ms": str(updated_time_ms),
                            },
                            local_trade_id=t.get("id"),
                            severity="CRITICAL",
                        )
                    )
                    matched = True
                    break

            if matched:
                continue

            # 2. Check if it matches an already closed trade
            closed_candidates = closed_index.get(ccxt_sym, [])
            for t in closed_candidates:
                if (
                    _normalize_side(t["side"]) != side
                    or t.get("id") in matched_local_ids
                ):
                    continue

                local_pnl = _safe_decimal(t.get("pnl", "0"))

                # Check if it matches closely enough in time (within a few hours) or exact PnL
                local_exit_time = t.get("exit_time")
                if local_exit_time:
                    bybit_exit_time = datetime.fromtimestamp(
                        updated_time_ms / 1000, tz=UTC
                    )
                    time_diff = abs((local_exit_time - bybit_exit_time).total_seconds())
                    if time_diff < 1800:  # 30 mins
                        matched_local_ids.add(t.get("id"))
                        matched = True
                        # Check PnL mismatch
                        if pnl != Decimal("0") and local_pnl != Decimal("0"):
                            diff = abs(local_pnl - pnl) / max(
                                abs(pnl), Decimal("0.0001")
                            )
                            if diff > self.PNL_TOLERANCE:
                                discrepancies.append(
                                    Discrepancy(
                                        kind="pnl_mismatch",
                                        symbol=ccxt_sym,
                                        side=side,
                                        bybit_data={
                                            "bybit_pnl": str(pnl),
                                            "local_pnl": str(local_pnl),
                                        },
                                        local_trade_id=t.get("id"),
                                        severity="WARNING",
                                    )
                                )
                        break

            if not matched:
                # No open trade to close, no closed trade matched -> Backfill as complete trade
                discrepancies.append(
                    Discrepancy(
                        kind="missing_exit",
                        symbol=ccxt_sym,
                        side=side,
                        bybit_data={
                            "closed_pnl": str(pnl),
                            "avg_entry_price": str(entry),
                            "avg_exit_price": str(exit_price),
                            "qty": str(qty),
                            "updated_time_ms": str(updated_time_ms),
                        },
                        local_trade_id=None,
                        severity="WARNING",
                    )
                )

        return discrepancies

    async def _check_unrealized_drift(self) -> None:
        """Compare exchange-reported unrealized PnL vs local calculation.

        Alerts if divergence exceeds 1%. Auto-repair is not attempted —
        unrealized PnL is transient and drift is expected to be small.
        """
        try:
            positions = await self.client.fetch_positions()
        except Exception as e:
            logger.warning(f"PnL drift check: fetch_positions failed: {e}")
            return

        for pos in positions:
            symbol = pos.get("symbol", "")
            side = pos.get("side", "")
            exchange_upnl = Decimal(str(pos.get("unrealized_pnl", "0")))
            entry_price = Decimal(str(pos.get("entry_price", "0")))
            amount = Decimal(str(pos.get("amount", "0")))

            if exchange_upnl == 0 or amount <= 0 or entry_price <= 0:
                continue

            # Fetch current price from Redis (same source as APM)
            try:
                redis = self.client._redis if hasattr(self.client, "_redis") else None
                if not redis:
                    continue
                state = await redis.get_global_state(symbol)
                if not state:
                    continue
                best_bid = state.get("best_bid")
                best_ask = state.get("best_ask")
                if not best_bid or not best_ask:
                    continue
                mid_price = (Decimal(str(best_bid)) + Decimal(str(best_ask))) / 2
            except Exception:
                continue

            # Calculate local unrealized PnL
            if side == "LONG":
                local_upnl = (mid_price - entry_price) * amount
            else:
                local_upnl = (entry_price - mid_price) * amount

            # Compare with tolerance
            if abs(exchange_upnl) > 0:
                drift_pct = abs(local_upnl - exchange_upnl) / abs(exchange_upnl)
                if drift_pct > Decimal("0.01"):
                    logger.warning(
                        f"PnL DRIFT: {symbol} {side} exchange={exchange_upnl} "
                        f"local={local_upnl} drift={drift_pct:.2%}"
                    )
                    metrics.pnl_unrealized_drift.labels(symbol=symbol).set(
                        float(drift_pct)
                    )
                    if drift_pct > Decimal("0.05"):
                        await self.alert.send(
                            f"⚠️ PnL DRIFT ALERT: {symbol} {side} "
                            f"exchange={exchange_upnl:.2f} local={local_upnl:.2f} "
                            f"drift={drift_pct:.2%}"
                        )

    async def _auto_repair(self, discrepancies: list[Discrepancy]) -> int:
        """Auto-repair missing entries and missing exits. Returns count of repairs made."""
        repairable = [
            d
            for d in discrepancies
            if d.kind in ("missing_entry", "missing_exit") and not d.repaired
        ]
        if not repairable:
            kinds = {}
            for d in discrepancies:
                kinds[d.kind] = kinds.get(d.kind, 0) + 1
            logger.info(f"Trade reconciler: 0 repairable — breakdown: {kinds}")
            return 0

        if len(repairable) > self.MAX_REPAIRS_PER_CYCLE:
            logger.warning(
                f"Trade reconciler: {len(repairable)} repairable discrepancies exceeds cap "
                f"({self.MAX_REPAIRS_PER_CYCLE}). Repairing first {self.MAX_REPAIRS_PER_CYCLE} this cycle."
            )
            repairable = repairable[: self.MAX_REPAIRS_PER_CYCLE]

        repairs = 0
        now = datetime.now(UTC)
        for d in repairable:
            try:
                amount = _safe_decimal(d.bybit_data.get("qty", "0"))
                if d.kind == "missing_entry":
                    price = _safe_decimal(d.bybit_data.get("price", "0"))
                    if amount <= 0 or price <= 0:
                        logger.warning(
                            f"Trade reconciler: skip missing_entry {d.symbol} — amount={amount} price={price}"
                        )
                        continue
                    # Dedup: skip if an open trade already exists for this symbol+side
                    existing = await self.store.get_open_trade_by_symbol(d.symbol)
                    if existing and _normalize_side(
                        existing.get("side", "")
                    ) == _normalize_side(d.side):
                        logger.info(
                            f"Trade reconciler: skip missing_entry {d.symbol} {d.side} — open trade already exists id={existing['id']}"
                        )
                        d.repaired = True
                        continue
                    await self.store.record_entry(
                        symbol=d.symbol,
                        side=d.side,
                        amount=amount,
                        entry_price=price,
                        regime="RECONCILED",
                    )
                    d.repaired = True
                    repairs += 1
                    logger.info(
                        f"Trade reconciler: repaired missing entry {d.symbol} {d.side} "
                        f"@ {price} qty={amount}"
                    )
                    await self._audit_repair(
                        d, {"entry_price": str(price), "qty": str(amount)}
                    )
                elif d.kind == "missing_exit":
                    exit_price = _safe_decimal(d.bybit_data.get("avg_exit_price", "0"))
                    pnl = _safe_decimal(d.bybit_data.get("closed_pnl", "0"))
                    if amount <= 0 or exit_price <= 0:
                        logger.warning(
                            f"Trade reconciler: skip missing_exit {d.symbol} — amount={amount} exit_price={exit_price}"
                        )
                        continue
                    if d.local_trade_id:
                        # Close existing open trade — no duplicate
                        updated = await self.store.close_trade(
                            symbol=d.symbol,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason="bybit_reconciled",
                            trade_id=d.local_trade_id,
                            regime="RECONCILED",
                        )
                        if updated == 0:
                            logger.warning(
                                f"Trade reconciler: missing_exit repair got 0 rows — "
                                f"{d.symbol} {d.side} id={d.local_trade_id} "
                                f"(already closed or missing). Skipping."
                            )
                            continue
                    else:
                        # No local trade found — backfill complete trade
                        entry_price = _safe_decimal(
                            d.bybit_data.get("avg_entry_price", "0")
                        )
                        if entry_price <= 0:
                            continue
                        await self.store.record_full_trade(
                            symbol=d.symbol,
                            side=d.side,
                            amount=amount,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            regime="RECONCILED",
                            entry_time=now - timedelta(hours=1),
                            exit_time=now,
                            exit_reason="bybit_reconciled",
                        )
                    d.repaired = True
                    repairs += 1
                    logger.info(
                        f"Trade reconciler: repaired missing exit {d.symbol} {d.side} "
                        f"pnl={pnl}"
                    )
                    await self._audit_repair(
                        d,
                        {
                            "exit_price": str(exit_price),
                            "pnl": str(pnl),
                            "trade_id": d.local_trade_id,
                        },
                    )

                    # Send normal exit alert if it was recent
                    try:
                        updated_time_ms = int(d.bybit_data.get("updated_time_ms", 0))
                        now_ms = int(now.timestamp() * 1000)
                        is_recent = (now_ms - updated_time_ms) < (
                            15 * 60 * 1000
                        )  # 15 minutes

                        if is_recent:
                            from app.bot.utils.formatters import (
                                format_breakeven_alert,
                                format_sl_alert,
                                format_tp_alert,
                            )

                            entry_price = _safe_decimal(
                                d.bybit_data.get("avg_entry_price", "0")
                            )
                            pnl_pct = (
                                (pnl / (entry_price * amount) * 100)
                                if entry_price * amount
                                else Decimal("0")
                            )
                            if pnl > 0:
                                msg = format_tp_alert(
                                    d.symbol,
                                    d.side,
                                    float(entry_price),
                                    float(exit_price),
                                    float(pnl),
                                    float(pnl_pct),
                                )
                            elif pnl < 0:
                                msg = format_sl_alert(
                                    d.symbol,
                                    d.side,
                                    float(entry_price),
                                    float(exit_price),
                                    float(pnl),
                                    float(pnl_pct),
                                )
                            else:
                                msg = format_breakeven_alert(
                                    d.symbol,
                                    d.side,
                                    float(entry_price),
                                    float(exit_price),
                                    float(pnl),
                                    float(pnl_pct),
                                )
                            await self.alert.send(msg)
                        else:
                            logger.info(
                                f"Trade reconciler: skipping Telegram alert for old hanging position {d.symbol} {d.side}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Trade reconciler: failed to send exit alert: {e}"
                        )
            except Exception as e:
                logger.error(
                    f"Trade reconciler: repair failed for {d.symbol} {d.side}: {e}"
                )
                metrics.trade_reconcile_errors.labels(error_type="repair_failure").inc()

        return repairs

    async def _alert_discrepancies(self, discrepancies: list[Discrepancy]) -> None:
        """Send Telegram alert for CRITICAL discrepancies."""
        critical = [
            d for d in discrepancies if d.severity == "CRITICAL" and not d.repaired
        ]
        if not critical:
            return

        lines = ["⚠️ <b>TRADE RECONCILIATION ALERT</b>\n"]
        for d in critical:
            lines.append(
                f"🔴 {d.kind}: {d.symbol} {d.side}\n"
                f"   Bybit: {d.bybit_data}\n"
                f"   Local trade ID: {d.local_trade_id or 'N/A'}\n"
            )
        lines.append(f"\n{len(critical)} critical issue(s) require manual review.")

        try:
            await self.alert.send("\n".join(lines))
        except Exception as e:
            logger.error(f"Trade reconciler alert failed: {e}")

    async def _audit_repair(self, d: Discrepancy, details: dict[str, Any]) -> None:
        """Write reconciliation repair to ai_decisions audit trail."""
        import json

        try:
            await self.store.record_ai_decision(
                symbol=d.symbol,
                decision_type="reconciliation_repair",
                output_json=json.dumps(
                    {
                        "kind": d.kind,
                        "side": d.side,
                        "trade_id": d.local_trade_id,
                        **details,
                    }
                ),
            )
        except Exception as e:
            logger.warning(f"Trade reconciler: audit log failed for {d.symbol}: {e}")
