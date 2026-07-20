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

import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from loguru import logger

from app.alpha.regime_classifier import MarketRegime

# --- Constants (cross-ref: docs/SYSTEM_CONSTANTS.md §15.3) ---
TREND_SIZE_MULT = Decimal("1.0")
RANGE_SIZE_MULT = Decimal("0.7")
CHOP_SIZE_MULT = Decimal("0.3")

TREND_MAX_HOLD_MINS = 1440
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
    MarketRegime.RANGE: RiskProfile(
        regime="RANGE",
        size_multiplier=RANGE_SIZE_MULT,
        take_profit_type="FIXED",
        stop_loss_type="TIGHT",
        max_hold_time_mins=RANGE_MAX_HOLD_MINS,
        use_post_only=True,
        trail_atr_mult=Decimal("2.0"),
        sl_atr_buffer=Decimal("1.5"),  # widened from 1.0 — volatile tokens hit 1.0 ATR SL immediately
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
}


class DynamicRiskGate:
    """Regime → RiskProfile lookup. No state, no LLM."""

    def get_profile(self, regime: MarketRegime) -> RiskProfile:
        """Get the risk profile for a given market regime."""
        profile = _PROFILES.get(regime)
        if profile is None:
            logger.warning(
                f"DynamicRiskGate: unknown regime {regime}, using CHOP profile"
            )
            return _PROFILES[MarketRegime.CHOP]
        return profile
