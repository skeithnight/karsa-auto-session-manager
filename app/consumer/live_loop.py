"""karsa-live entrypoint — wires MarketConsumer to SmartOrderRouter.

Connects to Redis, subscribes to candle channels, and executes real
trades through the Bybit SmartOrderRouter. Every entry passes
PortfolioRiskManager before execution — no bypass.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.alpha.hybrid_decision_engine import HybridDecisionEngine
from app.alpha.ai_ranker import AISignalRanker
from app.alpha.regime_classifier import MarketRegime, RegimeClassifier
from app.alpha.statistical_engine import StatisticalFeatureEngine
from app.alpha.strategy_router import StrategyRouter
from app.bot.alert_service import AlertService
from app.consumer.decision_engine import DecisionEngine, TradeSignal
from app.consumer.market_consumer import MarketConsumer
from app.core.config import get_settings
from app.core.dependencies import get_pool, get_redis, shutdown, startup
from app.core.position_store import PositionStore
from app.core.telemetry import TelemetryEmitter
from app.core.trade_store import TradeStore
from app.data.market_data_ingestor import MarketDataIngestor
from app.data.ohlcv_fetcher import OHLCVFetcher
from app.risk.dynamic_risk_gate import DynamicRiskGate
from app.alpha.ml_prefilter import MLPrefilter

logger = logging.getLogger("karsa.live")
ml_prefilter = MLPrefilter()


def _configure_logging() -> None:
    from app.core.context import TraceIdFilter

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","trace_id":"%(trace_id)s","msg":"%(message)s"}'
        )
    )
    handler.addFilter(TraceIdFilter())
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)


async def _on_candle(_symbol: str, _candle: list) -> None:
    """Per-candle callback — extend with Prometheus metrics if needed."""
    pass


async def _wallet_metrics_loop(
    bybit: Any,
    position_store: PositionStore,
    redis: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 30,
) -> None:
    """Periodically publish wallet balance, position metrics, and max positions to Prometheus."""
    from app.core import metrics

    while not shutdown_event.is_set():
        try:
            try:
                max_pos = int(await redis.get("karsa:settings:max_positions") or 5)
            except Exception:
                max_pos = 5
            metrics.max_positions.set(max_pos)

            if bybit:
                wallet = await bybit.get_wallet_balance()
                available = float(wallet.get("available", 0))
                balance = float(wallet.get("balance", 0))
                metrics.wallet_balance.set(available)
                try:
                    import json
                    await redis.set(
                        "karsa:wallet:latest",
                        json.dumps({"balance": balance, "available": available, "ok": not wallet.get("error")}),
                        ex=120,
                    )
                except Exception as cache_exc:
                    logger.debug("failed_to_cache_wallet_in_redis: %s", cache_exc)

            open_positions = await position_store.list_all()

            # Wallet equity = balance + unrealized PnL
            if bybit:
                total_unrealized = sum(float(p.get("pnl", 0)) for p in open_positions)
                metrics.wallet_total_equity.set(balance + total_unrealized)

            # Per-position Prometheus metrics (replaces position_lifecycle.py)
            tracked_symbols = set()
            for pos in open_positions:
                sym = pos.get("symbol", "")
                if not sym:
                    continue
                tracked_symbols.add(sym)
                pos.get("side", "LONG")
                entry = Decimal(str(pos.get("entry_price", 0)))
                amount = Decimal(str(pos.get("amount", 0)))
                sl_price = Decimal(str(pos.get("sl_price", 0) or pos.get("virtual_sl", 0) or 0))

                metrics.position_size.labels(symbol=sym).set(amount)
                metrics.position_entry_price.labels(symbol=sym).set(entry)
                if sl_price > 0:
                    metrics.position_sl_price.labels(symbol=sym).set(sl_price)

                # Duration from entered_at
                entered_at = pos.get("entered_at", "")
                if entered_at:
                    from datetime import timezone, datetime

                    try:
                        entered = datetime.fromisoformat(entered_at)
                        elapsed = (datetime.now(timezone.utc) - entered).total_seconds()
                        metrics.position_duration.labels(symbol=sym).set(elapsed)
                    except Exception:
                        pass

                # Unrealized PnL — compute from current price if not stored
                pnl = float(pos.get("pnl", 0))
                metrics.position_unrealized_pnl.labels(symbol=sym).set(pnl)

            # Clear metrics for closed positions
            # (prometheus_client doesn't support removal, but 0 amount = no display in table)

        except Exception:
            logger.warning("wallet_metrics_loop error", exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval_s)
        except TimeoutError:
            pass


async def _on_signal_live(  # noqa: PLR0913  # noqa: PLR0913
    symbol: str,
    signal: TradeSignal,
    position_store: PositionStore,
    executor: Any,
    risk_manager: Any,
    trade_store: TradeStore,
    engine: Any | None = None,
    hybrid_engine: HybridDecisionEngine | None = None,
    ai_ranker: AISignalRanker | None = None,
) -> None:
    """Handle a TradeSignal by executing a real order on Bybit.

    from app.core.context import trace_id_ctx
    trace_id_ctx.set(signal.trace_id or "")

    Checks:
    1. No duplicate position already open.
    2. Consecutive loss block (3+ losses in same regime).
    3. PortfolioRiskManager approves.
    4. Execute via SmartOrderRouter.
    5. Record trade in DB.
    """
    # ASM gate — fail-closed: must be explicitly "1" to allow trade.
    # If key is missing, Redis is down, or any exception → block (never open).
    try:
        _asm_raw = await engine._redis.get("karsa:auto:state:active") if engine and engine._redis else None
        if str(_asm_raw or "").strip() != "1":
            logger.debug("skip %s — ASM not active (state=%r)", symbol, _asm_raw)
            return
    except Exception:
        logger.warning("skip %s — ASM state check failed (fail-closed), blocking trade", symbol)
        return

    # --- Proactive Blocklist Check ---
    try:
        if position_store.redis:
            is_blocked = await position_store.redis.get(f"karsa:blocked_symbol:{symbol}")
            if is_blocked:
                reason = is_blocked.decode('utf-8') if isinstance(is_blocked, bytes) else is_blocked
                logger.debug(f"⛔ Signal {symbol} SKIPPED: Symbol is on temporary blocklist ({reason})")
                from app.core import metrics
                metrics.signals_blocked_unauthorized_total.inc()
                return # Silently drop the signal, no need to waste AI/Risk Gate compute
    except Exception as e:
        logger.warning(f"Failed to check blocklist for {symbol}: {e}")

    # Check Auto-Adjustment Regime Overrides and Apply Dynamic Sizing
    try:
        raw_cfg = await position_store.redis.get("karsa:auto:config")
        if raw_cfg:
            import json
            from decimal import Decimal

            cfg = json.loads(raw_cfg)
            overrides = cfg.get("regime_overrides", {})
            sizing = cfg.get("regime_sizing", {})
            current_regime = signal.regime.value

            if overrides.get(current_regime) == "DISABLE":
                if signal.regime == MarketRegime.SNIPER:
                    logger.info("SNIPER trap bypassing regime_overrides['DISABLE'] rule for %s", symbol)
                else:
                    logger.info(
                        "skip %s — %s regime is temporarily DISABLED by Shadow Auto-Adjustment",
                        symbol,
                        current_regime,
                    )
                    from app.core import metrics
                    metrics.regime_disabled_blocks.labels(symbol=symbol, regime=current_regime).inc()
                    return

            # Apply Dynamic Sizing (Session sizing is now handled in decision_engine Kelly size)
            multiplier_val = sizing.get(current_regime, 1.0)
            multiplier = Decimal(str(multiplier_val))

            if multiplier == Decimal("0"):
                logger.warning(f"skip {symbol} — Multiplier for '{current_regime}' is 0.0 but bypassed DISABLE override.")
                return

            base_amount = signal.amount
            adjusted_amount = base_amount * multiplier

            if multiplier < Decimal("1.0"):
                logger.info(
                    "Dynamic Sizing Applied for %s | Regime: %s | Multiplier: %sx | Base: %s -> Adjusted: %s",
                    symbol, current_regime, multiplier, base_amount, adjusted_amount
                )
                from app.core import metrics
                metrics.regime_sizing_applied.labels(symbol=symbol, regime=current_regime).inc()

            # Check Exchange Minimum Order Size
            min_order_qty = Decimal("0")
            bybit = getattr(executor, "client", None)
            if bybit and hasattr(bybit, "_min_qty"):
                min_order_qty = bybit._min_qty.get(symbol, Decimal("0"))
                
            if adjusted_amount < min_order_qty:
                logger.info(
                    "skip %s — Adjusted size (%s) is below exchange minimum (%s). Refusing to round up.",
                    symbol, adjusted_amount, min_order_qty
                )
                from app.core import metrics
                metrics.size_below_minimum_skips.labels(symbol=symbol, regime=current_regime).inc()
                return

            # Update the signal object
            signal.amount = adjusted_amount
            # Ensure we don't crash if signal doesn't have base_amount attribute
            setattr(signal, "base_amount", base_amount)

    except Exception as e:
        logger.debug("regime_overrides and sizing check failed: %s", e)

    # Skip if position already open
    has_pos = await position_store.has_position(symbol)
    if has_pos:
        logger.info("skip %s — position already open", symbol)
        return

    # Slot checking
    open_positions = await position_store.list_all()
    total_open = len(open_positions)
    hyper_open = sum(1 for p in open_positions if str(p.get("regime", "")).startswith("HYPER"))

    try:
        max_pos = int(await position_store.redis.get("karsa:settings:max_positions") or 5)
        max_hyper = int(await position_store.redis.get("karsa:settings:max_hyper_slots") or 2)
    except Exception:
        max_pos = 5
        max_hyper = 2

    is_hyper = signal.regime.value.startswith("HYPER")

    if is_hyper:
        if hyper_open >= max_hyper:
            logger.info("skip %s — HYPER slots full (%d/%d)", symbol, hyper_open, max_hyper)
            return
    elif total_open >= max_pos:
        logger.info(
            "skip %s — all slots full (%d/%d)",
            symbol,
            total_open,
            max_pos,
        )
        return

    # ML Prefilter Gate
    signal_features = {
        'cvd_slope': signal.cvd_slope,
        'spread_bps': signal.spread_bps,
        'session_mult': signal.session_mult,
        'regime_encoded': signal.regime_encoded,
        'atr_pct': signal.atr_pct,
        'vol_factor': signal.vol_factor,
        'ai_confidence_before': signal.score / 100.0,
    }
    ml_prob = ml_prefilter.predict_probability(signal_features)
    if ml_prob < 0.55:
        logger.info("🤖 ML Prefilter REJECTED %s (prob: %.2f%%)", symbol, ml_prob * 100)
        from app.core import metrics
        # Note: If ml_prefilter_rejections_total doesn't exist yet, we will increment a counter here
        if hasattr(metrics, "ml_prefilter_rejections_total"):
            metrics.ml_prefilter_rejections_total.inc()
        return

    logger.info("🤖 ML Prefilter PASSED %s (prob: %.2f%%). Sending to AI Analyst...", symbol, ml_prob * 100)

    # Hybrid Decision Engine evaluation (statistical guardrails + AI)
    if hybrid_engine is not None:
        try:
            import pandas as _pd

            # Build OHLCV DataFrame from candle buffer
            candles_list = signal.candles if hasattr(signal, 'candles') and signal.candles else []
            if candles_list and len(candles_list) >= 50:
                ohlcv_df = _pd.DataFrame(
                    candles_list,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )
                # Fetch BTC OHLCV for beta/correlation
                btc_ohlcv_df = _pd.DataFrame(
                    candles_list,  # placeholder — real BTC data fetched below
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )
                # Try to get real BTC data from consumer buffer via engine redis
                if engine and hasattr(engine, '_redis') and engine._redis:
                    try:
                        from app.data.ohlcv_fetcher import OHLCVFetcher as _Fetch
                        import ccxt.async_support as _ccxt
                        _ex = _ccxt.bybit({"enableRateLimit": True})
                        _fetcher = _Fetch(_ex)
                        btc_raw = await _fetcher.fetch("BTC/USDT", "1h", limit=len(candles_list))
                        if btc_raw and len(btc_raw) >= 50:
                            btc_ohlcv_df = _pd.DataFrame(
                                btc_raw,
                                columns=["timestamp", "open", "high", "low", "close", "volume"],
                            )
                        await _ex.close()
                    except Exception:
                        pass  # fallback to signal candles

                # Get regime and funding rate from signal context
                regime_str = signal.regime.value if hasattr(signal.regime, 'value') else str(signal.regime)
                funding = 0.0
                if engine and hasattr(engine, '_redis') and engine._redis:
                    try:
                        fr_raw = await engine._redis.get(f"karsa:funding:{symbol}")
                        if fr_raw:
                            funding = float(fr_raw)
                    except Exception:
                        pass

                # Count concurrent positions
                open_positions = await position_store.list_all()
                concurrent = len(open_positions)

                # Fetch actual BTC regime from Redis (not hardcoded RANGE)
                btc_regime = "RANGE"  # default fallback
                if engine._redis:
                    try:
                        btc_regime_raw = await engine._redis.get("system:regime:BTC:USDT")
                        if not btc_regime_raw:
                            btc_regime_raw = await engine._redis.get("system:config:regime")
                        if btc_regime_raw:
                            raw_s = btc_regime_raw if isinstance(btc_regime_raw, str) else btc_regime_raw.decode()
                            try:
                                data = json.loads(raw_s)
                                btc_regime = data.get("regime", raw_s) if isinstance(data, dict) else raw_s
                            except Exception:
                                btc_regime = raw_s
                    except Exception:
                        pass

                hybrid_decision = await hybrid_engine.evaluate(
                    symbol=symbol,
                    regime=regime_str,
                    btc_regime=btc_regime,
                    ohlcv=ohlcv_df,
                    btc_ohlcv=btc_ohlcv_df,
                    direction=signal.direction,
                    funding_rate=funding,
                    concurrent_positions=concurrent,
                    current_price=float(signal.entry_price),
                )

                # Store hybrid decision in Redis
                try:
                    import json as _json
                    decision_dict = {
                        "action": hybrid_decision.action,
                        "size": hybrid_decision.size,
                        "size_pct": hybrid_decision.size_pct,
                        "confidence": hybrid_decision.confidence,
                        "risk_level": hybrid_decision.risk_level,
                        "entry_strategy": hybrid_decision.entry_strategy,
                        "stop_loss_strategy": hybrid_decision.stop_loss_strategy,
                        "reasoning": hybrid_decision.reasoning,
                        "guardrails_triggered": hybrid_decision.guardrails_triggered,
                    }
                    await engine._redis.set(
                        f"karsa:hybrid_decision:{symbol}",
                        _json.dumps(decision_dict),
                    )
                except Exception:
                    logger.debug("Failed to store hybrid decision for %s", symbol)

                # BLOCK: skip trade entirely
                if hybrid_decision.action == "BLOCK":
                    logger.info(
                        "HybridDecisionEngine BLOCKED %s: %s",
                        symbol,
                        hybrid_decision.reasoning,
                    )
                    from app.core import metrics
                    if hasattr(metrics, "hybrid_blocks_total"):
                        metrics.hybrid_blocks_total.labels(symbol=symbol).inc()
                    return

                # Size adjustment: apply hybrid size recommendation
                if hybrid_decision.size_pct > 0 and hybrid_decision.size_pct < 1.0:
                    original_amount = signal.amount
                    adjusted_amount = original_amount * Decimal(str(hybrid_decision.size_pct))
                    object.__setattr__(signal, "amount", adjusted_amount)
                    logger.info(
                        "HybridDecisionEngine sizing %s: %s -> %s (size=%s, confidence=%d)",
                        symbol,
                        original_amount,
                        adjusted_amount,
                        hybrid_decision.size,
                        hybrid_decision.confidence,
                    )

        except Exception as e:
            logger.warning("HybridDecisionEngine evaluation failed for %s: %s", symbol, e)

    # AI Signal Ranker — adjust sizing based on signal quality ranking
    if ai_ranker is not None:
        try:
            signal_dict = {
                "symbol": symbol,
                "direction": signal.direction,
                "score": signal.score,
                "ev_score": getattr(signal, "expected_value", 0.0),
                "regime": signal.regime.value if hasattr(signal.regime, "value") else str(signal.regime),
            }
            ranked = await ai_ranker.rank([signal_dict])
            if ranked and ranked[0].sizing_multiplier != 1.0:
                original_amount = signal.amount
                adjusted_amount = original_amount * Decimal(str(ranked[0].sizing_multiplier))
                object.__setattr__(signal, "amount", adjusted_amount)
                logger.info(
                    "AI Ranker sizing %s: %s -> %s (multiplier=%.2f, thesis=%s)",
                    symbol, original_amount, adjusted_amount,
                    ranked[0].sizing_multiplier, ranked[0].edge_thesis,
                )
        except Exception as e:
            logger.debug("AI Ranker failed for %s: %s", symbol, e)

    # PortfolioRiskManager gate (mandatory, no bypass)
    if risk_manager is None:
        logger.error(
            "skip %s - PortfolioRiskManager is uninitialized! Blocking trade for safety.",
            symbol,
        )
        return

    from app.risk.portfolio_risk_manager import PRMResult

    result: PRMResult = await risk_manager.check(signal)
    if not result.approved:
        from app.core import metrics
        metrics.risk_gate_reject.labels(symbol=symbol, reason="portfolio_risk").inc()
        metrics.funnel_risk_rejected.inc()
        logger.info("skip %s — portfolio risk rejected: %s", symbol, result.reason)
        return

    from app.core import metrics
    metrics.risk_gate_pass.labels(symbol=symbol).inc()
    metrics.funnel_risk_passed.inc()

    # Pre-check: can we place an SL for this symbol?
    try:
        bybit = getattr(executor, "client", None)
        if bybit and hasattr(bybit, "fetch_open_orders"):
            open_orders = await bybit.fetch_open_orders(symbol=symbol)
            stop_count = sum(1 for o in open_orders
                             if o.get("type") in ("stop", "stoporder", "stop")
                             or o.get("stopOrderType"))
            if stop_count >= 9:  # Leave room for at least 1 new SL
                logger.warning("skip %s — %d stop orders already on exchange (limit 10)", symbol, stop_count)
                return
    except Exception as e:
        logger.debug("SL pre-check failed for %s: %s", symbol, e)
        pass  # Don't block entry if check fails

    # Execute via SmartOrderRouter / BybitExecutor natively
    result = await executor.execute(
        symbol=symbol,
        side=signal.direction,
        amount=signal.amount,
        price=signal.entry_price,
        max_loss_usd=abs(signal.entry_price - signal.sl_price) * signal.amount,
    )

    if result is None:
        logger.warning("execution failed for %s", symbol)
        return

    # Bybit V5 returns avgPrice, SOR returns average or price
    fill_price = Decimal(str(result.get("average", result.get("avgPrice", result.get("price", 0)))))

    # Guard: reject zero-price fill
    if fill_price <= 0:
        logger.error("REJECTING trade %s — fill_price is 0, fetching from order history", symbol)
        # Try to get actual fill price from Bybit
        try:
            await asyncio.sleep(1.0)
            if hasattr(executor, "client") and hasattr(executor.client, "fetch_my_trades"):
                trades = await executor.client.fetch_my_trades(symbol, limit=1)
                if trades:
                    fill_price = Decimal(str(trades[-1].get("price", 0)))
        except Exception:
            pass
        if fill_price <= 0:
            logger.critical("ABORTING entry %s — cannot determine fill price", symbol)
            # Cannot track without fill_price, bail out (ideally we should market close here too,
            # but since we couldn't fetch order history, Bybit API is likely degraded)
            return

    # Compute initial_risk_per_unit from actual fill price and signal SL.
    # This is the CRITICAL field APM uses for breakeven/trailing/SL placement.
    # Without it, APM bails out on every cycle and leaves position unprotected.
    initial_risk_per_unit = abs(fill_price - signal.sl_price)
    # If the fill price exactly equals SL price (or is weirdly zero), fallback to ATR to guarantee APM protection
    if initial_risk_per_unit <= Decimal("0") or initial_risk_per_unit < (signal.atr * Decimal("0.1")):
        # Fallback: derive from ATR and RiskProfile sl_atr_buffer
        initial_risk_per_unit = signal.atr * signal.risk_profile.sl_atr_buffer
        # Absolute hard floor if ATR is also completely busted
        if initial_risk_per_unit <= Decimal("0"):
            initial_risk_per_unit = fill_price * Decimal("0.01")  # 1% fallback

    # Save position — all APM-critical fields must be present here
    await position_store.save(
        symbol=symbol,
        side=signal.direction,
        entry_price=fill_price,
        amount=signal.amount,
        sl_order_id=result.get("sl_order_id", ""),  # Guarantee sl_order_id isn't dropped
        atr=signal.atr,
        entry_confidence=signal.score,
        regime=signal.regime.value,
        entry_regime=signal.regime.value,
        initial_risk_per_unit=str(initial_risk_per_unit),
        risk_profile_json=signal.risk_profile.to_json(),
    )

    # Phase 2: Shadow vs. Live Divergence Metrics
    try:
        import json
        from datetime import timezone, datetime

        shadow_key = f"shadow:position:{symbol}:{signal.direction}"
        shadow_raw = await position_store.redis.get(shadow_key)
        if shadow_raw:
            shadow_pos = json.loads(shadow_raw)
            shadow_entry_time_str = shadow_pos.get("entered_at")
            shadow_entry_price_str = shadow_pos.get("entry_price")

            if shadow_entry_time_str:
                shadow_dt = datetime.fromisoformat(shadow_entry_time_str)
                divergence_secs = (datetime.now(timezone.utc) - shadow_dt).total_seconds()
                from app.core import metrics

                metrics.shadow_live_entry_divergence_seconds.labels(symbol=symbol).observe(divergence_secs)

            if shadow_entry_price_str:
                shadow_entry_price = float(shadow_entry_price_str)
                if shadow_entry_price > 0:
                    diff_bps = ((float(fill_price) - shadow_entry_price) / shadow_entry_price) * 10000
                    if signal.direction == "SHORT":
                        diff_bps = -diff_bps  # Positive means better fill for short
                    from app.core import metrics

                    metrics.shadow_live_slippage_bps.labels(symbol=symbol, side=signal.direction).observe(diff_bps)
    except Exception as e:
        logger.debug("Failed to calculate shadow divergence for %s: %s", symbol, e)

    # Record trade
    if trade_store:
        # Capture AI confidence from analyst result (if available)
        ai_conf = None
        if 'analyst_result' in dir() and analyst_result is not None:
            ai_conf = analyst_result.ai_confidence

        await trade_store.record_entry(
            symbol=symbol,
            side=signal.direction,
            amount=signal.amount,
            entry_price=fill_price,
            regime=signal.regime.value,
            ai_confidence=ai_conf,
            risk_profile_json=signal.risk_profile.to_json(),
            trace_id=signal.trace_id,
            cvd_slope=signal.cvd_slope,
            spread_bps=signal.spread_bps,
            session_mult=signal.session_mult,
            regime_encoded=signal.regime_encoded,
            atr_pct=signal.atr_pct,
            vol_factor=signal.vol_factor,
        )

    logger.info(
        "executed %s %s @ %s (score=%.1f, regime=%s)",
        symbol,
        signal.direction,
        fill_price,
        signal.score,
        signal.regime.value,
    )

    # Prometheus: count opened position
    from app.core import metrics
    metrics.positions_opened.labels(symbol=symbol, side=signal.direction).inc()
    metrics.funnel_orders_placed.inc()


async def _read_universe(redis: Any) -> list[str] | None:
    """Read universe symbols from DynamicUniverseScanner Redis key."""
    try:
        import json as _json

        raw = await redis.get("system:universe:symbols")
        if raw:
            data = _json.loads(raw)
            symbols = data.get("symbols")
            if symbols and isinstance(symbols, list):
                return symbols
    except Exception:
        logger.debug("live: failed to read universe from Redis")
    return None


def _start_ingestor(
    settings: Any,
    redis: Any,
    consumer: MarketConsumer,
    initial_symbols: list[str],
) -> tuple[MarketDataIngestor, asyncio.Task]:
    """Create ingestor + sync loop. Returns (ingestor, task)."""
    ingestor = MarketDataIngestor(
        redis_client=redis,
        symbols=initial_symbols,
        poll_interval_s=30,
        api_key=settings.bybit_api_key or "",
        api_secret=settings.bybit_api_secret or "",
        testnet=settings.bybit_testnet,
    )

    async def _sync_loop() -> None:
        task = asyncio.create_task(ingestor.start())
        try:
            while True:
                await asyncio.sleep(31)
                ingestor.update_consumer(consumer)
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return ingestor, asyncio.create_task(_sync_loop(), name="live-ingestor")


async def _universe_refresh_loop(redis: Any, ingestor: MarketDataIngestor, interval_s: int = 14400) -> None:
    """Periodically refresh symbol list from DynamicUniverseScanner."""
    while True:
        await asyncio.sleep(interval_s)
        new_symbols = await _read_universe(redis)
        if new_symbols:
            ingestor.update_symbols(new_symbols)


async def _position_exit_loop(
    bybit: Any,
    position_store: PositionStore,
    trade_store: TradeStore | None,
    alert_service: AlertService | None,
    interval_s: int = 15,
    trade_memory: Any = None,
    redis: Any = None,
) -> None:
    """Poll Bybit positions, detect closures, send TP/SL alerts."""
    if not bybit or not alert_service:
        return

    from app.bot.utils.formatters import (
        format_breakeven_alert,
        format_sl_alert,
        format_tp_alert,
    )

    while True:
        await asyncio.sleep(interval_s)
        try:
            # Get current positions from Bybit
            exchange_positions = await bybit.fetch_positions() or []
            exchange_map: dict[str, dict] = {}
            for p in exchange_positions:
                sym = (p.get("symbol") or "").replace("/", "")
                side_raw = p.get("side", "")  # "buy"/"sell"
                exchange_map[f"{sym}:{side_raw}"] = p

            # Get positions from Redis
            internal = await position_store.list_all()
            for pos in internal:
                symbol = pos.get("symbol", "")
                side = pos.get("side", "LONG")
                api_side = "buy" if side == "LONG" else "sell"
                ccxt_sym = symbol.replace("/", "")
                key = f"{ccxt_sym}:{api_side}"

                if key in exchange_map:
                    continue  # still open

                # Position closed on exchange — determine TP/SL/breakeven
                entry_price = Decimal(str(pos.get("entry_price", 0)))
                amount = Decimal(str(pos.get("amount", 0)))

                # Check if exit price was already stored by APM (most reliable — no API delay)
                exit_price = Decimal(str(pos.get("exit_price", 0)))
                exit_reason = pos.get("exit_reason", "unknown")

                # Fallback: fetch from Bybit closed PnL records
                net_pnl = None
                if exit_price <= 0:
                    for attempt, delay in enumerate([(1.5, "1st"), (3.0, "2nd"), (5.0, "3rd")]):
                        await asyncio.sleep(delay[0])
                        try:
                            if hasattr(bybit, "get_closed_pnl"):
                                pnl_result = await bybit.get_closed_pnl(symbol=symbol, limit=5)
                                pnl_records = pnl_result.get("closed_pnl", [])
                                if pnl_records:
                                    last_record = pnl_records[0]
                                    exit_price = Decimal(str(last_record.get("avgExitPrice", last_record.get("avgPrice", 0))))
                                    net_pnl = Decimal(str(last_record.get("closedPnl", 0)))
                                    if exit_price > 0:
                                        if net_pnl > 0:
                                            exit_reason = "tp"
                                        elif net_pnl < 0:
                                            exit_reason = "sl"
                                        break
                        except Exception as e:
                            logger.debug("get_closed_pnl failed for %s on attempt %d: %s", symbol, attempt + 1, e)

                        # Fallback: try fetch_closed_orders
                        if exit_price <= 0:
                            try:
                                orders = (
                                    await bybit.fetch_closed_orders(symbol) if hasattr(bybit, "fetch_closed_orders") else []
                                )
                                if orders:
                                    last = orders[0]
                                    exit_price = Decimal(str(last.get("average", last.get("price", 0))))
                            except Exception:
                                pass

                        # Fallback: try fetch_my_trades for actual fill price
                        if exit_price <= 0:
                            try:
                                trades = (
                                    await bybit.fetch_my_trades(symbol, limit=5) if hasattr(bybit, "fetch_my_trades") else []
                                )
                                if trades:
                                    last_trade = trades[-1]  # most recent trade
                                    exit_price = Decimal(str(last_trade.get("price", 0)))
                            except Exception:
                                pass

                        if exit_price > 0:
                            break

                if exit_price <= 0:
                    # Fallback: use entry_price as exit_price to close the loop
                    exit_price = entry_price
                    exit_reason = "closed_unknown"
                    logger.error(
                        "exit_price STILL unknown for %s after 3 retries — using entry_price fallback", symbol
                    )

                # Calculate Net PnL (including fees)
                if net_pnl is not None:
                    pnl = net_pnl
                else:
                    if side == "LONG":
                        pnl = (exit_price - entry_price) * amount
                    else:
                        pnl = (entry_price - exit_price) * amount

                    # Deduct estimated Bybit Taker Fees (0.06% average per side)
                    fee_pct = Decimal("0.0006")
                    entry_fee = entry_price * amount * fee_pct
                    exit_fee = exit_price * amount * fee_pct
                    pnl -= (entry_fee + exit_fee)

                entry_value = entry_price * amount
                pnl_pct = (pnl / entry_value * 100) if entry_value > 0 else 0

                # Determine exit reason by price comparison
                if pnl > 0:
                    exit_reason = "tp"
                    msg = format_tp_alert(symbol, side, entry_price, exit_price, pnl, pnl_pct)
                elif pnl < -0.15:
                    exit_reason = "sl"
                    msg = format_sl_alert(symbol, side, entry_price, exit_price, pnl, pnl_pct)
                else:
                    exit_reason = "breakeven"
                    msg = format_breakeven_alert(symbol, side, entry_price, exit_price, pnl, pnl_pct)

                # ─── DEDUP GUARD ─────────────────────────────────────────────────
                # The exit loop polls every 15s. Without this guard, the same
                # closed position fires the alert on every cycle until remove() finishes.
                dedup_key = f"karsa:exit_alerted:{symbol}:{side}"
                already_sent = False
                if redis:
                    try:
                        already_sent = await redis.get(dedup_key) is not None
                    except Exception:
                        pass

                if already_sent:
                    # Alert already sent for this exit — just clean up Redis position
                    try:
                        await position_store.remove(symbol, side)
                    except Exception:
                        pass
                    continue

                if redis:
                    try:
                        # Mark as sent for 60 seconds — gives position_store.remove() time to fire
                        await redis.setex(dedup_key, 60, "1")
                    except Exception:
                        pass
                # ─── END DEDUP GUARD ──────────────────────────────────────────────

                try:
                    await alert_service.send(msg)
                except Exception:
                    logger.warning("failed to send exit alert for %s", symbol)

                # Record in trade store
                if trade_store:
                    try:
                        await trade_store.close_trade(
                            symbol,
                            Decimal(str(exit_price)),
                            Decimal(str(pnl)),
                            exit_reason,
                        )
                    except Exception:
                        logger.warning("failed to record exit trade for %s", symbol)

                # Remove from Redis
                try:
                    await position_store.remove(symbol, side)
                except Exception:
                    pass

                # Record in TradeMemory for Anti-Whipsaw Cooldown tracking
                if trade_memory:
                    try:
                        hold_min = 0
                        entry_time_str = pos.get("entry_time")
                        if entry_time_str:
                            try:
                                if isinstance(entry_time_str, str):
                                    from datetime import datetime
                                    et = datetime.fromisoformat(entry_time_str)
                                    if et.tzinfo is None:
                                        et = et.replace(tzinfo=timezone.utc)
                                    now = datetime.now(timezone.utc)
                                    hold_min = int((now - et).total_seconds() / 60)
                            except Exception:
                                pass

                        regime = pos.get("entry_regime", "UNKNOWN")
                        conf = float(pos.get("entry_confidence", 0.0))

                        await trade_memory.store(
                            symbol=symbol,
                            pnl_pct=Decimal(str(pnl_pct)),
                            hold_duration_min=hold_min,
                            regime=regime,
                            exit_reason=exit_reason,
                            entry_confidence=Decimal(str(conf)),
                        )
                    except Exception as e:
                        logger.warning("failed to store trade memory in live_loop for %s: %s", symbol, e)

                logger.info(
                    "exit detected: %s %s pnl=%.2f reason=%s",
                    symbol,
                    side,
                    pnl,
                    exit_reason,
                )

                # Prometheus: count closed position
                from app.core import metrics

                metrics.positions_closed.labels(symbol=symbol, side=side, exit_reason=exit_reason).inc()
                metrics.funnel_positions_closed.inc()

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("position_exit_loop error")


async def _status_update_loop(
    bybit: Any,
    position_store: PositionStore,
    alert_service: AlertService | None,
    redis: Any,
    interval_s: int = 900,
) -> None:
    """Send 15-minute position status update to Telegram."""
    if not bybit or not alert_service:
        return

    while True:
        await asyncio.sleep(interval_s)
        try:
            internal = await position_store.list_all()
            if not internal:
                continue

            # Fetch live prices from Redis instead of Bybit API
            price_map: dict[str, float] = {}
            for pos in internal:
                sym = pos.get("symbol", "")
                if sym:
                    import json
                    raw = await redis.get(f"global:state:{sym}")
                    if raw:
                        try:
                            state = json.loads(raw)
                            if state and state.get("best_bid") and state.get("best_ask"):
                                bid = float(str(state["best_bid"]))
                                ask = float(str(state["best_ask"]))
                                price_map[sym.replace("/", "")] = (bid + ask) / 2
                        except Exception:
                            pass

            try:
                bybit_positions = await bybit.fetch_positions() if hasattr(bybit, "fetch_positions") else []
            except Exception as e:
                logger.error(f"status_update: Bybit fetch failed: {e}")
                bybit_positions = []

            # Fallback to internal if Bybit is completely down, but prefer Bybit data
            active_count = len(bybit_positions) if bybit_positions else len(internal)
            lines = [f"⏰ <b>15min Status — {active_count} open</b>", ""]
            total_pnl = 0.0

            if bybit_positions:
                for pos in bybit_positions:
                    symbol = pos.get("symbol", "")
                    side = pos.get("side", "LONG").upper()
                    pnl = Decimal(str(pos.get("unrealized_pnl", 0)))
                    total_pnl += pnl

                    # Compute ROE% if we have leverage, otherwise raw asset %
                    leverage = Decimal(str(pos.get("leverage", 1)))
                    entry = Decimal(str(pos.get("entry_price", 0)))
                    mark = Decimal(str(pos.get("markPrice", pos.get("current_price", entry))))

                    if entry > 0 and mark > 0 and leverage > 0:
                        raw_pct = ((mark - entry) / entry) if side in ("BUY", "LONG") else ((entry - mark) / entry)
                        pnl_pct = raw_pct * leverage * 100
                    else:
                        pnl_pct = 0.0

                    arrow = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
                    lines.append(f"{arrow} <b>{symbol}</b> {side} | ${pnl:+.2f} ({pnl_pct:+.2f}%)")
            else:
                for pos in internal:
                    symbol = pos.get("symbol", "")
                    side = pos.get("side", "LONG")
                    entry_price = Decimal(str(pos.get("entry_price", 0)))
                    amount = Decimal(str(pos.get("amount", 0)))
                    ccxt_sym = symbol.replace("/", "")
                    live_price = price_map.get(ccxt_sym, entry_price)

                    if side == "LONG":
                        pnl = (live_price - entry_price) * amount
                    else:
                        pnl = (entry_price - live_price) * amount

                    if side == "LONG":
                        pnl_pct = ((live_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                    else:
                        pnl_pct = ((entry_price - live_price) / entry_price * 100) if entry_price > 0 else 0
                    total_pnl += pnl

                    arrow = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
                    lines.append(f"{arrow} <b>{symbol}</b> {side} | ${pnl:+.2f} ({pnl_pct:+.2f}%)")

            lines.append(f"\n⚖️ <b>Unrealized: ${total_pnl:+.2f}</b>")

            await alert_service.send("\n".join(lines))

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("status_update_loop error")


async def _redis_health_check_loop(
    redis: Any,
    alert_service: AlertService | None,
    interval_s: int = 30,
) -> None:
    """Scheduled Redis health check — runs every `interval_s` seconds.

    Checks:
    1. Redis ping — alert and log on failure.
    2. All karsa:position:* keys contain valid JSON with canonical side (LONG/SHORT).
    3. Critical fields (symbol, side, entry_price, amount) are non-empty.
    4. Purges malformed keys that would cause APM to crash or loop.
    """
    import json as _json

    _CRITICAL_FIELDS = ["symbol", "side", "entry_price", "amount"]
    _consecutive_redis_failures = 0

    while True:
        await asyncio.sleep(interval_s)
        try:
            # 1. Ping check
            try:
                await redis.ping()
                if _consecutive_redis_failures > 0:
                    logger.info(
                        "redis_health: Redis recovered after %d failures",
                        _consecutive_redis_failures,
                    )
                    if alert_service:
                        try:
                            await alert_service.send("✅ Redis recovered — connection restored.")
                        except Exception:
                            pass
                _consecutive_redis_failures = 0
            except Exception as ping_err:
                _consecutive_redis_failures += 1
                logger.error(
                    "redis_health: PING FAILED (failure #%d): %s",
                    _consecutive_redis_failures,
                    ping_err,
                )
                if _consecutive_redis_failures in (1, 5, 10):
                    if alert_service:
                        try:
                            await alert_service.send(
                                f"🔴 Redis ping FAILED (#{_consecutive_redis_failures}). "
                                "APM cannot read positions — check Redis connectivity!"
                            )
                        except Exception:
                            pass
                continue  # skip key audit if Redis is down

            # 2. Audit position keys
            try:
                keys = await redis.keys("karsa:position:*")
            except Exception:
                continue

            malformed = 0
            for key in keys:
                key_str = key if isinstance(key, str) else key.decode()
                try:
                    raw = await redis.get(key_str)
                    if not raw:
                        await redis.delete(key_str)
                        malformed += 1
                        logger.warning("redis_health: purged empty key %s", key_str)
                        continue

                    pos = _json.loads(raw)

                    # Check canonical side
                    side = pos.get("side", "")
                    if side not in ("LONG", "SHORT"):
                        logger.warning(
                            "redis_health: key %s has non-canonical side=%r — normalising",
                            key_str,
                            side,
                        )
                        pos["side"] = "LONG" if side in ("buy", "Buy") else "SHORT"
                        await redis.set(key_str, _json.dumps(pos))

                    # Check critical fields
                    missing = [f for f in _CRITICAL_FIELDS if not pos.get(f)]
                    if missing:
                        logger.warning(
                            "redis_health: key %s missing fields %s (will be repaired by APM health check)",
                            key_str,
                            missing,
                        )

                except _json.JSONDecodeError:
                    logger.error("redis_health: purging corrupt (non-JSON) key %s", key_str)
                    await redis.delete(key_str)
                    malformed += 1
                except Exception as key_err:
                    logger.warning("redis_health: error inspecting key %s: %s", key_str, key_err)

            if malformed:
                logger.warning("redis_health: purged %d malformed position keys", malformed)
                if alert_service:
                    try:
                        await alert_service.send(
                            f"⚠️ Redis health: purged {malformed} malformed position key(s). " "Check logs for details."
                        )
                    except Exception:
                        pass

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("redis_health: unexpected error in health check loop")


async def _ranking_refresh_loop(
    redis: Any,
    trade_store: Any,
    interval_s: int = 3600,
) -> None:
    """Periodically recompute strategy ranking from trade history.

    Reads closed trades from TradeStore, computes metrics via MetricsEngine,
    evaluates via RankingEngine, and caches the decision in Redis at
    ``karsa:ranking:decision`` and ``karsa:ranking:details``.
    """
    from app.research.metrics_engine import MetricsEngine
    from app.research.ranking_engine import RankingEngine, PromotionPolicy

    policy = PromotionPolicy()
    while True:
        await asyncio.sleep(interval_s)
        try:
            if trade_store is None:
                continue
            trades = await trade_store.get_recent_trades(limit=200)  # type: ignore[attr-defined]
            if not trades or len(trades) < 10:
                logger.debug("Ranking refresh: insufficient trades (%d)", len(trades) if trades else 0)
                continue

            trade_dicts = [
                {"pnl_pct": float(t.get("realized_pnl", 0)), "entry_time": t.get("entry_time", "")}
                for t in trades
            ]
            metrics_result = MetricsEngine.compute(trade_dicts)
            ranking = RankingEngine.evaluate(metrics_result, stats=None, policy=policy)

            await redis.set("karsa:ranking:decision", ranking["decision"])
            import json as _json
            await redis.set("karsa:ranking:details", _json.dumps(ranking))
            logger.info("Ranking refresh: %s (trades=%d)", ranking["decision"], len(trades))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Ranking refresh error: %s", e)


async def _gate_calibration_loop(
    redis: Any,
    trade_store: Any,
    interval_s: int = 3600,
) -> None:
    """Background loop: calibrate gate threshold from historical EV.

    Reads winning trades, computes median EV, sets threshold = median_EV * 0.8.
    Writes to Redis key ``karsa:gate:dynamic_threshold``.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            if trade_store is None:
                continue
            trades = await trade_store.get_recent_trades(limit=200)
            if not trades or len(trades) < 20:
                continue

            # Extract EVs from winning trades
            winning_evs = []
            for t in trades:
                pnl = float(t.get("realized_pnl", 0))
                if pnl > 0:
                    winning_evs.append(pnl)

            if len(winning_evs) < 5:
                logger.debug("Gate calibration: too few winning trades (%d)", len(winning_evs))
                continue

            import statistics
            median_ev = statistics.median(winning_evs)
            # Threshold = median EV * 0.8 (ensure we only take high-EV signals)
            dynamic_threshold = max(50.0, min(90.0, median_ev * 0.8))

            import json as _json
            await redis.set("karsa:gate:dynamic_threshold", _json.dumps({
                "threshold": round(dynamic_threshold, 2),
                "median_ev": round(median_ev, 4),
                "winning_trades": len(winning_evs),
                "total_trades": len(trades),
            }))
            logger.info(
                "Gate calibration: threshold=%.1f (median_ev=%.4f, wins=%d/%d)",
                dynamic_threshold, median_ev, len(winning_evs), len(trades),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Gate calibration error: %s", e)


async def _elo_refresh_loop(
    redis: Any,
    trade_store: Any,
    interval_s: int = 300,
) -> None:
    """Background loop: update strategy ELO ratings from closed trades.

    Reads recent closed trades, updates ELO for each strategy (regime:direction).
    Writes per-strategy ELO to Redis key ``karsa:elo:{strategy}``.
    """
    K_FACTOR = 32.0
    BASELINE_ELO = 1500.0
    processed_key = "karsa:elo:last_processed_id"

    while True:
        await asyncio.sleep(interval_s)
        try:
            if trade_store is None:
                continue

            # Get last processed trade ID to avoid reprocessing
            last_id = None
            try:
                raw = await redis.get(processed_key)
                if raw:
                    last_id = raw.decode() if isinstance(raw, bytes) else str(raw)
            except Exception:
                pass

            trades = await trade_store.get_recent_trades(limit=50)
            if not trades:
                continue

            import json as _json

            for trade in trades:
                trade_id = trade.get("id", "")
                if last_id and trade_id <= last_id:
                    continue

                regime = trade.get("regime", "UNKNOWN")
                direction = trade.get("side", "LONG")
                pnl_pct = float(trade.get("realized_pnl", 0))
                strategy_key = f"{regime}:{direction}"

                # Read current ELO
                elo_raw = await redis.get(f"karsa:elo:{strategy_key}")
                current_elo = BASELINE_ELO
                wins = 0
                losses = 0
                if elo_raw:
                    try:
                        elo_data = _json.loads(elo_raw)
                        current_elo = elo_data.get("elo", BASELINE_ELO)
                        wins = elo_data.get("wins", 0)
                        losses = elo_data.get("losses", 0)
                    except Exception:
                        pass

                # Update ELO: win = 1.0, loss = 0.0
                score = 1.0 if pnl_pct > 0 else 0.0
                new_elo = current_elo + K_FACTOR * (score - 0.5)  # Simple ELO update
                if pnl_pct > 0:
                    wins += 1
                else:
                    losses += 1

                await redis.set(f"karsa:elo:{strategy_key}", _json.dumps({
                    "elo": round(new_elo, 2),
                    "wins": wins,
                    "losses": losses,
                    "win_rate": round(wins / max(1, wins + losses), 4),
                    "last_trade_id": trade_id,
                }))

                if abs(new_elo - current_elo) > 5:
                    logger.info(
                        "ELO update: %s %.0f → %.0f (trade=%s, pnl=%.2f%%)",
                        strategy_key, current_elo, new_elo, trade_id, pnl_pct,
                    )

                last_id = trade_id

            # Persist last processed ID
            if last_id:
                await redis.set(processed_key, last_id)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("ELO refresh error: %s", e)


async def _vol_surface_loop(
    redis: Any,
    ohlcv_fetcher: Any,
    interval_s: int = 1800,
) -> None:
    """Background loop: build and publish volatility surface every 30 minutes.

    Fetches BTC/ETH 1H candles, computes realized vol at multiple timeframes,
    and writes to Redis keys ``karsa:vol_surface:*``.
    """
    import numpy as np
    from app.risk.volatility_surface import VolatilitySurface

    surface = VolatilitySurface(redis_client=redis)

    while True:
        await asyncio.sleep(interval_s)
        try:
            btc_closes: dict[str, np.ndarray] = {}
            eth_closes: dict[str, np.ndarray] = {}

            # Fetch candles for each timeframe
            for tf, limit in [("1h", 25), ("4h", 50), ("1d", 35)]:
                try:
                    btc_raw = await ohlcv_fetcher.fetch("BTC/USDT", tf, limit=limit)
                    if btc_raw and len(btc_raw) > 5:
                        btc_closes[tf] = np.array([c[4] for c in btc_raw], dtype=float)
                except Exception:
                    pass

                try:
                    eth_raw = await ohlcv_fetcher.fetch("ETH/USDT", tf, limit=limit)
                    if eth_raw and len(eth_raw) > 5:
                        eth_closes[tf] = np.array([c[4] for c in eth_raw], dtype=float)
                except Exception:
                    pass

            if btc_closes or eth_closes:
                surface.build_surface(btc_closes, eth_closes)
                await surface.publish_to_redis()
                logger.info(
                    "VolSurface: published (btc_vol=%.1f%%, eth_vol=%.1f%%)",
                    surface._surface.get("btc", {}).get("composite", 0) * 100,
                    surface._surface.get("eth", {}).get("composite", 0) * 100,
                )
            else:
                logger.debug("VolSurface: insufficient candle data, skipping")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("VolSurface error: %s", e)


async def _balance_refresh_loop(
    bybit: Any,
    engine: DecisionEngine,
    interval_s: int = 60,
) -> None:
    """Fetch wallet balance every 60s and update engine for position sizing."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            bal = await bybit.fetch_balance()
            free = Decimal(str(bal.get("free", 0)))
            if free > 0:
                engine.set_wallet_balance(free)
                logger.debug("balance refreshed: %s USDT", free)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("balance refresh failed")


async def _daily_summary_loop(
    redis: Any,
    db_engine: Any,
    alert_service: Any,
    shutdown_event: asyncio.Event,
) -> None:
    """Send daily summary at midnight UTC.

    Sleeps until next midnight, generates summary via DailySummaryService,
    and sends via AlertService. Respects user's daily_summary preference.
    """
    from app.bot.daily_summary import DailySummaryService

    while not shutdown_event.is_set():
        try:
            now = datetime.now(timezone.utc)
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait_seconds = (tomorrow - now).total_seconds()
            logger.info(
                "daily_summary: sleeping %.0fs until midnight UTC", wait_seconds
            )
            await asyncio.sleep(wait_seconds)

            if shutdown_event.is_set():
                break

            # Check if daily summary is enabled
            service = DailySummaryService(redis_client=redis, db_engine=db_engine)
            if not await service.is_enabled():
                logger.info("daily_summary: disabled by user settings, skipping")
                continue

            # Generate and send summary for the completed day (yesterday)
            completed_date = datetime.now(timezone.utc) - timedelta(days=1)
            message = await service.generate_summary(completed_date)
            if alert_service:
                await alert_service.send(message)
                logger.info("daily_summary: sent successfully for %s", completed_date.strftime("%Y-%m-%d"))
            else:
                logger.warning("daily_summary: no alert_service, cannot send")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("daily_summary_loop error: %s", e)
            # Sleep 60s before retrying to avoid tight error loops
            await asyncio.sleep(60)


async def main() -> None:  # noqa: PLR0915
    _configure_logging()
    settings = get_settings()

    if prom_port := __import__("os").getenv("PROMETHEUS_PORT"):
        from prometheus_client import start_http_server

        start_http_server(int(prom_port))

    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("karsa-live starting")

    await startup(settings)
    redis = get_redis()
    pool = get_pool()

    emitter = TelemetryEmitter(redis, "live")
    await emitter.start()

    # Build components
    import ccxt.async_support as ccxt

    from app.alpha.multi_tf import MultiTFFilter
    from app.alpha.trade_memory import TradeMemory
    from app.alpha.market_analyzer import MarketAnalyzer

    classifier = RegimeClassifier(redis_client=redis)
    analyzer = MarketAnalyzer(redis_client=redis)
    router = StrategyRouter()
    risk_gate = DynamicRiskGate()
    trade_memory = TradeMemory(redis)

    # Init Fetcher & Multi-TF
    exchange = ccxt.bybit({"enableRateLimit": True})
    ohlcv_fetcher = OHLCVFetcher(exchange)
    multi_tf = MultiTFFilter(ohlcv_fetcher)

    engine = DecisionEngine(
        analyzer,
        router,
        risk_gate,
        trade_memory=trade_memory,
        redis_client=redis,
        multi_tf=multi_tf,
    )

    # AI Signal Ranker — batch ranks top signals instead of individual veto
    ai_ranker = None
    try:
        from app.core.ai_client import AIClient
        ai_client = AIClient(
            router_url=settings.nine_router_base_url,
            auth_token=settings.nine_router_auth_token,
            model=settings.nine_router_model,
        )
        ai_ranker = AISignalRanker(ai_client)
        logger.info("AISignalRanker initialized")
    except Exception as e:
        logger.warning(f"AISignalRanker init failed: {e}")

    position_store = PositionStore(redis)

    # Build execution components
    from app.execution.sor import SmartOrderRouter

    bybit = None
    try:
        from app.execution.bybit_client import BybitClient as _BybitClient

        bybit = _BybitClient()
        await bybit.connect()
        from app.core import metrics

        metrics.vpn_status.set(1)
        metrics.bybit_status.set(1)
    except Exception:
        logger.warning("BybitClient unavailable — live execution disabled")

    # Telegram alerts for live trades
    alert_service: AlertService | None = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        try:
            from telegram import Bot as _TGBot

            _tg_bot = _TGBot(token=settings.telegram_bot_token)
            alert_service = AlertService(settings.telegram_chat_id)
            alert_service.register_bot(_tg_bot)
            logger.info("telegram alerts enabled")
        except Exception:
            logger.warning("telegram bot init failed — alerts disabled")

    executor = SmartOrderRouter(bybit, alert_service=alert_service) if bybit is not None else None

    # APM — manages open positions: breakeven, trailing, time exit, regime kill switch
    from app.execution.position_manager import ActivePositionManager

    apm = (
        ActivePositionManager(
            bybit_client=bybit,
            position_store=position_store,
            regime_classifier=classifier,
            alert_service=alert_service,
            trade_memory=trade_memory,
            redis_client=redis,
            logger_=logger,
        )
        if bybit is not None
        else None
    )

    from app.risk.portfolio_risk_manager import PortfolioRiskManager

    trade_store = None
    try:
        from app.core.database import DatabaseEngine
        from app.core.trade_store import TradeStore as _TradeStore

        db_engine = DatabaseEngine()
        await db_engine.connect(settings.postgres_url)
        trade_store = _TradeStore(db_engine)
        engine._trade_store = trade_store

        # Sync user settings from DB to Redis on startup (survives Redis restarts)
        try:
            from app.core.settings_store import SettingsStore

            _settings_store = SettingsStore(db_engine)
            # Use owner user_id=0 as default (single-user bot)
            await _settings_store.sync_db_to_redis(redis, user_id=0)
        except Exception as sync_exc:
            logger.warning("settings_db_sync_failed: %s", sync_exc)
    except Exception:
        logger.warning("TradeStore unavailable — trades will not be recorded")

    class _SectorMapping:
        """Wrapper to adapt sync get_sector to async interface expected by PRM."""

        async def get_sector(self, symbol: str) -> str:
            from app.data.sector_mapping import get_sector

            return get_sector(symbol)

    risk_manager = PortfolioRiskManager(
        redis_client=redis,
        position_store=position_store,
        trade_store=trade_store,
        sector_mapping=_SectorMapping(),
        bybit_client=bybit,
    )

    # Hybrid Intelligence System — statistical guardrails + AI evaluation
    hybrid_engine: HybridDecisionEngine | None = None
    try:
        from app.ai.nine_router_service import NineRouterService

        stat_engine = StatisticalFeatureEngine(redis_client=redis)
        nine_router = NineRouterService()
        hybrid_engine = HybridDecisionEngine(
            statistical_engine=stat_engine,
            ai_service=nine_router,
        )
        logger.info("HybridDecisionEngine initialized")
    except Exception as e:
        logger.warning(f"HybridDecisionEngine init failed: {e}")
        hybrid_engine = None

    # Actor Model: single execution worker to serialize trade execution.
    # Prevents max_positions race condition where multiple signals pass the
    # position count check simultaneously before any of them write to Redis.
    WORKER_COUNT = int(__import__("os").getenv("KARSA_WORKER_COUNT", "1"))
    signal_queues = [asyncio.Queue(maxsize=100) for _ in range(WORKER_COUNT)]

    async def _signal_worker(worker_id: int, q: asyncio.Queue) -> None:
        import time

        from app.core import metrics

        while not shutdown_event.is_set():
            try:
                metrics.pipeline_queue_depth.labels(worker_id=str(worker_id)).set(q.qsize())
                try:
                    queued_ts, sym, sig = await asyncio.wait_for(q.get(), timeout=1.0)
                except TimeoutError:
                    continue

                wait_time = time.time() - queued_ts
                metrics.pipeline_queue_wait_seconds.labels(worker_id=str(worker_id)).observe(wait_time)
                metrics.pipeline_worker_utilization.labels(worker_id=str(worker_id)).set(1.0)
                try:
                    await _on_signal_live(sym, sig, position_store, executor, risk_manager, trade_store, engine, hybrid_engine, ai_ranker)
                except Exception as e:
                    logger.error(f"Worker {worker_id} failed on {sym}: {e}", exc_info=True)
                finally:
                    q.task_done()
                    metrics.pipeline_worker_utilization.labels(worker_id=str(worker_id)).set(0.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} crashed: {e}")
                await asyncio.sleep(1.0)

    worker_tasks = [asyncio.create_task(_signal_worker(i, signal_queues[i]), name=f"live-worker-{i}") for i in range(WORKER_COUNT)]

    async def on_signal(symbol: str, sig: TradeSignal) -> None:
        emitter.record_signal()
        worker_idx = hash(symbol) % WORKER_COUNT
        q = signal_queues[worker_idx]
        if q.full():
            from app.core import metrics
            metrics.decision_queue_full_total.labels(symbol=symbol).inc()
            metrics.decision_rejected_total.labels(symbol=symbol, reason="QUEUE_FULL").inc()
            logger.warning(f"Worker queue {worker_idx} full, REJECTING {symbol}")

            from app.core.observability import ObservabilityLogger
            ObservabilityLogger.log_decision_trace(
                strategy=sig.regime.value,
                confidence=sig.score,
                regime=sig.regime.value,
                evidence=[],
                entry_decision="FLAT",
                exit_decision=None,
                decision_id=f"rej-{int(time.time()*1000)}",
                symbol=symbol,
            )
            return
        await q.put((time.time(), symbol, sig))

    background_tasks = set()

    async def on_candle(symbol: str, candle: list) -> None:
        history = consumer._buffer.as_list(symbol)
        if history:
            task = asyncio.create_task(analyzer.update_on_candle_close(symbol, history))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

            # Statistical Feature Engine: compute features on each candle close
            if hybrid_engine is not None and len(history) >= 50:
                async def _compute_features(sym: str, candles: list) -> None:
                    try:
                        import pandas as _pd

                        ohlcv_df = _pd.DataFrame(
                            candles,
                            columns=["timestamp", "open", "high", "low", "close", "volume"],
                        )
                        # Fetch BTC data for beta/correlation
                        btc_ohlcv_df = ohlcv_df  # fallback
                        try:
                            btc_raw = await ohlcv_fetcher.fetch("BTC/USDT", "1h", limit=len(candles))
                            if btc_raw and len(btc_raw) >= 50:
                                btc_ohlcv_df = _pd.DataFrame(
                                    btc_raw,
                                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                                )
                        except Exception:
                            pass

                        funding = 0.0
                        try:
                            fr_raw = await redis.get(f"karsa:funding:{sym}")
                            if fr_raw:
                                funding = float(fr_raw)
                        except Exception:
                            pass

                        stat_engine = hybrid_engine._stat_engine
                        await stat_engine.calculate_features(
                            symbol=sym,
                            ohlcv=ohlcv_df,
                            btc_ohlcv=btc_ohlcv_df,
                            funding_rate=funding,
                        )
                    except Exception as e:
                        logger.debug(f"Feature calculation failed for {sym}: {e}")

                feature_task = asyncio.create_task(_compute_features(symbol, history))
                background_tasks.add(feature_task)
                feature_task.add_done_callback(background_tasks.discard)

    consumer = MarketConsumer(redis, engine, on_signal, on_candle)

    # Read dynamic universe from Redis, fall back to static config
    universe_symbols = await _read_universe(redis)
    initial_symbols = (
        universe_symbols
        if universe_symbols
        else (settings.watchlist.split(",") if settings.watchlist else settings.symbols)
    )
    logger.info(f"live universe: {len(initial_symbols)} symbols from {'redis' if universe_symbols else 'config'}")

    # Pre-fill CandleBuffer concurrently with historical candles (max 5 concurrent requests)
    try:
        # exchange and ohlcv_fetcher already initialized above for MultiTFFilter
        prefill_sem = asyncio.Semaphore(5)

        async def _prefill_symbol(sym: str) -> None:
            async with prefill_sem:
                try:
                    candles = await ohlcv_fetcher.fetch(sym, "1h", 60)
                    if candles:
                        for c in candles:
                            consumer._buffer.append(sym, c)
                    logger.info(f"live pre-filled buffer for {sym} with {len(candles or [])} candles")
                except Exception as e:
                    logger.warning(f"failed to pre-fill {sym}: {e}")

        await asyncio.gather(*[_prefill_symbol(sym) for sym in initial_symbols])
    except Exception as e:
        logger.warning(f"OHLCVFetcher init failed — no candle pre-fill: {e}")

    # ─── BOOTSTRAP PHASE ─────────────────────────────────────────────────
    # Feed pre-filled candles to MarketAnalyzer so it can classify regimes
    # immediately, instead of waiting for new candles from Redis Pub/Sub.
    # This eliminates the 30-60min cold-start degradation window.
    bootstrap_ready_count = 0
    bootstrap_total = len(initial_symbols)
    try:
        for sym in initial_symbols:
            candles = consumer._buffer.as_list(sym)
            if candles and len(candles) >= 50:
                try:
                    await analyzer.update_on_candle_close(sym, candles)
                    bootstrap_ready_count += 1
                except Exception as e:
                    logger.debug(f"bootstrap: MarketAnalyzer update failed for {sym}: {e}")
            else:
                logger.debug(f"bootstrap: {sym} has {len(candles)} candles, need 50")
        bootstrap_pct = (bootstrap_ready_count / bootstrap_total * 100) if bootstrap_total > 0 else 0
        logger.info(
            f"bootstrap: {bootstrap_ready_count}/{bootstrap_total} symbols ready "
            f"({bootstrap_pct:.0f}%) — MarketAnalyzer seeded with historical candles"
        )
        if bootstrap_pct < 80:
            logger.warning(
                f"bootstrap: only {bootstrap_pct:.0f}% of universe ready — "
                f"signals may be degraded until more candles arrive"
            )
    except Exception as e:
        logger.warning(f"bootstrap: MarketAnalyzer seeding failed: {e}")

    # Startup state reconciliation — sync exchange positions with internal stores
    try:
        from app.core.state_reconciliation import StateReconciler

        bybit_client = None
        try:
            from app.execution.bybit_client import BybitClient as _BybitClient

            bybit_client = _BybitClient()
            await bybit_client.connect()
        except Exception:
            logger.warning("BybitClient unavailable — skipping reconciliation")

        if bybit_client is not None:
            reconciler = StateReconciler(
                bybit_client=bybit_client,
                position_store=position_store,
                trade_store=trade_store,
                db_engine=pool,
            )
            recon_results = await reconciler.reconcile()
            logger.info("reconciliation_complete: %s", recon_results)

            # Sync exchange positions to Redis — save any orphaned exchange
            # positions so max_positions count is accurate on restart
            try:
                exchange_positions = recon_results.get("exchange_positions_raw") or []
                if not exchange_positions:
                    exchange_positions = await bybit_client.fetch_positions() or []

                # Build set of exchange positions: symbol (ccxt format) + side
                exchange_map: dict[str, dict] = {}
                for p in exchange_positions:
                    sym = (p.get("symbol") or "").replace("/", "")
                    side = p.get("side", "")  # already "buy"/"sell" from fetch_positions
                    exchange_map[f"{sym}:{side}"] = p

                # Check which exchange positions are NOT in Redis
                existing_keys = await position_store.redis.keys("karsa:position:*")
                existing: set[str] = set()
                for key in existing_keys:
                    try:
                        raw = await position_store.redis.get(key if isinstance(key, str) else key.decode())
                        if raw:
                            pos = json.loads(raw)
                            p_sym = (pos.get("symbol") or "").replace("/", "")
                            p_side = pos.get("side", "")
                            existing.add(f"{p_sym}:{p_side}")
                    except Exception:
                        pass

                # Also clean stale keys (in Redis but NOT on exchange)
                stale_keys: list[str] = []
                for key in existing_keys:
                    key_str = key if isinstance(key, str) else key.decode()
                    raw = await position_store.redis.get(key_str)
                    if not raw:
                        stale_keys.append(key_str)
                        continue
                    try:
                        pos = json.loads(raw)
                        p_sym = (pos.get("symbol") or "").replace("/", "")
                        p_side = pos.get("side", "")
                        if f"{p_sym}:{p_side}" not in exchange_map:
                            stale_keys.append(key_str)
                    except Exception:
                        stale_keys.append(key_str)

                for key_str in stale_keys:
                    await position_store.redis.delete(key_str)
                if stale_keys:
                    logger.warning("stale_cleanup: removed %d stale Redis keys", len(stale_keys))

                # Save exchange positions missing from Redis
                synced = 0
                for ex_key, p in exchange_map.items():
                    if ex_key not in existing:
                        # Convert buy/sell back to LONG/SHORT for position_store
                        ccxt_sym = (p.get("symbol") or "").replace("/", "")
                        ccxt_sym_fmt = ccxt_sym[:-4] + "/" + ccxt_sym[-4:] if len(ccxt_sym) > 4 else ccxt_sym
                        side_long = "LONG" if p.get("side") == "buy" else "SHORT"
                        entry_price = Decimal(str(p.get("entry_price", 0)))
                        amount = Decimal(str(p.get("contracts", 0)))

                        # Compute regime/atr for APM time-exit and trailing
                        entry_regime = ""
                        atr_val = Decimal("0")
                        try:
                            import numpy as np

                            _candles = []
                            for _c in consumer._buffer.get(ccxt_sym_fmt) or []:
                                _candles.append(_c)
                            if len(_candles) >= 50:
                                _arr = np.array(_candles[-60:], dtype=np.float64)
                                entry_regime = classifier.classify(_arr).value
                                atr_val = engine._calculate_atr(_arr)
                        except Exception:
                            pass

                        initial_risk = Decimal("0")
                        try:
                            rp = risk_gate.get_profile(MarketRegime(entry_regime)) if entry_regime else None
                            if rp and atr_val > 0:
                                initial_risk = atr_val * rp.sl_atr_buffer
                        except Exception:
                            pass

                        await position_store.save(
                            symbol=ccxt_sym_fmt,
                            side=side_long,
                            entry_price=entry_price,
                            amount=amount,
                            atr=atr_val,
                            entry_confidence="0",
                            regime=entry_regime,
                        )
                        # Patch fields that save() doesn't write
                        key = f"karsa:position:{ccxt_sym_fmt}:{side_long}"
                        try:
                            raw = await position_store.redis.get(key)
                            if raw:
                                pos_data = json.loads(raw)
                                pos_data["entry_regime"] = entry_regime
                                pos_data["initial_risk_per_unit"] = str(initial_risk)
                                await position_store.redis.set(key, json.dumps(pos_data))
                        except Exception:
                            pass
                        synced += 1
                        logger.info(
                            "reconciled_position: saved %s %s regime=%s atr=%s",
                            ccxt_sym_fmt,
                            side_long,
                            entry_regime,
                            atr_val,
                        )

                if synced:
                    logger.warning(
                        "reconciled_position: synced %d exchange positions to Redis",
                        synced,
                    )
            except Exception:
                logger.exception("position_sync failed")
    except Exception:
        logger.exception("reconciliation_failed — continuing startup")

    # Market data ingestor — feeds orderbook/funding/OI to CHOP scorer
    ingestor, ingestor_task = _start_ingestor(settings, redis, consumer, initial_symbols)
    universe_task = asyncio.create_task(_universe_refresh_loop(redis, ingestor), name="live-universe")

    # Fetch balance once before starting consumer — prevents over-allocation on first signal
    if bybit:
        try:
            bal = await bybit.fetch_balance()
            free = Decimal(str(bal.get("free", 0)))
            if free > 0:
                engine.set_wallet_balance(free)
                logger.info("initial balance: %s USDT", free)
        except Exception:
            logger.warning("initial balance fetch failed — using fallback sizing")

    consumer_task = asyncio.create_task(consumer.start(), name="live-consumer")

    # Position exit monitor + 15-min status update + APM
    exit_task = asyncio.create_task(
        _position_exit_loop(bybit, position_store, trade_store, alert_service, trade_memory=trade_memory, redis=redis),
        name="live-exit-monitor",
    )
    status_task = asyncio.create_task(
        _status_update_loop(bybit, position_store, alert_service, redis),
        name="live-status-update",
    )
    apm_task = asyncio.create_task(apm.start_monitoring(), name="live-apm") if apm else None
    # APM health check: every 60s, detect + auto-repair positions with missing fields
    apm_health_task = (
        asyncio.create_task(apm.start_health_check_loop(interval_s=60), name="live-apm-health") if apm else None
    )
    # Redis health check: every 30s, verify connectivity and alert on degradation
    redis_health_task = asyncio.create_task(_redis_health_check_loop(redis, alert_service), name="live-redis-health")
    balance_task = asyncio.create_task(_balance_refresh_loop(bybit, engine), name="live-balance") if bybit else None
    wallet_metrics_task = asyncio.create_task(
        _wallet_metrics_loop(bybit, position_store, redis, shutdown_event),
        name="live-wallet-metrics",
    )

    # ── Ranking Engine: Strategy Promotion Gate ─────────────────────────
    ranking_task = asyncio.create_task(
        _ranking_refresh_loop(redis, trade_store),
        name="live-ranking",
    )

    # ── Gate Calibration: Adaptive Threshold from Historical EV ────────
    gate_calibration_task = asyncio.create_task(
        _gate_calibration_loop(redis, trade_store),
        name="live-gate-calibration",
    )

    # ── ELO Rating: Per-Strategy Win/Loss Tracking ─────────────────────
    elo_task = asyncio.create_task(
        _elo_refresh_loop(redis, trade_store),
        name="live-elo",
    )

    # ── Sprint 3: HMM Regime Classification Loop ─────────────────────
    from app.alpha.hmm_regime_classifier import HMMRegimeClassifier
    hmm_classifier = HMMRegimeClassifier(redis_client=redis)
    hmm_task = asyncio.create_task(
        hmm_classifier.run_classification_loop(
            ohlcv_fetcher=ohlcv_fetcher, symbol="BTC/USDT", interval_seconds=3600,
        ),
        name="live-hmm",
    )

    # ── Sprint 3: GARCH Volatility Forecast Loop ─────────────────────
    from app.risk.garch_volatility_forecaster import GARCHVolatilityForecaster
    garch_forecaster = GARCHVolatilityForecaster(redis_client=redis)
    garch_task = asyncio.create_task(
        garch_forecaster.run_forecast_loop(
            ohlcv_fetcher=ohlcv_fetcher, symbol="BTC/USDT", interval_seconds=3600,
        ),
        name="live-garch",
    )

    # ── Vol Surface: Cross-Asset Volatility Term Structure ────────────
    vol_surface_task = asyncio.create_task(
        _vol_surface_loop(redis, ohlcv_fetcher),
        name="live-vol-surface",
    )

    # ── Daily Summary: Midnight UTC summary push ──────────────────────
    daily_summary_task = asyncio.create_task(
        _daily_summary_loop(redis, pool, alert_service, shutdown_event),
        name="live-daily-summary",
    )

    try:
        await shutdown_event.wait()
    finally:
        consumer.stop()
        for t in background_tasks:
            t.cancel()
        for wt in worker_tasks:
            wt.cancel()
        ingestor_task.cancel()
        universe_task.cancel()
        consumer_task.cancel()
        exit_task.cancel()
        status_task.cancel()
        redis_health_task.cancel()
        if apm_task:
            apm_task.cancel()
        if apm_health_task:
            apm_health_task.cancel()
        if balance_task:
            balance_task.cancel()
        wallet_metrics_task.cancel()
        hmm_task.cancel()
        garch_task.cancel()
        ranking_task.cancel()
        gate_calibration_task.cancel()
        elo_task.cancel()
        vol_surface_task.cancel()
        daily_summary_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*worker_tasks)
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task
        with contextlib.suppress(asyncio.CancelledError):
            await ingestor_task
        with contextlib.suppress(asyncio.CancelledError):
            await universe_task
        with contextlib.suppress(asyncio.CancelledError):
            await exit_task
        with contextlib.suppress(asyncio.CancelledError):
            await status_task
        if apm_task:
            with contextlib.suppress(asyncio.CancelledError):
                await apm_task
        if balance_task:
            with contextlib.suppress(asyncio.CancelledError):
                await balance_task
        with contextlib.suppress(asyncio.CancelledError):
            await wallet_metrics_task
        with contextlib.suppress(asyncio.CancelledError):
            await daily_summary_task
        await ingestor.stop()
        await emitter.stop()
        if "exchange" in locals() and exchange is not None:
            with contextlib.suppress(Exception):
                await exchange.close()
        await db_engine.dispose()
        await shutdown()
        logger.info("karsa-live stopped")


if __name__ == "__main__":
    asyncio.run(main())
