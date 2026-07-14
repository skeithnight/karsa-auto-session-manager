"""Tests for Sector Cap."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.risk.sector_cap import SectorCap


class TestSectorCap:
    def setup_method(self):
        self.position_store = AsyncMock()
        self.cap = SectorCap(position_store=self.position_store, max_per_sector=2)

    @pytest.mark.asyncio
    async def test_check_allowed_below_cap(self):
        self.position_store.list_all.return_value = [
            {"symbol": "ETH/USDT"},
        ]
        assert await self.cap.check("BTC/USDT") is True

    @pytest.mark.asyncio
    async def test_check_rejected_at_cap(self):
        self.position_store.list_all.return_value = [
            {"symbol": "BTC/USDT"},
            {"symbol": "ETH/USDT"},
        ]
        # MAJORS at 2/2 cap — any new MAJORS symbol rejected
        assert await self.cap.check("BTC/USDT") is False

    @pytest.mark.asyncio
    async def test_check_unknown_sector_allowed(self):
        self.position_store.list_all.return_value = []
        assert await self.cap.check("XYZ/USDT") is True

    @pytest.mark.asyncio
    async def test_get_status(self):
        self.position_store.list_all.return_value = [
            {"symbol": "BTC/USDT"},
            {"symbol": "ETH/USDT"},
            {"symbol": "SOL/USDT"},
        ]
        status = await self.cap.get_status()
        assert status["MAJORS"] == {"current": 2, "max": 2}
        assert status["L1"] == {"current": 1, "max": 2}

    @pytest.mark.asyncio
    async def test_multiple_sectors(self):
        self.position_store.list_all.return_value = [
            {"symbol": "BTC/USDT"},
            {"symbol": "SOL/USDT"},
            {"symbol": "UNI/USDT"},
        ]
        # MAJORS at 1/2, L1 at 1/2, DEFI at 1/2 — all below cap
        assert await self.cap.check("ETH/USDT") is True
        assert await self.cap.check("AVAX/USDT") is True
        assert await self.cap.check("AAVE/USDT") is True
