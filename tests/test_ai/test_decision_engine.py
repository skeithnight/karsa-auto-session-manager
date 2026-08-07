"""Tests for the AI Decision Engine.

Covers:
- Prompt generation (all features included)
- JSON parsing (valid and invalid responses)
- Fallback logic on timeout
- Fallback logic on parsing error
- Caching mechanism
- Multi-provider fallback
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.ai.circuit_breaker import AICircuitBreaker
from app.ai.dto import (
    AIDecisionDTO,
    EntryStrategy,
    PositionSize,
    RiskLevel,
    StopLossStrategy,
)
from app.ai.parser import ParseError, parse_decision
from app.ai.prompt_builder import build_prompts
from app.core.decision_context import DecisionContext
from app.core.feature_extractor import FeatureVector

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_feature_vector(**overrides: Any) -> FeatureVector:
    """Build a FeatureVector with sensible defaults, overridable per test."""
    defaults = dict(
        close=64000.0,
        ema_20=63500.0,
        ema_200=60000.0,
        sma_20=63400.0,
        atr=800.0,
        atr_pct=55.0,
        rsi_14=62.0,
        adx_14=28.0,
        hurst=0.55,
        funding_rate=0.0001,
        oi_change=0.02,
        orderbook_delta=0.3,
        cvd_slope=0.1,
        spread_pct=0.0002,
        market_quality_score=0.7,
        candle_quality_score=0.8,
        noise_score=0.3,
        liquidity_score=0.75,
    )
    defaults.update(overrides)
    return FeatureVector(**defaults)


def _make_context(symbol: str = "BTC/USDT", direction: str = "LONG", **fv_overrides: Any) -> DecisionContext:
    """Build a DecisionContext for testing."""
    from app.alpha.regime_classifier import MarketRegime

    return DecisionContext(
        symbol=symbol,
        regime=MarketRegime.TREND_BULL,
        direction=direction,
        features=_make_feature_vector(**fv_overrides),
    )


def _valid_ai_response(**overrides: Any) -> str:
    """Return a valid JSON AI response string, overridable."""
    data = {
        "confidence_score": 72,
        "risk_level": "LOW",
        "position_size": "HALF",
        "entry_strategy": "MARKET",
        "stop_loss_strategy": "NORMAL",
        "reasoning": "Strong bullish momentum with supportive macro conditions.",
        "key_risks": ["Funding rate elevated"],
        "key_opportunities": ["Volume breakout confirmed"],
        "bullish_probability": 65.0,
        "bearish_probability": 35.0,
        "summary": "Bullish setup with moderate risk.",
    }
    data.update(overrides)
    return json.dumps(data)


# ---------------------------------------------------------------------------
# Prompt Builder Tests
# ---------------------------------------------------------------------------


class TestPromptBuilder:
    def test_all_features_included(self) -> None:
        """Every feature from FeatureVector appears in the user prompt."""
        context = _make_context()
        result = build_prompts(context)

        assert "system" in result
        assert "user" in result
        assert "messages" in result
        assert "features_snapshot" in result

        user = result["user"]
        assert "BTC/USDT" in user
        assert "LONG" in user
        assert "TREND_BULL" in user
        # Price features
        assert "64000" in user
        assert "55" in user  # ATR %
        # Trend
        assert "62" in user  # RSI
        assert "28" in user  # ADX
        # Derivatives
        assert "funding_rate" in user.lower() or "funding rate" in user.lower()

    def test_messages_format(self) -> None:
        """Messages list has system + user roles."""
        context = _make_context()
        result = build_prompts(context)

        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_none_features_rendered_as_na(self) -> None:
        """None feature values render as N/A."""
        context = _make_context(close=None, atr=None)
        result = build_prompts(context)

        assert "N/A" in result["user"]

    def test_short_direction(self) -> None:
        """SHORT direction appears in the prompt."""
        context = _make_context(direction="SHORT")
        result = build_prompts(context)
        assert "SHORT" in result["user"]

    def test_system_prompt_contains_json_schema(self) -> None:
        """System prompt contains the required JSON schema."""
        result = build_prompts(_make_context())
        assert "confidence_score" in result["system"]
        assert "risk_level" in result["system"]
        assert "position_size" in result["system"]


# ---------------------------------------------------------------------------
# Parser Tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_valid_json(self) -> None:
        """Valid JSON response parses correctly."""
        raw = _valid_ai_response()
        dto = parse_decision(raw)

        assert dto.confidence_score == 72
        assert dto.risk_level == RiskLevel.LOW
        assert dto.position_size == PositionSize.HALF
        assert dto.entry_strategy == EntryStrategy.MARKET
        assert dto.stop_loss_strategy == StopLossStrategy.NORMAL
        assert dto.reasoning == "Strong bullish momentum with supportive macro conditions."
        assert dto.key_risks == ["Funding rate elevated"]
        assert dto.key_opportunities == ["Volume breakout confirmed"]
        assert dto.bullish_probability == 65.0
        assert dto.bearish_probability == 35.0
        assert dto.provider == "9router"

    def test_markdown_fenced_json(self) -> None:
        """JSON wrapped in ```json fences is parsed correctly."""
        raw = f"```json\n{_valid_ai_response()}\n```"
        dto = parse_decision(raw)
        assert dto.confidence_score == 72

    def test_markdown_fenced_without_lang(self) -> None:
        """JSON wrapped in ``` fences (no lang tag) is parsed correctly."""
        raw = f"```\n{_valid_ai_response()}\n```"
        dto = parse_decision(raw)
        assert dto.confidence_score == 72

    def test_missing_required_field(self) -> None:
        """Missing required fields raise ParseError."""
        data = json.loads(_valid_ai_response())
        del data["risk_level"]
        with pytest.raises(ParseError, match="Missing required fields"):
            parse_decision(json.dumps(data))

    def test_invalid_json(self) -> None:
        """Non-JSON text raises ParseError."""
        with pytest.raises(ParseError, match="Invalid JSON"):
            parse_decision("not json at all")

    def test_confidence_out_of_range(self) -> None:
        """Confidence > 100 raises ParseError."""
        with pytest.raises(ParseError, match="confidence_score must be 0-100"):
            parse_decision(_valid_ai_response(confidence_score=150))

    def test_confidence_negative(self) -> None:
        """Negative confidence raises ParseError."""
        with pytest.raises(ParseError, match="confidence_score must be 0-100"):
            parse_decision(_valid_ai_response(confidence_score=-5))

    def test_confidence_not_integer(self) -> None:
        """Non-integer confidence raises ParseError."""
        with pytest.raises(ParseError, match="confidence_score must be an integer"):
            parse_decision(_valid_ai_response(confidence_score="high"))

    def test_invalid_risk_level(self) -> None:
        """Invalid risk_level raises ParseError."""
        with pytest.raises(ParseError, match="Invalid risk_level"):
            parse_decision(_valid_ai_response(risk_level="CRITICAL"))

    def test_invalid_position_size(self) -> None:
        """Invalid position_size raises ParseError."""
        with pytest.raises(ParseError, match="Invalid position_size"):
            parse_decision(_valid_ai_response(position_size="ALL_IN"))

    def test_invalid_entry_strategy(self) -> None:
        """Invalid entry_strategy raises ParseError."""
        with pytest.raises(ParseError, match="Invalid entry_strategy"):
            parse_decision(_valid_ai_response(entry_strategy="YOLO"))

    def test_invalid_stop_loss_strategy(self) -> None:
        """Invalid stop_loss_strategy raises ParseError."""
        with pytest.raises(ParseError, match="Invalid stop_loss_strategy"):
            parse_decision(_valid_ai_response(stop_loss_strategy="PRAY"))

    def test_case_insensitive_enums(self) -> None:
        """Enum parsing is case-insensitive."""
        raw = _valid_ai_response(
            risk_level="low",
            position_size="half",
            entry_strategy="market",
            stop_loss_strategy="normal",
        )
        dto = parse_decision(raw)
        assert dto.risk_level == RiskLevel.LOW
        assert dto.position_size == PositionSize.HALF

    def test_optional_fields_defaults(self) -> None:
        """Optional fields get sensible defaults when missing."""
        data = {
            "confidence_score": 60,
            "risk_level": "MEDIUM",
            "position_size": "QUARTER",
            "entry_strategy": "LIMIT_RETEST",
            "stop_loss_strategy": "TIGHT",
            "reasoning": "Cautious entry.",
        }
        dto = parse_decision(json.dumps(data))
        assert dto.key_risks == []
        assert dto.key_opportunities == []
        assert dto.bullish_probability == 50.0
        assert dto.bearish_probability == 50.0
        assert dto.summary == ""

    def test_probability_out_of_range(self) -> None:
        """Probability > 100 raises ParseError."""
        with pytest.raises(ParseError, match="bullish_probability must be 0-100"):
            parse_decision(_valid_ai_response(bullish_probability=120.0))

    def test_non_dict_json(self) -> None:
        """JSON array instead of object raises ParseError."""
        with pytest.raises(ParseError, match="Expected JSON object"):
            parse_decision("[1, 2, 3]")


# ---------------------------------------------------------------------------
# NineRouterService Tests
# ---------------------------------------------------------------------------


class TestNineRouterService:
    """Tests for the NineRouterService HTTP + fallback pipeline."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        redis.delete = AsyncMock()
        redis.expire = AsyncMock()
        return redis

    @pytest.fixture
    def mock_http_client(self) -> httpx.AsyncClient:
        """Return an httpx.AsyncClient that will be mocked at the transport level."""
        return httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    def _mock_httpx_response(self, status: int = 200, body: dict | None = None) -> httpx.Response:
        """Build a mock httpx.Response."""
        if body is None:
            body = {
                "choices": [{"message": {"content": _valid_ai_response()}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        return httpx.Response(
            status_code=status,
            json=body,
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )

    @pytest.mark.asyncio
    async def test_successful_decision(self, mock_http_client: httpx.AsyncClient, mock_redis: AsyncMock) -> None:
        """Happy path: 9router returns valid JSON, decision is parsed and cached."""
        mock_resp = self._mock_httpx_response()
        mock_http_client.send = AsyncMock(return_value=mock_resp)

        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        provider = _ProviderConfig(name="test", base_url="http://test", api_key="key", model="m")
        service = NineRouterService(
            http_client=mock_http_client,
            redis_client=mock_redis,
            provider_chain=[provider],
        )

        context = _make_context()
        result = await service.analyze_market(context)

        assert result is not None
        assert result.confidence_score == 72
        assert result.position_size == PositionSize.HALF
        assert result.provider == "test"

        # Verify cache was written
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_returns_default(self, mock_http_client: httpx.AsyncClient, mock_redis: AsyncMock) -> None:
        """Timeout → fallback default BLOCK decision."""
        mock_http_client.send = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        provider = _ProviderConfig(name="test", base_url="http://test", api_key="key", model="m")
        service = NineRouterService(
            http_client=mock_http_client,
            redis_client=mock_redis,
            provider_chain=[provider],
        )

        result = await service.analyze_market(_make_context())

        assert result is not None
        assert result.position_size == PositionSize.BLOCK
        assert result.risk_level == RiskLevel.HIGH
        assert "All AI providers failed" in result.reasoning

    @pytest.mark.asyncio
    async def test_parse_error_returns_default(
        self, mock_http_client: httpx.AsyncClient, mock_redis: AsyncMock
    ) -> None:
        """Invalid JSON from LLM → fallback default BLOCK decision."""
        bad_body = {
            "choices": [{"message": {"content": "I think you should buy BTC"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
        mock_resp = self._mock_httpx_response(body=bad_body)
        mock_http_client.send = AsyncMock(return_value=mock_resp)

        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        provider = _ProviderConfig(name="test", base_url="http://test", api_key="key", model="m")
        service = NineRouterService(
            http_client=mock_http_client,
            redis_client=mock_redis,
            provider_chain=[provider],
        )

        result = await service.analyze_market(_make_context())

        assert result is not None
        assert result.position_size == PositionSize.BLOCK

    @pytest.mark.asyncio
    async def test_multi_provider_fallback(self, mock_redis: AsyncMock) -> None:
        """If provider 1 fails, provider 2 is tried."""
        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        bad_resp = httpx.Response(
            500,
            json={"error": "Server error"},
            request=httpx.Request("POST", "http://p1/chat/completions"),
        )
        good_resp = httpx.Response(
            status_code=200,
            json={
                "choices": [{"message": {"content": _valid_ai_response()}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            request=httpx.Request("POST", "http://p2/chat/completions"),
        )

        client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        # First call → bad parse, second call → good
        call_count = 0

        async def _mock_send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return bad_resp
            return good_resp

        client.send = AsyncMock(side_effect=_mock_send)

        p1 = _ProviderConfig(name="provider1", base_url="http://p1", api_key="k", model="m1")
        p2 = _ProviderConfig(name="provider2", base_url="http://p2", api_key="k", model="m2")
        service = NineRouterService(
            http_client=client,
            redis_client=mock_redis,
            provider_chain=[p1, p2],
        )

        result = await service.analyze_market(_make_context())

        assert result is not None
        assert result.confidence_score == 72
        assert result.provider == "provider2"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_hit_skips_http(self, mock_redis: AsyncMock) -> None:
        """If cache has a valid entry, HTTP is not called."""
        cached_data = {
            "confidence_score": 80,
            "risk_level": "LOW",
            "position_size": "FULL",
            "entry_strategy": "MARKET",
            "stop_loss_strategy": "TIGHT",
            "reasoning": "Cached decision.",
            "key_risks": [],
            "key_opportunities": [],
            "bullish_probability": 70.0,
            "bearish_probability": 30.0,
            "summary": "Cached.",
            "provider": "cache",
            "model": "cache",
            "regime": "TREND_BULL",
            "cached_price": 64000.0,
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))

        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        mock_http = AsyncMock()
        provider = _ProviderConfig(name="test", base_url="http://test", api_key="key", model="m")
        service = NineRouterService(
            http_client=mock_http,
            redis_client=mock_redis,
            provider_chain=[provider],
        )

        result = await service.analyze_market(_make_context())

        assert result is not None
        assert result.confidence_score == 80
        assert result.position_size == PositionSize.FULL
        # HTTP should NOT have been called
        mock_http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_invalidated_on_regime_change(
        self, mock_http_client: httpx.AsyncClient, mock_redis: AsyncMock
    ) -> None:
        """Cache is invalidated when regime changes."""
        cached_data = {
            "confidence_score": 80,
            "risk_level": "LOW",
            "position_size": "FULL",
            "entry_strategy": "MARKET",
            "stop_loss_strategy": "TIGHT",
            "reasoning": "Old regime.",
            "regime": "RANGE",  # different from current TREND_BULL
            "cached_price": 64000.0,
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))

        mock_resp = self._mock_httpx_response()
        mock_http_client.send = AsyncMock(return_value=mock_resp)

        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        provider = _ProviderConfig(name="test", base_url="http://test", api_key="key", model="m")
        service = NineRouterService(
            http_client=mock_http_client,
            redis_client=mock_redis,
            provider_chain=[provider],
        )

        result = await service.analyze_market(_make_context())

        assert result is not None
        # Cache was deleted (regime mismatch) and fresh decision was fetched
        mock_redis.delete.assert_called()

    @pytest.mark.asyncio
    async def test_cache_invalidated_on_price_move(
        self, mock_http_client: httpx.AsyncClient, mock_redis: AsyncMock
    ) -> None:
        """Cache is invalidated when price moves >5%."""
        cached_data = {
            "confidence_score": 80,
            "risk_level": "LOW",
            "position_size": "FULL",
            "entry_strategy": "MARKET",
            "stop_loss_strategy": "TIGHT",
            "reasoning": "Old price.",
            "regime": "TREND_BULL",
            "cached_price": 60000.0,  # 6.25% below current 64000
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))

        mock_resp = self._mock_httpx_response()
        mock_http_client.send = AsyncMock(return_value=mock_resp)

        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        provider = _ProviderConfig(name="test", base_url="http://test", api_key="key", model="m")
        service = NineRouterService(
            http_client=mock_http_client,
            redis_client=mock_redis,
            provider_chain=[provider],
        )

        result = await service.analyze_market(_make_context())

        assert result is not None
        mock_redis.delete.assert_called()

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_request(
        self, mock_http_client: httpx.AsyncClient, mock_redis: AsyncMock
    ) -> None:
        """Circuit breaker in OPEN state blocks requests."""
        cb = AICircuitBreaker(failure_threshold=1, reset_timeout_seconds=300)
        cb.record_failure()  # trip immediately

        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        provider = _ProviderConfig(name="test", base_url="http://test", api_key="key", model="m")
        service = NineRouterService(
            http_client=mock_http_client,
            circuit_breaker=cb,
            redis_client=mock_redis,
            provider_chain=[provider],
        )

        result = await service.analyze_market(_make_context())
        assert result is None

    @pytest.mark.asyncio
    async def test_close_releases_client(self) -> None:
        """close() releases the owned httpx client."""
        from app.ai.nine_router_service import NineRouterService

        service = NineRouterService()
        assert service._owned_client is True

        with patch.object(httpx.AsyncClient, "aclose", new_callable=AsyncMock) as mock_close:
            await service.close()
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_redis_still_works(self, mock_http_client: httpx.AsyncClient) -> None:
        """Service works without Redis (no caching)."""
        mock_resp = self._mock_httpx_response()
        mock_http_client.send = AsyncMock(return_value=mock_resp)

        from app.ai.nine_router_service import NineRouterService, _ProviderConfig

        provider = _ProviderConfig(name="test", base_url="http://test", api_key="key", model="m")
        service = NineRouterService(
            http_client=mock_http_client,
            redis_client=None,
            provider_chain=[provider],
        )

        result = await service.analyze_market(_make_context())
        assert result is not None
        assert result.confidence_score == 72


# ---------------------------------------------------------------------------
# DTO Tests
# ---------------------------------------------------------------------------


class TestDTOs:
    def test_to_evidence(self) -> None:
        """AIDecisionDTO.to_evidence() downcasts correctly."""
        dto = AIDecisionDTO(
            confidence_score=75,
            risk_level=RiskLevel.LOW,
            position_size=PositionSize.HALF,
            entry_strategy=EntryStrategy.MARKET,
            stop_loss_strategy=StopLossStrategy.NORMAL,
            reasoning="Test reasoning.",
            key_risks=["risk1"],
            key_opportunities=["opp1"],
            bullish_probability=60.0,
            bearish_probability=40.0,
            summary="Test summary.",
        )
        evidence = dto.to_evidence()
        assert evidence.confidence == 75.0
        assert evidence.bullish_probability == 60.0
        assert evidence.bearish_probability == 40.0
        assert evidence.summary == "Test summary."
        assert "risk1" in evidence.reasons
        assert "opp1" in evidence.reasons

    def test_enum_values(self) -> None:
        """Enum string values match expected constants."""
        assert RiskLevel.LOW.value == "LOW"
        assert RiskLevel.MEDIUM.value == "MEDIUM"
        assert RiskLevel.HIGH.value == "HIGH"
        assert PositionSize.BLOCK.value == "BLOCK"
        assert PositionSize.QUARTER.value == "QUARTER"
        assert PositionSize.HALF.value == "HALF"
        assert PositionSize.FULL.value == "FULL"
        assert EntryStrategy.MARKET.value == "MARKET"
        assert EntryStrategy.LIMIT_RETEST.value == "LIMIT_RETEST"
        assert EntryStrategy.WAIT_PULLBACK.value == "WAIT_PULLBACK"
        assert StopLossStrategy.TIGHT.value == "TIGHT"
        assert StopLossStrategy.NORMAL.value == "NORMAL"
        assert StopLossStrategy.WIDE.value == "WIDE"


# ---------------------------------------------------------------------------
# Circuit Breaker Tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_initial_state_closed(self) -> None:
        cb = AICircuitBreaker(failure_threshold=3)
        assert cb.state == "CLOSED"
        assert cb.allow_request() is True

    def test_opens_after_threshold(self) -> None:
        cb = AICircuitBreaker(failure_threshold=2)
        cb.record_failure()
        assert cb.state == "CLOSED"
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_success_resets(self) -> None:
        cb = AICircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == "CLOSED"
        assert cb.failures == 0

    def test_half_open_after_timeout(self) -> None:
        cb = AICircuitBreaker(failure_threshold=1, reset_timeout_seconds=0)
        cb.record_failure()
        assert cb.state == "OPEN"
        # timeout=0 means immediate transition
        assert cb.allow_request() is True
        assert cb.state == "HALF_OPEN"
