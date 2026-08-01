"""Decision Trace (Sprint 7).

Explains how a decision was produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TraceNode:
    """A single step in the decision evaluation pipeline."""
    node_name: str
    input_data: str
    output_data: str
    reason: str
    timestamp: float


@dataclass
class DecisionTrace:
    """Full explainability trace of a decision."""
    nodes: list[TraceNode] = field(default_factory=list)
    duration_ms: int = 0
    metadata: dict[str, str] = field(default_factory=dict)

    def add_node(self, name: str, input_data: str, output_data: str, reason: str, ts: float) -> None:
        self.nodes.append(TraceNode(name, input_data, output_data, reason, ts))
