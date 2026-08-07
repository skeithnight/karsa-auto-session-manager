"""AI Data Transfer Objects.

AIEvidenceDTO  — legacy evidence format (kept for backward compat).
AIDecisionDTO  — full decision with sizing, entry, SL strategy.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskLevel(enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PositionSize(enum.Enum):
    BLOCK = "BLOCK"
    QUARTER = "QUARTER"
    HALF = "HALF"
    FULL = "FULL"


class EntryStrategy(enum.Enum):
    MARKET = "MARKET"
    LIMIT_RETEST = "LIMIT_RETEST"
    WAIT_PULLBACK = "WAIT_PULLBACK"


class StopLossStrategy(enum.Enum):
    TIGHT = "TIGHT"
    NORMAL = "NORMAL"
    WIDE = "WIDE"


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass
class AIEvidenceDTO:
    """Domain object returned by AI service representing probabilistic market evidence."""

    bullish_probability: float
    bearish_probability: float
    confidence: float
    summary: str
    reasons: list[str]


@dataclass
class AIDecisionDTO:
    """Full AI decision output for a trading symbol.

    Superset of AIEvidenceDTO — adds sizing, entry/SL strategy, and risk metadata.
    """

    confidence_score: int  # 0-100
    risk_level: RiskLevel
    position_size: PositionSize
    entry_strategy: EntryStrategy
    stop_loss_strategy: StopLossStrategy
    reasoning: str
    key_risks: list[str] = field(default_factory=list)
    key_opportunities: list[str] = field(default_factory=list)
    bullish_probability: float = 50.0
    bearish_probability: float = 50.0
    summary: str = ""
    provider: str = "9router"
    model: str = "karsa-combo"

    def to_evidence(self) -> AIEvidenceDTO:
        """Downcast to the legacy AIEvidenceDTO format."""
        return AIEvidenceDTO(
            bullish_probability=self.bullish_probability,
            bearish_probability=self.bearish_probability,
            confidence=float(self.confidence_score),
            summary=self.summary or self.reasoning,
            reasons=self.key_risks + self.key_opportunities,
        )
