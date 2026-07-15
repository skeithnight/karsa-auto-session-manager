"""Pydantic Settings — all secrets live ONLY here."""

from __future__ import annotations

from functools import lru_cache
from typing import List

from loguru import logger
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )

    # Bybit API credentials
    bybit_api_key: str
    bybit_api_secret: str
    bybit_testnet: bool = False

    # PostgreSQL
    postgres_url: str = "postgresql+asyncpg://karsa:karsa@db:5432/karsa"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Telegram alerts
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Trading
    symbols: List[str] = [
        # Tier 1 — majors ($100M+ daily turnover on Bybit)
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
        # Tier 2 — large caps ($20M+)
        "BNB/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT",
        "SUI/USDT", "NEAR/USDT", "WLD/USDT", "TAO/USDT", "AAVE/USDT",
        "ENA/USDT", "LTC/USDT", "APT/USDT", "ARB/USDT", "BCH/USDT",
        "UNI/USDT", "ONDO/USDT",
        # Tier 3 — mid caps ($5M+)
        "OP/USDT", "INJ/USDT", "ATOM/USDT", "DOT/USDT",
        "FIL/USDT", "ICP/USDT", "CRV/USDT", "PENDLE/USDT", "SEI/USDT",
        "ETC/USDT", "TIA/USDT", "RUNE/USDT", "XLM/USDT", "HBAR/USDT",
        "JUP/USDT", "GALA/USDT", "LDO/USDT", "ORDI/USDT",
        "DYDX/USDT", "STX/USDT", "KAS/USDT", "MNT/USDT", "STRK/USDT",
        "BLUR/USDT", "IMX/USDT", "GRT/USDT", "SNX/USDT", "TRB/USDT",
        "NOT/USDT", "MANA/USDT", "SAND/USDT", "VET/USDT",
        # Tier 4 — new/trending high-volume
        "FARTCOIN/USDT", "KAITO/USDT", "DEXE/USDT", "VANRY/USDT",
    ]

    # Circuit breaker
    daily_drawdown_limit: str = "-0.02"  # -2%, stored as str for Decimal conversion
    min_liquidity_usd: str = "500"  # $500 minimum L1 notional depth (L1 typically $200-1500)

    # Watchdog
    dead_mans_switch_url: str = ""
    dead_mans_switch_interval: int = 60  # seconds

    # 9router AI proxy (supports both 9ROUTER_* and nine_router_* env vars)
    nine_router_base_url: str = Field(
        default="http://127.0.0.1:20128",
        validation_alias=AliasChoices("9ROUTER_BASE_URL", "nine_router_base_url"),
    )
    nine_router_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices("9ROUTER_AUTH_TOKEN", "nine_router_auth_token"),
    )
    nine_router_model: str = Field(
        default="karsa-combo",
        validation_alias=AliasChoices("9ROUTER_MODEL", "nine_router_model"),
    )
    # AI mandatory — toggles removed per CONTEXT.md Issue #8
    # ai_analyst_enabled and ai_position_judge_enabled removed: AI is not optional


@lru_cache
def get_settings() -> Settings:
    """Cached singleton for app settings."""
    logger.debug("get_settings: entering")
    result = Settings()
    logger.debug("get_settings: returning Settings")
    return result
