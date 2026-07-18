"""Tests for PortfolioRiskManager — Phase 6.4."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.risk.portfolio_risk_manager import (
    PortfolioRiskManager,
)


def _make_signal(symbol: str = "SOL/USDT") -> MagicMock:
    s = MagicMock()
    s.symbol = symbol
    return s


def _make_prm(
    positions: list[dict] | None = None,
    sector_map: dict[str, str] | None = None,
) -> PortfolioRiskManager:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    position_store = AsyncMock()
    position_store.list_all.return_value = positions or []
    trade_store = AsyncMock()
    sector_mapping = AsyncMock()
    if sector_map:
        sector_mapping.get_sector.side_effect = lambda s: sector_map.get(s, "unknown")
    else:
        sector_mapping.get_sector.return_value = "L1"
    bybit_client = AsyncMock()
    bybit_client.get_wallet_balance.return_value = {
        "equity": "10000",
        "available": "10000",
    }
    return PortfolioRiskManager(
        redis, position_store, trade_store, sector_mapping, bybit_client
    )


class TestCorrelationTrap:
    @pytest.mark.asyncio
    async def test_two_l1_alts_third_blocked(self) -> None:
        prm = _make_prm(
            positions=[{"symbol": "SOL/USDT"}, {"symbol": "AVAX/USDT"}],
            sector_map={"SOL/USDT": "L1", "AVAX/USDT": "L1", "ADA/USDT": "L1"},
        )
        result = await prm.check(_make_signal("ADA/USDT"))
        assert result.approved is False
        assert "L1" in result.reason

    @pytest.mark.asyncio
    async def test_two_l1_alts_btc_allowed(self) -> None:
        prm = _make_prm(
            positions=[{"symbol": "SOL/USDT"}, {"symbol": "AVAX/USDT"}],
            sector_map={"SOL/USDT": "L1", "AVAX/USDT": "L1"},
        )
        result = await prm.check(_make_signal("BTC/USDT"))
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_no_positions_all_pass(self) -> None:
        prm = _make_prm(positions=[])
        result = await prm.check(_make_signal("SOL/USDT"))
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_different_sectors_dont_interfere(self) -> None:
        prm = _make_prm(
            positions=[{"symbol": "SOL/USDT"}, {"symbol": "AVAX/USDT"}],
            sector_map={"SOL/USDT": "L1", "AVAX/USDT": "L1", "UNI/USDT": "DeFi"},
        )
        result = await prm.check(_make_signal("UNI/USDT"))
        assert result.approved is True


class TestFailSafe:
    @pytest.mark.asyncio
    async def test_sector_exception_blocks_via_failsafe(self) -> None:
        """Exception inside check path → fail-safe BLOCK."""
        prm = _make_prm(positions=[{"symbol": "SOL/USDT"}])
        prm._sector_mapping.get_sector.side_effect = RuntimeError("boom")
        result = await prm.check(_make_signal("SOL/USDT"))
        assert result.approved is False
        assert result.reason  # has a reason string

    @pytest.mark.asyncio
    async def test_sector_mapping_failure_blocks(self) -> None:
        prm = _make_prm(positions=[{"symbol": "SOL/USDT"}])
        prm._sector_mapping.get_sector.side_effect = RuntimeError("boom")
        result = await prm.check(_make_signal("SOL/USDT"))
        assert result.approved is False


class TestCheckStructure:
    @pytest.mark.asyncio
    async def test_all_checks_present_on_success(self) -> None:
        prm = _make_prm(positions=[])
        result = await prm.check(_make_signal())
        assert result.approved is True
        assert result.checks is not None
        assert len(result.checks) == 4

    @pytest.mark.asyncio
    async def test_signal_no_symbol_blocked(self) -> None:
        prm = _make_prm()
        signal = MagicMock()
        signal.symbol = None
        result = await prm.check(signal)
        assert result.approved is False
