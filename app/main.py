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
import signal
import sys
import time
from decimal import Decimal
from typing import Optional

from loguru import logger

from app.core.config import get_settings
from app.core.redis_client import RedisClient
from app.core.state import StateManager
from app.data.ccxt_manager import CCXTManager
from app.data.filters import BadTickFilter
from app.data.normalizer import Normalizer
from app.alpha.metrics import AlphaMetrics
from app.alpha.signals import SignalGenerator
from app.alpha.regime import RegimeEngine
from app.alpha.lead_lag_buffer import LeadLagBuffer
from app.alpha.entry_filter import EntryFilter
from app.alpha.analyst import CryptoAnalyst
from app.alpha.position_judge import PositionJudge
from app.core.ai_client import AIClient
from app.data.ohlcv_fetcher import OHLCVFetcher
from app.execution.bybit_client import BybitClient
from app.execution.sor import SmartOrderRouter
from app.execution.position_lifecycle import TrailingStopManager, CheckpointManager
from app.core.position_store import PositionStore
from app.core.trade_store import TradeStore
from app.risk.gates import RiskGate
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.sector_cap import SectorCap
from app.alpha.multi_tf import MultiTFFilter
from app.alpha.ta_tools import calculate_atr
from app.alpha.trade_memory import TradeMemory
from app.data.universe_scorer import UniverseScorer
from app.watchdog.monitor import Watchdog
from app.watchdog.dead_mans_switch import DeadMansSwitch
from app.bot.runner import run_bot
from app.bot.alert_service import AlertService
from app.core.session import AutonomousSessionManager
from app.core.database import DatabaseEngine
from app.core import metrics

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
    while not kill_switch.is_set():
        try:
            orderbook = await ccxt_manager.watch_orderbook(symbol, exchange_id)
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
            metrics.orderbook_errors.labels(
                exchange=exchange_id, symbol=symbol, error_type=type(e).__name__
            ).inc()
            logger.error(f"Data Engine error {exchange_id}/{symbol}: {e}")
            logger.debug(f"_stream_orderbook: error={e}")
            await asyncio.sleep(1)
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
    exchanges = ["binance", "okx", "bybit"]
    logger.info(
        f"Data Engine starting — {len(symbols)} symbols × {len(exchanges)} exchanges = {len(symbols) * len(exchanges)} streams"
    )

    await asyncio.gather(
        *[
            _stream_orderbook(
                symbol,
                eid,
                ccxt_manager,
                normalizer,
                bad_tick_filter,
                redis_client,
                lead_lag_buffer,
            )
            for symbol in symbols
            for eid in exchanges
        ],
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
    crypto_analyst: Optional[CryptoAnalyst],
    ohlcv_fetcher: Optional[OHLCVFetcher] = None,
    position_store: Optional[PositionStore] = None,
) -> None:
    """Key 2: Generate trading signals from market state."""
    logger.debug("alpha_bridge_task: entering")
    logger.info("Alpha Bridge starting...")

    while not kill_switch.is_set():
        if alpha_paused.is_set():
            logger.warning("Alpha Bridge paused — stale data")
            await asyncio.sleep(5)
            continue
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

                    # Read regime from Redis (set by regime_engine_task)
                    regime = await redis_client.get_session_config()

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

                    # Multi-signal inputs (Phase 2)
                    lead_lag_delta = lead_lag_buffer.get_lead_lag_delta(symbol)
                    funding_rate = await alpha_metrics.get_funding_rate(symbol)
                    funding_float = (
                        float(funding_rate) if funding_rate is not None else None
                    )
                    oi_val = await alpha_metrics.get_open_interest(symbol)
                    oi_prev = alpha_metrics._oi_cache.get(symbol)
                    oi_change = None
                    if oi_val is not None and oi_prev is not None:
                        oi_change = float(oi_val - oi_prev[1])

                    signal = signal_generator.generate(
                        symbol,
                        vwap,
                        skew,
                        regime=regime,
                        lead_lag_delta=lead_lag_delta,
                        funding_rate=funding_float,
                        oi_change=oi_change,
                    )
                    # Attach pre-computed ATR to signal for position lifecycle
                    if signal and atr_val:
                        signal.atr = atr_val
                    if signal:
                        metrics.signals_entered_pipeline.labels(symbol=symbol).inc()
                        # Multi-TF confirmation: 4H EMA penalty if contradicts
                        if signal.direction in ("LONG", "SHORT"):
                            mtf = await multi_tf.check(symbol, signal.direction)
                            if mtf["penalty_applied"] < 1:
                                signal.confidence *= float(mtf["penalty_applied"])
                                logger.info(
                                    f"Multi-TF penalty {symbol}: {mtf['penalty_applied']}x → conf={signal.confidence:.3f}"
                                )

                        # AI analyst — ambiguous confidence zone only (0.55-0.85)
                        if crypto_analyst and 0.55 <= signal.confidence < 0.85:
                            analyst_result = await crypto_analyst.analyze(
                                symbol=signal.symbol,
                                direction=signal.direction,
                                confidence=signal.confidence,
                                regime=regime or "UNKNOWN",
                                spread_pct=(
                                    spread_pct if spread_pct is not None else 0.0
                                ),
                                funding_rate=(
                                    funding_float if funding_float is not None else 0.0
                                ),
                                oi_change=oi_change if oi_change is not None else 0.0,
                                price=Decimal(str(state.get("global_vwap", "0"))),
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
                        signal._generated_at = time.time()
                        await signal_queue.put(signal)
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
) -> None:
    """Key 3: Gate signals through risk checks."""
    logger.debug("risk_gate_task: entering")
    logger.info("Risk Gate starting...")

    while not kill_switch.is_set():
        try:
            signal = await asyncio.wait_for(signal_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        if circuit_breaker.is_halted() or circuit_breaker.is_paused():
            logger.warning("Signal rejected — circuit breaker active")
            continue

        # Pull live market data from Redis global state
        state = await redis_client.get_global_state(signal.symbol)
        if not state or not state.get("best_bid") or not state.get("best_ask"):
            logger.warning(f"Signal rejected — no live state for {signal.symbol}")
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
    ohlcv_fetcher: OHLCVFetcher,
    redis_client: RedisClient,
) -> None:
    """Regime Engine: classify market regime from BTC 1H+4H OHLCV every 15 min."""
    logger.debug("regime_engine_task: entering")
    logger.info("Regime Engine starting...")

    while not kill_switch.is_set():
        try:
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

                # Update Prometheus metrics
                regime_map = {
                    "CHOP": 0,
                    "MEAN_REVERSION": 1,
                    "TREND_BEAR": 2,
                    "TREND_BULL": 3,
                }
                metrics.regime_state.set(regime_map.get(regime, 0))
                metrics.regime_hurst.set(hurst)
                metrics.regime_adx.set(adx)
                # 4H ADX for AND-gate visibility
                adx_4h = 0.0
                if candles_4h and len(candles_4h) >= 50:
                    _, _, adx_4h = await asyncio.to_thread(
                        regime_engine.classify, candles_4h, 50
                    )
                    metrics.regime_adx_4h.set(adx_4h)
                logger.info(
                    f"Regime updated: {regime} (hurst={hurst:.4f} adx={adx:.2f} adx_4h={adx_4h:.2f})"
                )
            else:
                logger.warning(f"Insufficient candles for regime: {len(candles)}")
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
    risk_pct: Decimal = Decimal("0.03"),
) -> None:
    """Key 4: Execute trades via Smart Order Router."""
    logger.debug("executor_task: entering")
    logger.info("Executor starting...")

    while not kill_switch.is_set():
        try:
            signal = await asyncio.wait_for(risk_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

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
        except Exception:
            max_pos = 5
        open_positions = await position_store.list_all()
        if len(open_positions) >= max_pos:
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

        # Position sizing: risk_pct of available balance / entry price
        try:
            wallet = await bybit_client.get_wallet_balance()
            available = wallet.get("available", Decimal("0"))
            if available <= 0:
                logger.warning(f"No available balance, skipping {signal.symbol}")
                continue
            amount = (available * risk_pct) / price
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
            exec_start = time.time()
            result = await sor.execute(
                symbol=signal.symbol,
                side=side_str,
                amount=amount,
                price=price,
            )
            exec_latency_ms = int((time.time() - exec_start) * 1000)

            if result:
                logger.info(
                    f"SOR fill: {signal.symbol} {signal.direction} latency={exec_latency_ms}ms"
                )
                # Register position in store for trailing stop + checkpoint management
                await position_store.save(
                    symbol=signal.symbol,
                    side=side_str,
                    entry_price=price,
                    amount=amount,
                    sl_order_id=result.get("sl_order_id"),
                    atr=getattr(signal, "atr", None),
                )
                # Record trade entry in Postgres
                regime_data = await redis_client.get_session_config()
                regime = (
                    regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"
                )
                await trade_store.record_entry(
                    symbol=signal.symbol,
                    side=signal.direction,
                    amount=amount,
                    entry_price=price,
                    regime=regime,
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


async def universe_refresh_task(
    scorer: UniverseScorer, config_symbols: list[str], interval_hours: int = 4
) -> None:
    """Periodic universe scorer refresh. Falls back to static config on failure."""
    logger.debug("universe_refresh_task: entering")
    while not kill_switch.is_set():
        try:
            symbols = await scorer.refresh(config_symbols)
            logger.info(f"Universe refresh complete: {len(symbols)} symbols")
        except Exception as e:
            logger.error(f"Universe refresh failed: {e}")
        await asyncio.sleep(interval_hours * 3600)
    logger.debug("universe_refresh_task: returning None")


async def kill_switch_sequence(
    bybit_client: BybitClient, state_manager: StateManager, sor: SmartOrderRouter
) -> None:
    """Execute graceful shutdown sequence."""
    logger.debug("kill_switch_sequence: entering")
    try:
        await sor.cancel_all_positions()
        logger.info("Kill switch sequence complete")
    except Exception as e:
        logger.error(f"Kill switch error: {e}")
        logger.debug(f"kill_switch_sequence: error={e}")
    logger.debug("kill_switch_sequence: returning None")


async def _get_price(redis_client: RedisClient, symbol: str) -> Optional[Decimal]:
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

    start_http_server(8001)
    logger.info("Prometheus metrics server on :8001")

    bybit_client = BybitClient()
    await bybit_client.connect()
    metrics.bybit_status.set(1)  # Bybit connected
    alert_service = AlertService(settings.telegram_chat_id)
    sor = SmartOrderRouter(bybit_client, alert_service=alert_service)
    circuit_breaker = CircuitBreaker(
        alert_service=alert_service, redis_client=redis_client
    )
    await circuit_breaker.restore()  # Persisted halt state survives restarts
    risk_gate = RiskGate(circuit_breaker=circuit_breaker)  # Fix #5: shared CB instance
    state_manager = StateManager(redis_client, bybit_client)
    watchdog = Watchdog(
        redis_client, alpha_paused=alpha_paused, sor=sor, kill_switch=kill_switch
    )
    dead_mans_switch = DeadMansSwitch()
    session_manager = AutonomousSessionManager(redis_client, kill_switch)
    ohlcv_fetcher = OHLCVFetcher(ccxt_manager.exchanges["binance"])
    lead_lag_buffer = LeadLagBuffer()
    entry_filter = EntryFilter()

    # AI client + analyst (off hot-path, safe per CLAUDE.md Rule 7)
    ai_client = AIClient(
        router_url=settings.nine_router_base_url,
        auth_token=settings.nine_router_auth_token,
        model=settings.nine_router_model,
    )
    # AI mandatory — always create (Issue #8: toggles removed)
    crypto_analyst = CryptoAnalyst(ai_client, ohlcv_fetcher, redis_client)
    position_judge = PositionJudge(ai_client, ohlcv_fetcher, redis_client)

    position_store = PositionStore(redis_client)
    trade_store = TradeStore(db_engine)
    trailing_stop = TrailingStopManager(position_store, bybit_client)
    checkpoint_mgr = CheckpointManager(
        position_store,
        bybit_client,
        position_judge=position_judge,
        trade_store=trade_store,
        alert_service=alert_service,
    )

    # Phase 4.5 modules
    multi_tf = MultiTFFilter(ohlcv_fetcher)
    trade_memory = TradeMemory(redis_client)
    sector_cap = SectorCap(position_store)
    universe_scorer = UniverseScorer(redis_client, ohlcv_fetcher, settings.symbols)

    # Startup reconciliation — trust nothing
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

    signal_queue: asyncio.Queue = asyncio.Queue()
    risk_queue: asyncio.Queue = asyncio.Queue()

    # Validate symbol universe — keep only symbols present on all 3 exchanges
    # Fall back to raw config if no exchange markets were loaded (VPN down etc.)
    validated = ccxt_manager.get_valid_universe(settings.symbols)
    if validated:
        valid_symbols = validated
        dropped = len(settings.symbols) - len(valid_symbols)
        if dropped:
            logger.warning(
                f"Dropped {dropped} symbols not available on all exchanges. {len(valid_symbols)} remaining."
            )
        else:
            logger.info(
                f"All {len(valid_symbols)} symbols validated across all exchanges."
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
            )
        ),
        asyncio.create_task(
            risk_gate_task(
                risk_gate, circuit_breaker, redis_client, signal_queue, risk_queue
            )
        ),
        asyncio.create_task(
            executor_task(
                sor,
                state_manager,
                circuit_breaker,
                risk_queue,
                watchdog,
                redis_client,
                position_store,
                sector_cap,
                bybit_client,
                trade_store,
            )
        ),
        asyncio.create_task(watchdog_task(watchdog, redis_client)),
        asyncio.create_task(universe_refresh_task(universe_scorer, valid_symbols)),
        asyncio.create_task(dead_mans_switch.start()),
        asyncio.create_task(session_manager.run_loop()),
        asyncio.create_task(
            run_bot(
                redis_client,
                bybit_client,
                kill_switch,
                session_manager,
                db_engine,
                alert_service,
            )
        ),
        asyncio.create_task(
            regime_engine_task(regime_engine, ohlcv_fetcher, redis_client)
        ),
        asyncio.create_task(
            trailing_stop.run(kill_switch, lambda s: _get_price(redis_client, s))
        ),
        asyncio.create_task(
            checkpoint_mgr.run(
                kill_switch, lambda s: _get_price(redis_client, s), state_manager
            )
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
