"""Tests for Alpha Metrics."""

from __future__ import annotations

from decimal import Decimal


from app.alpha.metrics import (
    calculate_vwap,
    calculate_skew,
    calculate_lead_lag,
    AlphaMetrics,
)


class TestCalculateVwap:
    def test_basic_vwap(self):
        prices = [Decimal("100"), Decimal("101"), Decimal("102")]
        volumes = [Decimal("10"), Decimal("20"), Decimal("30")]
        result = calculate_vwap(prices, volumes)
        # (100*10 + 101*20 + 102*30) / 60 = (1000+2020+3060)/60 = 6080/60
        assert result == Decimal("6080") / Decimal("60")

    def test_empty_input(self):
        assert calculate_vwap([], []) is None

    def test_mismatched_lengths(self):
        assert calculate_vwap([Decimal("100")], [Decimal("10"), Decimal("20")]) is None

    def test_zero_volume(self):
        assert calculate_vwap([Decimal("100")], [Decimal("0")]) is None


class TestCalculateSkew:
    def test_all_bids(self):
        assert calculate_skew(Decimal("100"), Decimal("0")) == 1.0

    def test_all_asks(self):
        assert calculate_skew(Decimal("0"), Decimal("100")) == -1.0

    def test_balanced(self):
        assert calculate_skew(Decimal("50"), Decimal("50")) == 0.0

    def test_empty(self):
        assert calculate_skew(Decimal("0"), Decimal("0")) == 0.0


class TestCalculateLeadLag:
    def test_basic(self):
        result = calculate_lead_lag(Decimal("64100"), Decimal("64000"))
        assert result == Decimal("100")

    def test_zero_reference(self):
        assert calculate_lead_lag(Decimal("0"), Decimal("64000")) is None


class TestAlphaMetrics:
    def test_update_and_vwap(self):
        am = AlphaMetrics()
        am.update("binance", Decimal("100"), Decimal("10"))
        am.update("binance", Decimal("102"), Decimal("20"))

        vwap = am.get_vwap("binance")
        assert vwap is not None
        assert vwap > Decimal("100")

    def test_get_skew(self):
        am = AlphaMetrics()
        skew = am.get_skew(Decimal("70"), Decimal("30"))
        assert skew == Decimal("0.4")

    def test_get_lead_lag(self):
        am = AlphaMetrics(lead_exchange="binance", lag_exchange="bybit")
        am.update("binance", Decimal("64100"), Decimal("10"))
        am.update("bybit", Decimal("64000"), Decimal("10"))

        lag = am.get_lead_lag()
        assert lag == Decimal("100")

    def test_get_lead_lag_no_data(self):
        am = AlphaMetrics()
        assert am.get_lead_lag() is None
