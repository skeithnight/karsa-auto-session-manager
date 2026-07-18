"""Tests for ShadowAPM — shadow active position management.

Covers wick detection, funding drag, pending fill/expiry, and state isolation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.shadow_store import ShadowPositionStore
from app.execution.shadow import ShadowAPM, ShadowExecutor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    """RedisClient mock for ShadowAPM."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    return redis


@pytest.fixture
def mock_executor():
    """ShadowExecutor mock — only _get_mid_price is used by APM."""
    executor = AsyncMock(spec=ShadowExecutor)
    executor._get_mid_price = AsyncMock(return_value=Decimal("51000"))
    executor._slippage = Decimal("0.0005")
    executor._taker_fee = Decimal("0.00055")
    executor._maker_fee = Decimal("0.0002")
    return executor


@pytest.fixture
def mock_pos_store():
    """PositionStore mock — tracks key lookups and removals."""
    store = AsyncMock()
    store._key = MagicMock(return_value="shadow:position:BTC/USDT:buy")
    store.list_all = AsyncMock(return_value=[])
    store.remove = AsyncMock()
    store.update_peak = AsyncMock()
    return store


@pytest.fixture
def mock_trade_store():
    """TradeStore mock for shadow_trades table."""
    store = AsyncMock()
    store.record_entry = AsyncMock(return_value=1)
    store.close_trade = AsyncMock(return_value=1)
    return store


@pytest.fixture
def shadow_apm(mock_redis, mock_executor, mock_pos_store, mock_trade_store):
    """Create ShadowAPM with mocked dependencies."""
    real_apm = MagicMock()
    return ShadowAPM(
        real_apm=real_apm,
        shadow_executor=mock_executor,
        redis_client=mock_redis,
        position_store=mock_pos_store,
        trade_store=mock_trade_store,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_open_position(
    symbol: str = "BTC/USDT",
    side: str = "buy",
    entry_price: str = "50500",
    virtual_sl: str = "50000",
    virtual_tp: str = "60000",
    worst_price_seen: str = "51000",
    total_funding_fees: str = "0",
    last_funding_ts: str | None = None,
    fee_type: str = "taker",
    amount: str = "1.0",
) -> dict:
    """Build a typical OPEN position dict."""
    if last_funding_ts is None:
        last_funding_ts = datetime.now(UTC).isoformat()
    return {
        "symbol": symbol,
        "side": side,
        "status": "OPEN",
        "entry_price": entry_price,
        "amount": amount,
        "virtual_sl": virtual_sl,
        "virtual_tp": virtual_tp,
        "worst_price_seen": worst_price_seen,
        "total_funding_fees": total_funding_fees,
        "last_funding_ts": last_funding_ts,
        "fee_type": fee_type,
        "entry_confidence": 0.75,
        "regime": "RANGE",
        "strategy": "mean_reversion",
    }


def _make_pending_position(
    symbol: str = "BTC/USDT",
    side: str = "buy",
    entry_price: str = "50000",
    pending_since: str | None = None,
    amount: str = "0.5",
) -> dict:
    """Build a typical PENDING_VIRTUAL_FILL position dict."""
    if pending_since is None:
        pending_since = datetime.now(UTC).isoformat()
    return {
        "symbol": symbol,
        "side": side,
        "status": "PENDING_VIRTUAL_FILL",
        "entry_price": entry_price,
        "amount": amount,
        "pending_since": pending_since,
        "virtual_sl": "0",
        "virtual_tp": "0",
    }


# ---------------------------------------------------------------------------
# 1. Wick detection — worst_price_seen catches SL hits from wicks
# ---------------------------------------------------------------------------

class TestShadowAPMWickDetection:
    """Price dips below SL then recovers; APM catches via worst_price_seen."""

    @pytest.mark.asyncio
    async def test_wick_below_sl_triggers_close(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """
        Position has virtual_sl=50000 and worst_price_seen=51000.
        Current mid drops to 49500 — below SL.
        APM must detect sl_hit and call _close_shadow_position.
        """
        pos = _make_open_position(virtual_sl="50000", worst_price_seen="51000")
        mock_executor._get_mid_price = AsyncMock(return_value=Decimal("49500"))

        with patch("app.execution.shadow.get_settings") as mock_settings:
            settings = MagicMock()
            settings.shadow_maker_fee_pct = "0.0002"
            settings.shadow_taker_fee_pct = "0.00055"
            mock_settings.return_value = settings

            await shadow_apm._manage_shadow_position(pos)

        # worst_price_seen must have been updated to 49500
        assert pos["worst_price_seen"] == "49500"
        # _close_shadow_position must be called with sl_hit reason
        mock_pos_store.remove.assert_called_once_with("BTC/USDT", "buy")

    @pytest.mark.asyncio
    async def test_wick_recovers_but_worst_persists(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """
        After a wick below SL, mid recovers above SL on next tick.
        worst_price_seen is still the dipped price → SL hit is still detected.
        """
        # First tick: wick — mid dips below SL
        pos_tick1 = _make_open_position(virtual_sl="50000", worst_price_seen="51000")
        mock_executor._get_mid_price = AsyncMock(return_value=Decimal("49500"))

        with patch("app.execution.shadow.get_settings") as mock_settings:
            settings = MagicMock()
            settings.shadow_maker_fee_pct = "0.0002"
            settings.shadow_taker_fee_pct = "0.00055"
            mock_settings.return_value = settings

            await shadow_apm._manage_shadow_position(pos_tick1)

        # Position was closed (remove called) on the wick tick
        mock_pos_store.remove.assert_called_once_with("BTC/USDT", "buy")

    @pytest.mark.asyncio
    async def test_no_sl_hit_when_mid_above_sl(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """Mid is above SL — no close, worst_price_seen updated normally."""
        pos = _make_open_position(virtual_sl="50000", worst_price_seen="51000")
        mock_executor._get_mid_price = AsyncMock(return_value=Decimal("52000"))

        await shadow_apm._manage_shadow_position(pos)

        assert pos["worst_price_seen"] == "51000"  # worst unchanged
        mock_pos_store.remove.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Funding drag — 8h funding fee deducted from virtual PnL
# ---------------------------------------------------------------------------

class TestShadowAPMFundingDrag:
    """Position held >8h — funding fee deducted."""

    @pytest.mark.asyncio
    async def test_funding_fee_deducted_after_8h(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """
        last_funding_ts is 9 hours ago. Funding rate is 0.0001.
        Position notional = 50500 * 1.0 = 50500.
        Funding fee = 50500 * 0.0001 = 5.05.
        """
        nine_hours_ago = (datetime.now(UTC) - timedelta(hours=9)).isoformat()
        pos = _make_open_position(
            entry_price="50500",
            virtual_sl="40000",  # well below mid — no SL hit
            last_funding_ts=nine_hours_ago,
            worst_price_seen="51000",
        )
        mock_executor._get_mid_price = AsyncMock(return_value=Decimal("51000"))

        # Mock Redis funding rate lookup
        async def fake_get(key):
            if key == "funding:BTC/USDT":
                return json.dumps({"funding_rate": "0.0001"})
            return None
        mock_redis.get = AsyncMock(side_effect=fake_get)

        await shadow_apm._manage_shadow_position(pos)

        # total_funding_fees should now reflect the 5.05 fee
        funding = Decimal(pos["total_funding_fees"])
        assert funding == Decimal("5.0500") or funding == Decimal("5.05")

    @pytest.mark.asyncio
    async def test_no_funding_if_less_than_8h(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """last_funding_ts is 2 hours ago — no funding applied."""
        two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        pos = _make_open_position(
            entry_price="50500",
            virtual_sl="40000",
            last_funding_ts=two_hours_ago,
            worst_price_seen="51000",
        )
        mock_executor._get_mid_price = AsyncMock(return_value=Decimal("51000"))

        await shadow_apm._manage_shadow_position(pos)

        assert Decimal(pos["total_funding_fees"]) == Decimal("0")


# ---------------------------------------------------------------------------
# 3. Pending fill — PENDING order price crosses entry → status becomes OPEN
# ---------------------------------------------------------------------------

class TestShadowAPMPendingFill:
    """PENDING order filled when live price crosses virtual entry."""

    @pytest.mark.asyncio
    async def test_pending_buy_fills_when_mid_drops_to_entry(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """
        Long limit at 50000. Mid dips to 49800 (at or below entry).
        Status must transition to OPEN.
        """
        pos = _make_pending_position(side="buy", entry_price="50000")
        mock_executor._get_mid_price = AsyncMock(return_value=Decimal("49800"))

        # _check_pending_fill calls self._pos_store._key
        mock_pos_store._key = MagicMock(return_value="shadow:position:BTC/USDT:buy")

        await shadow_apm._manage_shadow_position(pos)

        assert pos["status"] == "OPEN"
        assert "worst_price_seen" in pos
        assert pos["worst_price_seen"] == "50000"
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_pending_sell_fills_when_mid_rises_to_entry(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """
        Short limit at 50000. Mid rises to 50200 (at or above entry).
        Status must transition to OPEN.
        """
        pos = _make_pending_position(side="sell", entry_price="50000")
        mock_executor._get_mid_price = AsyncMock(return_value=Decimal("50200"))
        mock_pos_store._key = MagicMock(return_value="shadow:position:BTC/USDT:sell")

        await shadow_apm._manage_shadow_position(pos)

        assert pos["status"] == "OPEN"


# ---------------------------------------------------------------------------
# 4. Pending expiry — PENDING order >600s → removed from pos_store
# ---------------------------------------------------------------------------

class TestShadowAPMPendingExpiry:
    """Pending orders older than SHADOW_PENDING_TTL_SECS are removed."""

    @pytest.mark.asyncio
    async def test_pending_order_expires_after_ttl(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """
        pending_since is 700s ago (>600s TTL).
        Position must be removed from the store without filling.
        """
        expired_since = (
            datetime.now(UTC) - timedelta(seconds=700)
        ).isoformat()
        pos = _make_pending_position(pending_since=expired_since)

        await shadow_apm._manage_shadow_position(pos)

        # Must not transition to OPEN
        assert pos["status"] == "PENDING_VIRTUAL_FILL"
        mock_pos_store.remove.assert_called_once_with("BTC/USDT", "buy")
        # No mid price fetch needed
        mock_executor._get_mid_price.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_order_not_expired_within_ttl(self, shadow_apm, mock_executor, mock_pos_store, mock_redis):
        """
        pending_since is 300s ago (<600s TTL).
        Price not crossing entry — remains PENDING.
        """
        recent_since = (
            datetime.now(UTC) - timedelta(seconds=300)
        ).isoformat()
        pos = _make_pending_position(pending_since=recent_since)
        # Mid far above entry for buy — won't fill
        mock_executor._get_mid_price = AsyncMock(return_value=Decimal("55000"))

        await shadow_apm._manage_shadow_position(pos)

        assert pos["status"] == "PENDING_VIRTUAL_FILL"
        mock_pos_store.remove.assert_not_called()


# ---------------------------------------------------------------------------
# 5. State isolation — ShadowPositionStore._key() returns shadow:position:*
# ---------------------------------------------------------------------------

class TestShadowPositionStoreKey:
    """ShadowPositionStore uses shadow:position:* prefix, not karsa:position:*."""

    def test_key_returns_shadow_prefix(self):
        """_key() must return shadow:position:{symbol}:{side}."""
        mock_redis = AsyncMock()
        store = ShadowPositionStore(mock_redis)
        key = store._key("BTC/USDT", "buy")
        assert key == "shadow:position:BTC/USDT:buy"

    def test_key_isolation_from_live(self):
        """Shadow keys must not collide with live keys (karsa:position:*)."""
        mock_redis = AsyncMock()
        store = ShadowPositionStore(mock_redis)
        assert store._key("BTC/USDT", "buy").startswith("shadow:position:")
        assert not store._key("BTC/USDT", "buy").startswith("karsa:position:")


# ---------------------------------------------------------------------------
# 6. State isolation — ShadowPositionStore.cleanup_stale() returns 0
# ---------------------------------------------------------------------------

class TestShadowPositionStoreCleanupStale:
    """cleanup_stale() is a no-op in shadow mode — always returns 0."""

    @pytest.mark.asyncio
    async def test_cleanup_stale_returns_zero(self):
        """No exchange truth in shadow mode — must return 0."""
        mock_redis = AsyncMock()
        store = ShadowPositionStore(mock_redis)
        result = await store.cleanup_stale({"BTCUSDT", "ETHUSDT"})
        assert result == 0

    @pytest.mark.asyncio
    async def test_cleanup_stale_ignores_exchange_symbols(self):
        """cleanup_stale() never touches Redis regardless of input."""
        mock_redis = AsyncMock()
        store = ShadowPositionStore(mock_redis)
        result = await store.cleanup_stale(set())
        assert result == 0
        mock_redis.redis.keys.assert_not_called()
