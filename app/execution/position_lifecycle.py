"""Position Lifecycle — trailing stop + performance checkpoints.

Runs as two async tasks:
  - Trailing stop: every 60s, amend SL if price moves favorably
  - Checkpoint manager: every 5min, evaluate time-based exits and hard stops

Classes are now in dedicated modules:
  - app.execution.trailing_stop.TrailingStopManager
  - app.execution.checkpoint_manager.CheckpointManager
"""

from __future__ import annotations

from app.execution.checkpoint_manager import CheckpointManager
from app.execution.trailing_stop import TrailingStopManager

__all__ = ["TrailingStopManager", "CheckpointManager"]
