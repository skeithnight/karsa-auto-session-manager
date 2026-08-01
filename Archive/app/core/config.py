"""Pydantic Settings — all secrets live ONLY here."""

from __future__ import annotations

from functools import lru_cache

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("karsa.config")  # type: ignore[assignment]
try:
    from pydantic import AliasChoices, Field
except ImportError:
    def AliasChoices(*args, **kwargs): return args[0] if args else None  # type: ignore
    def Field(*args, **kwargs): return None  # type: ignore

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    class BaseSettings:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items(): setattr(self, k, v)
            if not hasattr(self, "shadow_mode_enabled"): self.shadow_mode_enabled = False
    def SettingsConfigDict(*args, **kwargs): return {}  # type: ignore


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

    # ── Portfolio Risk Limits ──────────────────────────────────
    max_gross_exposure_pct: str = "0.50"
    max_net_exposure_pct: str = "0.30"
    max_single_position_pct: str = "0.40"

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
    stablecoins: set[str] = {"USDT/USDT", "USDC/USDT", "FDUSD/USDT"}
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

    # ── Sprint 1: Drawdown Velocity Breaker ────────────────────
    velocity_1h_loss_pct: str = "-0.015"  # -1.5% of equity in 1h triggers 2h pause
    velocity_pause_seconds: int = 7200  # 2 hours cooldown

    # ── Sprint 1: Drawdown-Adaptive Sizing (Anti-Martingale) ────
    dd_severe_threshold: str = "0.10"   # >10% drawdown → 0.25x Kelly
    dd_moderate_threshold: str = "0.05" # >5% drawdown → 0.50x Kelly
    dd_near_peak_threshold: str = "0.02" # <2% drawdown → 1.25x Kelly (house money)
    dd_severe_mult: str = "0.25"
    dd_moderate_mult: str = "0.50"
    dd_near_peak_mult: str = "1.25"

    # ── Sprint 1: Funding Rate Carry Strategy ──────────────────
    carry_funding_threshold: str = "-0.0003"  # -0.03% per 8h period
    carry_consecutive_periods: int = 3  # 3 consecutive negative periods
    carry_max_price_change_pct: str = "0.02"  # Price change < 2% during carry
    carry_score_bonus: int = 25  # Score bonus for carry signal

    # ── Sprint 1: Trailing Limit Exit ──────────────────────────
    trailing_limit_offset_pct: str = "0.0005"  # 0.05% from best bid/ask
    trailing_limit_timeout_s: int = 60  # 60s before market fallback

    # ── Sprint 2: Liquidation Heatmap ──────────────────────────
    liq_heatmap_confluence_threshold: int = 3  # min liquidation clusters within range
    liq_heatmap_range_pct: str = "0.02"  # 2% range to search for clusters
    liq_heatmap_score_bonus: int = 20  # score bonus when heatmap confluence found

    # ── Sprint 2: Cross-Asset Momentum ─────────────────────────
    cross_asset_btc_weight: str = "0.60"  # BTC dominance weight
    cross_asset_eth_weight: str = "0.30"  # ETH weight
    cross_asset_alt_weight: str = "0.10"  # Altcoin weight
    cross_asset_lookback_bars: int = 6  # 6 bars = 1h on 10m candles
    cross_asset_score_bonus: int = 15  # bonus for aligned cross-asset momentum

    # ── Sprint 2: Token Unlock Calendar ────────────────────────
    unlock_impact_threshold_pct: str = "0.01"  # >1% of supply unlocks
    unlock_window_hours: int = 48  # filter within 48h of unlock
    unlock_penalty_score: int = 30  # penalty subtracted from score

    # ── Sprint 2: Half-Kelly with Uncertainty ──────────────────
    half_kelly_uncertainty_factor: str = "0.50"  # reduce Kelly by 50% when uncertainty high
    half_kelly_min_trades_for_confidence: int = 30  # need 30+ trades for full confidence
    half_kelly_variance_threshold: str = "0.15"  # if win rate variance > 15%, uncertainty applies

    # ── Sprint 3: HMM Regime Prediction ────────────────────────
    hmm_n_states: int = 3  # 0=LOW_VOL, 1=TRANSITION, 2=HIGH_VOL
    hmm_rolling_window_days: int = 30  # 30-day rolling window for training
    hmm_score_breakout_bonus: int = 15  # +15 for HMM_BREAKOUT_IMMINENT
    hmm_score_chop_penalty: int = 20  # -20 for HMM_CHOP_IMMINENT
    hmm_redis_key: str = "system:hmm:state"  # Redis key for current HMM state

    # ── Sprint 3: Walk-Forward Optimization ────────────────────
    wfo_train_days: int = 45  # in-sample training window
    wfo_test_days: int = 15  # out-of-sample validation window
    wfo_redis_key: str = "karsa:config:optimized_params"  # Redis key for optimized params
    wfo_ttl_seconds: int = 604800  # 7-day TTL for optimized params

    # ── Sprint 3: GARCH Volatility Forecasting ─────────────────
    garch_rolling_window_days: int = 30  # 30-day rolling window for GARCH fit
    garch_high_vol_threshold: str = "1.5"  # forecast > 1.5x hist avg → reduce Kelly
    garch_low_vol_threshold: str = "0.7"  # forecast < 0.7x hist avg → increase Kelly
    garch_high_vol_multiplier: str = "0.5"  # multiplier when high vol forecasted
    garch_low_vol_multiplier: str = "1.2"  # multiplier when low vol forecasted

    # ── Profitability Triage: Session Hard-Block ───────────────
    session_block_enabled: bool = True  # enable Asian dead zone block
    session_block_start_hour: int = 4   # UTC hour to start blocking (04:00 UTC)
    session_block_end_hour: int = 12    # UTC hour to stop blocking (12:00 UTC)
    session_block_allow_btc_eth: bool = True  # allow BTC/ETH entries during block

    # ── Profitability Triage: Volatility Floor ─────────────────
    vol_floor_percentile: int = 25     # 25th percentile of 90-day BTC 1H ATR
    vol_floor_lookback_days: int = 90  # rolling window for ATR calculation
    vol_floor_update_interval_s: int = 900  # recalculate every 15 minutes

    # ── Profitability Triage: AI Macro Narrator ────────────────
    macro_narrator_interval_s: int = 14400  # 4 hours between macro assessments
    macro_narrator_redis_key: str = "system:macro:narrator"
    macro_narrator_ttl_s: int = 18000  # 5 hour TTL (slightly > interval)

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
