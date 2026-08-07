"""Universe Tier Manager — tiered symbol classification for focused trading.

Tier 1 (5-10 symbols): Highest volume + proven lead-lag + positive backtest
Tier 2 (10-15 symbols): Good volume + emerging edge
Tier 3 (cut): Low volume, no edge proof

Redis keys:
  karsa:universe:tier:{symbol} — tier classification
  karsa:universe:performance:{symbol} — per-symbol performance metrics
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger

REDIS_TIER_KEY = "karsa:universe:tier:{symbol}"
REDIS_PERFORMANCE_KEY = "karsa:universe:performance:{symbol}"
REDIS_TTL = 604800  # 7 days


@dataclass
class SymbolPerformance:
    """Per-symbol performance metrics for tier classification."""

    symbol: str
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    total_pnl: float = 0.0
    trade_count: int = 0
    avg_volume_24h: float = 0.0
    lead_lag_correlation: float = 0.0
    spread_quality: float = 0.0
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "win_rate": round(self.win_rate, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "total_pnl": round(self.total_pnl, 4),
            "trade_count": self.trade_count,
            "avg_volume_24h": round(self.avg_volume_24h, 2),
            "lead_lag_correlation": round(self.lead_lag_correlation, 4),
            "spread_quality": round(self.spread_quality, 4),
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SymbolPerformance:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TierClassification:
    """Tier classification for a symbol."""

    symbol: str
    tier: int  # 1, 2, or 3
    score: float  # composite score for ranking
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "tier": self.tier,
            "score": round(self.score, 4),
            "reasons": self.reasons,
        }


class UniverseTierManager:
    """Manages tiered symbol classification for focused trading.

    Usage:
        manager = UniverseTierManager(redis_client)
        tier = await manager.classify_symbol("BTC/USDT", performance)
        tier1_symbols = await manager.get_tier_symbols(tier=1)
    """

    # Tier thresholds
    TIER1_MIN_SHARPE = 0.5
    TIER1_MIN_WIN_RATE = 0.55
    TIER1_MIN_VOLUME = 100_000_000  # $100M daily
    TIER1_MAX_DRAWDOWN = 0.15  # 15%

    TIER2_MIN_SHARPE = 0.2
    TIER2_MIN_WIN_RATE = 0.50
    TIER2_MIN_VOLUME = 10_000_000  # $10M daily
    TIER2_MAX_DRAWDOWN = 0.25  # 25%

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client

    async def classify_symbol(
        self,
        symbol: str,
        performance: SymbolPerformance,
    ) -> TierClassification:
        """Classify a symbol into a tier based on performance metrics.

        Args:
            symbol: Trading pair
            performance: Historical performance metrics

        Returns:
            TierClassification with tier, score, and reasons
        """
        reasons = []
        score = 0.0

        # Volume score (0-30 points)
        vol_score = min(30, (performance.avg_volume_24h / 1_000_000) * 0.3)
        score += vol_score

        # Sharpe score (0-25 points)
        sharpe_score = min(25, max(0, performance.sharpe_ratio * 25))
        score += sharpe_score

        # Win rate score (0-20 points)
        wr_score = min(20, max(0, (performance.win_rate - 0.4) * 100))
        score += wr_score

        # Drawdown penalty (0-15 points, lower is better)
        dd_score = max(0, 15 - (performance.max_drawdown_pct * 100))
        score += dd_score

        # Lead-lag correlation bonus (0-10 points)
        ll_score = min(10, performance.lead_lag_correlation * 10)
        score += ll_score

        # Determine tier
        tier = 3  # Default: cut

        if (
            performance.sharpe_ratio >= self.TIER1_MIN_SHARPE
            and performance.win_rate >= self.TIER1_MIN_WIN_RATE
            and performance.avg_volume_24h >= self.TIER1_MIN_VOLUME
            and performance.max_drawdown_pct <= self.TIER1_MAX_DRAWDOWN
        ):
            tier = 1
            reasons.append(f"Tier 1: Sharpe={performance.sharpe_ratio:.2f}, WR={performance.win_rate:.1%}, Vol=${performance.avg_volume_24h/1e6:.0f}M")
        elif (
            performance.sharpe_ratio >= self.TIER2_MIN_SHARPE
            and performance.win_rate >= self.TIER2_MIN_WIN_RATE
            and performance.avg_volume_24h >= self.TIER2_MIN_VOLUME
            and performance.max_drawdown_pct <= self.TIER2_MAX_DRAWDOWN
        ):
            tier = 2
            reasons.append(f"Tier 2: Sharpe={performance.sharpe_ratio:.2f}, WR={performance.win_rate:.1%}, Vol=${performance.avg_volume_24h/1e6:.0f}M")
        else:
            reasons.append(f"Tier 3: Below thresholds (Sharpe={performance.sharpe_ratio:.2f}, WR={performance.win_rate:.1%})")

        classification = TierClassification(
            symbol=symbol,
            tier=tier,
            score=score,
            reasons=reasons,
        )

        # Save to Redis
        await self._save_tier(symbol, classification)

        return classification

    async def get_tier_symbols(self, tier: int) -> list[str]:
        """Get all symbols in a specific tier."""
        if self._redis is None:
            return []

        try:
            # Scan for all tier keys
            symbols = []
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(  # type: ignore[attr-defined]
                    cursor=cursor,
                    match="karsa:universe:tier:*",
                    count=100,
                )
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else str(key)
                    symbol = key_str.split(":")[-1]
                    raw = await self._redis.get(key)  # type: ignore[attr-defined]
                    if raw:
                        data = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
                        if data.get("tier") == tier:
                            symbols.append(symbol)
                if cursor == 0:
                    break
            return symbols
        except Exception as e:
            logger.debug("Failed to get tier symbols: %s", e)
            return []

    async def get_performance(self, symbol: str) -> SymbolPerformance | None:
        """Get performance metrics for a symbol."""
        if self._redis is None:
            return None

        try:
            raw = await self._redis.get(f"karsa:universe:performance:{symbol}")  # type: ignore[attr-defined]
            if raw:
                data = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
                return SymbolPerformance.from_dict(data)
        except Exception as e:
            logger.debug("Failed to get performance for %s: %s", symbol, e)

        return None

    async def update_performance(
        self,
        symbol: str,
        win_rate: float,
        sharpe_ratio: float,
        max_drawdown_pct: float,
        total_pnl: float,
        trade_count: int,
        avg_volume_24h: float,
        lead_lag_correlation: float = 0.0,
        spread_quality: float = 0.0,
    ) -> None:
        """Update performance metrics for a symbol."""
        performance = SymbolPerformance(
            symbol=symbol,
            win_rate=win_rate,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            total_pnl=total_pnl,
            trade_count=trade_count,
            avg_volume_24h=avg_volume_24h,
            lead_lag_correlation=lead_lag_correlation,
            spread_quality=spread_quality,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )

        if self._redis is None:
            return

        try:
            await self._redis.set(  # type: ignore[attr-defined]
                f"karsa:universe:performance:{symbol}",
                json.dumps(performance.to_dict()),
                ex=REDIS_TTL,
            )
        except Exception as e:
            logger.debug("Failed to update performance for %s: %s", symbol, e)

    async def _save_tier(self, symbol: str, classification: TierClassification) -> None:
        """Save tier classification to Redis."""
        if self._redis is None:
            return

        try:
            await self._redis.set(  # type: ignore[attr-defined]
                f"karsa:universe:tier:{symbol}",
                json.dumps(classification.to_dict()),
                ex=REDIS_TTL,
            )
        except Exception as e:
            logger.debug("Failed to save tier for %s: %s", symbol, e)

    async def auto_demote(self, days_underperforming: int = 30) -> list[str]:
        """Auto-demote symbols that have underperformed for N days.

        Returns list of demoted symbols.
        """
        demoted = []
        tier1_symbols = await self.get_tier_symbols(tier=1)
        tier2_symbols = await self.get_tier_symbols(tier=2)

        for symbol in tier1_symbols + tier2_symbols:
            performance = await self.get_performance(symbol)
            if performance is None:
                continue

            # Check if underperforming
            if (
                performance.sharpe_ratio < 0.0
                and performance.trade_count >= 10
            ):
                # Demote to lower tier
                current_tier = 1 if symbol in tier1_symbols else 2
                new_tier = current_tier + 1

                classification = TierClassification(
                    symbol=symbol,
                    tier=new_tier,
                    score=0.0,
                    reasons=[f"Auto-demoted from Tier {current_tier}: Sharpe={performance.sharpe_ratio:.2f}"],
                )
                await self._save_tier(symbol, classification)
                demoted.append(symbol)
                logger.info("Auto-demoted %s from Tier %d to Tier %d", symbol, current_tier, new_tier)

        return demoted

    async def auto_promote(self) -> list[str]:
        """Auto-promote symbols that have outperformed.

        Returns list of promoted symbols.
        """
        promoted = []
        tier2_symbols = await self.get_tier_symbols(tier=2)
        tier3_symbols = await self.get_tier_symbols(tier=3)

        for symbol in tier2_symbols + tier3_symbols:
            performance = await self.get_performance(symbol)
            if performance is None:
                continue

            # Check if outperforming
            if (
                performance.sharpe_ratio >= self.TIER1_MIN_SHARPE
                and performance.win_rate >= self.TIER1_MIN_WIN_RATE
                and performance.avg_volume_24h >= self.TIER1_MIN_VOLUME
            ):
                # Promote to Tier 1
                classification = TierClassification(
                    symbol=symbol,
                    tier=1,
                    score=100.0,
                    reasons=[f"Auto-promoted: Sharpe={performance.sharpe_ratio:.2f}, WR={performance.win_rate:.1%}"],
                )
                await self._save_tier(symbol, classification)
                promoted.append(symbol)
                logger.info("Auto-promoted %s to Tier 1", symbol)

        return promoted
