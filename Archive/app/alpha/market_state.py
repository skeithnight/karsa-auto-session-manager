"""MarketState — Immutable snapshot of quantitative market analysis for KASM 2.1.

Thread-safe and lock-free atomic replacement model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # type: ignore[misc]
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class MarketState:
    """Immutable market state containing quantitative analysis outputs."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    regime: str = "RANGE"  # TREND_BULL, TREND_BEAR, RANGE, CHOP
    hmm_prediction: str = "NEUTRAL"  # BULL, BEAR, NEUTRAL
    hurst: float = 0.5
    adx: float = 0.0
    atr: Decimal = Decimal("0")
    atr_percentile: float = 50.0
    state_freshness_seconds: float = 0.0

    @property
    def is_degraded(self) -> bool:
        """Returns True if the market state is older than 10 minutes (600 seconds)."""
        age = (datetime.now(UTC) - self.timestamp).total_seconds()
        return age > 600.0

    def to_dict(self) -> dict[str, Any]:
        """Convert state to serializable dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "regime": self.regime,
            "hmm_prediction": self.hmm_prediction,
            "hurst": round(self.hurst, 3),
            "adx": round(self.adx, 2),
            "atr": str(self.atr),
            "atr_percentile": round(self.atr_percentile, 1),
            "is_degraded": self.is_degraded,
        }
