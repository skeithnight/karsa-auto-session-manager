"""Full End-to-End System Validation Script for Karsa ASM Container Fleet."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from datetime import datetime, timezone

from app.core.redis_client import RedisClient
from app.core.database import DatabaseEngine
from app.defi.whitelist_registry import WhitelistRegistry
from app.defi.carry_manager import DeltaNeutralCarryManager
from app.defi.security_scanner import OnChainSecurityScanner
from app.defi.v4_hook_controller import V4HookController
from app.defi.evm_router import EVMRouter
from app.defi.pendle_client import PendleClient
from app.defi.hlp_client import HLPClient
from app.defi.treasury_manager import TreasuryManager
from app.alpha.lvr_calculator import LVRCalculator
from app.execution.sor import SmartOrderRouter


async def test_end_to_end() -> None:
    print("=====================================================")
    print("  🚀 KARSA ASM: FULL SYSTEM END-TO-END VALIDATION   ")
    print("=====================================================")

    from app.core.config import get_settings
    settings = get_settings()

    # 1. Redis & Database connectivity
    redis = RedisClient()
    await redis.connect()
    db = DatabaseEngine()
    await db.connect(settings.postgres_url)
    print("✅ Step 1: Redis & Postgres connected successfully.")

    # 2. Whitelist Registry & Security Screening
    registry = WhitelistRegistry()
    scanner = OnChainSecurityScanner(redis_client=redis, whitelist_registry=registry)
    pendle_entry = registry.get_entry("Pendle")
    assert pendle_entry is not None
    audit_res = await scanner.scan_token(pendle_entry.contract_addresses[0])
    assert audit_res.is_safe is True
    print("✅ Step 2: Protocol Whitelist & Pre-Trade Security passed.")

    # 3. LVR & DEX-CEX Divergence Calculation
    lvr_res = LVRCalculator.compute_opportunity(
        symbol="ETH/USDT",
        binance_mid=Decimal("2800.0"),
        okx_mid=Decimal("2800.0"),
        dex_price=Decimal("2808.5"),
        gas_gwei=Decimal("20.0"),
        eth_price_usd=Decimal("2800.0"),
    )
    assert lvr_res is not None and lvr_res.spread_bps > 0
    print(f"✅ Step 3: LVR Calculator active (Spread: {lvr_res.spread_bps:.1f} bps, EV: {lvr_res.net_ev_bps:.1f} bps).")

    # 4. Uniswap v4 Dynamic Fee Hook Oracle Push
    evm_router = EVMRouter(redis_client=redis)
    hook = V4HookController(redis_client=redis, evm_router=evm_router, whitelist_registry=registry)
    await hook.update_hook_regime("TREND_BULL")
    assert hook.current_fee_bps == 100
    print(f"✅ Step 4: Uniswap v4 Hook Oracle updated (Regime: TREND_BULL -> {hook.current_fee_bps} bps).")

    # 5. Delta-Neutral Carry Engine Sizing & 8h Checkpoint
    carry = DeltaNeutralCarryManager(redis_client=redis, whitelist_registry=registry)
    carry_opp = await carry.evaluate_carry_entry(
        symbol="BTC/USDT",
        funding_rate_8h=Decimal("0.0002"),
        spot_price=Decimal("60000"),
        perp_price=Decimal("60010"),
        available_capital_usd=Decimal("1000"),
    )
    assert carry_opp is not None
    await carry.open_position(
        "BTC/USDT",
        "DEX",
        "Bybit",
        carry_opp["trade_amount"],
        carry_opp["trade_amount"],
        Decimal("60000"),
        Decimal("60010"),
    )
    cp_res = await carry.process_8h_funding_checkpoint(
        "BTC/USDT", Decimal("0.0002"), Decimal("60000"), Decimal("60010")
    )
    assert cp_res["status"] == "HARVESTED"
    print(f"✅ Step 5: Delta-Neutral Carry harvested funding payment (${cp_res['payment_usd']:.2f}).")

    # 6. Active Treasury Allocation (Low EV Regime)
    pendle = PendleClient(redis_client=redis, evm_router=evm_router, whitelist_registry=registry)
    hlp = HLPClient(redis_client=redis, whitelist_registry=registry)
    treasury = TreasuryManager(
        redis_client=redis,
        evm_router=evm_router,
        whitelist_registry=registry,
        pendle_client=pendle,
        hlp_client=hlp,
    )
    treasury_res = await treasury.evaluate_and_allocate(
        idle_usdc=Decimal("200.0"), total_equity=Decimal("1000.0"), market_ev=0.40
    )
    assert treasury_res is not None and treasury_res["status"] == "CONFIRMED"
    print(f"✅ Step 6: Treasury Manager deployed idle USDC to {treasury_res['venue']} (${treasury_res['deposit_usdc']:.2f}).")

    # 7. Smart Order Router Method Verification
    assert hasattr(SmartOrderRouter, "execute_fee_aware")
    assert hasattr(SmartOrderRouter, "execute_regime_aware")
    print("✅ Step 7: SOR Fee-Aware and Regime-Aware routing methods verified.")

    await redis.disconnect()
    await db.dispose()
    print("=====================================================")
    print("  🎉 ALL 7 END-TO-END VALIDATION STEPS PASSED!       ")
    print("=====================================================")


if __name__ == "__main__":
    asyncio.run(test_end_to_end())
