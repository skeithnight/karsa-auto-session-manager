"""Session Activity Manager — unified session quality scoring.

Phase 6 (Execution Velocity): Replaces double time blocks in entry_filter.py
and decision_engine.py with a single session quality scorer. Never fully blocks —
just adjusts sizing and minimum EV threshold.

Kill Zone (Audit Fix): Hard block Asia dead hours (02:00-06:00 UTC) unless
funding edge > 5% annualized or signal score > 85.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger


# Kill Zone: hard block Asia dead hours (02:00-06:00 UTC)
KILL_ZONE_START = 2  # 02:00 UTC
KILL_ZONE_END = 6    # 06:00 UTC
KILL_ZONE_FUNDING_EDGE_MIN = 5.0  # 5% annualized funding edge required
KILL_ZONE_SCORE_MIN = 85  # Or signal score > 85


@dataclass
class SessionConfig:
    """Session quality configuration for a time period."""

    sizing_mult: float       # Position size multiplier (0.3 to 1.2)
    min_ev_threshold: float  # Minimum EV threshold (0.50 to 0.75)
    session_name: str        # Human-readable session name
    is_kill_zone: bool = False  # Hard block dead hours (Audit Fix)


# Session definitions (UTC hours)
SESSIONS = [
    (0, 3, SessionConfig(0.5, 0.65, "LATE_ASIA")),
    (3, 6, SessionConfig(0.3, 0.75, "DEAD_ZONE", is_kill_zone=True)),
    (6, 8, SessionConfig(0.7, 0.60, "EARLY_EU")),
    (8, 12, SessionConfig(1.0, 0.55, "LONDON")),
    (12, 14, SessionConfig(1.2, 0.50, "LDN_NY_OVERLAP")),
    (14, 17, SessionConfig(1.1, 0.52, "NY_AFTERNOON")),
    (17, 21, SessionConfig(1.0, 0.55, "NEW_YORK")),
    (21, 24, SessionConfig(0.6, 0.70, "PACIFIC")),
]

# BTC/ETH get slightly better treatment during thin sessions
BTC_ETH_BONUS = 0.2  # Add 0.2 to sizing_mult for major assets in thin sessions


class SessionActivityManager:
    """Unified session quality scoring — replaces double time blocks.

    Usage:
        mgr = SessionActivityManager()
        config = mgr.get_session_config(hour_utc=3, symbol="SOL/USDT")
        if config.sizing_mult < 0.5:
            # Thin liquidity — reduce size significantly
    """

    def get_session_config(
        self, hour_utc: int | None = None, symbol: str = ""
    ) -> SessionConfig:
        """Get session configuration for current time.

        Args:
            hour_utc: UTC hour (default: now).
            symbol: Trading pair (BTC/ETH get bonus in thin sessions).

        Returns:
            SessionConfig with sizing_mult, min_ev_threshold, session_name.
        """
        if hour_utc is None:
            hour_utc = datetime.now(timezone.utc).hour

        for start, end, config in SESSIONS:
            if start <= hour_utc < end:
                # BTC/ETH bonus in thin sessions
                sizing = config.sizing_mult
                if symbol in ("BTC/USDT", "ETH/USDT") and sizing < 0.7:
                    sizing = min(1.0, sizing + BTC_ETH_BONUS)

                return SessionConfig(
                    sizing_mult=sizing,
                    min_ev_threshold=config.min_ev_threshold,
                    session_name=config.session_name,
                    is_kill_zone=config.is_kill_zone,
                )

        # Fallback
        return SessionConfig(1.0, 0.55, "DEFAULT")

    def is_kill_zone(
        self,
        hour_utc: int | None = None,
        funding_annualized_pct: float = 0.0,
        ev_score: float = 0.0,
    ) -> bool:
        """Check if current session is a kill zone (hard block).

        Kill zone is active (returns True) when:
        - Hour is in [02:00, 06:00 UTC), AND
        - Funding edge < 5% annualized, AND
        - EV score < 85

        Returns True if trading should be BLOCKED.
        """
        if hour_utc is None:
            hour_utc = datetime.now(timezone.utc).hour

        if KILL_ZONE_START <= hour_utc < KILL_ZONE_END:
            # Allow only extreme funding edge or very high score
            if funding_annualized_pct < KILL_ZONE_FUNDING_EDGE_MIN and ev_score < KILL_ZONE_SCORE_MIN:
                return True
        return False

    def is_thin_liquidity(self, hour_utc: int | None = None) -> bool:
        """Check if current session has thin liquidity."""
        config = self.get_session_config(hour_utc)
        return config.sizing_mult < 0.7

    def get_sizing_multiplier(
        self, hour_utc: int | None = None, symbol: str = ""
    ) -> float:
        """Get session-adjusted sizing multiplier."""
        return self.get_session_config(hour_utc, symbol).sizing_mult
