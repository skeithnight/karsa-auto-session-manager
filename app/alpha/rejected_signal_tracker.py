"""Rejected Signal Tracker — logs hypothetical EV of every rejected signal.

Phase 1 (Filter Collapse): Tracks what you're leaving on the table.
After 1 week of tracking, you know:
  - How many rejected signals would have been profitable
  - Which filters kill the most good signals
  - What the optimal EV threshold should be

Data stored in Redis stream: karsa:rejected_signals (maxlen=10000)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STREAM_KEY = "karsa:rejected_signals"
MAX_LEN = 10000


class RejectedSignalTracker:
    """Logs hypothetical EV of every rejected signal for post-hoc analysis.

    Usage:
        tracker = RejectedSignalTracker(redis_client)
        await tracker.track(
            symbol="BTC/USDT", direction="LONG",
            reject_reason="spread_balloon", hypothetical_ev=0.62,
            regime="TREND_BULL", price=Decimal("65000"),
        )
    """

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client

    async def track(
        self,
        symbol: str,
        direction: str,
        reject_reason: str,
        hypothetical_ev: float,
        regime: str | None = None,
        price: object = None,
    ) -> None:
        """Store rejected signal with hypothetical outcome tracking.

        Args:
            symbol: Trading pair.
            direction: LONG or SHORT.
            reject_reason: Why the signal was rejected (filter name).
            hypothetical_ev: EV score the signal would have received.
            regime: Market regime at time of rejection.
            price: Entry price if signal had been taken.
        """
        if self._redis is None:
            return

        try:
            entry = {
                "symbol": symbol,
                "direction": direction,
                "reason": reject_reason,
                "ev_score": f"{hypothetical_ev:.4f}",
                "regime": regime or "UNKNOWN",
                "price": str(price) if price is not None else "0",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self._redis.xadd(STREAM_KEY, entry, maxlen=MAX_LEN)
            logger.debug(
                "RejectedSignal tracked: %s %s reason=%s ev=%.4f",
                symbol, direction, reject_reason, hypothetical_ev,
            )
        except Exception as e:
            logger.debug("RejectedSignal tracking failed: %s", e)

    async def get_recent_rejected(
        self, count: int = 100
    ) -> list[dict]:
        """Read recent rejected signals for analysis.

        Returns list of dicts with keys: symbol, direction, reason, ev_score, regime, price, timestamp.
        """
        if self._redis is None:
            return []

        try:
            entries = await self._redis.xrevrange(STREAM_KEY, count=count)
            results = []
            for entry_id, fields in entries:
                row = {k.decode(): v.decode() for k, v in fields.items()}
                row["id"] = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                results.append(row)
            return results
        except Exception as e:
            logger.debug("RejectedSignal read failed: %s", e)
            return []
