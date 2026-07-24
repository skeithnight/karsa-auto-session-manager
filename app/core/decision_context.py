"""Decision Context (Sprint 2).

Standardized representation of a probabilistic trading decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel: pass  # type: ignore[no-redef]
    def Field(*args, **kwargs): return None  # type: ignore[no-redef]

from app.alpha.regime_classifier import MarketRegime
from app.core.decision_lifecycle import DecisionLifecycle
from app.core.decision_trace import DecisionTrace
from app.core.feature_extractor import FeatureVector


@dataclass
class Evidence:
    """A piece of evidence contributing to a trading decision."""
    name: str
    value: float
    weight: float
    description: str


@dataclass
class DecisionContext:
    """Standardized representation of a probabilistic decision context."""

    symbol: str
    regime: MarketRegime
    direction: str
    features: FeatureVector

    # Model Governance Fields
    feature_schema_version: int = 2
    confidence_model_version: int = 1
    policy_version: int = 1
    similarity_model_version: int = 1
    experiment_id: str = "v2.2-default"
    ai_provider: str = "9router"
    ai_model: str = "karsa-combo"
    prompt_version: int = 1

    # State and Tracing
    lifecycle_state: DecisionLifecycle = DecisionLifecycle.CANDIDATE
    trace: DecisionTrace = field(default_factory=DecisionTrace)

    total_confidence: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)

    def add_evidence(self, name: str, value: float, weight: float, description: str) -> None:
        self.evidence.append(Evidence(name, value, weight, description))
        self.total_confidence += (value * weight)

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for persistence/registry."""
        return {
            "symbol": self.symbol,
            "regime": self.regime.value,
            "direction": self.direction,
            "features": self.features.__dict__,
            "total_confidence": self.total_confidence,
            "evidence": [
                {
                    "name": e.name,
                    "value": e.value,
                    "weight": e.weight,
                    "description": e.description
                }
                for e in self.evidence
            ],
            "governance": {
                "feature_schema_version": self.feature_schema_version,
                "confidence_model_version": self.confidence_model_version,
                "policy_version": self.policy_version,
                "similarity_model_version": self.similarity_model_version,
                "experiment_id": self.experiment_id,
                "ai_provider": self.ai_provider,
                "ai_model": self.ai_model,
                "prompt_version": self.prompt_version,
            },
            "lifecycle": self.lifecycle_state.value
        }


class SniperTrapMetadata(BaseModel):
    """Metadata for an AI pre-approved Sniper Trap."""
    symbol: str
    target_entry_price: Decimal
    max_sniper_slippage_bps: int = Field(default=20, le=50)
    ai_thesis: str
    ai_confidence_score: float = Field(ge=0.0, le=100.0)
    created_at: datetime
    expires_at: datetime
    invalidation_conditions: dict[str, Any]

