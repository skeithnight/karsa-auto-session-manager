"""Evidence TTL Wrapper (v3.5 Prototype).

Adds freshness tracking to evidence sources. Every evidence piece has a TTL
that indicates how fresh the data is. The Action Optimizer can then decide
whether to trust, discount, or reject stale evidence.

Design Principles:
- Evidence with TTL=0 is always fresh (e.g., real-time price)
- Evidence with TTL>0 expires after N seconds
- Stale evidence is not removed, but flagged for discounting
- TTL is per-source, not per-evidence-piece (source freshness determines all)

Example:
    EvidenceTTL(source="orderbook", ttl_seconds=0.5)  # 500ms
    EvidenceTTL(source="funding", ttl_seconds=28800)    # 8 hours
    EvidenceTTL(source="news", ttl_seconds=1800)        # 30 minutes
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Freshness(Enum):
    """Freshness classification based on TTL remaining."""
    FRESH = "fresh"           # TTL remaining > 50%
    ACCEPTABLE = "acceptable" # TTL remaining 20-50%
    STALE = "stale"           # TTL remaining 0-20%
    EXPIRED = "expired"       # TTL remaining <= 0


@dataclass(frozen=True)
class EvidenceTTL:
    """Evidence with freshness tracking.

    Attributes:
        source: Evidence source name (e.g., "orderbook", "funding")
        value: The evidence value (direction * weight)
        weight: Importance weight
        description: Human-readable reason
        ttl_seconds: Time-to-live in seconds (0 = always fresh)
        collected_at: Timestamp when evidence was collected (epoch seconds)
        metadata: Optional additional data
    """
    source: str
    value: float
    weight: float
    description: str
    ttl_seconds: float
    collected_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        """How old is this evidence?"""
        return time.time() - self.collected_at

    @property
    def remaining_seconds(self) -> float:
        """How much TTL remains?"""
        return max(0.0, self.ttl_seconds - self.age_seconds)

    @property
    def freshness(self) -> Freshness:
        """Classify current freshness."""
        if self.ttl_seconds <= 0:
            return Freshness.FRESH  # Always fresh

        remaining_ratio = self.remaining_seconds / self.ttl_seconds

        if remaining_ratio > 0.5:
            return Freshness.FRESH
        elif remaining_ratio > 0.2:
            return Freshness.ACCEPTABLE
        elif remaining_ratio > 0:
            return Freshness.STALE
        else:
            return Freshness.EXPIRED

    @property
    def freshness_penalty(self) -> float:
        """Penalty factor based on freshness (1.0 = no penalty, 0.0 = fully discounted).

        Returns:
            Multiplier for evidence weight: 1.0 for fresh, decreasing to 0.0 for expired.
        """
        if self.ttl_seconds <= 0:
            return 1.0  # Always fresh

        remaining_ratio = self.remaining_seconds / self.ttl_seconds

        if remaining_ratio > 0.5:
            return 1.0
        elif remaining_ratio > 0.2:
            # Linear decay from 1.0 to 0.6
            return 0.6 + (remaining_ratio - 0.2) * (0.4 / 0.3)
        elif remaining_ratio > 0:
            # Linear decay from 0.6 to 0.2
            return remaining_ratio * (0.6 / 0.2)
        else:
            return 0.2  # Expired but not zero — still some signal

    @property
    def is_usable(self) -> bool:
        """Is this evidence usable for decision-making?"""
        return self.freshness != Freshness.EXPIRED or self.freshness_penalty > 0.1

    def discounted_weight(self) -> float:
        """Weight adjusted for freshness."""
        return self.weight * self.freshness_penalty

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logging/storage."""
        return {
            "source": self.source,
            "value": self.value,
            "weight": self.weight,
            "description": self.description,
            "ttl_seconds": self.ttl_seconds,
            "collected_at": self.collected_at,
            "age_seconds": round(self.age_seconds, 3),
            "remaining_seconds": round(self.remaining_seconds, 3),
            "freshness": self.freshness.value,
            "freshness_penalty": round(self.freshness_penalty, 4),
            "metadata": self.metadata,
        }


# Default TTL values for known evidence sources
DEFAULT_TTL: dict[str, float] = {
    # Hot path (<100ms)
    "price": 0,            # Always fresh (real-time)
    "orderbook": 0.5,      # 500ms
    "spread": 0.5,         # 500ms
    "cvd": 1.0,            # 1s

    # Warm path (<5s)
    "funding": 28800,      # 8 hours
    "oi": 60,              # 1 minute
    "liquidations": 30,    # 30 seconds
    "volume": 5,           # 5 seconds

    # Cold path (<60s)
    "regime": 300,         # 5 minutes
    "atr": 300,            # 5 minutes
    "rsi": 60,             # 1 minute
    "adx": 300,            # 5 minutes

    # Very cold (<30min)
    "news": 1800,          # 30 minutes
    "macro": 3600,         # 1 hour
    "ai": 300,             # 5 minutes (AI analysis)

    # Portfolio
    "portfolio": 10,       # 10 seconds
    "correlation": 300,    # 5 minutes
    "sector": 600,         # 10 minutes
}


def get_default_ttl(source: str) -> float:
    """Get default TTL for a known source, or fallback to 60s."""
    return DEFAULT_TTL.get(source, 60.0)


class EvidenceTTLRegistry:
    """Registry that wraps evidence with TTL tracking.

    Usage:
        registry = EvidenceTTLRegistry()

        # Collect evidence with TTL
        registry.collect(
            source="orderbook",
            value=1.0,
            weight=15.0,
            description="Strong bid support"
        )

        # Get usable evidence (filters expired)
        usable = registry.get_usable()

        # Get all evidence with freshness info
        all_evidence = registry.get_all()
    """

    def __init__(self) -> None:
        self._evidence: list[EvidenceTTL] = []
        self._last_collect: dict[str, float] = {}  # source -> timestamp

    def collect(
        self,
        source: str,
        value: float,
        weight: float,
        description: str,
        ttl_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceTTL:
        """Collect evidence with TTL tracking.

        Args:
            source: Evidence source name
            value: Evidence value (direction * weight, typically 1.0/-1.0)
            weight: Importance weight
            description: Human-readable reason
            ttl_seconds: Override default TTL for this source
            metadata: Optional additional data

        Returns:
            The created EvidenceTTL object
        """
        if ttl_seconds is None:
            ttl_seconds = get_default_ttl(source)

        evidence = EvidenceTTL(
            source=source,
            value=value,
            weight=weight,
            description=description,
            ttl_seconds=ttl_seconds,
            metadata=metadata or {},
        )

        self._evidence.append(evidence)
        self._last_collect[source] = time.time()

        return evidence

    def get_usable(self) -> list[EvidenceTTL]:
        """Get evidence that is still usable (not fully expired)."""
        return [e for e in self._evidence if e.is_usable]

    def get_fresh(self) -> list[EvidenceTTL]:
        """Get only fresh evidence (>50% TTL remaining)."""
        return [e for e in self._evidence if e.freshness == Freshness.FRESH]

    def get_expired(self) -> list[EvidenceTTL]:
        """Get expired evidence."""
        return [e for e in self._evidence if e.freshness == Freshness.EXPIRED]

    def get_all(self) -> list[EvidenceTTL]:
        """Get all evidence with freshness info."""
        return list(self._evidence)

    def get_by_source(self, source: str) -> list[EvidenceTTL]:
        """Get evidence from a specific source."""
        return [e for e in self._evidence if e.source == source]

    def get_freshness_summary(self) -> dict[str, dict[str, Any]]:
        """Get a summary of freshness by source.

        Returns:
            Dict mapping source to {count, avg_freshness, avg_penalty}
        """
        summary: dict[str, dict[str, Any]] = {}

        for evidence in self._evidence:
            source = evidence.source
            if source not in summary:
                summary[source] = {
                    "count": 0,
                    "total_penalty": 0.0,
                    "fresh_count": 0,
                    "stale_count": 0,
                    "expired_count": 0,
                }

            entry = summary[source]
            entry["count"] += 1
            entry["total_penalty"] += evidence.freshness_penalty

            if evidence.freshness == Freshness.FRESH:
                entry["fresh_count"] += 1
            elif evidence.freshness in (Freshness.ACCEPTABLE, Freshness.STALE):
                entry["stale_count"] += 1
            else:
                entry["expired_count"] += 1

        # Compute averages
        for source, entry in summary.items():
            entry["avg_penalty"] = entry["total_penalty"] / entry["count"]
            del entry["total_penalty"]

        return summary

    def clear(self) -> None:
        """Clear all evidence."""
        self._evidence.clear()
        self._last_collect.clear()

    def clear_source(self, source: str) -> None:
        """Clear evidence from a specific source."""
        self._evidence = [e for e in self._evidence if e.source != source]
        self._last_collect.pop(source, None)

    def compute_weighted_score(self) -> float:
        """Compute weighted score with freshness adjustment.

        Returns:
            Sum of (value * discounted_weight) for all usable evidence.
        """
        total = 0.0
        for evidence in self.get_usable():
            total += evidence.value * evidence.discounted_weight()
        return total

    def compute_raw_score(self) -> float:
        """Compute weighted score WITHOUT freshness adjustment (for comparison).

        Returns:
            Sum of (value * weight) for all usable evidence.
        """
        total = 0.0
        for evidence in self.get_usable():
            total += evidence.value * evidence.weight
        return total
