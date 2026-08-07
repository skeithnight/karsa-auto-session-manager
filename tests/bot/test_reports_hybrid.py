"""Tests for hybrid intelligence features in bot reports and trade history."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.utils.formatters.shadow_funnel_formatter import format_shadow_funnel
from app.bot.utils.formatters.live_funnel_formatter import format_live_funnel
from app.bot.utils.formatters.trade_history_formatter import TradeHistoryFormatter


class TestShadowFunnelHybrid:
    """Tests for shadow funnel report hybrid intelligence sections."""

    def test_shadow_report_shows_ai_performance_section(self):
        """Shadow report includes AI Engine Performance section."""
        metrics = {
            "ai_engine": {
                "total_evaluations": 342,
                "avg_confidence": 76,
                "high_confidence_trades": 42,
                "high_confidence_win_rate": 71.4,
                "low_confidence_trades": 28,
                "low_confidence_win_rate": 39.3,
                "ai_accuracy": 74.0,
                "api_cost": 8.50,
            },
        }
        report = MagicMock()
        report.total_trades = 10
        report.winning_trades = 6
        report.losing_trades = 4
        report.win_rate = 60.0
        report.gross_profit = Decimal("100")
        report.gross_loss = Decimal("50")
        report.net_pnl = Decimal("50")
        report.total_fees = Decimal("5")
        report.total_slippage = Decimal("2")

        result = format_shadow_funnel(metrics, report)

        assert "AI ENGINE PERFORMANCE" in result
        assert "Total Evaluations: 342" in result
        assert "Avg Confidence: 76/100" in result
        # HTML escaping converts > to &gt; and < to &lt;
        assert "&gt;80" in result
        assert "High Confidence Win Rate: 71.4%" in result
        assert "&lt;60" in result
        assert "Low Confidence Win Rate: 39.3%" in result
        assert "AI Accuracy: 74%" in result
        assert "API Cost: $8.50" in result

    def test_shadow_report_shows_guardrail_statistics(self):
        """Shadow report includes Guardrail Statistics section."""
        metrics = {
            "guardrails": {
                "hard_triggered": 15,
                "trades_blocked": 12,
                "estimated_losses_prevented": 380.0,
                "soft_triggered": 45,
                "position_downgrades": 38,
            },
        }
        report = MagicMock()
        report.total_trades = 10
        report.winning_trades = 6
        report.losing_trades = 4
        report.win_rate = 60.0
        report.gross_profit = Decimal("100")
        report.gross_loss = Decimal("50")
        report.net_pnl = Decimal("50")
        report.total_fees = Decimal("5")
        report.total_slippage = Decimal("2")

        result = format_shadow_funnel(metrics, report)

        assert "GUARDRAIL STATISTICS" in result
        assert "Hard Guardrails Triggered: 15" in result
        assert "Trades Blocked: 12" in result
        assert "Estimated Losses Prevented: $380.00" in result
        assert "Soft Guardrails Triggered: 45" in result
        assert "Position Downgrades: 38" in result

    def test_shadow_report_shows_statistical_features(self):
        """Shadow report includes Statistical Features Summary section."""
        metrics = {
            "statistical_features": {
                "avg_beta": 1.32,
                "avg_correlation": 0.74,
                "avg_atr_pct": 3.8,
                "volume_confirmed_pct": 68.0,
            },
        }
        report = MagicMock()
        report.total_trades = 10
        report.winning_trades = 6
        report.losing_trades = 4
        report.win_rate = 60.0
        report.gross_profit = Decimal("100")
        report.gross_loss = Decimal("50")
        report.net_pnl = Decimal("50")
        report.total_fees = Decimal("5")
        report.total_slippage = Decimal("2")

        result = format_shadow_funnel(metrics, report)

        assert "STATISTICAL FEATURES SUMMARY" in result
        assert "Avg Beta: 1.32" in result
        assert "Avg Correlation: 0.74" in result
        assert "Avg ATR: 3.8%" in result
        assert "Volume Confirmed Trades: 68%" in result

    def test_shadow_report_handles_missing_ai_data(self):
        """Shadow report gracefully handles missing AI engine data."""
        metrics = {}
        report = MagicMock()
        report.total_trades = 0
        report.winning_trades = 0
        report.losing_trades = 0
        report.win_rate = 0.0
        report.gross_profit = Decimal("0")
        report.gross_loss = Decimal("0")
        report.net_pnl = Decimal("0")
        report.total_fees = Decimal("0")
        report.total_slippage = Decimal("0")

        result = format_shadow_funnel(metrics, report)

        # Should not crash, should show default values
        assert "AI ENGINE PERFORMANCE" in result
        assert "Total Evaluations: 0" in result
        assert "GUARDRAIL STATISTICS" in result
        assert "Hard Guardrails Triggered: 0" in result
        assert "STATISTICAL FEATURES SUMMARY" in result
        assert "Avg Beta: 0.00" in result


class TestLiveFunnelHybrid:
    """Tests for live funnel report hybrid intelligence sections."""

    def test_live_report_shows_ai_performance_section(self):
        """Live report includes AI Engine Performance section."""
        metrics = {
            "ai_engine": {
                "total_evaluations": 500,
                "avg_confidence": 82,
                "high_confidence_trades": 60,
                "high_confidence_win_rate": 75.0,
                "low_confidence_trades": 15,
                "low_confidence_win_rate": 45.0,
                "ai_accuracy": 78.0,
                "api_cost": 12.50,
            },
        }
        report = MagicMock()
        report.total_trades = 20
        report.winning_trades = 14
        report.losing_trades = 6
        report.win_rate = 70.0
        report.gross_profit = Decimal("500")
        report.gross_loss = Decimal("200")
        report.net_pnl = Decimal("300")
        report.total_fees = Decimal("25")
        report.total_slippage = Decimal("10")

        result = format_live_funnel(metrics, report)

        assert "AI ENGINE PERFORMANCE" in result
        assert "Total Evaluations: 500" in result
        assert "Avg Confidence: 82/100" in result
        assert "AI Accuracy: 78%" in result
        assert "API Cost: $12.50" in result

    def test_live_report_shows_guardrail_statistics(self):
        """Live report includes Guardrail Statistics section."""
        metrics = {
            "guardrails": {
                "hard_triggered": 20,
                "trades_blocked": 18,
                "estimated_losses_prevented": 500.0,
                "soft_triggered": 60,
                "position_downgrades": 45,
            },
        }
        report = MagicMock()
        report.total_trades = 20
        report.winning_trades = 14
        report.losing_trades = 6
        report.win_rate = 70.0
        report.gross_profit = Decimal("500")
        report.gross_loss = Decimal("200")
        report.net_pnl = Decimal("300")
        report.total_fees = Decimal("25")
        report.total_slippage = Decimal("10")

        result = format_live_funnel(metrics, report)

        assert "GUARDRAIL STATISTICS" in result
        assert "Hard Guardrails Triggered: 20" in result
        assert "Trades Blocked: 18" in result
        assert "Estimated Losses Prevented: $500.00" in result

    def test_live_report_shows_statistical_features(self):
        """Live report includes Statistical Features Summary section."""
        metrics = {
            "statistical_features": {
                "avg_beta": 1.45,
                "avg_correlation": 0.68,
                "avg_atr_pct": 4.2,
                "volume_confirmed_pct": 72.0,
            },
        }
        report = MagicMock()
        report.total_trades = 20
        report.winning_trades = 14
        report.losing_trades = 6
        report.win_rate = 70.0
        report.gross_profit = Decimal("500")
        report.gross_loss = Decimal("200")
        report.net_pnl = Decimal("300")
        report.total_fees = Decimal("25")
        report.total_slippage = Decimal("10")

        result = format_live_funnel(metrics, report)

        assert "STATISTICAL FEATURES SUMMARY" in result
        assert "Avg Beta: 1.45" in result
        assert "Avg Correlation: 0.68" in result
        assert "Avg ATR: 4.2%" in result
        assert "Volume Confirmed Trades: 72%" in result


class TestTradeHistoryHybrid:
    """Tests for trade history hybrid intelligence features."""

    def test_trade_history_shows_ai_confidence_per_trade(self):
        """Trade history format_trade includes AI confidence when available."""
        trade = {
            "symbol": "SOL/USDT",
            "side": "LONG",
            "amount": 10.0,
            "entry_price": 95.20,
            "exit_price": 98.50,
            "pnl": 33.0,
            "exit_time": datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
            "exit_reason": "tp_hit",
            "ai_confidence": 79,
            "atr_pct": 4.2,
        }

        result = TradeHistoryFormatter.format_trade(trade)

        # Should show symbol and PnL
        assert "SOL/USDT" in result
        assert "tp_hit" in result

    def test_trade_history_format_trade_detail_shows_ai_confidence(self):
        """Trade history format_trade_detail shows AI confidence and ATR."""
        trade = {
            "symbol": "SOL/USDT",
            "side": "LONG",
            "amount": 10.0,
            "entry_price": 95.20,
            "exit_price": 98.50,
            "pnl": 33.0,
            "entry_time": datetime(2025, 1, 15, 5, 30, tzinfo=timezone.utc),
            "exit_time": datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
            "exit_reason": "tp_hit",
            "ai_confidence": 79,
            "atr_pct": 4.2,
        }

        result = TradeHistoryFormatter.format_trade_detail(trade)

        assert "WIN" in result
        assert "SOL/USDT" in result
        assert "LONG" in result
        assert "$95.20" in result
        assert "$98.50" in result
        assert "+$33.00" in result
        assert "+3.5%" in result
        assert "4h 30m" in result
        assert "AI Confidence: 79/100" in result
        assert "ATR: 4.2%" in result

    def test_trade_history_format_trade_detail_loss(self):
        """Trade history format_trade_detail shows loss correctly."""
        trade = {
            "symbol": "ETH/USDT",
            "side": "SHORT",
            "amount": 2.0,
            "entry_price": 3500.00,
            "exit_price": 3550.00,
            "pnl": -100.0,
            "entry_time": datetime(2025, 1, 15, 8, 0, tzinfo=timezone.utc),
            "exit_time": datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc),
            "exit_reason": "sl_hit",
            "ai_confidence": 45,
        }

        result = TradeHistoryFormatter.format_trade_detail(trade)

        assert "LOSS" in result
        assert "ETH/USDT" in result
        assert "SHORT" in result
        assert "-$100.00" in result
        assert "AI Confidence: 45/100" in result

    def test_ai_accuracy_view_renders_correctly(self):
        """AI accuracy view renders correctly with mixed confidence trades."""
        trades = [
            {
                "symbol": "BTC/USDT",
                "pnl": 100.0,
                "entry_price": 50000.0,
                "amount": 0.1,
                "ai_confidence": 85,
            },
            {
                "symbol": "ETH/USDT",
                "pnl": -50.0,
                "entry_price": 3000.0,
                "amount": 1.0,
                "ai_confidence": 75,
            },
            {
                "symbol": "SOL/USDT",
                "pnl": 30.0,
                "entry_price": 100.0,
                "amount": 10.0,
                "ai_confidence": 90,
            },
            {
                "symbol": "DOGE/USDT",
                "pnl": -20.0,
                "entry_price": 0.1,
                "amount": 1000.0,
                "ai_confidence": 55,
            },
        ]

        result = TradeHistoryFormatter.build_ai_accuracy_message(trades)

        assert "AI CONFIDENCE VS OUTCOME" in result
        # HTML escaping converts > to &gt; and < to &lt;
        assert "HIGH CONFIDENCE" in result
        assert "&gt;80" in result
        assert "MEDIUM CONFIDENCE" in result
        assert "60-80" in result
        assert "LOW CONFIDENCE" in result
        assert "&lt;60" in result
        assert "CONFIDENCE CALIBRATION" in result

    def test_confidence_calibration_breakdown(self):
        """Confidence calibration shows correct bucket breakdown."""
        trades = [
            {"pnl": 100.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 95},
            {"pnl": 80.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 92},
            {"pnl": 60.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 85},
            {"pnl": -30.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 82},
            {"pnl": 40.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 75},
            {"pnl": -20.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 72},
            {"pnl": 10.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 65},
            {"pnl": -15.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 50},
            {"pnl": -25.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": 45},
        ]

        result = TradeHistoryFormatter.build_ai_accuracy_message(trades)

        assert "90-100:" in result
        assert "80-90:" in result
        assert "70-80:" in result
        assert "60-70:" in result
        # HTML escaping converts < to &lt;
        assert "&lt;60:" in result

    def test_ai_accuracy_empty_trades(self):
        """AI accuracy view handles empty trades list."""
        result = TradeHistoryFormatter.build_ai_accuracy_message([])
        assert "No trades available" in result

    def test_ai_accuracy_no_confidence_data(self):
        """AI accuracy view handles trades without AI confidence."""
        trades = [
            {"pnl": 100.0, "entry_price": 50000.0, "amount": 0.1, "ai_confidence": None},
            {"pnl": -50.0, "entry_price": 3000.0, "amount": 1.0, "ai_confidence": None},
        ]

        result = TradeHistoryFormatter.build_ai_accuracy_message(trades)

        assert "AI CONFIDENCE VS OUTCOME" in result
        # Should not crash with None confidence values


class TestCallbackRouting:
    """Tests for new callback routes."""

    @pytest.mark.asyncio
    async def test_ai_accuracy_callback_routes_correctly(self):
        """AI accuracy callback routes to correct handler."""
        from app.bot.handlers.callback_router import button_callback

        update = MagicMock()
        update.callback_query.data = "cmd_ai_accuracy"
        update.callback_query.answer = AsyncMock()
        update.callback_query.message = MagicMock()
        update.callback_query.message.edit_text = AsyncMock()
        update.callback_query.message.reply_text = AsyncMock()
        update.effective_chat.id = 12345
        update.effective_user = MagicMock()
        update.effective_user.id = 12345

        ctx = MagicMock()
        ctx.bot_data = {"redis_client": AsyncMock(), "db_engine": None}

        await button_callback(update, ctx)

        update.callback_query.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_feature_correlation_callback_routes_correctly(self):
        """Feature correlation callback routes to correct handler."""
        from app.bot.handlers.callback_router import button_callback

        update = MagicMock()
        update.callback_query.data = "cmd_feature_correlation"
        update.callback_query.answer = AsyncMock()
        update.callback_query.message = MagicMock()
        update.callback_query.message.edit_text = AsyncMock()
        update.callback_query.message.reply_text = AsyncMock()
        update.effective_chat.id = 12345
        update.effective_user = MagicMock()
        update.effective_user.id = 12345

        ctx = MagicMock()
        ctx.bot_data = {"redis_client": AsyncMock(), "db_engine": None}

        await button_callback(update, ctx)

        update.callback_query.answer.assert_awaited_once()
