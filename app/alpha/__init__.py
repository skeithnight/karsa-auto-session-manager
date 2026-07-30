"""Alpha module — lazy imports to avoid circular dependencies.

HybridDecisionEngine and StatisticalFeatureEngine are heavy modules with
deep dependency chains (AI service, prompt builder, etc.). Import them
directly from their submodules when needed:
    from app.alpha.hybrid_decision_engine import HybridDecisionEngine
    from app.alpha.statistical_engine import StatisticalFeatureEngine
"""

__all__ = [
    "HybridDecision",
    "HybridDecisionEngine",
    "StatisticalFeatureEngine",
]


def __getattr__(name: str):  # noqa: ANN001
    if name in ("HybridDecision", "HybridDecisionEngine"):
        from app.alpha.hybrid_decision_engine import HybridDecision, HybridDecisionEngine

        return HybridDecision if name == "HybridDecision" else HybridDecisionEngine
    if name == "StatisticalFeatureEngine":
        from app.alpha.statistical_engine import StatisticalFeatureEngine

        return StatisticalFeatureEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
