"""Background Loops — Standalone async loops extracted from live_loop.py.

Each loop is a standalone async function with:
- Clear signature (dependencies injected)
- Configurable interval
- Proper error handling (try/except + asyncio.sleep on error)
- Testable in isolation
"""

from app.consumer.loops.wallet_metrics import wallet_metrics_loop
from app.consumer.loops.ranking_refresh import ranking_refresh_loop
from app.consumer.loops.elo_refresh import elo_refresh_loop
from app.consumer.loops.gate_calibration import gate_calibration_loop
from app.consumer.loops.vol_surface import vol_surface_loop
from app.consumer.loops.hmm_classification import hmm_classification_loop
from app.consumer.loops.garch_forecast import garch_forecast_loop
from app.consumer.loops.position_exit import position_exit_loop

__all__ = [
    "wallet_metrics_loop",
    "ranking_refresh_loop",
    "elo_refresh_loop",
    "gate_calibration_loop",
    "vol_surface_loop",
    "hmm_classification_loop",
    "garch_forecast_loop",
    "position_exit_loop",
]
