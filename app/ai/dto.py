"""AI Data Transfer Objects (Sprint 5)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AIEvidenceDTO:
    """Domain object returned by AI service representing probabilistic market evidence."""
    bullish_probability: float
    bearish_probability: float
    confidence: float
    summary: str
    reasons: list[str]
