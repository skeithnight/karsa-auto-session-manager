"""Dynamic Risk Gate — Phase 6 regime-aware risk profiles.

Maps MarketRegime to RiskProfile with sizing, timing, and order-type
parameters. No LLM. Pure lookup + serialization.

RiskProfile fields (from docs/architecture/adaptive_multi_strategy.md §5.1):
  regime:             str         # MarketRegime value
  size_multiplier:    Decimal     # Position size multiplier
  take_profit_type:   str         # 'TRAILING' | 'FIXED' | 'SCALP'
  stop_loss_type:     str         # 'WIDE' | 'TIGHT' | 'MICRO'
  max_hold_time_mins: int         # Hard time exit
  use_post_only:      bool        # Force Post-Only for maker fee
  trail_atr_mult:     Decimal     # ATR multiple for trailing
  sl_atr_buffer:      Decimal     # ATR buffer for SL placement
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from loguru import logger

from app.alpha.regime_classifier import MarketRegime

# --- Constants (cross-ref: docs/SYSTEM_CONSTANTS.md §15.3) ---
TREND_SIZE_MULT = Decimal("1.0")
RANGE_SIZE_MULT = Decimal("0.7")
CHOP_SIZE_MULT = Decimal("0.3")

TREND_MAX_HOLD_MINS = 2880
RANGE_MAX_HOLD_MINS = 240
CHOP_MAX_HOLD_MINS = 30


@dataclass(frozen=True)
class RiskProfile:
    """Immutable risk profile attached to each filled position."""

    regime: str
    size_multiplier: Decimal
    take_profit_type: str
    stop_loss_type: str
    max_hold_time_mins: int
    use_post_only: bool
    trail_atr_mult: Decimal
    sl_atr_buffer: Decimal

    def to_json(self) -> str:
        """Serialize to JSON string for Redis storage."""
        d = asdict(self)
        d["size_multiplier"] = str(self.size_multiplier)
        d["trail_atr_mult"] = str(self.trail_atr_mult)
        d["sl_atr_buffer"] = str(self.sl_atr_buffer)
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> RiskProfile:
        """Deserialize from JSON string."""
        d = json.loads(raw)
        d["size_multiplier"] = Decimal(d["size_multiplier"])
        d["trail_atr_mult"] = Decimal(d["trail_atr_mult"])
        d["sl_atr_buffer"] = Decimal(d["sl_atr_buffer"])
        return cls(**d)


# --- Preset profiles per regime ---
_PROFILES: dict[MarketRegime, RiskProfile] = {
    MarketRegime.TREND_BULL: RiskProfile(
        regime="TREND_BULL",
        size_multiplier=TREND_SIZE_MULT,
        take_profit_type="TRAILING",
        stop_loss_type="WIDE",
        max_hold_time_mins=TREND_MAX_HOLD_MINS,
        use_post_only=False,
        trail_atr_mult=Decimal("3.0"),
        sl_atr_buffer=Decimal("1.5"),
    ),
    MarketRegime.TREND_BEAR: RiskProfile(
        regime="TREND_BEAR",
        size_multiplier=TREND_SIZE_MULT,
        take_profit_type="TRAILING",
        stop_loss_type="WIDE",
        max_hold_time_mins=TREND_MAX_HOLD_MINS,
        use_post_only=False,
        trail_atr_mult=Decimal("3.0"),
        sl_atr_buffer=Decimal("1.5"),
    ),
    MarketRegime.HYPER_BULL: RiskProfile(
        regime="HYPER_BULL",
        size_multiplier=Decimal("0.5"),
        take_profit_type="SCALP",
        stop_loss_type="MICRO",
        max_hold_time_mins=15,
        use_post_only=False,
        trail_atr_mult=Decimal("0.5"),  # Tightened from 1.0
        sl_atr_buffer=Decimal("0.5"),   # Tightened from 0.8
    ),
    MarketRegime.HYPER_BEAR: RiskProfile(
        regime="HYPER_BEAR",
        size_multiplier=Decimal("0.5"),
        take_profit_type="SCALP",
        stop_loss_type="MICRO",
        max_hold_time_mins=15,
        use_post_only=False,
        trail_atr_mult=Decimal("0.5"),  # Tightened from 1.0
        sl_atr_buffer=Decimal("0.5"),   # Tightened from 0.8
    ),
    MarketRegime.RANGE: RiskProfile(
        regime="RANGE",
        size_multiplier=RANGE_SIZE_MULT,
        take_profit_type="FIXED",
        stop_loss_type="TIGHT",
        max_hold_time_mins=RANGE_MAX_HOLD_MINS,
        use_post_only=True,
        trail_atr_mult=Decimal("2.0"),
        sl_atr_buffer=Decimal("1.8"),  # 1.8x ATR buffer protects against noise wicks while Kelly sizes risk
    ),
    MarketRegime.CHOP: RiskProfile(
        regime="CHOP",
        size_multiplier=CHOP_SIZE_MULT,
        take_profit_type="SCALP",
        stop_loss_type="TIGHT",
        max_hold_time_mins=CHOP_MAX_HOLD_MINS,
        use_post_only=True,
        trail_atr_mult=Decimal("1.5"),
        sl_atr_buffer=Decimal("1.5"),
    ),
    # Phase 2: Transition states — most profitable moment to trade
    MarketRegime.TRANSITION_BULL: RiskProfile(
        regime="TRANSITION_BULL",
        size_multiplier=Decimal("1.2"),      # Larger than normal — best setup
        take_profit_type="TRAILING",
        stop_loss_type="TIGHT",
        max_hold_time_mins=1440,             # 24 hours — let the trend develop
        use_post_only=False,                 # Speed matters more than fees
        trail_atr_mult=Decimal("2.5"),
        sl_atr_buffer=Decimal("1.2"),
    ),
    MarketRegime.TRANSITION_BEAR: RiskProfile(
        regime="TRANSITION_BEAR",
        size_multiplier=Decimal("1.2"),
        take_profit_type="TRAILING",
        stop_loss_type="TIGHT",
        max_hold_time_mins=1440,
        use_post_only=False,
        trail_atr_mult=Decimal("2.5"),
        sl_atr_buffer=Decimal("1.2"),
    ),
}

# Phase 2: CHOP sub-strategy profiles (used by strategy_router for CHOP regime)
CHOP_PROFILES = {
    "CHOP_CARRY": RiskProfile(
        regime="CHOP_CARRY",
        size_multiplier=Decimal("0.5"),      # Half size for carry (low risk)
        take_profit_type="FIXED",
        stop_loss_type="TIGHT",
        max_hold_time_mins=480,              # 8 hours (one funding period)
        use_post_only=True,
        trail_atr_mult=Decimal("1.0"),
        sl_atr_buffer=Decimal("1.0"),
    ),
    "CHOP_MEAN_REVERT": RiskProfile(
        regime="CHOP_MEAN_REVERT",
        size_multiplier=Decimal("0.4"),
        take_profit_type="FIXED",
        stop_loss_type="TIGHT",
        max_hold_time_mins=240,              # 4 hours (up from 30 min)
        use_post_only=True,
        trail_atr_mult=Decimal("1.5"),
        sl_atr_buffer=Decimal("1.0"),
    ),
    "CHOP_SWEEP": RiskProfile(
        regime="CHOP_SWEEP",
        size_multiplier=Decimal("0.3"),
        take_profit_type="SCALP",
        stop_loss_type="MICRO",
        max_hold_time_mins=15,               # Quick in-and-out
        use_post_only=False,                 # Market order for speed
        trail_atr_mult=Decimal("0.5"),
        sl_atr_buffer=Decimal("0.5"),
    ),
}


class DynamicRiskGate:
    """Regime → RiskProfile lookup. No state, no LLM."""

    def __init__(
        self,
        override_sl_buffer: Decimal | None = None,
        override_trail_mult: Decimal | None = None,
    ) -> None:
        self.override_sl_buffer = override_sl_buffer
        self.override_trail_mult = override_trail_mult

    def get_profile(self, regime: MarketRegime) -> RiskProfile:
        """Get the risk profile for a given market regime."""
        profile = _PROFILES.get(regime)
        if profile is None:
            logger.warning(
                f"DynamicRiskGate: unknown regime {regime}, using CHOP profile"
            )
            profile = _PROFILES[MarketRegime.CHOP]

        if self.override_sl_buffer is not None or self.override_trail_mult is not None:
            updates = {}
            if self.override_sl_buffer is not None:
                updates["sl_atr_buffer"] = self.override_sl_buffer
            if self.override_trail_mult is not None:
                updates["trail_atr_mult"] = self.override_trail_mult
            return dataclasses.replace(profile, **updates)

        return profile
