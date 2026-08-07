"""Shadow Mode — simulated execution on live market data.

Intercepts SOR calls, records virtual entries/exits with fees/slippage.
Zero real orders placed. Separate Redis namespace (shadow:position:*)
and separate DB table (shadow_trades).

Refinements applied (from docs/review/refinement_shadom_plan.md):
  1. Fee asymmetry: maker (0.02%) vs taker (0.055%) based on is_post_only
  2. Wick miss prevention: worst_price_seen tracking in Redis position state
  3. Funding rate drag: 8h funding deduction on held positions
  4. Pending limit orders: PENDING_VIRTUAL_FILL state for post-only entries

Components:
  ShadowExecutor       — same interface as SmartOrderRouter, no inheritance
  ShadowExchangeClient — wraps Redis for APM, same interface as BybitClient
  ShadowAPM            — wraps real APM, adds SL hit + wick + funding logic

This module re-exports the classes from their individual modules for
backward compatibility. New code should import directly from:
  - app.execution.shadow_executor
  - app.execution.shadow_exchange_client
  - app.execution.shadow_apm
"""

from app.execution.shadow_executor import ShadowExecutor
from app.execution.shadow_exchange_client import ShadowExchangeClient
from app.execution.shadow_apm import ShadowAPM

__all__ = ["ShadowExecutor", "ShadowExchangeClient", "ShadowAPM"]
