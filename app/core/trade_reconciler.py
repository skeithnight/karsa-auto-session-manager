"""Trade Reconciler — compare local Postgres trades against Bybit execution history.

Detects: missing entries (bot was down), missing exits, price mismatches.
Auto-repairs missing entries (regime="RECONCILED"). Alerts on CRITICAL gaps.
Backfills Postgres from Bybit closed PnL on startup.

ponytail: single class, no ABC/interface. Reuses TradeStore + BybitClient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from typing import Any, Dict, List, Optional

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
    bybit_data: Dict[str, Any]
    local_trade_id: Optional[int] = None
    severity: str = "WARNING"  # WARNING or CRITICAL
    repaired: bool = False


@dataclass
class ReconcileReport:
    """Result of one reconciliation cycle."""

    timestamp: datetime
    bybit_fills_checked: int
    local_trades_checked: int
    discrepancies: List[Discrepancy] = field(default_factory=list)
    repairs_made: int = 0
    errors: List[str] = field(default_factory=list)


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
        all_records: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

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
        oldest_dt = datetime.fromtimestamp(oldest_ts / 1000, tz=timezone.utc)
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
            entry_time = datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc)
            exit_time = datetime.fromtimestamp(updated_ts / 1000, tz=timezone.utc)

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
        now = datetime.now(timezone.utc)
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

            # 7. Alert on critical discrepancies
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

    async def _fetch_bybit_fills(self, since: datetime) -> List[Dict[str, Any]]:
        """Fetch all executions since timestamp, with pagination."""
        all_fills: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
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

    async def _fetch_bybit_closed_pnl(self, since: datetime) -> List[Dict[str, Any]]:
        """Fetch closed PnL records since timestamp."""
        all_records: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
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
        bybit_fills: List[Dict[str, Any]],
        local_trades: List[Dict[str, Any]],
        grace: datetime,
    ) -> List[Discrepancy]:
        """Pass 1: match Bybit executions to local trades."""
        discrepancies: List[Discrepancy] = []
        window = timedelta(minutes=self.TIME_WINDOW_MINUTES)

        # Index local trades by (symbol, normalized_side) for fast lookup
        local_index: Dict[str, List[Dict[str, Any]]] = {}
        for t in local_trades:
            key = f"{t['symbol']}:{_normalize_side(t['side'])}"
            local_index.setdefault(key, []).append(t)

        # Group Bybit fills by (symbol, side, orderId) to handle partial fills
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for f in bybit_fills:
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
            exec_time = datetime.fromtimestamp(exec_time_ms / 1000, tz=timezone.utc)

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
                        < window.total_seconds() * 60
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
        bybit_records: List[Dict[str, Any]],
        local_trades: List[Dict[str, Any]],
    ) -> List[Discrepancy]:
        """Pass 2: match Bybit closed PnL to local trades."""
        discrepancies: List[Discrepancy] = []
        reverse_map = {v: k for k, v in self.client._symbol_map.items()}

        # Index local open trades by symbol
        open_trades = [t for t in local_trades if t.get("exit_time") is None]
        open_index: Dict[str, List[Dict[str, Any]]] = {}
        for t in open_trades:
            open_index.setdefault(t["symbol"], []).append(t)

        # Index all closed local trades by symbol for PnL comparison
        closed_trades = [t for t in local_trades if t.get("exit_time") is not None]
        closed_index: Dict[str, List[Dict[str, Any]]] = {}
        for t in closed_trades:
            closed_index.setdefault(t["symbol"], []).append(t)

        # Group Bybit closed PnL records by (symbol, position_side) to combine partial fills
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for r in bybit_records:
            bybit_sym = r.get("symbol", "")
            ccxt_sym = reverse_map.get(bybit_sym, bybit_sym)
            if ccxt_sym.endswith("USDT") and "/" not in ccxt_sym:
                ccxt_sym = ccxt_sym[:-4] + "/USDT"

            # Bybit closedPnl side is the closing order's side. Invert to get position side.
            closing_side = _normalize_side(r.get("side", ""))
            side = "Buy" if closing_side == "Sell" else "Sell"
            
            key = f"{ccxt_sym}:{side}"
            grouped.setdefault(key, []).append(r)

        matched_local_ids = set()

        for key, records in grouped.items():
            ccxt_sym, side = key.split(":")
            
            total_pnl = Decimal("0")
            total_qty = Decimal("0")
            weighted_entry = Decimal("0")
            weighted_exit = Decimal("0")
            
            for r in records:
                qty = _safe_decimal(r.get("qty", "0"))
                pnl = _safe_decimal(r.get("closedPnl", "0"))
                entry = _safe_decimal(r.get("avgEntryPrice", "0"))
                exit_price = _safe_decimal(r.get("avgExitPrice", "0"))
                
                total_pnl += pnl
                total_qty += qty
                weighted_entry += qty * entry
                weighted_exit += qty * exit_price
                
            if total_qty > 0:
                avg_entry = weighted_entry / total_qty
                avg_exit = weighted_exit / total_qty
            else:
                avg_entry = Decimal("0")
                avg_exit = Decimal("0")

            # Check if there's an open local trade that should be closed
            open_candidates = open_index.get(ccxt_sym, [])
            for t in open_candidates:
                if _normalize_side(t["side"]) == side and t.get("id") not in matched_local_ids:
                    matched_local_ids.add(t.get("id"))
                    discrepancies.append(
                        Discrepancy(
                            kind="missing_exit",
                            symbol=ccxt_sym,
                            side=side,
                            bybit_data={
                                "closed_pnl": str(total_pnl),
                                "avg_entry_price": str(avg_entry),
                                "avg_exit_price": str(avg_exit),
                                "qty": str(total_qty),
                            },
                            local_trade_id=t.get("id"),
                            severity="CRITICAL",
                        )
                    )
                    break
            else:
                # No open trade found — check PnL match on closed trades
                closed_candidates = closed_index.get(ccxt_sym, [])
                for t in closed_candidates:
                    if _normalize_side(t["side"]) != side or t.get("id") in matched_local_ids:
                        continue
                        
                    local_pnl = _safe_decimal(t.get("pnl", "0"))
                    if total_pnl != Decimal("0") and local_pnl != Decimal("0"):
                        diff = abs(local_pnl - total_pnl) / abs(total_pnl)
                        if diff > self.PNL_TOLERANCE:
                            matched_local_ids.add(t.get("id"))
                            discrepancies.append(
                                Discrepancy(
                                    kind="pnl_mismatch",
                                    symbol=ccxt_sym,
                                    side=side,
                                    bybit_data={
                                        "bybit_pnl": str(total_pnl),
                                        "local_pnl": str(local_pnl),
                                    },
                                    local_trade_id=t.get("id"),
                                    severity="WARNING",
                                )
                            )
                            break

        return discrepancies

    async def _auto_repair(self, discrepancies: List[Discrepancy]) -> int:
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
        now = datetime.now(timezone.utc)
        for d in repairable:
            try:
                amount = _safe_decimal(d.bybit_data.get("qty", "0"))
                if d.kind == "missing_entry":
                    price = _safe_decimal(d.bybit_data.get("price", "0"))
                    if amount <= 0 or price <= 0:
                        logger.warning(f"Trade reconciler: skip missing_entry {d.symbol} — amount={amount} price={price}")
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
                    await self._audit_repair(d, {"entry_price": str(price), "qty": str(amount)})
                elif d.kind == "missing_exit":
                    exit_price = _safe_decimal(d.bybit_data.get("avg_exit_price", "0"))
                    pnl = _safe_decimal(d.bybit_data.get("closed_pnl", "0"))
                    if amount <= 0 or exit_price <= 0:
                        logger.warning(f"Trade reconciler: skip missing_exit {d.symbol} — amount={amount} exit_price={exit_price}")
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
                    await self._audit_repair(d, {
                        "exit_price": str(exit_price),
                        "pnl": str(pnl),
                        "trade_id": d.local_trade_id,
                    })
            except Exception as e:
                logger.error(
                    f"Trade reconciler: repair failed for {d.symbol} {d.side}: {e}"
                )
                metrics.trade_reconcile_errors.labels(error_type="repair_failure").inc()

        return repairs

    async def _alert_discrepancies(self, discrepancies: List[Discrepancy]) -> None:
        """Send Telegram alert for CRITICAL discrepancies."""
        critical = [d for d in discrepancies if d.severity == "CRITICAL"]
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

    async def _audit_repair(self, d: Discrepancy, details: Dict[str, Any]) -> None:
        """Write reconciliation repair to ai_decisions audit trail."""
        import json
        try:
            await self.store.record_ai_decision(
                symbol=d.symbol,
                decision_type="reconciliation_repair",
                output_json=json.dumps({
                    "kind": d.kind,
                    "side": d.side,
                    "trade_id": d.local_trade_id,
                    **details,
                }),
            )
        except Exception as e:
            logger.warning(f"Trade reconciler: audit log failed for {d.symbol}: {e}")
