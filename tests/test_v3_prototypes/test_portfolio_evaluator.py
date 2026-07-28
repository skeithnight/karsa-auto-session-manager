"""Tests for PortfolioEvaluator (v3.5 prototype)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.alpha.evaluators.portfolio_evaluator import PortfolioEvaluator
from app.core.portfolio_snapshot import PortfolioSnapshot


def _make_portfolio(**kwargs) -> PortfolioSnapshot:
    """Create a PortfolioSnapshot with defaults for testing."""
    defaults = {
        "timestamp": 1000000.0,
        "equity": Decimal("10000"),
        "cash": Decimal("5000"),
        "positions": {},
        "sector_exposure": {},
        "symbol_correlation": {},
        "drawdown": Decimal("0.05"),
    }
    defaults.update(kwargs)
    return PortfolioSnapshot(**defaults)


class TestPortfolioEvaluator:
    """Tests for PortfolioEvaluator."""

    def setup_method(self):
        self.evaluator = PortfolioEvaluator()

    def test_healthy_portfolio(self):
        """Healthy portfolio should produce favorable result."""
        portfolio = _make_portfolio(
            equity=Decimal("10000"),
            cash=Decimal("5000"),
            positions={"BTC": {"pnl": 100}},
            drawdown=Decimal("0.02"),
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.direction == 1.0
        assert result.weight > 20.0
        assert result.confidence > 0.5

    def test_stressed_portfolio(self):
        """Stressed portfolio should produce unfavorable result."""
        portfolio = _make_portfolio(
            equity=Decimal("10000"),
            cash=Decimal("500"),
            positions={"BTC": {}, "ETH": {}, "SOL": {}, "DOGE": {}, "ADA": {}, "DOT": {}},
            drawdown=Decimal("0.25"),
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.direction == -1.0
        assert result.weight > 20.0

    def test_contributions_present(self):
        """Result should contain all contributions."""
        portfolio = _make_portfolio()
        result = self.evaluator.evaluate(portfolio)

        assert "drawdown" in result.contributions
        assert "position_count" in result.contributions
        assert "sector_concentration" in result.contributions
        assert "correlation" in result.contributions
        assert "cash_reserve" in result.contributions

    def test_drawdown_low(self):
        """Low drawdown should produce positive score."""
        portfolio = _make_portfolio(drawdown=Decimal("0.01"))
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["drawdown"] > 0

    def test_drawdown_high(self):
        """High drawdown should produce negative score."""
        portfolio = _make_portfolio(drawdown=Decimal("0.25"))
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["drawdown"] < 0

    def test_position_count_few(self):
        """Few positions should produce positive score."""
        portfolio = _make_portfolio(positions={"BTC": {}})
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["position_count"] > 0

    def test_position_count_many(self):
        """Many positions should produce negative score."""
        portfolio = _make_portfolio(
            positions={"BTC": {}, "ETH": {}, "SOL": {}, "DOGE": {}, "ADA": {}, "DOT": {}, "AVAX": {}, "LINK": {}}
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["position_count"] < 0

    def test_cash_reserve_high(self):
        """High cash reserve should produce positive score."""
        portfolio = _make_portfolio(
            equity=Decimal("10000"),
            cash=Decimal("6000"),
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["cash_reserve"] > 0

    def test_cash_reserve_low(self):
        """Low cash reserve should produce negative score."""
        portfolio = _make_portfolio(
            equity=Decimal("10000"),
            cash=Decimal("200"),
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["cash_reserve"] < 0

    def test_sector_diversified(self):
        """Diversified sectors should produce positive score."""
        portfolio = _make_portfolio(
            sector_exposure={
                "DeFi": Decimal("0.2"),
                "L1": Decimal("0.2"),
                "L2": Decimal("0.2"),
                "Meme": Decimal("0.2"),
                "AI": Decimal("0.2"),
            }
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["sector_concentration"] > 0

    def test_sector_concentrated(self):
        """Concentrated sectors should produce negative score."""
        portfolio = _make_portfolio(
            sector_exposure={
                "DeFi": Decimal("0.8"),
                "L1": Decimal("0.2"),
            }
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["sector_concentration"] < 0

    def test_correlation_low(self):
        """Low correlation should produce positive score."""
        portfolio = _make_portfolio(
            symbol_correlation={"BTC-ETH": 0.3, "ETH-SOL": 0.2}
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["correlation"] > 0

    def test_correlation_high(self):
        """High correlation should produce negative score."""
        portfolio = _make_portfolio(
            symbol_correlation={"BTC-ETH": 0.8, "ETH-SOL": 0.9}
        )
        result = self.evaluator.evaluate(portfolio)

        assert result.contributions["correlation"] < 0

    def test_reason_generation(self):
        """Result should have a meaningful reason."""
        portfolio = _make_portfolio(
            equity=Decimal("10000"),
            cash=Decimal("5000"),
            drawdown=Decimal("0.02"),
        )
        result = self.evaluator.evaluate(portfolio)

        assert len(result.reason) > 0

    def test_metadata_present(self):
        """Result should contain metadata with portfolio values."""
        portfolio = _make_portfolio(
            equity=Decimal("10000"),
            cash=Decimal("5000"),
        )
        result = self.evaluator.evaluate(portfolio)

        assert "equity" in result.metadata
        assert "drawdown" in result.metadata
        assert "position_count" in result.metadata
