"""OnChainNormalizer — converts EVM pool events (sqrtPriceX96) to Decimal price models.

All calculations use Decimal to avoid precision loss.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class OnChainPriceState(BaseModel):
    """Real-time DEX pool price model."""

    symbol: str
    pool_address: str
    dex_price: Decimal
    tick: int = 0
    liquidity: Decimal = Field(default_factory=lambda: Decimal("0"))
    fee_tier: int = 3000
    block_number: int = 0
    timestamp_iso: str = ""


class OnChainNormalizer:
    """Utility class for converting Uniswap pool parameters to Decimal prices."""

    Q96 = Decimal(2**96)

    @classmethod
    def sqrt_price_x96_to_price(
        cls,
        sqrt_price_x96: int | str | Decimal,
        decimals_token0: int = 18,
        decimals_token1: int = 6,
    ) -> Decimal:
        """Convert Uniswap v3/v4 sqrtPriceX96 to Decimal price.
        
        price = (sqrtPriceX96 / 2^96)^2 * 10^(decimals0 - decimals1)
        """
        sqrt_p = Decimal(str(sqrt_price_x96))
        ratio = sqrt_p / cls.Q96
        raw_price = ratio * ratio
        decimal_adjustment = Decimal(10 ** (decimals_token0 - decimals_token1))
        adjusted_price = raw_price * decimal_adjustment
        return adjusted_price.quantize(Decimal("0.00000001"))
