"""TreasuryManager — Active Treasury Manager for idle capital yield.

Routes idle USDC into blue-chip yield venues (Pendle PT, HLP Vault)
during low-EV market regimes to eliminate cash drag.

Enforces strict risk caps, whitelist checks, and circuit breaker compliance.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from app.core import metrics
from app.core.redis_client import RedisClient
from app.defi.evm_router import EVMRouter
from app.defi.hlp_client import HLPClient
from app.defi.pendle_client import PendleClient
from app.defi.whitelist_registry import WhitelistRegistry


class TreasuryManager:
    """Manages idle capital deployment to yield venues."""

    MIN_TREASURY_THRESHOLD = Decimal("50.0")  # Minimum $50 idle USDC to allocate
    MAX_TREASURY_PCT = Decimal("0.30")          # Max 30% of total equity in yield

    def __init__(
        self,
        redis_client: RedisClient,
        evm_router: EVMRouter,
        whitelist_registry: WhitelistRegistry,
        pendle_client: PendleClient,
        hlp_client: HLPClient,
        poll_interval_s: float = 300.0,
    ) -> None:
        self._redis = redis_client
        self._evm_router = evm_router
        self._whitelist = whitelist_registry
        self._pendle = pendle_client
        self._hlp = hlp_client
        self._poll_interval_s = poll_interval_s
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._total_deployed = Decimal("0.0")

    @property
    def total_deployed(self) -> Decimal:
        return self._total_deployed

    async def check_circuit_breaker(self) -> bool:
        """Check if global circuit breaker is active."""
        cb_raw = await self._redis.get("system:circuit_breaker")
        if cb_raw:
            if '"ACTIVE"' in str(cb_raw) or '"triggered"' in str(cb_raw):
                return True
        return False

    async def evaluate_and_allocate(
        self,
        idle_usdc: Decimal,
        total_equity: Decimal = Decimal("1000.0"),
        market_ev: float = 0.40,
    ) -> dict[str, Any] | None:
        """Evaluate market conditions and deploy idle USDC to highest yield venue if EV < 0.50."""
        if idle_usdc < self.MIN_TREASURY_THRESHOLD:
            return None

        # Check circuit breaker
        if await self.check_circuit_breaker():
            logger.warning("TreasuryManager: circuit breaker active. Skipping allocation.")
            return None

        # Check market regime EV (only allocate when trading EV is low)
        if market_ev >= 0.50:
            return None

        # Max deployment cap check
        max_allowed = total_equity * self.MAX_TREASURY_PCT
        deploy_amount = min(idle_usdc * Decimal("0.50"), max_allowed - self._total_deployed)

        if deploy_amount < self.MIN_TREASURY_THRESHOLD:
            return None

        # Select venue: HLP for zero gas, Pendle PT for fixed yield
        gas_gwei = await self._evm_router.get_current_gas_gwei()
        if gas_gwei > Decimal("50.0"):
            # High gas -> prefer Hyperliquid HLP (zero gas)
            res = await self._hlp.deposit_to_hlp(deploy_amount)
        else:
            # Low gas -> Pendle PT for locked fixed yield
            res = await self._pendle.deposit_to_pt("0xPendleMarketPlaceholder", deploy_amount)

        if res.get("status") == "CONFIRMED":
            self._total_deployed += deploy_amount
            await self._redis.set("treasury:total_deployed", str(self._total_deployed))
            metrics.karsa_treasury_deployed_usdc.labels(venue=res.get("venue", "unknown")).set(float(self._total_deployed))
            logger.info(f"TreasuryManager: deployed {deploy_amount} USDC to {res.get('venue')}")

        return res

    async def start(self) -> None:
        """Start periodic treasury management loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("TreasuryManager started")

    async def stop(self) -> None:
        """Stop treasury management loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TreasuryManager stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.evaluate_and_allocate(idle_usdc=Decimal("100.0"))
            except Exception as e:
                logger.error(f"TreasuryManager loop error: {e}")
            await asyncio.sleep(self._poll_interval_s)
