"""WhitelistRegistry — audited protocol whitelist management for PRM.

All smart contract interactions MUST be checked against the WhitelistRegistry
before any on-chain transaction or treasury deployment is authorized.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class DeFiWhitelistEntry(BaseModel):
    """Model for an audited protocol whitelist entry."""

    protocol_name: str
    chain_id: int = 1  # 1 = Ethereum Mainnet, 8453 = Base, 42161 = Arbitrum
    contract_addresses: list[str]
    audit_firm: str = "OpenZeppelin"
    audit_date_iso: str = ""
    max_tvl_exposure_pct: Decimal = Field(default_factory=lambda: Decimal("0.30"))
    risk_tier: str = "BLUE_CHIP"  # BLUE_CHIP, ESTABLISHED, EXPERIMENTAL


DEFAULT_WHITELIST = [
    DeFiWhitelistEntry(
        protocol_name="Pendle",
        chain_id=1,
        contract_addresses=["0x00000000005BBB0039000C663000000000000000"],  # Pendle Router placeholder
        audit_firm="OpenZeppelin / WatchPug",
        audit_date_iso="2024-01-01T00:00:00Z",
        max_tvl_exposure_pct=Decimal("0.30"),
        risk_tier="BLUE_CHIP",
    ),
    DeFiWhitelistEntry(
        protocol_name="Hyperliquid",
        chain_id=1,
        contract_addresses=["0x0000000000000000000000000000000000000000"],  # HL L1 bridge placeholder
        audit_firm="Zellic / Trail of Bits",
        audit_date_iso="2024-01-01T00:00:00Z",
        max_tvl_exposure_pct=Decimal("0.30"),
        risk_tier="BLUE_CHIP",
    ),
    DeFiWhitelistEntry(
        protocol_name="Uniswapv4",
        chain_id=1,
        contract_addresses=["0x000000000004444c5dc75cB358380D2e3dE08A90"],  # PoolManager
        audit_firm="Dedaub / Cantina",
        audit_date_iso="2024-01-01T00:00:00Z",
        max_tvl_exposure_pct=Decimal("0.20"),
        risk_tier="BLUE_CHIP",
    ),
]


class WhitelistRegistry:
    """Manages audited DeFi protocols and contract address checks."""

    def __init__(self, entries: list[DeFiWhitelistEntry] | None = None) -> None:
        self._entries: dict[str, DeFiWhitelistEntry] = {}
        for entry in (entries or DEFAULT_WHITELIST):
            self._entries[entry.protocol_name.lower()] = entry

    def is_protocol_whitelisted(self, protocol_name: str) -> bool:
        """Check if protocol is registered."""
        return protocol_name.lower() in self._entries

    def is_address_whitelisted(self, address: str) -> bool:
        """Check if a contract address belongs to any whitelisted protocol."""
        addr_lower = address.lower()
        for entry in self._entries.values():
            if any(c.lower() == addr_lower for c in entry.contract_addresses):
                return True
        return False

    def get_entry(self, protocol_name: str) -> DeFiWhitelistEntry | None:
        """Get whitelist entry for protocol."""
        return self._entries.get(protocol_name.lower())

    def add_entry(self, entry: DeFiWhitelistEntry) -> None:
        """Register a new whitelisted protocol."""
        self._entries[entry.protocol_name.lower()] = entry
