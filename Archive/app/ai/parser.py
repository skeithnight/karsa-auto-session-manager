"""AI Response Parser (Sprint 5)."""
from __future__ import annotations

import json

from app.ai.dto import AIEvidenceDTO


class NineRouterParser:
    """Parses JSON responses from 9router into AIEvidenceDTO."""

    @staticmethod
    def parse(response_text: str) -> AIEvidenceDTO | None:
        try:
            data = json.loads(response_text)
            return AIEvidenceDTO(
                bullish_probability=float(data.get("bullish_probability", 50.0)),
                bearish_probability=float(data.get("bearish_probability", 50.0)),
                confidence=float(data.get("confidence", 0.0)),
                summary=data.get("summary", ""),
                reasons=data.get("reasons", [])
            )
        except Exception:
            return None
