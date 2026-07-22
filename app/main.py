"""Main entrypoint — asyncio loop starting all 6 Keys."""

from __future__ import annotations

# Bypass ISP (Telkomsel) DNS poisoning at Python level.
# ponytail: queries 1.1.1.1 directly via UDP, bypasses system resolv.conf entirely.
# Upgrade to DoH/DoT when infra supports it.
import socket as _socket
import struct as _struct


def _dns_query(server, hostname):
    """Query a DNS server directly via UDP. Returns list of IPs or empty list."""
    txid = b"\xaa\xbb"
    flags = b"\x01\x00"
    counts = _struct.pack(">HHHH", 1, 0, 0, 0)
    question = b""
    for part in hostname.encode().split(b"."):
        question += bytes([len(part)]) + part
    question += b"\x00" + _struct.pack(">HH", 1, 1)
    packet = txid + flags + counts + question
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    sock.settimeout(2)
    try:
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(512)
    finally:
        sock.close()
    offset = 12
    while data[offset] != 0:
        offset += data[offset] + 1
    offset += 5
    answers = _struct.unpack(">H", data[6:8])[0]
    ips = []
    for _ in range(answers):
        if data[offset] & 0xC0:
            offset += 2
        else:
            while data[offset] != 0:
                offset += data[offset] + 1
            offset += 1
        rtype, rclass, ttl, rdlength = _struct.unpack(
            ">HHIH", data[offset : offset + 10]
        )
        offset += 10
        if rtype == 1 and rdlength == 4:
            ip = ".".join(str(b) for b in data[offset : offset + 4])
            ips.append(ip)
        offset += rdlength
    return ips


_orig_getaddrinfo = _socket.getaddrinfo


def _bypass_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Override socket.getaddrinfo — try gluetun DNS (external) then Docker DNS (internal)."""
    # 1. Try gluetun DNS (127.0.0.1) — forwards to Cloudflare 1.1.1.1 via VPN tunnel (not poisoned)
    try:
        ips = _dns_query("127.0.0.1", host)
        if ips:
            af = _socket.AF_INET6 if ":" in ips[0] else _socket.AF_INET
            return [
                (
                    af,
                    _socket.SOCK_STREAM,
                    0,
                    "",
                    (ips[0], port if isinstance(port, int) else 0),
                )
            ]
    except Exception:
        pass
    # 2. Try Docker internal DNS (127.0.0.11) — resolves db, redis, 9router
    try:
        ips = _dns_query("127.0.0.11", host)
        if ips:
            af = _socket.AF_INET6 if ":" in ips[0] else _socket.AF_INET
            return [
                (
                    af,
                    _socket.SOCK_STREAM,
                    0,
                    "",
                    (ips[0], port if isinstance(port, int) else 0),
                )
            ]
    except Exception:
        pass
    # 3. Fallback to system resolver
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


_socket.getaddrinfo = _bypass_getaddrinfo

import asyncio
import random
import signal
import sys
import time
from decimal import Decimal
from typing import Any

from loguru import logger

from app.alpha.analyst import CryptoAnalyst
from app.alpha.entry_filter import EntryFilter
from app.alpha.lead_lag_buffer import LeadLagBuffer
from app.alpha.metrics import AlphaMetrics
from app.alpha.multi_tf import MultiTFFilter
from app.alpha.position_judge import PositionJudge
from app.alpha.regime import RegimeEngine

# Phase 6: Adaptive Multi-Strategy
from app.alpha.regime_classifier import RegimeClassifier
from app.alpha.signals import SignalGenerator
from app.alpha.strategy_router import StrategyRouter
from app.alpha.ta_tools import calculate_atr
from app.alpha.trade_memory import TradeMemory
from app.bot.alert_service import AlertService
from app.bot.runner import run_bot
from app.core import metrics
from app.core.ai_client import AIClient
from app.core.config import get_settings
from app.core.database import DatabaseEngine
from app.core.position_store import PositionStore
from app.core.redis_client import RedisClient
from app.core.session import AutonomousSessionManager
from app.core.shadow_store import ShadowPositionStore, ShadowTradeStore
from app.core.state import StateManager
from app.core.trade_reconciler import TradeReconciler
from app.core.trade_store import TradeStore
from app.data.ccxt_manager import CCXTManager
from app.data.filters import BadTickFilter
from app.data.normalizer import Normalizer
from app.data.ohlcv_fetcher import OHLCVFetcher
from app.data.universe_scorer import UniverseScorer
from app.execution.bybit_client import BybitClient
from app.execution.position_lifecycle import CheckpointManager, TrailingStopManager
from app.execution.position_manager import ActivePositionManager
from app.execution.shadow import ShadowAPM, ShadowExecutor
from app.execution.sor import SmartOrderRouter
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.dynamic_risk_gate import DynamicRiskGate
from app.risk.gates import RiskGate
from app.risk.portfolio_risk_manager import PortfolioRiskManager
from app.risk.sector_cap import SectorCap
from app.watchdog.dead_mans_switch import DeadMansSwitch
from app.watchdog.monitor import Watchdog

# Kill Switch event — global, set on SIGINT/SIGTERM
kill_switch = asyncio.Event()
alpha_paused = asyncio.Event()


async def _stream_orderbook(
    symbol: str,
    exchange_id: str,
    ccxt_manager: CCXTManager,
    normalizer: Normalizer,
    bad_tick_filter: BadTickFilter,
    redis_client: RedisClient,
    lead_lag_buffer: LeadLagBuffer,
) -> None:
    """Stream orderbook for a single (symbol, exchange) pair. Runs until kill_switch."""
    logger.info(f"Starting stream {exchange_id}/{symbol}")
    retries = 0
    while not kill_switch.is_set():
        try:
            orderbook = await ccxt_manager.watch_orderbook(symbol, exchange_id)
            retries = 0  # reset backoff on success
            metrics.orderbook_received.labels(exchange=exchange_id, symbol=symbol).inc()
            await redis_client.set_exchange_heartbeat(exchange_id)
            exchange_data = normalizer.normalize_orderbook(
                orderbook, exchange_id, symbol
            )
            exchange_data = bad_tick_filter.filter_orderbook(exchange_data)
            if exchange_data.is_stale:
                metrics.bad_tick_rejected.labels(
                    exchange=exchange_id, symbol=symbol
                ).inc()
            metrics.orderbook_normalized.labels(
                exchange=exchange_id, symbol=symbol
            ).inc()

            # Feed lead-lag buffer with mid price
            best_bid = (
                max(exchange_data.bids, key=lambda x: x[0])[0]
                if exchange_data.bids
                else None
            )
            best_ask = (
                min(exchange_data.asks, key=lambda x: x[0])[0]
                if exchange_data.asks
                else None
            )
            if best_bid and best_ask:
                mid = (float(best_bid) + float(best_ask)) / 2
                lead_lag_buffer.update(symbol, exchange_id, mid)

            if not exchange_data.is_stale:
                global_state = normalizer.build_global_state(symbol, [exchange_data])
                if global_state:
                    await redis_client.set_global_state(
                        symbol,
                        {
                            "global_vwap": str(global_state.global_vwap),
                            "aggregate_skew": global_state.aggregate_skew,
                            "best_bid": (
                                str(global_state.best_bid)
                                if global_state.best_bid
                                else None
                            ),
                            "best_ask": (
                                str(global_state.best_ask)
                                if global_state.best_ask
                                else None
                            ),
                            "total_volume": (
                                str(global_state.total_volume)
                                if global_state.total_volume
                                else None
                            ),
                            "updated_at": global_state.updated_at.isoformat(),
                        },
                    )
                    metrics.global_state_written.labels(symbol=symbol).inc()
                    if global_state.global_vwap is not None:
                        metrics.vwap_value.labels(symbol=symbol).set(
                            float(global_state.global_vwap)
                        )
                    if global_state.aggregate_skew is not None:
                        metrics.skew_value.labels(symbol=symbol).set(
                            float(global_state.aggregate_skew)
                        )
        except asyncio.CancelledError:
            logger.info(f"Stream {exchange_id}/{symbol} cancelled")
            raise
        except Exception as e:
            retries += 1
            metrics.orderbook_errors.labels(
                exchange=exchange_id, symbol=symbol, error_type=type(e).__name__
            ).inc()
            logger.warning(
                f"Stream {exchange_id}/{symbol} error (attempt {retries}): {e}"
            )
            if retries >= 10:
                logger.error(
                    f"Stream {exchange_id}/{symbol} gave up after {retries} retries"
                )
                break
            # Exponential backoff with jitter to prevent thundering herd
            delay = min(2 ** min(retries, 6), 30) + random.random()
            logger.info(
                f"Stream {exchange_id}/{symbol} retry {retries} in {delay:.1f}s"
            )
            await asyncio.sleep(delay)
    logger.debug(f"Stream {exchange_id}/{symbol} exiting")


async def data_engine_task(
    ccxt_manager: CCXTManager,
    normalizer: Normalizer,
    bad_tick_filter: BadTickFilter,
    redis_client: RedisClient,
    lead_lag_buffer: LeadLagBuffer,
    symbols: list[str],
) -> None:
    """Key 1: Ingest global market data from exchanges concurrently."""
    logger.debug("data_engine_task: entering")
    # Bybit: all valid symbols (authoritative)
    bybit_symbols = symbols
    # Binance/OKX: only symbols they also list (for lead-lag and skew enrichment)
    binance_symbols = ccxt_manager.get_reference_symbols(symbols, "binance")
    okx_symbols = ccxt_manager.get_reference_symbols(symbols, "okx")

    streams = (
        [(_s, "bybit") for _s in bybit_symbols]
        + [(_s, "binance") for _s in binance_symbols]
        + [(_s, "okx") for _s in okx_symbols]
    )
    logger.info(
        f"Data Engine starting — bybit={len(bybit_symbols)}, "
        f"binance={len(binance_symbols)}, okx={len(okx_symbols)} streams"
    )

    async def _staggered_stream(idx: int, sym: str, eid: str) -> None:
        # ponytail: stagger startup to prevent Redis connection storm
        await asyncio.sleep(idx * 0.05)
        await _stream_orderbook(
            sym,
            eid,
            ccxt_manager,
            normalizer,
            bad_tick_filter,
            redis_client,
            lead_lag_buffer,
        )

    await asyncio.gather(
        *[_staggered_stream(i, sym, eid) for i, (sym, eid) in enumerate(streams)],
        return_exceptions=True,
    )
    logger.debug("data_engine_task: returning None")


async def alpha_bridge_task(
    alpha_metrics: AlphaMetrics,
    signal_generator: SignalGenerator,
    lead_lag_buffer: LeadLagBuffer,
    entry_filter: EntryFilter,
    redis_client: RedisClient,
    symbols: list[str],
    signal_queue: asyncio.Queue,
    multi_tf: MultiTFFilter,
    crypto_analyst: CryptoAnalyst | None,
    ohlcv_fetcher: Any | None = None,
    position_store: PositionStore | None = None,
    trade_memory: Any | None = None,
    trade_store: TradeStore | None = None,
    strategy_router: Any | None = None,
) -> None:
    """Key 2: Generate trading signals from market state."""
    logger.debug("alpha_bridge_task: entering")
    logger.info("Alpha Bridge starting...")
    _signal_cooldown: dict[str, float] = {}
    SIGNAL_COOLDOWN_SECONDS = 45
    SIGNAL_TTL_S = (
        5  # Signal expires after 5 seconds — micro-structure alpha decays fast
    )
    _loop_count = 0

    while not kill_switch.is_set():
        _loop_count += 1
        if _loop_count % 100 == 0:
            try:
                raw_univ = await redis_client.redis.get("system:universe:symbols")
                if raw_univ:
                    import json as _json

                    universe_data = _json.loads(raw_univ)
                    refreshed = universe_data.get("symbols", [])
                    if refreshed:
                        symbols = refreshed
                        logger.info(
                            f"Alpha bridge universe refreshed: {len(symbols)} symbols"
                        )
            except Exception as _ue:
                logger.warning(f"Universe refresh read failed: {_ue}")

        if alpha_paused.is_set():
            logger.warning("Alpha Bridge paused — stale data")
            await asyncio.sleep(5)
            continue
        _signals_queued_this_cycle = 0
        MAX_SIGNALS_PER_CYCLE = 5
        for symbol in symbols:
            try:
                state = await redis_client.get_global_state(symbol)
                if state:
                    raw_vwap = state.get("global_vwap")
                    vwap = (
                        Decimal(str(raw_vwap))
                        if raw_vwap and str(raw_vwap) not in ("None", "null")
                        else None
                    )
                    raw_skew = state.get("aggregate_skew")
                    skew = (
                        float(raw_skew)
                        if raw_skew is not None
                        and str(raw_skew) not in ("None", "null")
                        else 0.0
                    )

                    # Read per-symbol regime from Redis (set by regime_engine_task)
                    raw_sym_regime = await redis_client.get_symbol_regime(symbol)
                    regime = "CHOP"
                    if raw_sym_regime:
                        import json

                        try:
                            parsed = json.loads(raw_sym_regime)
                            regime = (
                                parsed.get("regime", "CHOP")
                                if isinstance(parsed, dict)
                                else str(parsed)
                            )
                        except json.JSONDecodeError:
                            regime = str(raw_sym_regime)

                    # Entry quality filter (Phase 3)
                    best_bid = state.get("best_bid")
                    best_ask = state.get("best_ask")
                    spread_pct = None
                    if best_bid and best_ask:
                        bid_f = float(best_bid)
                        ask_f = float(best_ask)
                        mid = (bid_f + ask_f) / 2
                        if mid > 0:
                            spread_pct = (ask_f - bid_f) / mid

                    has_pos = False
                    if position_store:
                        has_pos = await position_store.has_position(symbol)

                    # Compute ATR for entry filter + position lifecycle
                    atr_val = None
                    if ohlcv_fetcher:
                        try:
                            candles_1h = await ohlcv_fetcher.fetch(symbol, "1h", 20)
                            if candles_1h and len(candles_1h) >= 15:
                                highs = [Decimal(str(c[2])) for c in candles_1h]
                                lows = [Decimal(str(c[3])) for c in candles_1h]
                                closes = [Decimal(str(c[4])) for c in candles_1h]
                                atr_val = calculate_atr(highs, lows, closes, period=14)
                        except Exception as e:
                            logger.warning(f"ATR computation failed for {symbol}: {e}")

                    entry_ok, entry_reason = entry_filter.check(
                        regime=regime,
                        spread_pct=spread_pct,
                        has_position=has_pos,
                        atr=float(atr_val) if atr_val else None,
                    )
                    if not entry_ok:
                        metrics.signals_skipped.labels(
                            symbol=symbol, reason=f"entry_filter:{entry_reason}"
                        ).inc()
                        logger.debug(f"Entry filtered {symbol}: {entry_reason}")
                        continue

                    # Phase 6: StrategyRouter quality gate
                    # Scores signal 0-100 per regime. Below 65 = reject.
                    # Replaces CHOP hard-block with regime-aware scoring.
                    # Fetch funding + OI early — StrategyRouter needs them for CHOP confluence.
                    _funding_rate = await alpha_metrics.get_funding_rate(symbol)
                    funding_float = (
                        float(_funding_rate) if _funding_rate is not None else None
                    )
                    oi_val = await alpha_metrics.get_open_interest(symbol)
                    oi_prev = alpha_metrics._oi_cache.get(symbol)
                    oi_change = None
                    if oi_val is not None and oi_prev is not None:
                        oi_change = float(oi_val - oi_prev[1])
                    strategy_score: float | None = None
                    if strategy_router is not None:
                        try:
                            from app.alpha.regime_classifier import MarketRegime

                            _regime_map = {
                                "CHOP": MarketRegime.CHOP,
                                "MEAN_REVERSION": MarketRegime.RANGE,
                                "TREND_BULL": MarketRegime.TREND_BULL,
                                "TREND_BEAR": MarketRegime.TREND_BEAR,
                            }
                            regime_enum = _regime_map.get(regime, MarketRegime.CHOP)

                            # Preliminary direction from skew for scoring
                            _direction = (
                                "LONG"
                                if skew > 0
                                else ("SHORT" if skew < 0 else "FLAT")
                            )
                            if _direction == "FLAT":
                                metrics.signals_skipped.labels(
                                    symbol=symbol, reason="strategy_neutral_skew"
                                ).inc()
                                continue

                            strategy_score, vol_factor = (
                                strategy_router.evaluate_signal(
                                    candles=candles_1h if candles_1h else [],
                                    regime=regime_enum,
                                    direction=_direction,
                                    funding_rate=funding_float,
                                    orderbook_delta=-skew,  # CHOP fades the skew (contrarian)
                                    global_prices=lead_lag_buffer.get_latest_prices(
                                        symbol
                                    ),
                                    oi_change=oi_change,
                                )
                            )
                            metrics.strategy_score.labels(
                                symbol=symbol, regime=regime
                            ).observe(strategy_score)
                            from app.alpha.strategy_router import (
                                STRATEGY_GATE_THRESHOLD,
                            )

                            # CHOP regime requires higher confluence (85) because the OI
                            # component fires on noise. Require near-perfect 3/4 components.
                            regime_gate = (
                                85.0 if regime == "CHOP"
                                else float(STRATEGY_GATE_THRESHOLD)
                            )
                            effective_gate = regime_gate * vol_factor
                            if strategy_score < effective_gate:
                                metrics.signals_skipped.labels(
                                    symbol=symbol,
                                    reason=f"strategy_gate:{strategy_score:.0f}",
                                ).inc()
                                logger.debug(
                                    f"StrategyRouter rejected {symbol}: "
                                    f"score={strategy_score:.0f} < {effective_gate:.0f} "
                                    f"(regime={regime} vol_factor={vol_factor:.2f})"
                                )
                                continue
                            logger.info(
                                f"StrategyRouter passed {symbol}: "
                                f"score={strategy_score:.0f} regime={regime}"
                            )
                        except Exception as e:
                            logger.warning(f"StrategyRouter error {symbol}: {e}")
                            # Fail-open: continue without strategy gate

                    # Multi-signal inputs (Phase 2)
                    lead_lag_delta = lead_lag_buffer.get_lead_lag_delta(symbol)
                    # funding_float + oi_change already fetched above for StrategyRouter

                    metrics.signals_pipeline_attempted.labels(symbol=symbol).inc()

                    # Anti-Whipsaw Cooldown: skip if this symbol recently took a loss
                    if trade_memory and await trade_memory.is_in_cooldown(symbol, cooldown_mins=45):
                        metrics.signals_skipped.labels(symbol=symbol, reason="whipsaw_cooldown").inc()
                        continue

                    # Data freshness gate — skip scoring if micro-structure data is stale
                    if alpha_metrics.is_stale(symbol):
                        logger.warning(
                            "Alpha stale: %s — skipping signal generation", symbol
                        )
                        metrics.signals_skipped.labels(
                            symbol=symbol, reason="stale_data"
                        ).inc()
                        continue

                    signal = signal_generator.generate(
                        symbol,
                        vwap,
                        skew,
                        regime=regime,
                        lead_lag_delta=lead_lag_delta,
                        funding_rate=funding_float,
                        oi_change=oi_change,
                        strategy_score=strategy_score,
                    )
                    # Attach pre-computed ATR to signal for position lifecycle
                    if signal and atr_val:
                        signal.atr = atr_val
                    if signal:
                        metrics.signals_entered_pipeline.labels(symbol=symbol).inc()
                        # Multi-TF confirmation: hard block if 4H trend contradicts
                        if signal.direction in ("LONG", "SHORT"):
                            mtf = await multi_tf.check(symbol, signal.direction)
                            if mtf.get("blocked", False):
                                logger.info(
                                    f"Multi-TF BLOCK {symbol}: {signal.direction} contradicts 4H trend — skipping"
                                )
                                metrics.signals_skipped.labels(
                                    symbol=symbol, reason="multi_tf_blocked"
                                ).inc()
                                continue
                            elif mtf["penalty_applied"] < 1:
                                signal.confidence *= float(mtf["penalty_applied"])
                                logger.info(
                                    f"Multi-TF penalty {symbol}: {mtf['penalty_applied']}x → conf={signal.confidence:.3f}"
                                )
                                if signal.confidence < signal_generator.min_confidence:
                                    logger.info(
                                        f"Multi-TF penalty killed {symbol}: conf={signal.confidence:.3f} < {signal_generator.min_confidence}"
                                    )
                                    metrics.signals_skipped.labels(
                                        symbol=symbol, reason="mtf_penalty_low"
                                    ).inc()
                                    continue

                        # AI analyst — ambiguous confidence zone (0.55-0.85)
                        if crypto_analyst and signal.confidence >= 0.55:
                            logger.info(f"AI Analyst validating {signal.symbol} signal")
                            trade_ctx = (
                                await trade_memory.get_prompt_context(
                                    symbol=signal.symbol, regime=regime or None
                                )
                                if trade_memory
                                else ""
                            )
                            # Compute mid price from orderbook
                            mid_price = None
                            if best_bid and best_ask:
                                mid_price = Decimal(
                                    str((float(best_bid) + float(best_ask)) / 2)
                                )
                            _analyst_start = time.time()
                            analyst_result = await crypto_analyst.analyze(
                                symbol=signal.symbol,
                                direction=signal.direction,
                                confidence=signal.confidence,
                                regime=regime or "CHOP",
                                spread_pct=spread_pct or 0.0,
                                funding_rate=funding_float or 0.0,
                                oi_change=oi_change or 0.0,
                                price=mid_price or Decimal("0"),
                                recent_trades=trade_ctx,
                            )
                            _analyst_ms = int((time.time() - _analyst_start) * 1000)
                            # Persist AI decision for Grafana audit trail (even on parse failure)
                            if trade_store:
                                try:
                                    import json as _json

                                    _output = {
                                        "ai_confidence": analyst_result.ai_confidence
                                        if analyst_result
                                        else 0,
                                        "direction": analyst_result.direction
                                        if analyst_result
                                        else "UNKNOWN",
                                        "reasoning": analyst_result.reasoning
                                        if analyst_result
                                        else "parse_failed",
                                        "deterministic_confidence": float(
                                            signal.confidence
                                        ),
                                    }
                                    await trade_store.record_ai_decision(
                                        symbol=signal.symbol,
                                        decision_type="analyst",
                                        model=(
                                            analyst_result.model_used
                                            if analyst_result
                                            else crypto_analyst.ai_client.model
                                        ),
                                        output_json=_json.dumps(_output),
                                        latency_ms=_analyst_ms,
                                    )
                                except Exception as _db_err:
                                    logger.warning(
                                        f"Failed to record ai_decision for {symbol}: {_db_err}"
                                    )
                            if analyst_result:
                                # Blend: 50% deterministic + 50% AI
                                final_conf = (
                                    signal.confidence * 0.5
                                    + (analyst_result.ai_confidence / 100.0) * 0.5
                                )
                                if final_conf < 0.65:
                                    logger.info(
                                        f"AI analyst rejected {symbol}: {analyst_result.reasoning}"
                                    )
                                    metrics.signals_skipped.labels(
                                        symbol=symbol, reason="ai_rejected"
                                    ).inc()
                                    metrics.ai_analyst_rejections.labels(
                                        reason="low_confidence"
                                    ).inc()
                                    continue
                                signal.confidence = final_conf
                                signal.metrics["ai_analyst"] = analyst_result.direction
                                signal.metrics["ai_confidence"] = (
                                    analyst_result.ai_confidence
                                )

                        metrics.signals_generated.labels(
                            symbol=symbol, direction=signal.direction
                        ).inc()
                        metrics.signal_confidence.labels(symbol=symbol).observe(
                            float(signal.confidence)
                        )
                        now_ts = time.time()
                        if (
                            now_ts - _signal_cooldown.get(signal.symbol, 0)
                            < SIGNAL_COOLDOWN_SECONDS
                        ):
                            logger.debug(
                                f"Signal cooldown active for {signal.symbol}, skipping"
                            )
                            continue
                        _signal_cooldown[signal.symbol] = now_ts
                        signal._generated_at = now_ts
                        signal.expires_at = now_ts + SIGNAL_TTL_S
                        await signal_queue.put(signal)
                        _signals_queued_this_cycle += 1
                        logger.debug(f"alpha_bridge_task: queued {signal.symbol}")
                        if _signals_queued_this_cycle >= MAX_SIGNALS_PER_CYCLE:
                            logger.info(
                                f"Alpha Bridge: {MAX_SIGNALS_PER_CYCLE} signals queued this cycle, skipping remaining symbols"
                            )
                            break
                    else:
                        metrics.signals_skipped.labels(
                            symbol=symbol, reason="low_confidence"
                        ).inc()
            except Exception as e:
                logger.error(f"Alpha Bridge error {symbol}: {e}")
                logger.debug(f"alpha_bridge_task: error={e}")

        await asyncio.sleep(1)
    logger.debug("alpha_bridge_task: returning None")


async def risk_gate_task(
    risk_gate: RiskGate,
    circuit_breaker: CircuitBreaker,
    redis_client: RedisClient,
    signal_queue: asyncio.Queue,
    risk_queue: asyncio.Queue,
    portfolio_risk_manager: PortfolioRiskManager | None = None,
) -> None:
    """Key 3: Gate signals through risk checks."""
    logger.debug("risk_gate_task: entering")
    logger.info("Risk Gate starting...")

    metrics.param_threshold.labels("alpha_entry").set(0.65)
    metrics.param_threshold.labels("max_slippage").set(0.5)
    metrics.param_threshold.labels("ai_veto").set(0.85)

    while not kill_switch.is_set():
        try:
            signal = await asyncio.wait_for(signal_queue.get(), timeout=1.0)
        except TimeoutError:
            continue

        # Signal TTL — drop expired signals (micro-structure alpha decays fast)
        if signal.expires_at is not None and time.time() > signal.expires_at:
            logger.warning(
                f"Signal expired for {signal.symbol} — dropping stale signal"
            )
            metrics.signals_skipped.labels(symbol=signal.symbol, reason="expired").inc()
            continue

        if circuit_breaker.is_halted() or circuit_breaker.is_paused():
            logger.warning("Signal rejected — circuit breaker active")
            continue

        # Pull live market data from Redis global state
        state = await redis_client.get_global_state(signal.symbol)
        if not state or not state.get("best_bid") or not state.get("best_ask"):
            logger.warning(f"Signal rejected — no live state for {signal.symbol}")
            continue

        # Price deviation check — reject if signal price drifted from mark
        PRICE_DEVIATION_MAX = Decimal("0.005")  # 0.5%
        try:
            mark_price = (Decimal(state["best_bid"]) + Decimal(state["best_ask"])) / 2
            if mark_price > 0 and signal.price > 0:
                deviation = abs(Decimal(str(signal.price)) - mark_price) / mark_price
                if deviation > PRICE_DEVIATION_MAX:
                    logger.warning(
                        f"Signal price {signal.price} deviates {deviation:.4f} from mark {mark_price} for {signal.symbol} — rejecting"
                    )
                    metrics.signals_skipped.labels(
                        symbol=signal.symbol, reason="price_deviation"
                    ).inc()
                    continue
        except Exception:
            logger.debug(f"Price deviation check failed for {signal.symbol}, allowing")

        # Phase 6.4: Portfolio Risk Manager — runs BEFORE RiskGate
        if portfolio_risk_manager is not None:
            prm_result = await portfolio_risk_manager.check(signal)
            if not prm_result.approved:
                logger.warning(f"PRM blocked {signal.symbol}: {prm_result.reason}")
                continue

        decision = risk_gate.evaluate(
            volume_24h=(
                Decimal(state["total_volume"])
                if state.get("total_volume")
                else Decimal("0")
            ),
            bid_price=Decimal(state["best_bid"]),
            ask_price=Decimal(state["best_ask"]),
        )

        if decision["passed"]:
            metrics.risk_gate_pass.labels(symbol=signal.symbol).inc()
            metrics.signals_completed_pipeline.labels(
                symbol=signal.symbol, outcome="passed"
            ).inc()
            await risk_queue.put(signal)
        else:
            metrics.risk_gate_reject.labels(
                symbol=signal.symbol, reason=decision["failed_gate"]
            ).inc()
            metrics.signals_completed_pipeline.labels(
                symbol=signal.symbol, outcome="rejected"
            ).inc()
            logger.warning(f"Signal rejected: {decision['failed_gate']}")
    logger.debug("risk_gate_task: returning None")


async def regime_engine_task(
    regime_engine: RegimeEngine,
    regime_classifier: RegimeClassifier,
    ohlcv_fetcher: OHLCVFetcher,
    redis_client: RedisClient,
) -> None:
    """Regime Engine: classify per-symbol regime + BTC global fallback every 15 min."""
    logger.debug("regime_engine_task: entering")
    logger.info("Regime Engine starting (per-symbol mode)...")

    # ponytail: shared semaphore to cap concurrent OHLCV fetches (rate limit safety)
    _fetch_sem = asyncio.Semaphore(5)

    async def _classify_one(symbol: str) -> None:
        """Fetch 1H candles + classify single symbol."""
        try:
            async with _fetch_sem:
                candles_raw = await ohlcv_fetcher.fetch(
                    symbol, "1h", 200, ttl_seconds=900
                )
            if not candles_raw or len(candles_raw) < 50:
                logger.debug(
                    f"RegimeClassifier: {symbol} insufficient candles ({len(candles_raw) if candles_raw else 0})"
                )
                return
            import numpy as _np

            candles = _np.array(candles_raw, dtype=float)
            regime = await asyncio.to_thread(regime_classifier.classify, candles)
            await redis_client.set_symbol_regime(symbol, regime.value)
            logger.info(f"Regime {symbol}: {regime.value}")
        except Exception as e:
            logger.warning(f"RegimeClassifier {symbol} error: {e}")

    while not kill_switch.is_set():
        try:
            # 1. BTC global regime (legacy engine — keeps Prometheus metrics)
            candles_1h = await ohlcv_fetcher.fetch(
                "BTC/USDT", "1h", 200, ttl_seconds=900
            )
            candles_4h = await ohlcv_fetcher.fetch(
                "BTC/USDT", "4h", 60, ttl_seconds=3600
            )
            if candles_1h and len(candles_1h) >= 200:
                regime, hurst, adx = await asyncio.to_thread(
                    regime_engine.classify_multi, candles_1h, candles_4h or []
                )
                await redis_client.set_session_config(regime)

                regime_map = {
                    "CHOP": 0,
                    "MEAN_REVERSION": 1,
                    "TREND_BEAR": 2,
                    "TREND_BULL": 3,
                }
                metrics.regime_state.set(regime_map.get(regime, 0))
                metrics.regime_hurst.set(hurst)
                metrics.regime_adx.set(adx)
                adx_4h = 0.0
                if candles_4h and len(candles_4h) >= 50:
                    _, _, adx_4h = await asyncio.to_thread(
                        regime_engine.classify, candles_4h, 50
                    )
                    metrics.regime_adx_4h.set(adx_4h)
                logger.info(
                    f"BTC regime: {regime} (hurst={hurst:.4f} adx={adx:.2f} adx_4h={adx_4h:.2f})"
                )
            else:
                logger.warning(
                    f"Insufficient BTC candles: {len(candles_1h) if candles_1h else 0}"
                )

            # 2. Per-symbol regime (Phase 6.1 — classify each universe symbol)
            raw_universe = await redis_client.redis.get("system:universe:symbols")  # type: ignore[union-attr]
            if raw_universe:
                import json as _json

                try:
                    universe_data = _json.loads(raw_universe)
                    symbols = (
                        universe_data.get("symbols", [])
                        if isinstance(universe_data, dict)
                        else []
                    )
                except (_json.JSONDecodeError, AttributeError):
                    symbols = []
                if symbols:
                    logger.info(f"Classifying regime for {len(symbols)} symbols...")
                    await asyncio.gather(
                        *[_classify_one(s) for s in symbols],
                        return_exceptions=True,
                    )

        except Exception as e:
            logger.error(f"Regime Engine error: {e}")

        await asyncio.sleep(900)  # 15 min
    logger.debug("regime_engine_task: returning None")


async def executor_task(
    sor: SmartOrderRouter,
    state_manager: StateManager,
    circuit_breaker: CircuitBreaker,
    risk_queue: asyncio.Queue,
    watchdog: Watchdog,
    redis_client: RedisClient,
    position_store: PositionStore,
    sector_cap: SectorCap,
    bybit_client: BybitClient,
    trade_store: TradeStore,
    session_manager: AutonomousSessionManager,
    risk_pct: Decimal = Decimal("0.03"),
    dynamic_risk_gate: DynamicRiskGate | None = None,
) -> None:
    """Key 4: Execute trades via Smart Order Router."""
    logger.debug("executor_task: entering")
    logger.info("Executor starting...")

    while not kill_switch.is_set():
        try:
            signal = await asyncio.wait_for(risk_queue.get(), timeout=1.0)
        except TimeoutError:
            continue

        # ASM gate — skip execution when no active session
        dynamic_risk = risk_pct
        try:
            if not await session_manager.is_active():
                logger.debug(f"ASM inactive — skipping signal {signal.symbol}")
                continue

            # Fetch dynamic risk from session config
            config = await session_manager.get_config()
            if config and "risk_pct" in config:
                dynamic_risk = Decimal(str(config["risk_pct"])) / Decimal("100")
        except Exception as e:
            logger.warning(f"ASM check failed: {e} — skipping signal for safety")
            continue

        # Phase 6: get RiskProfile for regime-aware sizing
        risk_profile = None
        if dynamic_risk_gate is not None:
            try:
                from app.alpha.regime_classifier import MarketRegime

                raw_regime = await redis_client.get("system:config:regime")
                if raw_regime:
                    import json as _json

                    try:
                        regime_data = _json.loads(raw_regime)
                        regime_val = (
                            regime_data.get("regime", "CHOP")
                            if isinstance(regime_data, dict)
                            else str(regime_data)
                        )
                    except (_json.JSONDecodeError, AttributeError):
                        regime_val = str(raw_regime)
                    try:
                        regime_enum = MarketRegime(regime_val)
                    except ValueError:
                        regime_enum = MarketRegime.CHOP
                    risk_profile = dynamic_risk_gate.get_profile(regime_enum)
            except Exception as e:
                logger.warning(
                    f"DynamicRiskGate failed for {signal.symbol}: {e}, using flat sizing"
                )

        # Record execution latency
        generated_at = getattr(signal, "_generated_at", None)
        if generated_at:
            watchdog.record_latency(time.time() - generated_at)

        # Skip FLAT signals
        if signal.direction == "FLAT":
            logger.debug(f"Skipping FLAT signal for {signal.symbol}")
            continue

        # Max open positions check (from Telegram settings)
        try:
            max_pos = int(await redis_client.get("karsa:settings:max_positions") or 5)
            max_hyper = int(await redis_client.get("karsa:settings:max_hyper_slots") or 2)
        except Exception:
            max_pos = 5
            max_hyper = 2

        open_positions = await position_store.list_all()
        total_open = len(open_positions)
        hyper_open = sum(1 for p in open_positions if str(p.get("regime", "")).startswith("HYPER"))

        is_hyper = signal.regime.value.startswith("HYPER")

        if is_hyper:
            if hyper_open >= max_hyper:
                logger.warning(f"HYPER slots full ({hyper_open}/{max_hyper}), skipping {signal.symbol}")
                metrics.signals_skipped.labels(symbol=signal.symbol, reason="max_hyper_slots").inc()
                continue
        elif total_open >= max_pos:
            logger.warning(
                f"Max positions ({max_pos}) reached, skipping {signal.symbol}"
            )
            metrics.signals_skipped.labels(
                symbol=signal.symbol, reason="max_positions"
            ).inc()
            continue

        # Check duplicate position
        side_str = "buy" if signal.direction == "LONG" else "sell"
        if await position_store.has_position(signal.symbol, side_str):
            logger.info(
                f"Already have {signal.direction} position in {signal.symbol}, skipping"
            )
            continue

        # Sector diversity cap
        if not await sector_cap.check(signal.symbol):
            logger.warning(f"Sector cap reached for {signal.symbol}, skipping")
            metrics.risk_gate_reject.labels(
                symbol=signal.symbol, reason="sector_cap"
            ).inc()
            continue

        # Get live price
        price = await _get_price(redis_client, signal.symbol)
        if price is None:
            logger.warning(f"No live price for {signal.symbol}, skipping")
            continue

        # Position sizing: dynamic_risk of available balance / entry price
        try:
            wallet = await bybit_client.get_wallet_balance()
            available = wallet.get("available", Decimal("0"))
            if available <= 0:
                logger.warning(f"No available balance, skipping {signal.symbol}")
                continue
            amount = (available * dynamic_risk) / price
            # Phase 6: apply regime-aware size multiplier
            if risk_profile is not None:
                amount = amount * risk_profile.size_multiplier
            # Validate qty not zero after regime multiplier
            if amount <= 0:
                logger.warning(
                    f"Computed qty {amount} <= 0 for {signal.symbol}, skipping"
                )
                metrics.signals_skipped.labels(
                    symbol=signal.symbol, reason="qty_zero"
                ).inc()
                continue
            # Enforce Bybit's $5 USDT minimum order value
            order_value = amount * price
            if order_value < Decimal("5"):
                logger.warning(
                    f"Order value {order_value:.2f} USDT below $5 min for {signal.symbol}, skipping"
                )
                metrics.signals_skipped.labels(
                    symbol=signal.symbol, reason="below_min_order"
                ).inc()
                continue
        except Exception as e:
            logger.error(f"Balance lookup failed: {e}, skipping {signal.symbol}")
            continue

        logger.info(
            f"Executing {signal.direction} {signal.symbol} @ {price}, amount={amount}"
        )

        try:
            atr_val = getattr(signal, "atr", None)

            # Phase 6: compute initial_risk_per_unit from ATR + sl_atr_buffer
            initial_risk_per_unit = None
            risk_profile_json = None
            if risk_profile is not None and atr_val is not None:
                initial_risk_per_unit = (
                    Decimal(str(atr_val)) * risk_profile.sl_atr_buffer
                )
                risk_profile_json = risk_profile.to_json()
            elif risk_profile is not None:
                # No ATR — fall back to 1% of entry price as risk estimate
                initial_risk_per_unit = price * Decimal("0.01")
                risk_profile_json = risk_profile.to_json()

            # Absolute fallback to ensure APM tracks the position
            if initial_risk_per_unit is None or initial_risk_per_unit <= Decimal("0"):
                initial_risk_per_unit = price * Decimal("0.01")

            # Calculate total dollar risk for Bybit stop loss placement
            max_loss_usd = Decimal("1.00")
            if initial_risk_per_unit is not None:
                max_loss_usd = initial_risk_per_unit * amount

            exec_start = time.time()
            result = await sor.execute(
                symbol=signal.symbol,
                side=side_str,
                amount=amount,
                price=price,
                price_tick=bybit_client._price_ticks.get(
                    signal.symbol, Decimal("0.01")
                ),
                max_loss_usd=max_loss_usd,
            )
            exec_latency_ms = int((time.time() - exec_start) * 1000)

            if result:
                logger.info(
                    f"SOR fill: {signal.symbol} {signal.direction} latency={exec_latency_ms}ms"
                )
                # Register position in store for trailing stop + checkpoint management
                # Record trade entry in Postgres first to get regime
                raw_regime_data = await redis_client.get_symbol_regime(signal.symbol)
                regime = "UNKNOWN"
                if raw_regime_data:
                    import json as _json

                    try:
                        parsed_regime_data = _json.loads(raw_regime_data)
                        regime = (
                            parsed_regime_data.get("regime", "UNKNOWN")
                            if isinstance(parsed_regime_data, dict)
                            else str(parsed_regime_data)
                        )
                    except (_json.JSONDecodeError, TypeError):
                        regime = str(raw_regime_data)

                # Save position to Redis

                await position_store.save(
                    symbol=signal.symbol,
                    side=side_str,
                    entry_price=price,
                    amount=amount,
                    sl_order_id=result.get("sl_order_id", ""),
                    atr=atr_val,
                    entry_confidence=signal.confidence,
                    regime=regime,
                    entry_regime=regime,
                    initial_risk_per_unit=str(initial_risk_per_unit),
                    risk_profile_json=risk_profile_json,
                )
                await trade_store.record_entry(
                    symbol=signal.symbol,
                    side=signal.direction,
                    amount=amount,
                    entry_price=price,
                    regime=regime,
                    entry_regime=regime,
                    initial_risk_per_unit=initial_risk_per_unit,
                    risk_profile_json=risk_profile_json,
                )
            else:
                logger.warning(f"SOR returned no fill for {signal.symbol}")
        except Exception as e:
            logger.error(f"Executor SOR error {signal.symbol}: {e}")
            logger.debug(f"executor_task: sor error={e}")

        # Periodic reconciliation
        try:
            await state_manager.reconcile()
        except Exception as e:
            logger.error(f"Executor reconciliation error: {e}")
            logger.debug(f"executor_task: error={e}")
    logger.debug("executor_task: returning None")


async def watchdog_task(watchdog: Watchdog, redis_client: RedisClient) -> None:
    """Key 6: Watchdog health monitor."""
    logger.debug("watchdog_task: entering")
    await watchdog.start()
    logger.debug("watchdog_task: returning None")


async def position_reconciler_task(
    bybit_client: BybitClient,
    position_store: PositionStore,
    interval_seconds: int = 300,
) -> None:
    """Periodic Bybit-Redis reconciliation. Removes stale Redis positions."""
    logger.debug("position_reconciler_task: entering")
    reverse_map = {v: k for k, v in bybit_client._symbol_map.items()}
    while not kill_switch.is_set():
        try:
            bybit_positions = await bybit_client.fetch_positions()
            bybit_set = set()
            for p in bybit_positions:
                ccxt_sym = reverse_map.get(p["symbol"], p["symbol"])
                bybit_set.add(f"{ccxt_sym}:{p['side']}")

            redis_positions = await position_store.list_all()
            for pos in redis_positions:
                key = f"{pos['symbol']}:{pos['side']}"
                if key not in bybit_set:
                    logger.warning(f"Reconciler: stale {key} — removing (not on Bybit)")
                    await position_store.remove(pos["symbol"], pos["side"])
                    metrics.reconciler_stale_removed.labels(symbol=pos["symbol"]).inc()
                else:
                    logger.debug(f"Reconciler: {key} OK")
        except Exception as e:
            logger.error(f"Position reconciler error: {e}")
        await asyncio.sleep(interval_seconds)
    logger.debug("position_reconciler_task: returning")


async def trade_history_reconciler_task(
    reconciler: TradeReconciler,
    interval_seconds: int = 900,
) -> None:
    """Periodic trade history reconciliation with Bybit executions."""
    logger.debug("trade_history_reconciler_task: entering")
    await asyncio.sleep(60)  # Let bot settle after startup
    while not kill_switch.is_set():
        try:
            report = await reconciler.reconcile()
            metrics.trade_reconcile_cycles.inc()
            metrics.trade_reconcile_fills_checked.inc(report.bybit_fills_checked)
            for d in report.discrepancies:
                metrics.trade_reconcile_discrepancies.labels(kind=d.kind).inc()
                if d.repaired:
                    metrics.trade_reconcile_repairs.labels(kind=d.kind).inc()
        except Exception as e:
            logger.error(f"Trade reconciler error: {e}")
            metrics.trade_reconcile_errors.labels(error_type=type(e).__name__).inc()
        await asyncio.sleep(interval_seconds)
    logger.debug("trade_history_reconciler_task: returning")


async def universe_refresh_task(
    scorer: UniverseScorer, config_symbols: list[str], interval_hours: int = 4
) -> None:
    """Periodic universe scorer refresh. Falls back to static config on failure."""
    logger.debug("universe_refresh_task: entering")
    while not kill_switch.is_set():
        try:
            symbols = await scorer.refresh(config_symbols)
            metrics.universe_symbols_scored.inc(len(symbols))
            logger.info(f"Universe refresh complete: {len(symbols)} symbols")
        except Exception as e:
            logger.error(f"Universe refresh failed: {e}")
        await asyncio.sleep(interval_hours * 3600)
    logger.debug("universe_refresh_task: returning None")


async def kill_switch_sequence(
    bybit_client: BybitClient, state_manager: StateManager, sor: SmartOrderRouter
) -> None:
    """Execute graceful shutdown: cancel orders + market-close all positions."""
    logger.critical("kill_switch_sequence: starting emergency flatten")
    try:
        await sor.flatten_all_positions()
        logger.critical("Kill switch sequence complete")
    except Exception as e:
        logger.critical(f"Kill switch error: {e}")
    logger.debug("kill_switch_sequence: returning None")


async def _get_price(redis_client: RedisClient, symbol: str) -> Decimal | None:
    """Helper: get mid price from Redis global state for lifecycle managers."""
    state = await redis_client.get_global_state(symbol)
    if not state:
        return None
    best_bid = state.get("best_bid")
    best_ask = state.get("best_ask")
    if best_bid and best_ask:
        bid = Decimal(str(best_bid))
        ask = Decimal(str(best_ask))
        return (bid + ask) / 2
    return None


def _on_task_done(
    task: asyncio.Task, kill_switch: asyncio.Event, critical: set
) -> None:
    """Kill switch on critical task crash."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.critical(f"Task CRASHED: {task.get_name()} — {exc}")
        if task.get_name() in critical:
            logger.critical("Critical task died — triggering kill switch")
            kill_switch.set()
    else:
        logger.warning(f"Task exited normally: {task.get_name()}")


async def metrics_publisher_task(
    bybit_client: BybitClient,
    redis_client: RedisClient,
    position_store: PositionStore,
    kill_switch: asyncio.Event,
    interval_seconds: int = 30,
) -> None:
    """Periodically publish wallet and max positions metrics to Prometheus."""
    logger.info("Started metrics publisher task")
    while not kill_switch.is_set():
        try:
            # 1. Update max positions
            try:
                max_pos = int(
                    await redis_client.get("karsa:settings:max_positions") or 5
                )
            except Exception:
                max_pos = 5

            metrics.max_positions.set(max_pos)

            # 2. Update wallet balance
            wallet = await bybit_client.get_wallet_balance()
            available = float(wallet.get("available", 0))
            metrics.wallet_balance.set(available)

            # 3. Update total equity
            open_positions = await position_store.list_all()
            total_unrealized_pnl = sum([float(p.get("pnl", 0)) for p in open_positions])
            equity = float(wallet.get("balance", 0)) + total_unrealized_pnl
            metrics.wallet_total_equity.set(equity)

        except Exception as e:
            logger.error(f"Metrics publisher error: {e}")

        try:
            await asyncio.wait_for(kill_switch.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def main() -> None:
    """Initialize all components and run the main event loop."""
    # Suppress info/debug noise — WARNING+ only
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    settings = get_settings()
    logger.warning("Karsa Auto Session Manager starting...")

    # Initialize components
    redis_client = RedisClient()
    await redis_client.connect()

    db_engine = DatabaseEngine()
    await db_engine.connect(settings.postgres_url)

    ccxt_manager = CCXTManager()
    await ccxt_manager.start(testnet=settings.bybit_testnet)
    metrics.vpn_status.set(
        1
    )  # VPN up if ccxt_manager.start() succeeded (Bybit goes through WARP)
    normalizer = Normalizer()
    bad_tick_filter = BadTickFilter()
    alpha_metrics = AlphaMetrics()
    signal_generator = SignalGenerator()
    regime_engine = RegimeEngine()

    # Prometheus metrics HTTP endpoint — start early so metrics available even if Bybit fails
    from prometheus_client import start_http_server

    if prom_port := __import__("os").getenv("PROMETHEUS_PORT"):
        start_http_server(int(prom_port))
    logger.info("Prometheus metrics server on :8001")

    bybit_client = BybitClient()
    await bybit_client.connect()

    # CRITICAL FIX: Sync precise tick and lot sizes from CCXT to BybitClient
    # to prevent catastrophic 0.01 fallbacks if Bybit API rate limits on startup
    if "bybit" in ccxt_manager.markets_loaded:
        bybit_exchange = ccxt_manager.exchanges["bybit"]
        for sym, market in bybit_exchange.markets.items():
            if market.get("linear"):
                base_sym = sym.split(":")[0]  # HEMI/USDT:USDT -> HEMI/USDT
                price_tick = market.get("precision", {}).get("price")
                amount_tick = market.get("precision", {}).get("amount")
                min_qty = market.get("limits", {}).get("amount", {}).get("min")
                if price_tick:
                    bybit_client._price_ticks[base_sym] = Decimal(str(price_tick))
                if amount_tick:
                    bybit_client._lot_sizes[base_sym] = Decimal(str(amount_tick))
                if min_qty:
                    bybit_client._min_qty[base_sym] = Decimal(str(min_qty))

    metrics.bybit_status.set(1)  # Bybit connected
    alert_service = AlertService(settings.telegram_chat_id)
    sor = SmartOrderRouter(bybit_client, alert_service=alert_service, redis_client=redis_client)
    circuit_breaker = CircuitBreaker(
        alert_service=alert_service, redis_client=redis_client
    )
    await circuit_breaker.restore()  # Persisted halt state survives restarts
    risk_gate = RiskGate(
        min_liquidity_usd=Decimal(settings.min_liquidity_usd),
        circuit_breaker=circuit_breaker,
    )
    state_manager = StateManager(redis_client, bybit_client)
    watchdog = Watchdog(
        redis_client, alpha_paused=alpha_paused, sor=sor, kill_switch=kill_switch
    )
    dead_mans_switch = DeadMansSwitch()
    session_manager = AutonomousSessionManager(redis_client, kill_switch)
    ohlcv_fetcher = OHLCVFetcher(ccxt_manager.exchanges["bybit"])
    lead_lag_buffer = LeadLagBuffer()
    # Phase 6 modules
    # Phase 6.5: APM_ENABLED flag — set APM_ENABLED=1 in env to activate
    APM_ENABLED = __import__("os").environ.get("APM_ENABLED", "0") == "1"
    # Create stores before PortfolioRiskManager needs them
    position_store = PositionStore(redis_client)
    trade_store = TradeStore(db_engine)
    trade_memory = TradeMemory(redis_client)
    sector_cap = SectorCap(position_store)
    regime_classifier = RegimeClassifier(redis_client=redis_client)
    strategy_router = StrategyRouter()
    dynamic_risk_gate = DynamicRiskGate()
    portfolio_risk_manager = PortfolioRiskManager(
        redis_client=redis_client,
        position_store=position_store,
        trade_store=trade_store,
        sector_mapping=sector_cap,
        bybit_client=bybit_client,
    )
    active_position_manager = ActivePositionManager(
        bybit_client=bybit_client,
        position_store=position_store,
        redis_client=redis_client,
        regime_classifier=regime_classifier,
        alert_service=alert_service,
    )

    # --- Shadow Mode: conditional component substitution ---
    shadow_executor = None
    shadow_apm = None
    shadow_pos_store = None
    shadow_trade_store = None

    if settings.shadow_mode_enabled:
        logger.warning("SHADOW MODE ENABLED — no real orders will be placed")
        metrics.karsa_shadow_mode_active.set(1)

        shadow_pos_store = ShadowPositionStore(redis_client)
        shadow_trade_store = ShadowTradeStore(db_engine)

        shadow_executor = ShadowExecutor(
            redis_client=redis_client,
            position_store=shadow_pos_store,
            trade_store=shadow_trade_store,
            alert_service=alert_service,
        )

        shadow_apm = ShadowAPM(
            real_apm=active_position_manager,
            shadow_executor=shadow_executor,
            redis_client=redis_client,
            position_store=shadow_pos_store,
            trade_store=shadow_trade_store,
        )
    else:
        metrics.karsa_shadow_mode_active.set(0)

    entry_filter = EntryFilter(
        min_atr=0.008,  # require real volatility, skip dead markets
        max_spread_pct=0.001,  # 0.1% max spread — cuts micro-cap noise
    )

    # AI client + analyst (off hot-path, safe per CLAUDE.md Rule 7)
    ai_client = AIClient(
        router_url=settings.nine_router_base_url,
        auth_token=settings.nine_router_auth_token,
        model=settings.nine_router_model,
    )
    # AI mandatory — always create (Issue #8: toggles removed)
    crypto_analyst = CryptoAnalyst(ai_client, ohlcv_fetcher, redis_client)
    position_judge = PositionJudge(ai_client, ohlcv_fetcher, redis_client)

    trade_reconciler = TradeReconciler(bybit_client, trade_store, alert_service)
    # Backfill trade history from Bybit on startup (one-time sync)
    try:
        backfilled = await trade_reconciler.backfill_from_bybit()
        if backfilled:
            logger.info(f"Backfilled {backfilled} trades from Bybit closed PnL")
    except Exception as e:
        logger.warning(f"Trade backfill failed (non-fatal): {e}")
    trailing_stop = TrailingStopManager(
        position_store, bybit_client, max_loss_usd=Decimal("1.00")
    )
    checkpoint_mgr = CheckpointManager(
        position_store,
        bybit_client,
        hard_fail_30min_pct=Decimal("-0.035"),
        hard_fail_ever_pct=Decimal("-0.05"),
        position_judge=position_judge,
        trade_store=trade_store,
        alert_service=alert_service,
        trade_memory=trade_memory,
        sor=sor,
    )

    # Phase 4.5 modules
    multi_tf = MultiTFFilter(ohlcv_fetcher)
    universe_scorer = UniverseScorer(redis_client, ohlcv_fetcher, settings.symbols)

    # Startup reconciliation — trust nothing (skip in shadow mode)
    if not settings.shadow_mode_enabled:
        try:
            reconciled = await state_manager.reconcile()
            if not reconciled:
                logger.critical(
                    "Reconciliation failed — cannot start. Check Bybit connection."
                )
                sys.exit(1)
        except Exception as e:
            logger.critical(
                f"Reconciliation failed — cannot start. Check Bybit connection. {e}"
            )
            sys.exit(1)

    # Sync positions with Bybit truth — clean orphans + create missing (skip in shadow mode)
    if not settings.shadow_mode_enabled:
        try:
            live_positions = await bybit_client.fetch_positions()
            exchange_symbols = {p["symbol"] for p in live_positions}
            cleaned = await position_store.cleanup_stale(exchange_symbols)
            if cleaned:
                logger.info(f"Cleaned {cleaned} orphaned position keys from Redis")
            # Create Redis keys for Bybit positions missing from PositionStore
            for pos in live_positions:
                bybit_sym = pos["symbol"]
                side = pos["side"]
                # Convert Bybit format (BTCUSDT) to ccxt format (BTC/USDT)
                if bybit_sym.endswith("USDT"):
                    ccxt_sym = f"{bybit_sym[:-4]}/USDT"
                else:
                    ccxt_sym = bybit_sym
                if not await position_store.has_position(ccxt_sym, side):
                    entry_price = Decimal(str(pos.get("entry_price", 0)))
                    amount = Decimal(str(pos.get("contracts", 0)))
                    if entry_price > 0 and amount > 0:
                        await position_store.save(
                            symbol=ccxt_sym,
                            side=side,
                            entry_price=entry_price,
                            amount=amount,
                        )
                        logger.info(
                            f"Synced missing position to Redis: {ccxt_sym} {side}"
                        )
                        # Record in Postgres so trade_store stays in sync
                        try:
                            await trade_store.record_entry(
                                symbol=ccxt_sym,
                                side=side,
                                amount=amount,
                                entry_price=entry_price,
                            )
                        except Exception as te:
                            logger.warning(
                                f"Trade store record_entry failed for synced {ccxt_sym}: {te}"
                            )
        except Exception as e:
            logger.warning(f"Position sync failed (non-fatal): {e}")

    signal_queue: asyncio.Queue = asyncio.Queue()
    risk_queue: asyncio.Queue = asyncio.Queue()

    # Dynamic symbol discovery — fetch top Bybit USDT perps by 24h volume
    # Falls back to static config list if Bybit API unavailable
    dropped = 0
    dynamic_symbols = await ccxt_manager.fetch_bybit_perps(
        min_volume_usd=250_000, top_n=150
    )
    if dynamic_symbols:
        valid_symbols = dynamic_symbols
        logger.info(
            f"Dynamic universe: {len(valid_symbols)} Bybit USDT perps (>$250k vol)"
        )
    else:
        # Fallback: static config validated against Bybit
        validated = ccxt_manager.get_bybit_universe(settings.symbols)
        if validated:
            valid_symbols = validated
            dropped = len(settings.symbols) - len(valid_symbols)
            if dropped:
                logger.warning(
                    f"Dropped {dropped} symbols not available on Bybit. {len(valid_symbols)} remaining."
                )
            else:
                logger.info(
                    f"All {len(valid_symbols)} config symbols validated on Bybit."
                )
        else:
            valid_symbols = settings.symbols
            dropped = 0
            logger.warning(
                f"Symbol validation skipped (no exchange markets loaded). Using all {len(valid_symbols)} config symbols."
            )
        # Filter against Bybit available instruments (removes SHIB, FLOKI etc not on Bybit)
        if bybit_client._symbol_map:
            bybit_before = len(valid_symbols)
            valid_symbols = [s for s in valid_symbols if s in bybit_client._symbol_map]
            bybit_dropped = bybit_before - len(valid_symbols)
            if bybit_dropped:
                logger.warning(
                    f"Dropped {bybit_dropped} symbols not on Bybit. {len(valid_symbols)} remaining."
                )
            dropped += bybit_dropped

    metrics.symbol_universe_total.set(len(valid_symbols))
    metrics.symbol_universe_dropped.set(dropped)

    # Graceful shutdown on SIGINT/SIGTERM
    def handle_shutdown(signum: int, frame: object) -> None:
        logger.info(f"Received signal {signum}, initiating shutdown")
        kill_switch.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Start all tasks
    tasks = [
        asyncio.create_task(
            data_engine_task(
                ccxt_manager,
                normalizer,
                bad_tick_filter,
                redis_client,
                lead_lag_buffer,
                valid_symbols,
            )
        ),
        asyncio.create_task(
            alpha_bridge_task(
                alpha_metrics,
                signal_generator,
                lead_lag_buffer,
                entry_filter,
                redis_client,
                valid_symbols,
                signal_queue,
                multi_tf,
                crypto_analyst,
                ohlcv_fetcher,
                position_store,
                trade_memory,
                trade_store,
                strategy_router=strategy_router,
            )
        ),
        asyncio.create_task(
            risk_gate_task(
                risk_gate,
                circuit_breaker,
                redis_client,
                signal_queue,
                risk_queue,
                portfolio_risk_manager=portfolio_risk_manager,
            )
        ),
        asyncio.create_task(
            executor_task(
                shadow_executor if shadow_executor else sor,
                state_manager,
                circuit_breaker,
                risk_queue,
                watchdog,
                redis_client,
                shadow_pos_store if shadow_pos_store else position_store,
                sector_cap,
                bybit_client,
                shadow_trade_store if shadow_trade_store else trade_store,
                session_manager,
                dynamic_risk_gate=dynamic_risk_gate,
            )
        ),
        asyncio.create_task(watchdog_task(watchdog, redis_client)),
        asyncio.create_task(universe_refresh_task(universe_scorer, valid_symbols)),
        asyncio.create_task(dead_mans_switch.start()),
        asyncio.create_task(session_manager.run_loop()),

        asyncio.create_task(
            regime_engine_task(
                regime_engine, regime_classifier, ohlcv_fetcher, redis_client
            )
        ),
        # Phase 6.5: APM_ENABLED=True disables legacy lifecycle managers
        # Set APM_ENABLED=1 in env to activate ActivePositionManager
        *(
            []
            if APM_ENABLED
            else [
                asyncio.create_task(
                    trailing_stop.run(
                        kill_switch, lambda s: _get_price(redis_client, s)
                    )
                ),
                asyncio.create_task(
                    checkpoint_mgr.run(
                        kill_switch,
                        lambda s: _get_price(redis_client, s),
                        state_manager,
                    )
                ),
            ]
        ),
        *(
            []
            if settings.shadow_mode_enabled
            else [
                asyncio.create_task(
                    position_reconciler_task(
                        bybit_client, position_store, interval_seconds=300
                    )
                ),
            ]
        ),
        asyncio.create_task(
            trade_history_reconciler_task(trade_reconciler, interval_seconds=900)
        ),
        asyncio.create_task(
            metrics_publisher_task(
                bybit_client, redis_client, position_store, kill_switch
            )
        ),
        # Phase 6 tasks
        asyncio.create_task(
            regime_classifier.run_classification_loop(
                ohlcv_fetcher=ohlcv_fetcher, symbol="BTC/USDT", interval_seconds=900
            )
        ),
        asyncio.create_task(portfolio_risk_manager.reset_daily_state_loop()),
        asyncio.create_task(portfolio_risk_manager.monitor_circuit_breakers()),
        asyncio.create_task(
            shadow_apm.run()
            if shadow_apm
            else active_position_manager.start_monitoring()
        ),
    ]

    task_names = [
        "data_engine_task",
        "alpha_bridge_task",
        "risk_gate_task",
        "executor_task",
        "watchdog_task",
        "universe_refresh_task",
        "dead_mans_switch",
        "session_manager",
        "bot_runner",
        "regime_engine_task",
        "trailing_stop",
        "checkpoint_mgr",
        *([] if settings.shadow_mode_enabled else ["position_reconciler"]),
        "trade_history_reconciler",
        "metrics_publisher_task",
        "regime_classifier",
        "prm_daily_reset",
        "shadow_apm" if settings.shadow_mode_enabled else "active_position_manager",
    ]
    for t, name in zip(tasks, task_names):
        t.set_name(name)

    # Register critical tasks with watchdog for liveness monitoring
    CRITICAL_TASKS = {
        "data_engine_task",
        "alpha_bridge_task",
        "risk_gate_task",
        "executor_task",
        "regime_engine_task",
        "active_position_manager",
    }
    for t in tasks:
        if t.get_name() in CRITICAL_TASKS:
            watchdog.register_critical_task(t.get_name(), t)

    # Kill switch on critical task death
    for t in tasks:
        t.add_done_callback(
            lambda task: _on_task_done(task, kill_switch, CRITICAL_TASKS)
        )

    logger.info(f"All components started. Monitoring {len(valid_symbols)} symbols.")

    # Wait for kill switch
    await kill_switch.wait()

    # Cancel all tasks
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    # Execute kill switch sequence
    await kill_switch_sequence(bybit_client, state_manager, sor)

    # Cleanup
    await dead_mans_switch.stop()
    await redis_client.disconnect()
    await ccxt_manager.close()
    await bybit_client.disconnect()
    await db_engine.dispose()

    # Cleanup AI client
    await ai_client.close()

    logger.info("Shutdown complete")
    logger.debug("main: returning None")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
