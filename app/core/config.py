"""Pydantic Settings — all secrets live ONLY here."""

from __future__ import annotations

from functools import lru_cache

from loguru import logger
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Bybit API credentials ──────────────────────────────────
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    bybit_testnet: bool = False

    # ── Shadow Mode ────────────────────────────────────────────
    shadow_mode_enabled: bool = False
    shadow_initial_balance: str = "100.0"
    shadow_slippage_pct: str = "0.0005"
    shadow_taker_fee_pct: str = "0.00055"
    shadow_maker_fee_pct: str = "0.0002"

    # ── PostgreSQL ─────────────────────────────────────────────
    postgres_url: str = "postgresql+asyncpg://karsa:karsa@db:5432/karsa"

    # ── Redis ──────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── Telegram alerts ────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Commander ──────────────────────────────────────────────
    commander_bulk_backtest_interval_hours: int = 24
    commander_feedback_interval_hours: int = 1

    # ── Trading ────────────────────────────────────────────────
    symbols: list[str] = [
        # Tier 1 — majors ($100M+ daily turnover on Bybit)
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "XRP/USDT",
        "TON/USDT",
        # Tier 2 — large caps ($20M+)
        "BNB/USDT",
        "ADA/USDT",
        "DOGE/USDT",
        "AVAX/USDT",
        "LINK/USDT",
        "SUI/USDT",
        "NEAR/USDT",
        "WLD/USDT",
        "TAO/USDT",
        "AAVE/USDT",
        "ENA/USDT",
        "LTC/USDT",
        "APT/USDT",
        "ARB/USDT",
        "BCH/USDT",
        "UNI/USDT",
        "ONDO/USDT",
        "SHIB/USDT",
        "TRX/USDT",
        "PEPE/USDT",
        "RENDER/USDT",
        "FET/USDT",
        "BONK/USDT",
        # Tier 3 — mid caps ($5M+)
        "OP/USDT",
        "INJ/USDT",
        "ATOM/USDT",
        "DOT/USDT",
        "FIL/USDT",
        "ICP/USDT",
        "CRV/USDT",
        "PENDLE/USDT",
        "SEI/USDT",
        "ETC/USDT",
        "TIA/USDT",
        "RUNE/USDT",
        "XLM/USDT",
        "HBAR/USDT",
        "JUP/USDT",
        "GALA/USDT",
        "LDO/USDT",
        "ORDI/USDT",
        "DYDX/USDT",
        "STX/USDT",
        "KAS/USDT",
        "MNT/USDT",
        "STRK/USDT",
        "BLUR/USDT",
        "IMX/USDT",
        "GRT/USDT",
        "SNX/USDT",
        "TRB/USDT",
        "NOT/USDT",
        "MANA/USDT",
        "SAND/USDT",
        "VET/USDT",
        "WIF/USDT",
        "JTO/USDT",
        "PYTH/USDT",
        "W/USDT",
        "FLOKI/USDT",
        "MEW/USDT",
        "RONIN/USDT",
        "CAKE/USDT",
        "ALT/USDT",
        "PIXEL/USDT",
        # Tier 4 — new/trending high-volume
        "FARTCOIN/USDT",
        "KAITO/USDT",
        "DEXE/USDT",
        "VANRY/USDT",
        "AKE/USDT",
        "US/USDT",
        "MAGMA/USDT",
        "B3/USDT",
        "PUMPFUN/USDT",
        "PTB/USDT",
        "ARC/USDT",
        "1000XEC/USDT",
        "ALPINE/USDT",
        "CRWD/USDT",
        "UB/USDT",
        "BOT/USDT",
        "FIGHT/USDT",
        "RAVE/USDT",
        "MET/USDT",
        "ZEC/USDT",
        "VELO/USDT",
        "SKHY/USDT",
        "BMNR/USDT",
        "POPCAT/USDT",
        "MOG/USDT",
        "MYRO/USDT",
        "SLERF/USDT",
        "TURBO/USDT",
        "NEIRO/USDT",
        "GOAT/USDT",
        "ACT/USDT",
        "PNUT/USDT",
        "CHILLGUY/USDT",
    ]

    # ── Dynamic watchlist (override symbols at runtime) ─────────
    watchlist: str = ""  # comma-separated, empty = use static symbols list

    # ── Circuit breaker ────────────────────────────────────────
    daily_drawdown_limit: str = "-0.02"  # -2%, stored as str for Decimal conversion
    min_liquidity_usd: str = "10000"  # $10K minimum L1 notional depth

    # ── Watchdog ───────────────────────────────────────────────
    dead_mans_switch_url: str = ""
    dead_mans_switch_interval: int = 60  # seconds

    # ── 9router AI proxy ──────────────────────────────────────
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

    # ── Container role (set by docker-compose) ─────────────────
    karsa_role: str = "data-engine"

    @property
    def asyncpg_dsn(self) -> str:
        """Convert asyncpg-compatible DSN (strip driver prefix if present)."""
        dsn = self.postgres_url
        # pydantic-settings may give us postgresql+asyncpg:// — asyncpg wants plain postgresql://
        if "+asyncpg" in dsn:
            dsn = dsn.replace("+asyncpg", "")
        return dsn


@lru_cache
def get_settings() -> Settings:
    """Cached singleton for app settings."""
    logger.debug("get_settings: entering")
    result = Settings()
    logger.debug("get_settings: returning Settings")
    return result
