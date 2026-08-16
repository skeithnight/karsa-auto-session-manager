"""Settings and toggle handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _get_db_engine,
    _get_redis,
    _is_authorized,
    _reply,
    build_main_keyboard,
)
from app.bot.utils.format import bold, fmt, italic, pre
from app.bot.utils.telegram_helpers import send_or_edit_message

logger = logging.getLogger(__name__)

# Mapping from logical setting keys to Redis keys (for DB persistence)
_SETTING_KEY_MAP: dict[str, str] = {
    "risk_pct": "risk_pct",
    "max_positions": "max_positions",
    "trade_alerts": "trade_alerts",
    "daily_summary": "daily_summary",
    "guardrail_alerts": "guardrail_alerts",
    "mute_until": "mute_until",
}


async def _persist_setting(
    context: ContextTypes.DEFAULT_TYPE,
    key: str,
    value: str,
    user_id: int = 0,
) -> None:
    """Persist a setting to DB (best-effort). Logs errors but never raises."""
    db_engine = _get_db_engine(context)
    if db_engine is None:
        return
    try:
        from app.core.settings_store import SettingsStore

        store = SettingsStore(db_engine)
        await store.set_setting(user_id, key, value)
    except Exception as exc:
        logger.warning(
            "settings_db_persist_failed: key=%s error=%s", key, exc
        )


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot settings — table view with current values and section buttons."""
    logger.debug("settings_cmd: entering")
    if not _is_authorized(update):
        return

    r = _get_redis(context)

    # ── Read all settings from Redis ───────────────────────────────────
    try:
        risk_pct = int(await r.get("karsa:settings:risk_pct") or 30)
    except Exception as exc:
        logger.warning("settings_read_risk_failed", extra={"error": str(exc)})
        risk_pct = 30

    try:
        max_pos = int(await r.get("karsa:settings:max_positions") or 5)
    except Exception as exc:
        logger.warning("settings_read_max_pos_failed", extra={"error": str(exc)})
        max_pos = 5

    try:
        trade_alerts_raw = await r.get("karsa:settings:alerts:trade")
        trade_alerts = trade_alerts_raw in ("1", b"1") if trade_alerts_raw is not None else True
    except Exception:
        trade_alerts = True

    try:
        daily_summary_raw = await r.get("karsa:settings:alerts:daily_summary")
        daily_summary = daily_summary_raw in ("1", b"1") if daily_summary_raw is not None else True
    except Exception:
        daily_summary = True

    try:
        guardrail_alerts_raw = await r.get("karsa:settings:alerts:guardrail")
        guardrail_alerts = guardrail_alerts_raw in ("1", b"1") if guardrail_alerts_raw is not None else True
    except Exception:
        guardrail_alerts = True

    try:
        mute_until_raw = await r.get("karsa:settings:alerts:mute_until")
        mute_until = str(mute_until_raw) if mute_until_raw else None
    except Exception:
        mute_until = None

    # Check if currently muted
    is_muted = False
    if mute_until:
        try:
            mute_dt = datetime.fromisoformat(mute_until)
            if mute_dt > datetime.now(tz=timezone.utc):  # noqa: UP017
                is_muted = True
        except Exception:
            pass

    # ── Format settings table ──────────────────────────────────────────
    _dash = "─"
    _sep = _dash * 22 + _dash + _dash * 5 + _dash + _dash * 12

    alert_status = "MUTED" if is_muted else "ON"
    trade_icon = "✅" if trade_alerts else "❌"
    daily_icon = "✅" if daily_summary else "❌"
    guard_icon = "✅" if guardrail_alerts else "❌"

    settings_block = (
        f"{'Parameter':<22} {'Value':<5}  Options\n"
        f"{_sep}\n"
        f"{'Risk per Trade':<22} {risk_pct}%    [10% · 30% · 50% · 70% · 100%]\n"
        f"{'Max Positions':<22} {max_pos:<5}  [3 · 5 · 7]\n"
        f"{'Trade Alerts':<22} {trade_icon:<5}  [ON · OFF]\n"
        f"{'Daily Summary':<22} {daily_icon:<5}  [ON · OFF]\n"
        f"{'Guardrail Alerts':<22} {guard_icon:<5}  [ON · OFF]\n"
        f"{'Alert Status':<22} {alert_status:<5}  [MUTE · UNMUTE]"
    )

    text = fmt(
        bold("⚙️ BOT SETTINGS"),
        "\n",
        "━" * 36,
        "\n\n",
        pre(settings_block),
        "\n\n",
        italic("Tap a button below to change a setting."),
    )

    # ── Keyboard: risk buttons, position buttons, alert toggles ────────
    keyboard = [
        # Risk per Trade — dedicated buttons
        [
            InlineKeyboardButton("10%", callback_data="settings:risk:10"),
            InlineKeyboardButton("30%", callback_data="settings:risk:30"),
            InlineKeyboardButton("50%", callback_data="settings:risk:50"),
            InlineKeyboardButton("70%", callback_data="settings:risk:70"),
            InlineKeyboardButton("100%", callback_data="settings:risk:100"),
        ],
        # Max Positions — dedicated buttons
        [
            InlineKeyboardButton("3 Pos", callback_data="settings:max_pos:3"),
            InlineKeyboardButton("5 Pos", callback_data="settings:max_pos:5"),
            InlineKeyboardButton("7 Pos", callback_data="settings:max_pos:7"),
        ],
        # Alert toggles
        [
            InlineKeyboardButton(
                f"Trade: {'ON' if trade_alerts else 'OFF'}",
                callback_data="settings:alert:trade",
            ),
            InlineKeyboardButton(
                f"Daily: {'ON' if daily_summary else 'OFF'}",
                callback_data="settings:alert:daily_summary",
            ),
            InlineKeyboardButton(
                f"Guard: {'ON' if guardrail_alerts else 'OFF'}",
                callback_data="settings:alert:guardrail",
            ),
        ],
        # Mute controls
        [
            InlineKeyboardButton("\U0001f515 Mute 1h", callback_data="settings:mute:1h"),
            InlineKeyboardButton("\U0001f515 Mute 24h", callback_data="settings:mute:24h"),
            InlineKeyboardButton("\U0001f514 Unmute", callback_data="settings:mute:0"),
        ],
        # Navigation
        [
            InlineKeyboardButton("\U0001f39b️ Control Panel", callback_data="cmd_control"),
            InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
        ],
    ]

    await send_or_edit_message(update, str(text), reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("settings_cmd: returning None")


# ---------------------------------------------------------------------------
# Risk % Setting
# ---------------------------------------------------------------------------


async def _set_risk_pct(update: Update, context: ContextTypes.DEFAULT_TYPE, value: int):
    """Set risk percentage to a specific value."""
    logger.debug("_set_risk_pct: entering value=%d", value)
    r = _get_redis(context)
    try:
        await r.set("karsa:settings:risk_pct", str(value))
        await _persist_setting(context, "risk_pct", str(value))
        await settings_cmd(update, context)
    except Exception as exc:
        logger.error("set_risk_pct_failed", extra={"error": str(exc)})
        await _reply(update, "Failed to update risk setting.", reply_markup=build_main_keyboard())
    logger.debug("_set_risk_pct: returning None")


# ---------------------------------------------------------------------------
# Max Positions Setting
# ---------------------------------------------------------------------------


async def _set_max_pos(update: Update, context: ContextTypes.DEFAULT_TYPE, value: int):
    """Set max positions to a specific value."""
    logger.debug("_set_max_pos: entering value=%d", value)
    r = _get_redis(context)
    try:
        await r.set("karsa:settings:max_positions", str(value))
        await _persist_setting(context, "max_positions", str(value))
        await settings_cmd(update, context)
    except Exception as exc:
        logger.error("set_max_pos_failed", extra={"error": str(exc)})
        await _reply(
            update,
            "Failed to update max positions.",
            reply_markup=build_main_keyboard(),
        )
    logger.debug("_set_max_pos: returning None")


# ---------------------------------------------------------------------------
# Individual Alert Toggles
# ---------------------------------------------------------------------------


async def _toggle_trade_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle trade alert notifications on/off."""
    logger.debug("_toggle_trade_alerts: entering")
    r = _get_redis(context)
    try:
        current = await r.get("karsa:settings:alerts:trade")
        is_on = current in ("1", b"1") if current is not None else True
        new_val = "0" if is_on else "1"
        await r.set("karsa:settings:alerts:trade", new_val)
        await _persist_setting(context, "trade_alerts", new_val)
        await settings_cmd(update, context)
    except Exception as exc:
        logger.error("toggle_trade_alerts_failed", extra={"error": str(exc)})
    logger.debug("_toggle_trade_alerts: returning None")


async def _toggle_daily_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle daily summary notifications on/off."""
    logger.debug("_toggle_daily_summary: entering")
    r = _get_redis(context)
    try:
        current = await r.get("karsa:settings:alerts:daily_summary")
        is_on = current in ("1", b"1") if current is not None else True
        new_val = "0" if is_on else "1"
        await r.set("karsa:settings:alerts:daily_summary", new_val)
        await _persist_setting(context, "daily_summary", new_val)
        await settings_cmd(update, context)
    except Exception as exc:
        logger.error("toggle_daily_summary_failed", extra={"error": str(exc)})
    logger.debug("_toggle_daily_summary: returning None")


async def _toggle_guardrail_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle guardrail alert notifications on/off."""
    logger.debug("_toggle_guardrail_alerts: entering")
    r = _get_redis(context)
    try:
        current = await r.get("karsa:settings:alerts:guardrail")
        is_on = current in ("1", b"1") if current is not None else True
        new_val = "0" if is_on else "1"
        await r.set("karsa:settings:alerts:guardrail", new_val)
        await _persist_setting(context, "guardrail_alerts", new_val)
        await settings_cmd(update, context)
    except Exception as exc:
        logger.error("toggle_guardrail_alerts_failed", extra={"error": str(exc)})
    logger.debug("_toggle_guardrail_alerts: returning None")


# ---------------------------------------------------------------------------
# Mute / Unmute
# ---------------------------------------------------------------------------


async def _set_mute_duration(update: Update, context: ContextTypes.DEFAULT_TYPE, hours: int):
    """Mute all alerts for the specified number of hours. 0 = unmute."""
    logger.debug("_set_mute_duration: entering hours=%d", hours)
    r = _get_redis(context)
    try:
        if hours <= 0:
            # Unmute
            await r.delete("karsa:settings:alerts:mute_until")
            await _persist_setting(context, "mute_until", "")
        else:
            mute_dt = datetime.now(tz=timezone.utc).replace(  # noqa: UP017
                hour=datetime.now(tz=timezone.utc).hour + hours  # noqa: UP017
            )
            mute_iso = mute_dt.isoformat()
            await r.set("karsa:settings:alerts:mute_until", mute_iso)
            await _persist_setting(context, "mute_until", mute_iso)

        # Refresh settings view
        await settings_cmd(update, context)
    except Exception as exc:
        logger.error("set_mute_duration_failed", extra={"error": str(exc)})
    logger.debug("_set_mute_duration: returning None")


# ---------------------------------------------------------------------------
# Legacy Toggle Stubs (kept for backward compatibility)
# ---------------------------------------------------------------------------


async def _toggle_max_pos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cycle max positions: 3 -> 5 -> 8 -> 3 (legacy compatibility)."""
    await settings_cmd(update, context)


async def _toggle_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cycle risk percentage (legacy compatibility — prefer direct buttons)."""
    await settings_cmd(update, context)


async def _toggle_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle regime filter on/off (legacy compatibility)."""
    await settings_cmd(update, context)


async def _toggle_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle trade alert notifications (legacy compatibility)."""
    await _toggle_trade_alerts(update, context)
