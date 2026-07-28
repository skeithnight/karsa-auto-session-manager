"""Tests for EvidenceTTL wrapper (v3.5 prototype)."""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.core.evidence_ttl import (
    EvidenceTTL,
    EvidenceTTLRegistry,
    Freshness,
    get_default_ttl,
)


class TestEvidenceTTL:
    """Tests for EvidenceTTL dataclass."""

    def test_fresh_evidence(self):
        """Fresh evidence should have FRESH freshness."""
        evidence = EvidenceTTL(
            source="orderbook",
            value=1.0,
            weight=15.0,
            description="Strong bid",
            ttl_seconds=0.5,
            collected_at=time.time(),
        )
        assert evidence.freshness == Freshness.FRESH
        assert evidence.freshness_penalty == 1.0
        assert evidence.is_usable

    def test_expired_evidence(self):
        """Expired evidence should have EXPIRED freshness."""
        evidence = EvidenceTTL(
            source="orderbook",
            value=1.0,
            weight=15.0,
            description="Strong bid",
            ttl_seconds=0.5,
            collected_at=time.time() - 1.0,  # 1 second ago, TTL is 0.5s
        )
        assert evidence.freshness == Freshness.EXPIRED
        assert evidence.freshness_penalty == 0.2  # Minimum penalty
        assert evidence.is_usable  # Still usable with penalty

    def test_zero_ttl_always_fresh(self):
        """TTL=0 should always be fresh."""
        evidence = EvidenceTTL(
            source="price",
            value=1.0,
            weight=10.0,
            description="Current price",
            ttl_seconds=0,
            collected_at=time.time() - 1000,  # Very old
        )
        assert evidence.freshness == Freshness.FRESH
        assert evidence.freshness_penalty == 1.0

    def test_discounted_weight(self):
        """Discounted weight should apply freshness penalty."""
        evidence = EvidenceTTL(
            source="orderbook",
            value=1.0,
            weight=20.0,
            description="Strong bid",
            ttl_seconds=1.0,
            collected_at=time.time() - 0.8,  # 80% expired
        )
        # Should have penalty < 1.0
        assert evidence.discounted_weight() < 20.0
        assert evidence.discounted_weight() > 0.0

    def test_age_seconds(self):
        """Age should be computed from collected_at."""
        now = time.time()
        evidence = EvidenceTTL(
            source="test",
            value=1.0,
            weight=1.0,
            description="test",
            ttl_seconds=10.0,
            collected_at=now - 5.0,
        )
        assert 4.9 < evidence.age_seconds < 5.1

    def test_remaining_seconds(self):
        """Remaining TTL should be computed correctly."""
        now = time.time()
        evidence = EvidenceTTL(
            source="test",
            value=1.0,
            weight=1.0,
            description="test",
            ttl_seconds=10.0,
            collected_at=now - 3.0,
        )
        assert 6.9 < evidence.remaining_seconds < 7.1

    def test_to_dict(self):
        """to_dict should serialize all fields."""
        evidence = EvidenceTTL(
            source="test",
            value=1.0,
            weight=5.0,
            description="Test evidence",
            ttl_seconds=10.0,
            metadata={"key": "value"},
        )
        d = evidence.to_dict()
        assert d["source"] == "test"
        assert d["value"] == 1.0
        assert d["weight"] == 5.0
        assert d["description"] == "Test evidence"
        assert d["ttl_seconds"] == 10.0
        assert d["freshness"] in ["fresh", "acceptable", "stale", "expired"]
        assert d["metadata"] == {"key": "value"}


class TestFreshnessPenalty:
    """Tests for freshness penalty calculation."""

    def test_fresh_penalty(self):
        """Fresh evidence should have penalty 1.0."""
        evidence = EvidenceTTL(
            source="test", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
            collected_at=time.time(),  # Just collected
        )
        assert evidence.freshness_penalty == 1.0

    def test_acceptable_penalty(self):
        """Acceptable evidence should have penalty 0.6-1.0."""
        evidence = EvidenceTTL(
            source="test", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
            collected_at=time.time() - 7.0,  # 70% expired
        )
        assert 0.6 <= evidence.freshness_penalty <= 1.0

    def test_stale_penalty(self):
        """Stale evidence should have penalty 0.0-0.6."""
        evidence = EvidenceTTL(
            source="test", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
            collected_at=time.time() - 8.5,  # 85% expired
        )
        assert 0.0 <= evidence.freshness_penalty <= 0.6

    def test_expired_penalty(self):
        """Expired evidence should have penalty 0.2."""
        evidence = EvidenceTTL(
            source="test", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
            collected_at=time.time() - 15.0,  # Fully expired
        )
        assert evidence.freshness_penalty == 0.2


class TestEvidenceTTLRegistry:
    """Tests for EvidenceTTLRegistry."""

    def test_collect(self):
        """Collect should add evidence to registry."""
        registry = EvidenceTTLRegistry()
        evidence = registry.collect(
            source="orderbook",
            value=1.0,
            weight=15.0,
            description="Strong bid",
        )
        assert evidence.source == "orderbook"
        assert len(registry.get_all()) == 1

    def test_get_usable(self):
        """get_usable should return non-expired evidence."""
        registry = EvidenceTTLRegistry()
        registry.collect(
            source="price", value=1.0, weight=10.0,
            description="test", ttl_seconds=0,  # Always fresh
        )
        # Create an evidence directly with expired timestamp
        # Note: expired evidence is still "usable" (penalty 0.2 > 0.1 threshold)
        # so get_usable returns both. Use get_fresh to filter strictly.
        expired_evidence = EvidenceTTL(
            source="orderbook", value=1.0, weight=15.0,
            description="test", ttl_seconds=0.5,
            collected_at=time.time() - 1.0,  # Expired
        )
        registry._evidence.append(expired_evidence)
        usable = registry.get_usable()
        # Both are usable (expired evidence has penalty 0.2 > 0.1 threshold)
        assert len(usable) == 2
        # But only price is fresh
        fresh = registry.get_fresh()
        assert len(fresh) == 1
        assert fresh[0].source == "price"

    def test_get_fresh(self):
        """get_fresh should return only fresh evidence."""
        registry = EvidenceTTLRegistry()
        # Fresh evidence
        registry.collect(
            source="a", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
        )
        # Stale evidence - create directly
        stale_evidence = EvidenceTTL(
            source="b", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
            collected_at=time.time() - 8.0,  # Stale
        )
        registry._evidence.append(stale_evidence)
        fresh = registry.get_fresh()
        assert len(fresh) == 1
        assert fresh[0].source == "a"

    def test_get_by_source(self):
        """get_by_source should filter by source name."""
        registry = EvidenceTTLRegistry()
        registry.collect(source="a", value=1.0, weight=10.0, description="test")
        registry.collect(source="b", value=1.0, weight=10.0, description="test")
        registry.collect(source="a", value=1.0, weight=10.0, description="test")

        a_evidence = registry.get_by_source("a")
        assert len(a_evidence) == 2

    def test_clear_source(self):
        """clear_source should remove evidence from specific source."""
        registry = EvidenceTTLRegistry()
        registry.collect(source="a", value=1.0, weight=10.0, description="test")
        registry.collect(source="b", value=1.0, weight=10.0, description="test")

        registry.clear_source("a")
        assert len(registry.get_all()) == 1
        assert registry.get_all()[0].source == "b"

    def test_compute_weighted_score(self):
        """compute_weighted_score should apply freshness penalty."""
        registry = EvidenceTTLRegistry()
        # Fresh evidence
        registry.collect(
            source="a", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
        )
        # Expired evidence - create directly
        expired_evidence = EvidenceTTL(
            source="b", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
            collected_at=time.time() - 15.0,
        )
        registry._evidence.append(expired_evidence)

        weighted = registry.compute_weighted_score()
        raw = registry.compute_raw_score()

        # Weighted should be less than raw due to expired evidence penalty
        assert weighted < raw
        # But should still be positive (expired has 0.2 penalty)
        assert weighted > 0

    def test_get_freshness_summary(self):
        """get_freshness_summary should aggregate by source."""
        registry = EvidenceTTLRegistry()
        # Fresh evidence
        registry.collect(source="a", value=1.0, weight=10.0, description="test",
                        ttl_seconds=10.0)
        # Expired evidence - create directly
        expired_evidence = EvidenceTTL(
            source="a", value=1.0, weight=10.0,
            description="test", ttl_seconds=10.0,
            collected_at=time.time() - 15.0,
        )
        registry._evidence.append(expired_evidence)

        summary = registry.get_freshness_summary()
        assert "a" in summary
        assert summary["a"]["count"] == 2
        assert summary["a"]["fresh_count"] == 1
        assert summary["a"]["expired_count"] == 1


class TestDefaultTTL:
    """Tests for default TTL values."""

    def test_known_source(self):
        """Known sources should have defined TTL."""
        assert get_default_ttl("orderbook") == 0.5
        assert get_default_ttl("funding") == 28800
        assert get_default_ttl("price") == 0

    def test_unknown_source(self):
        """Unknown sources should fallback to 60s."""
        assert get_default_ttl("unknown_source") == 60.0
