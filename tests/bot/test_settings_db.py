from __future__ import annotations

"""Tests for SettingsStore -- Postgres-backed user settings persistence.

Importers: pytest auto-discovery. Callers: none (test file).
Affected API: SettingsStore.get_setting, set_setting, get_all_settings,
              sync_redis_to_db, sync_db_to_redis.
Data schemas: user_settings table from alembic/versions/005_add_user_settings.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.settings_store import (
    REDIS_SETTINGS_KEYS,
    SETTING_DEFAULTS,
    SettingsStore,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_engine():
    """Create a mock DatabaseEngine with a working connect()."""
    engine = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    engine.connect.return_value = conn
    return engine, conn


@pytest.fixture
def store(mock_db_engine):
    """Create a SettingsStore with a mock DB engine."""
    engine, _ = mock_db_engine
    db = MagicMock()
    db.engine = engine
    return SettingsStore(db)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    return redis


# ---------------------------------------------------------------------------
# get_setting tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_setting_returns_value(store, mock_db_engine):
    """get_setting returns the value when row exists."""
    _, conn = mock_db_engine
    result_mock = MagicMock()
    result_mock.fetchone.return_value = ("30",)
    conn.execute.return_value = result_mock

    value = await store.get_setting(user_id=123, key="risk_pct")
    assert value == "30"


@pytest.mark.asyncio
async def test_get_setting_returns_none_when_missing(store, mock_db_engine):
    """get_setting returns None when no row found."""
    _, conn = mock_db_engine
    result_mock = MagicMock()
    result_mock.fetchone.return_value = None
    conn.execute.return_value = result_mock

    value = await store.get_setting(user_id=999, key="risk_pct")
    assert value is None


@pytest.mark.asyncio
async def test_get_setting_handles_db_error(store, mock_db_engine):
    """get_setting returns None on DB error (never raises)."""
    _, conn = mock_db_engine
    conn.execute.side_effect = Exception("connection lost")

    value = await store.get_setting(user_id=123, key="risk_pct")
    assert value is None


# ---------------------------------------------------------------------------
# set_setting tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_setting_executes_upsert(store, mock_db_engine):
    """set_setting runs the INSERT ... ON CONFLICT upsert."""
    _, conn = mock_db_engine
    conn.execute.return_value = MagicMock()

    await store.set_setting(user_id=123, key="risk_pct", value="50")

    conn.execute.assert_called_once()
    call_args = conn.execute.call_args
    # Verify the SQL contains ON CONFLICT
    sql_str = str(call_args[0][0])
    assert "ON CONFLICT" in sql_str
    assert call_args[0][1] == {"user_id": 123, "key": "risk_pct", "value": "50"}


@pytest.mark.asyncio
async def test_set_setting_commits(store, mock_db_engine):
    """set_setting commits the transaction."""
    _, conn = mock_db_engine
    conn.execute.return_value = MagicMock()

    await store.set_setting(user_id=123, key="risk_pct", value="50")

    conn.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_setting_handles_db_error(store, mock_db_engine):
    """set_setting logs error but never raises."""
    _, conn = mock_db_engine
    conn.execute.side_effect = Exception("table locked")

    # Should not raise
    await store.set_setting(user_id=123, key="risk_pct", value="50")


# ---------------------------------------------------------------------------
# get_all_settings tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_settings_returns_dict(store, mock_db_engine):
    """get_all_settings returns a dict of all settings for the user."""
    _, conn = mock_db_engine
    result_mock = MagicMock()
    result_mock.fetchall.return_value = [
        ("risk_pct", "30"),
        ("max_positions", "5"),
        ("trade_alerts", "1"),
    ]
    conn.execute.return_value = result_mock

    settings = await store.get_all_settings(user_id=123)
    assert settings == {
        "risk_pct": "30",
        "max_positions": "5",
        "trade_alerts": "1",
    }


@pytest.mark.asyncio
async def test_get_all_settings_returns_empty_when_no_rows(store, mock_db_engine):
    """get_all_settings returns empty dict when no settings exist."""
    _, conn = mock_db_engine
    result_mock = MagicMock()
    result_mock.fetchall.return_value = []
    conn.execute.return_value = result_mock

    settings = await store.get_all_settings(user_id=999)
    assert settings == {}


@pytest.mark.asyncio
async def test_get_all_settings_handles_db_error(store, mock_db_engine):
    """get_all_settings returns empty dict on error."""
    _, conn = mock_db_engine
    conn.execute.side_effect = Exception("timeout")

    settings = await store.get_all_settings(user_id=123)
    assert settings == {}


# ---------------------------------------------------------------------------
# sync_redis_to_db tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_redis_to_db_writes_all_keys(store, mock_db_engine, mock_redis):
    """sync_redis_to_db reads each Redis key and persists to DB."""
    _, conn = mock_db_engine
    conn.execute.return_value = MagicMock()

    # Redis returns values for each key
    mock_redis.get = AsyncMock(side_effect=["30", "5", "1", "1", "1", ""])

    await store.sync_redis_to_db(mock_redis, user_id=123)

    # Should have called set_setting for each key
    assert conn.execute.call_count == len(REDIS_SETTINGS_KEYS)


@pytest.mark.asyncio
async def test_sync_redis_to_db_skips_missing_keys(store, mock_db_engine, mock_redis):
    """sync_redis_to_db skips keys that are None in Redis."""
    _, conn = mock_db_engine
    conn.execute.return_value = MagicMock()

    # All keys return None
    mock_redis.get = AsyncMock(return_value=None)

    await store.sync_redis_to_db(mock_redis, user_id=123)

    # Should not write anything (all keys missing)
    assert conn.execute.call_count == 0


# ---------------------------------------------------------------------------
# sync_db_to_redis tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_db_to_redis_populates_empty_redis(store, mock_db_engine, mock_redis):
    """sync_db_to_redis restores settings from DB when Redis is empty."""
    _, conn = mock_db_engine
    result_mock = MagicMock()
    result_mock.fetchall.return_value = [
        ("risk_pct", "50"),
        ("max_positions", "7"),
    ]
    conn.execute.return_value = result_mock

    # Redis.get returns None for all keys (empty Redis)
    mock_redis.get = AsyncMock(return_value=None)

    restored = await store.sync_db_to_redis(mock_redis, user_id=123)

    assert restored == 2
    assert mock_redis.set.call_count == 2


@pytest.mark.asyncio
async def test_sync_db_to_redis_skips_existing_redis_keys(store, mock_db_engine, mock_redis):
    """sync_db_to_redis does not overwrite existing Redis values."""
    _, conn = mock_db_engine
    result_mock = MagicMock()
    result_mock.fetchall.return_value = [
        ("risk_pct", "50"),
        ("max_positions", "7"),
    ]
    conn.execute.return_value = result_mock

    # Redis already has risk_pct set
    mock_redis.get = AsyncMock(side_effect=["30", None, None, None, None, None])

    restored = await store.sync_db_to_redis(mock_redis, user_id=123)

    # Only max_positions should be restored (risk_pct already in Redis)
    assert restored == 1
    assert mock_redis.set.call_count == 1


@pytest.mark.asyncio
async def test_sync_db_to_redis_skips_empty_mute_until(store, mock_db_engine, mock_redis):
    """sync_db_to_redis skips empty mute_until values."""
    _, conn = mock_db_engine
    result_mock = MagicMock()
    result_mock.fetchall.return_value = [
        ("mute_until", ""),
    ]
    conn.execute.return_value = result_mock

    mock_redis.get = AsyncMock(return_value=None)

    restored = await store.sync_db_to_redis(mock_redis, user_id=123)

    assert restored == 0
    mock_redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_db_to_redis_returns_zero_when_no_db_settings(store, mock_db_engine, mock_redis):
    """sync_db_to_redis returns 0 when DB has no settings."""
    _, conn = mock_db_engine
    result_mock = MagicMock()
    result_mock.fetchall.return_value = []
    conn.execute.return_value = result_mock

    mock_redis.get = AsyncMock(return_value=None)

    restored = await store.sync_db_to_redis(mock_redis, user_id=123)

    assert restored == 0


# ---------------------------------------------------------------------------
# Integration-style tests (cross-function)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_persist_across_writes(store, mock_db_engine):
    """Simulate writing settings then reading them back."""
    _, conn = mock_db_engine

    # First call: set_setting inserts
    conn.execute.return_value = MagicMock()
    await store.set_setting(user_id=1, key="risk_pct", value="50")

    # Second call: get_setting reads it back
    result_mock = MagicMock()
    result_mock.fetchone.return_value = ("50",)
    conn.execute.return_value = result_mock

    value = await store.get_setting(user_id=1, key="risk_pct")
    assert value == "50"


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_value(store, mock_db_engine):
    """Upsert replaces old value with new value."""
    _, conn = mock_db_engine

    # Write initial value
    conn.execute.return_value = MagicMock()
    await store.set_setting(user_id=1, key="risk_pct", value="30")

    # Write new value (upsert)
    conn.execute.return_value = MagicMock()
    await store.set_setting(user_id=1, key="risk_pct", value="70")

    # Read back - should be 70
    result_mock = MagicMock()
    result_mock.fetchone.return_value = ("70",)
    conn.execute.return_value = result_mock

    value = await store.get_setting(user_id=1, key="risk_pct")
    assert value == "70"


@pytest.mark.asyncio
async def test_different_users_are_isolated(store, mock_db_engine):
    """Settings for different users do not interfere."""
    _, conn = mock_db_engine

    # Write for user 1
    conn.execute.return_value = MagicMock()
    await store.set_setting(user_id=1, key="risk_pct", value="50")

    # Write for user 2
    conn.execute.return_value = MagicMock()
    await store.set_setting(user_id=2, key="risk_pct", value="10")

    # Read user 1
    result_mock_1 = MagicMock()
    result_mock_1.fetchone.return_value = ("50",)
    conn.execute.return_value = result_mock_1
    val1 = await store.get_setting(user_id=1, key="risk_pct")

    # Read user 2
    result_mock_2 = MagicMock()
    result_mock_2.fetchone.return_value = ("10",)
    conn.execute.return_value = result_mock_2
    val2 = await store.get_setting(user_id=2, key="risk_pct")

    assert val1 == "50"
    assert val2 == "10"


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


def test_redis_settings_keys_match_handler_keys():
    """REDIS_SETTINGS_KEYS should match the keys used in settings handler."""
    expected_keys = {
        "karsa:settings:risk_pct",
        "karsa:settings:max_positions",
        "karsa:settings:alerts:trade",
        "karsa:settings:alerts:daily_summary",
        "karsa:settings:alerts:guardrail",
        "karsa:settings:alerts:mute_until",
    }
    assert set(REDIS_SETTINGS_KEYS.values()) == expected_keys


def test_setting_defaults_cover_all_keys():
    """SETTING_DEFAULTS should have a default for every Redis key."""
    assert set(SETTING_DEFAULTS.keys()) == set(REDIS_SETTINGS_KEYS.keys())


@pytest.mark.asyncio
async def test_set_risk_pct_100_supported(store, mock_db_engine):
    """Verify that 100% risk level can be saved and retrieved in settings store."""
    _, conn = mock_db_engine
    conn.execute.return_value = MagicMock()
    await store.set_setting(user_id=1, key="risk_pct", value="100")

    result_mock = MagicMock()
    result_mock.fetchone.return_value = ("100",)
    conn.execute.return_value = result_mock
    val = await store.get_setting(user_id=1, key="risk_pct")
    assert val == "100"


@pytest.mark.asyncio
async def test_settings_cmd_keyboard_has_all_risk_buttons():
    """Verify that settings_cmd builds keyboard with 10%, 30%, 50%, 70%, and 100% buttons."""
    from app.bot.handlers.settings import settings_cmd

    mock_update = MagicMock()
    mock_update.effective_user.id = 12345
    mock_update.callback_query = None

    mock_context = MagicMock()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="100")
    mock_context.bot_data = {"redis": mock_redis, "db_engine": None}

    with patch("app.bot.handlers.settings._is_authorized", return_value=True), \
         patch("app.bot.handlers.settings.send_or_edit_message") as mock_send:
        await settings_cmd(mock_update, mock_context)
        assert mock_send.called
        call_kwargs = mock_send.call_args[1]
        reply_markup = call_kwargs.get("reply_markup") or mock_send.call_args[0][2]
        risk_row = reply_markup.inline_keyboard[0]
        risk_callbacks = [btn.callback_data for btn in risk_row]
        assert risk_callbacks == [
            "settings:risk:10",
            "settings:risk:30",
            "settings:risk:50",
            "settings:risk:70",
            "settings:risk:100",
        ]
        risk_labels = [btn.text for btn in risk_row]
        assert risk_labels == ["10%", "30%", "50%", "70%", "100%"]

