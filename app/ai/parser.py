"""AI Response Parser — validates and converts raw JSON into AIDecisionDTO.

Handles:
- Markdown-fenced JSON (```json ... ```)
- Missing / extra fields
- Enum validation
- Confidence range validation
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.dto import (
    AIDecisionDTO,
    EntryStrategy,
    PositionSize,
    RiskLevel,
    StopLossStrategy,
)

# Fields that MUST be present in the JSON response
_REQUIRED_FIELDS = frozenset(
    {
        "confidence_score",
        "risk_level",
        "position_size",
        "entry_strategy",
        "stop_loss_strategy",
        "reasoning",
    }
)

# Valid enum values (lowercase for case-insensitive matching)
_VALID_RISK_LEVELS = {e.value.lower() for e in RiskLevel}
_VALID_POSITION_SIZES = {e.value.lower() for e in PositionSize}
_VALID_ENTRY_STRATEGIES = {e.value.lower() for e in EntryStrategy}
_VALID_SL_STRATEGIES = {e.value.lower() for e in StopLossStrategy}


class ParseError(Exception):
    """Raised when the AI response cannot be parsed into AIDecisionDTO."""


def parse_decision(raw: str, provider: str = "9router", model: str = "karsa-combo") -> AIDecisionDTO:
    """Parse raw LLM response text into an AIDecisionDTO.

    Args:
        raw: Raw response string (may contain markdown fences).
        provider: Provider name for provenance tracking.
        model: Model name for provenance tracking.

    Returns:
        Parsed AIDecisionDTO.

    Raises:
        ParseError: If the response cannot be parsed or fails validation.
    """
    text = _strip_fences(raw.strip())
    data = _load_json(text)
    _validate_required_fields(data)
    _validate_confidence(data)
    _validate_enums(data)
    _validate_probabilities(data)

    return AIDecisionDTO(
        confidence_score=int(data["confidence_score"]),
        risk_level=RiskLevel(data["risk_level"].upper()),
        position_size=PositionSize(data["position_size"].upper()),
        entry_strategy=EntryStrategy(data["entry_strategy"].upper()),
        stop_loss_strategy=StopLossStrategy(data["stop_loss_strategy"].upper()),
        reasoning=str(data["reasoning"]),
        key_risks=[str(r) for r in data.get("key_risks", [])],
        key_opportunities=[str(o) for o in data.get("key_opportunities", [])],
        bullish_probability=float(data.get("bullish_probability", 50.0)),
        bearish_probability=float(data.get("bearish_probability", 50.0)),
        summary=str(data.get("summary", "")),
        provider=provider,
        model=model,
    )


def _strip_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON."""
    # Match ```json ... ``` or ``` ... ```
    pattern = r"^```(?:json)?\s*\n?(.*?)\n?\s*```$"
    match = re.match(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _load_json(text: str) -> dict[str, Any]:
    """Parse JSON, raising ParseError on failure."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ParseError(f"Expected JSON object, got {type(data).__name__}")
    return data


def _validate_required_fields(data: dict[str, Any]) -> None:
    """Check that all required fields are present."""
    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ParseError(f"Missing required fields: {sorted(missing)}")


def _validate_confidence(data: dict[str, Any]) -> None:
    """Validate confidence_score is an int in [0, 100]."""
    raw = data["confidence_score"]
    try:
        val = int(raw)
    except (TypeError, ValueError) as exc:
        raise ParseError(f"confidence_score must be an integer, got {raw!r}") from exc
    if not (0 <= val <= 100):
        raise ParseError(f"confidence_score must be 0-100, got {val}")


def _validate_enums(data: dict[str, Any]) -> None:
    """Validate enum fields (case-insensitive)."""
    _check_enum(data, "risk_level", _VALID_RISK_LEVELS)
    _check_enum(data, "position_size", _VALID_POSITION_SIZES)
    _check_enum(data, "entry_strategy", _VALID_ENTRY_STRATEGIES)
    _check_enum(data, "stop_loss_strategy", _VALID_SL_STRATEGIES)


def _check_enum(data: dict[str, Any], field: str, valid: set[str]) -> None:
    raw = str(data[field]).strip().lower()
    if raw not in valid:
        raise ParseError(f"Invalid {field}: {data[field]!r}. Must be one of {sorted(valid)}")


def _validate_probabilities(data: dict[str, Any]) -> None:
    """Validate optional probability fields are numeric."""
    for field in ("bullish_probability", "bearish_probability"):
        val = data.get(field)
        if val is not None:
            try:
                fval = float(val)
                if not (0.0 <= fval <= 100.0):
                    raise ParseError(f"{field} must be 0-100, got {fval}")
            except (TypeError, ValueError) as exc:
                raise ParseError(f"{field} must be numeric, got {val!r}") from exc
