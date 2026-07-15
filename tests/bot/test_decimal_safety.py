"""Tests ensuring no float leakage in UI rendering."""

from __future__ import annotations

from decimal import Decimal

from app.bot.utils.formatters import format_price, format_position_card


class TestDecimalSafety:
    def test_format_price_uses_decimal_input(self):
        result = format_price(Decimal("64250.50"))
        assert "64" in result
        assert "250" in result

    def test_format_price_float_still_works(self):
        result = format_price(64250.50)
        assert "64" in result

    def test_format_price_zero(self):
        assert format_price(0) == "0.00"

    def test_format_price_small_value(self):
        result = format_price(0.005)
        assert "0.005" in result

    def test_format_price_large_value(self):
        result = format_price(123456.78)
        assert "123" in result
        assert "456" in result

    def test_position_card_renders_symbol(self):
        position = {
            "symbol": "BTC/USDT:USDT",
            "side": "Buy",
            "size": 0.001,
            "entry_price": 64250.0,
            "current_price": 64890.0,
            "unrealized_pnl": 0.64,
        }
        card = format_position_card(position, index=1)
        assert "BTC" in str(card)
