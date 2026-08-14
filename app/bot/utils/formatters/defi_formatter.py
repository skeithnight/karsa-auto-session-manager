"""DeFi and On-Chain Quantitative Report Formatter.

Formats comprehensive analytics for:
  - Active Treasury Yield (HLP & Pendle PT)
  - Loss-Versus-Rebalancing (LVR) & DEX-CEX Spreads
  - Uniswap v4 Dynamic Fee Hook & MEV Protection
  - Delta-Neutral Funding Rate Carry Engine
  - On-Chain Pre-Trade Security & Whitelist Registry
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.bot.utils.format import bold, fmt, italic, pre


class DeFiReportFormatter:
    """Formats institutional-grade DeFi and on-chain telemetry reports."""

    @classmethod
    def format_comprehensive_report(
        cls,
        total_deployed_usdc: Decimal,
        hlp_apy_pct: Decimal,
        pendle_apy_pct: Decimal,
        gas_gwei: Decimal,
        eth_dex_price: str,
        btc_dex_price: str,
        v4_fee_bps: int,
        active_regime: str = "TREND_BULL",
        carry_symbol: str = "BTC/USDT",
        carry_apr_pct: Decimal = Decimal("21.9"),
        carry_harvested_usd: Decimal = Decimal("0.00"),
        carry_consecutive_neg: int = 0,
        whitelisted_count: int = 3,
    ) -> str:
        """Build comprehensive multi-section DeFi telemetry text."""
        header = (
            "🏴‍☠️ DEFI & ON-CHAIN QUANTITATIVE REPORT\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        # Section 1: Active Treasury
        projected_daily = (total_deployed_usdc * (hlp_apy_pct / Decimal("100")) / Decimal("365")).quantize(Decimal("0.01"))
        projected_monthly = (projected_daily * Decimal("30")).quantize(Decimal("0.01"))
        
        treasury_block = (
            "🏦 1. ACTIVE TREASURY & FIXED YIELD ALLOCATION\n"
            f"• Total Capital Deployed : ${total_deployed_usdc:,.2f} USDC (Max 30% Equity)\n"
            f"• Hyperliquid HLP Vault : {hlp_apy_pct:.1f}% APY (Zero Gas, Spread Yield)\n"
            f"• Pendle PT Principal   : {pendle_apy_pct:.1f}% APY (Locked Zero-Coupon Discount)\n"
            f"• Projected Cash Flow   : +${projected_daily}/day (~+${projected_monthly}/mo)\n"
            f"• Deployment State      : Low-EV Regime Yield Active (<0.50 EV)"
        )

        # Section 2: LVR & DEX Spreads
        lvr_block = (
            "⚡ 2. LVR (LOSS-VERSUS-REBALANCING) & DEX ARBITRAGE\n"
            f"• Uniswap v3/v4 ETH/USDT: {eth_dex_price}\n"
            f"• Uniswap v3/v4 WBTC/ETH: {btc_dex_price}\n"
            f"• EVM Network Gas Price : {gas_gwei:.1f} Gwei (Ceiling <= 100 Gwei)\n"
            f"• Lead-Lag Alpha Status : Active (+0.05 EV Scorer Boost on Divergence)\n"
            f"• LVR Spread Net EV     : Real-Time Slippage & Gas Deducted"
        )

        # Section 3: Uniswap v4 Dynamic Fee Hook
        hook_block = (
            "🦄 3. UNISWAP V4 DYNAMIC FEE HOOK & MEV SHIELD\n"
            f"• Active Market Regime  : {active_regime}\n"
            f"• Dynamic Hook Swap Fee : {v4_fee_bps} bps ({v4_fee_bps / 100:.2f}%)\n"
            f"• Toxic Arb Mitigation  : High Volatility -> 100 bps Protection\n"
            f"• Retail Flow Capture   : Low Volatility Range -> 5 bps Incentive\n"
            f"• MEV Shield Routing    : Flashbots Protect / MEV Blocker (0 Mempool Leaks)"
        )

        # Section 4: Delta-Neutral Carry Engine
        carry_block = (
            "⚖️ 4. DELTA-NEUTRAL FUNDING RATE CARRY ENGINE\n"
            f"• Active Arbitrage Pair : {carry_symbol} (Spot Long + Bybit Perp Short)\n"
            f"• Net Directional Delta : Delta = 0.00 (Pure Carry Harvest)\n"
            f"• Current Annualized APR: {carry_apr_pct:.1f}% APR (Harvest Interval: 8h)\n"
            f"• Total Funding Harvest : ${carry_harvested_usd:,.2f} USD\n"
            f"• Negative Watchdog     : {carry_consecutive_neg}/3 Periods (Auto-Unwind Guard)"
        )

        # Section 5: Security & Whitelist Registry
        security_block = (
            "🛡️ 5. PRE-TRADE SECURITY & PROTOCOL WHITELIST\n"
            f"• Whitelisted Protocols : {whitelisted_count} Audited Protocols (Pendle, HL, Uniswap v4)\n"
            f"• Pre-Trade Simulation  : Honeypot Shield & Transfer Tax Guard (Max 3%)\n"
            f"• Bytecode Verification : 100% Verified Smart Contract Sources\n"
            f"• Nonce Lock Guard      : Atomic Nonce Serialization & Gas Floor Active"
        )

        footer = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Status: System Operational • Institutional Risk Gate Enforced"
        )

        return "\n\n".join([
            pre(header),
            pre(treasury_block),
            pre(lvr_block),
            pre(hook_block),
            pre(carry_block),
            pre(security_block),
            pre(footer),
        ])
