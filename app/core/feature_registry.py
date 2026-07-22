"""Feature Registry (Sprint 5).

Provides metadata, versioning, dependencies, and ownership for standardized features.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeatureDefinition:
    """Metadata for a standard trading feature."""
    id: str
    category: str
    version: int
    owner: str
    dependencies: list[str]
    normalization: str
    range: list[float] | None
    description: str


class FeatureRegistry:
    """Central registry of all available features in the system."""

    def __init__(self) -> None:
        self._features: dict[str, FeatureDefinition] = {}
        self._register_defaults()

    def register(self, feature: FeatureDefinition) -> None:
        """Register a new feature definition."""
        self._features[feature.id] = feature

    def get(self, feature_id: str) -> FeatureDefinition | None:
        """Retrieve a feature definition by ID."""
        return self._features.get(feature_id)

    def get_all(self) -> list[FeatureDefinition]:
        """Retrieve all registered features."""
        return list(self._features.values())

    def _register_defaults(self) -> None:
        """Register the built-in system features."""
        self.register(FeatureDefinition(
            id="close",
            category="price",
            version=1,
            owner="MarketSnapshot",
            dependencies=[],
            normalization="none",
            range=None,
            description="Closing price of the current period."
        ))

        self.register(FeatureDefinition(
            id="adx_14",
            category="technical",
            version=1,
            owner="FeatureStore",
            dependencies=["close", "high", "low"],
            normalization="raw",
            range=[0.0, 100.0],
            description="14-period Average Directional Index."
        ))

        self.register(FeatureDefinition(
            id="hurst",
            category="technical",
            version=1,
            owner="FeatureStore",
            dependencies=["close"],
            normalization="raw",
            range=[0.0, 1.0],
            description="Hurst exponent for mean-reversion/trending classification."
        ))

        self.register(FeatureDefinition(
            id="atr_pct",
            category="volatility",
            version=1,
            owner="FeatureStore",
            dependencies=["close", "high", "low"],
            normalization="pct",
            range=None,
            description="Average True Range expressed as a percentage of price."
        ))

        self.register(FeatureDefinition(
            id="rsi_14",
            category="momentum",
            version=1,
            owner="FeatureStore",
            dependencies=["close"],
            normalization="raw",
            range=[0.0, 100.0],
            description="14-period Relative Strength Index."
        ))
