"""Tests for Lead-Lag Buffer."""

from __future__ import annotations

from app.alpha.lead_lag_buffer import LeadLagBuffer


class TestLeadLagBuffer:
    def setup_method(self):
        self.buf = LeadLagBuffer(window_seconds=900)

    def test_update_and_get(self):
        self.buf.update("BTC/USDT", "binance", 100.0)
        self.buf.update("BTC/USDT", "bybit", 101.0)
        self.buf.update("BTC/USDT", "binance", 102.0)
        self.buf.update("BTC/USDT", "bybit", 101.5)
        delta = self.buf.get_lead_lag_delta("BTC/USDT")
        assert delta is not None
        assert delta > 0

    def test_insufficient_data(self):
        self.buf.update("BTC/USDT", "binance", 100.0)
        assert self.buf.get_lead_lag_delta("BTC/USDT") is None

    def test_no_data(self):
        assert self.buf.get_lead_lag_delta("ETH/USDT") is None

    def test_custom_exchange_names(self):
        self.buf.update("BTC/USDT", "okx", 100.0)
        self.buf.update("BTC/USDT", "okx", 101.0)
        self.buf.update("BTC/USDT", "bybit", 100.0)
        self.buf.update("BTC/USDT", "bybit", 100.5)
        delta = self.buf.get_lead_lag_delta("BTC/USDT", lead="okx", lag="bybit")
        assert delta is not None
        assert delta > 0

    def test_clear(self):
        self.buf.update("BTC/USDT", "binance", 100.0)
        self.buf.clear()
        assert self.buf.get_lead_lag_delta("BTC/USDT") is None
