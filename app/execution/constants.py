"""Shared constants and utilities for position management sub-modules.

Cross-ref: docs/SYSTEM_CONSTANTS.md 15.4
"""

from __future__ import annotations

from decimal import Decimal

# --- APM Constants ---
APM_MONITOR_INTERVAL_S: int = 2
APM_ERROR_BACKOFF_S: int = 5
APM_RECONCILE_INTERVAL_S: int = 300
APM_BREAKEVEN_FEE_PCT = Decimal("0.0015")  # 0.15% -- covers 0.11% round-trip fees + locks net profit
APM_TREND_TRAIL_ATR_MULT = Decimal("2.5")
APM_TREND_TRAIL_ACTIVATE_R = Decimal("1.80")  # Trailing activates at +1.80R (after breakeven at +1.50R)
APM_BREAKEVEN_LOCK_R = Decimal("1.50")  # Fee-aware Breakeven Lock trigger: requires +1.50R raw move
APM_BREAKEVEN_ATR_MULT = Decimal("1.5")  # price must move > 1.5x ATR to trigger BE

# Sprint 1: Trailing Limit Exit (Maker-Only)
TRAILING_LIMIT_OFFSET_PCT = Decimal("0.0005")  # 0.05% from best bid/ask for limit order
TRAILING_LIMIT_TIMEOUT_S: int = 60  # 60s before market fallback
TRAILING_LIMIT_ACTIVATE_R = Decimal("1.5")  # Activate at +1.5R profit

# Regime shift hysteresis: require N consecutive shifted checks
REGIME_SHIFT_CONFIRM_COUNT: int = 5

# Regime family mapping -- shifts within the same family are noise, not real regime changes.
# RANGE->RANGE_LOW_VOL or RANGE->RANGE_HIGH_VOL should NOT trigger the kill switch.
REGIME_FAMILY: dict[str, str] = {
    "RANGE": "RANGE",
    "RANGE_LOW_VOL": "RANGE",
    "RANGE_HIGH_VOL": "RANGE",
    "CHOP": "CHOP",
    "SNIPER": "SNIPER",
    "TREND_BULL": "TREND",
    "TREND_BEAR": "TREND",
    "HYPER_BULL": "HYPER",
    "HYPER_BEAR": "HYPER",
    "UNKNOWN": "UNKNOWN",  # Orphan positions with lost historical context
}

# Orphan sync grace period -- don't re-sync a symbol force-closed within this window (seconds).
# Prevents the orphan->force-close->re-sync phantom loop observed in forensic reports.
# Set to 120s (was 60s) -- Bybit holds positions ~61s after closure during high load.
ORPHAN_RE_ENTRY_GRACE_S: int = 120


def _safe_dec(value: object, default: str = "0") -> Decimal:
    """Convert any value safely to Decimal without raising."""
    try:
        return Decimal(str(value)) if value is not None else Decimal(default)
    except Exception:
        return Decimal(default)
