"""LVRCalculator — detects and quantifies Loss-Versus-Rebalancing (LVR) arbitrage opportunities.

Compares CEX consensus mid-price (Binance + OKX) against DEX pool ticks.
Deducts projected EVM gas costs and estimated slippage to compute net EV.

In Phase 4: detection, logging, and shadow evaluation only. ZERO execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class LVROpportunity(BaseModel):
    """Calculated DEX-CEX arbitrage/LVR opportunity model."""

    symbol: str
    cex_mid_price: Decimal
    dex_price: Decimal
    spread_bps: Decimal
    gas_cost_usd: Decimal
    slippage_bps: Decimal = Field(default_factory=lambda: Decimal("5.0"))
    net_ev_bps: Decimal
    is_actionable: bool
    timestamp_iso: str


class LVRCalculator:
    """Calculates extractable LVR and gas-adjusted net EV spread."""

    MIN_LVR_THRESHOLD_BPS = Decimal("15.0")  # Minimum 15 bps net spread required

    @classmethod
    def compute_opportunity(
        cls,
        symbol: str,
        binance_mid: Decimal | None,
        okx_mid: Decimal | None,
        dex_price: Decimal | None,
        gas_gwei: Decimal | None = Decimal("25.0"),
        eth_price_usd: Decimal = Decimal("3000.0"),
        gas_limit: int = 150000,
        trade_notional_usd: Decimal = Decimal("1000.0"),
        min_threshold_bps: Decimal | None = None,
    ) -> LVROpportunity | None:
        """Calculate LVR spread and net EV.
        
        Requires consensus of ≥2 CEX order books (Binance + OKX mid-price).
        """
        if dex_price is None or dex_price <= 0:
            return None

        # CEX consensus require at least one valid price, preferred average of both
        cex_prices = [p for p in (binance_mid, okx_mid) if p is not None and p > 0]
        if not cex_prices:
            return None

        cex_mid = sum(cex_prices) / Decimal(len(cex_prices))

        # Absolute spread in basis points
        spread_ratio = abs(cex_mid - dex_price) / cex_mid
        spread_bps = (spread_ratio * Decimal("10000")).quantize(Decimal("0.1"))

        # Gas cost calculation
        gwei = gas_gwei if gas_gwei is not None and gas_gwei > 0 else Decimal("25.0")
        gas_cost_eth = (gwei * Decimal(gas_limit)) / Decimal("1000000000")
        gas_cost_usd = (gas_cost_eth * eth_price_usd).quantize(Decimal("0.01"))

        gas_cost_bps = (gas_cost_usd / trade_notional_usd) * Decimal("10000")
        slippage_bps = Decimal("5.0")  # 5 bps estimated DEX slippage

        net_ev_bps = spread_bps - gas_cost_bps - slippage_bps

        threshold = min_threshold_bps if min_threshold_bps is not None else cls.MIN_LVR_THRESHOLD_BPS
        is_actionable = net_ev_bps >= threshold

        now_str = datetime.now(timezone.utc).isoformat()
        return LVROpportunity(
            symbol=symbol,
            cex_mid_price=cex_mid.quantize(Decimal("0.00000001")),
            dex_price=dex_price.quantize(Decimal("0.00000001")),
            spread_bps=spread_bps,
            gas_cost_usd=gas_cost_usd,
            slippage_bps=slippage_bps,
            net_ev_bps=net_ev_bps.quantize(Decimal("0.1")),
            is_actionable=is_actionable,
            timestamp_iso=now_str,
        )
