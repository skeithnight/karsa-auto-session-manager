"""Decision Lifecycle Enum (Sprint 7)."""
from __future__ import annotations

from enum import Enum


class DecisionLifecycle(Enum):
    """Explicit state management for the decision lifecycle."""
    CANDIDATE = "candidate"
    EVALUATING = "evaluating"
    POLICY_APPROVED = "policy_approved"
    RISK_APPROVED = "risk_approved"
    EXECUTING = "executing"
    FILLED = "filled"
    MONITORING = "monitoring"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    REJECTED = "rejected"
