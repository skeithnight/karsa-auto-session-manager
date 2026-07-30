# Karsa Auto Session Manager - Hybrid Intelligence Trading System

## Technical Implementation Plan

**Version:** 2.0  
**Objective:** Implement Statistical + AI Hybrid Decision Engine for Multi-Coin Trend Following Strategy  
**Target:** Profit through trend capture with intelligent risk management

---

## 📋 Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Core Components Specification](#2-core-components-specification)
3. [Statistical Feature Engine](#3-statistical-feature-engine)
4. [AI Decision Engine](#4-ai-decision-engine)
5. [Hybrid Decision Logic](#5-hybrid-decision-logic)
6. [Execution Engine Modifications](#6-execution-engine-modifications)
7. [Data Flow & Integration Points](#7-data-flow--integration-points)
8. [Configuration & Parameters](#8-configuration--parameters)
9. [Testing & Validation Strategy](#9-testing--validation-strategy)
10. [Implementation Phases](#10-implementation-phases)

---

## 1. System Architecture Overview

### 1.1 High-Level Architecture

```
─────────────────────────────────────────────────────────────────┐
│                    DATA LAYER (Existing)                        │
│  - OHLCV Data (141 symbols, 1H/4H/1D timeframes)               │
│  - Funding Rates                                                │
│  - Order Book Depth                                             │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              STATISTICAL FEATURE ENGINE (NEW)                   │
│  - Rolling Beta vs BTC (30-day)                                │
│  - Rolling Correlation vs BTC (24h/7d)                         │
│  - Volatility Metrics (ATR, Standard Deviation)                │
│  - Volume Analysis (Spike Detection)                           │
│  - Price Position (Distance from EMA, VWAP)                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
─────────────────────────────────────────────────────────────────┐
│               REGIME CLASSIFICATION (Enhanced)                  │
│  - Per-symbol regime (TREND/RANGE/CHOP)                        │
│  - BTC global regime                                            │
│  - Sector/Group regime (optional)                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
─────────────────────────────────────────────────────────────────┐
│               AI DECISION ENGINE (NEW)                          │
│  - Contextual reasoning using LLM                              │
│  - Confidence scoring (0-100)                                  │
│  - Risk assessment (Low/Medium/High)                           │
│  - Position size recommendation                                │
│  - Natural language reasoning                                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
─────────────────────────────────────────────────────────────────┐
│           HYBRID DECISION LOGIC (NEW)                           │
│  - Statistical guardrails (hard rules)                         │
│  - AI recommendation validation                                │
│  - Conflict resolution                                         │
│  - Final decision output                                       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│            EXECUTION ENGINE (Enhanced)                          │
│  - Smart order execution (limit vs market)                     │
│  - Volatility-based position sizing                            │
│  - Close-based trailing stop                                   │
│  - Concurrent position limits                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Key Design Principles

- **Defense in Depth:** Multiple layers of validation before trade execution
- **Statistical First, AI Second:** Hard math rules override AI recommendations
- **Explainability:** Every decision must have audit trail (statistical + AI reasoning)
- **Fail-Safe:** If AI fails or returns invalid data, statistical rules protect capital
- **Adaptability:** AI provides contextual intelligence for unique market conditions

---

## 2. Core Components Specification

### 2.1 New Components to Build

| Component | File Location | Purpose | Dependencies |
| ----------- | -------------- | --------- | -------------- |
| **StatisticalFeatureEngine** | `src/features/statistical_engine.py` | Calculate beta, correlation, volatility, volume metrics | pandas, numpy, existing OHLCV data |
| **AIDecisionEngine** | `src/ai/decision_engine.py` | LLM-based contextual analysis and recommendation | OpenAI API (or compatible), JSON parsing |
| **HybridDecisionEngine** | `src/decision/hybrid_engine.py` | Combine statistical + AI with guardrails | StatisticalFeatureEngine, AIDecisionEngine |
| **RiskProfileManager** | `src/risk/profile_manager.py` | Manage beta-aware position sizing and correlation limits | StatisticalFeatureEngine |

### 2.2 Existing Components to Modify

| Component | File Location | Modification Type | Details |
| ----------- | -------------- | ------------------- | --------- |
| **TrendContinuationEdgeFamily** | `src/edges/trend_continuation.py` | Enhance entry logic | Add volume confirmation, pullback entry, funding rate filter |
| **ScoreComposer** | `src/scoring/composer.py` | Replace logic | Change from "pick highest score" to regime-based family selection |
| **SizingPipeline** | `src/sizing/pipeline.py` | Complete refactor | Remove multiplier soup, implement volatility-based sizing |
| **ExecutionEngine** | `src/execution/engine.py` | Enhance | Add limit order logic, close-based trailing stop |
| **RegimeClassifier** | `src/regime/classifier.py` | Extend | Add BTC global regime, correlation-aware classification |

---

## 3. Statistical Feature Engine

### 3.1 Feature Specifications

#### 3.1.1 Rolling Beta vs BTC

- **Purpose:** Measure sensitivity of coin price movements relative to BTC
- **Formula:** `Beta = Covariance(Coin_Returns, BTC_Returns) / Variance(BTC_Returns)`
- **Window:** 30 days (720 hours for 1H candles)
- **Update Frequency:** Every candle close
- **Output Range:** Typically -2.0 to +3.0
- **Interpretation:**
  - Beta > 1.5: High volatility, amplifies BTC moves
  - Beta 0.8-1.2: Moves roughly with BTC
  - Beta < 0.5: Low correlation, defensive/independent
  - Beta < 0: Negative correlation (rare, hedge opportunity)

#### 3.1.2 Rolling Correlation vs BTC

- **Purpose:** Measure synchronization of price movements
- **Formula:** Pearson correlation coefficient
- **Windows:**
  - Short-term: 24 hours (for immediate risk)
  - Medium-term: 7 days (for trend alignment)
- **Output Range:** -1.0 to +1.0
- **Interpretation:**
  - Correlation > 0.85: Highly synchronized (treat as same position)
  - Correlation 0.5-0.85: Moderate correlation
  - Correlation < 0.5: Independent movement (alpha opportunity)

#### 3.1.3 Volatility Metrics

- **ATR Percentage:** `ATR(14) / Close_Price * 100`
  - Purpose: Normalize volatility across different price levels
  - Window: 14 periods
  - Use case: Position sizing, stop-loss distance

- **Standard Deviation of Returns:** Rolling 20-period std dev of % returns
  - Purpose: Measure price dispersion
  - Use case: Volatility regime detection

#### 3.1.4 Volume Analysis

- **Volume Spike Ratio:** `Current_Volume / SMA(Volume, 20)`
  - Purpose: Detect unusual trading activity
  - Threshold: > 1.5x = confirmed breakout, < 0.7x = weak move
  - Use case: Breakout confirmation

- **Volume Trend:** Slope of volume SMA over last 5 periods
  - Purpose: Detect increasing/decreasing interest
  - Use case: Trend strength validation

#### 3.1.5 Price Position Metrics

- **Distance from EMA50:** `(Close - EMA50) / EMA50 * 100`
  - Purpose: Measure overextension
  - Use case: Entry timing (avoid buying at extreme extensions)

- **Distance from VWAP:** `(Close - VWAP) / VWAP * 100`
  - Purpose: Measure fair value deviation
  - Use case: Mean reversion signals

#### 3.1.6 Funding Rate Metrics

- **Current Funding Rate:** From exchange API
- **Funding Rate Trend:** 8-hour rolling average
- **Annualized Funding Cost:** `Funding_Rate * 3 * 365 * 100` (for perp markets)
- **Threshold:** > 0.01% per 8h = expensive to hold long

### 3.2 Data Structure Output

```python
# Feature output structure (dict or dataclass)
{
    "symbol": "SOL/USDT",
    "timestamp": "2025-01-15T14:00:00Z",
    
    # Beta & Correlation
    "beta_30d": 1.42,
    "correlation_24h": 0.78,
    "correlation_7d": 0.85,
    
    # Volatility
    "atr_pct": 4.23,
    "std_dev_returns": 2.15,
    "volatility_regime": "HIGH",  # LOW/MEDIUM/HIGH
    
    # Volume
    "volume_spike_ratio": 1.82,
    "volume_trend_slope": 0.15,
    "volume_regime": "ELEVATED",
    
    # Price Position
    "distance_from_ema50_pct": 3.2,
    "distance_from_vwap_pct": 1.8,
    "price_vs_ema50": "ABOVE",
    
    # Funding (for perps)
    "funding_rate": 0.0008,
    "funding_rate_8h_avg": 0.0012,
    "annualized_funding_cost_pct": 10.51,
    
    # Derived Signals
    "breakout_confirmed": True,
    "overextended": False,
    "volume_confirmed": True
}
```

---

## 4. AI Decision Engine

### 4.1 Architecture

**Model Selection:**

- Primary: GPT-4 or GPT-4-turbo (OpenAI)
- Alternative: Claude 3.5 Sonnet (Anthropic) or local LLM (Llama 3.1 70B)
- Requirements: JSON mode support, function calling capability

**API Configuration:**

- Temperature: 0.3 (low for consistency)
- Max tokens: 500
- Response format: Strict JSON
- Timeout: 10 seconds
- Retry logic: 3 attempts with exponential backoff

### 4.2 Prompt Engineering

#### 4.2.1 System Prompt

```
You are an expert crypto trading analyst with 10+ years of experience in quantitative trading. 
Your role is to evaluate trading setups based on statistical data and market context.

Guidelines:
1. Be conservative - prioritize capital preservation over aggressive gains
2. Consider risk-reward ratio in every decision
3. Account for market regime and macro context
4. Identify potential risks and red flags
5. Provide clear, actionable recommendations

You MUST respond in valid JSON format matching the specified schema.
```

#### 4.2.2 User Prompt Template

```
Evaluate this crypto trading setup:

## Symbol Information
Symbol: {symbol}
Current Price: ${price}
Timeframe: 1H

## Market Regime
Current Symbol Regime: {regime} (ADX: {adx})
BTC Regime: {btc_regime}
ETH Regime: {eth_regime}

## Statistical Features
Beta vs BTC (30d): {beta_30d}
Correlation vs BTC (24h): {correlation_24h}
Correlation vs BTC (7d): {correlation_7d}

## Volatility & Risk
ATR (%): {atr_pct}
Volatility Regime: {volatility_regime}
Standard Deviation (20p): {std_dev}

## Price Action
Distance from EMA50: {distance_from_ema50_pct}%
Distance from VWAP: {distance_from_vwap_pct}%
Price Position: {price_vs_ema50}

## Volume Analysis
Volume Spike: {volume_spike_ratio}x average
Volume Trend: {volume_trend}
Volume Confirmation: {volume_confirmed}

## Funding (Perpetual Futures)
Current Funding Rate: {funding_rate}%
8h Average: {funding_rate_8h_avg}%
Annualized Cost: {annualized_funding_cost_pct}%

## Context
- BTC is currently {btc_regime.lower()}
- This coin has {beta_description} beta ({beta_30d})
- Correlation with BTC is {correlation_description} ({correlation_24h})
- Volume is {volume_description} ({volume_spike_ratio}x average)
- {additional_context}

## Task
Analyze this setup and provide:
1. Confidence Score (0-100): How strong is this trading opportunity?
2. Risk Level: Low, Medium, or High
3. Position Size Recommendation: BLOCK, QUARTER, HALF, FULL
4. Entry Strategy: Market, Limit at retest, or Wait for pullback
5. Stop-Loss Strategy: Tight (1x ATR), Normal (2x ATR), or Wide (3x ATR)
6. Reasoning: 2-3 sentences explaining your decision

## Output Format
Respond in this exact JSON format:
{
  "confidence_score": 0-100,
  "risk_level": "LOW|MEDIUM|HIGH",
  "position_size": "BLOCK|QUARTER|HALF|FULL",
  "entry_strategy": "MARKET|LIMIT_RETEST|WAIT_PULLBACK",
  "stop_loss_strategy": "TIGHT|NORMAL|WIDE",
  "reasoning": "string",
  "key_risks": ["risk1", "risk2"],
  "key_opportunities": ["opp1", "opp2"]
}
```

### 4.3 AI Output Processing

**Validation Rules:**

1. JSON must be parseable
2. All required fields must be present
3. Confidence score must be 0-100
4. Risk level must be LOW/MEDIUM/HIGH
5. Position size must be BLOCK/QUARTER/HALF/FULL

**Fallback Logic:**

- If JSON parsing fails → Return default: confidence=50, size="BLOCK"
- If confidence < 60 → Override to size="BLOCK"
- If response time > 10s → Timeout and use statistical-only decision

**Caching Strategy:**

- Cache AI decisions per symbol per 4 hours
- Invalidate cache if:
  - Regime changes
  - Price moves > 5%
  - Volume spike > 2x

---

## 5. Hybrid Decision Logic

### 5.1 Decision Flow

```
1. Calculate Statistical Features
   ↓
2. Check Hard Guardrails (Statistical Rules)
   ├─ If FAIL → BLOCK trade (no AI needed)
   └─ If PASS → Continue
   ↓
3. Request AI Evaluation
   ├─ If AI timeout/error → Use statistical-only decision
   └─ If AI success → Continue
   ↓
4. Apply Hybrid Rules
   ├─ AI says BLOCK → BLOCK (conservative)
   ├─ AI says FULL but beta > 1.5 → DOWNGRADE to HALF
   ├─ AI confidence < 60 → BLOCK
   ├─ BTC DOWNTREND + beta > 1.2 → BLOCK
   └─ All checks pass → Use AI recommendation
   ↓
5. Final Position Sizing
   ├─ Apply volatility-based sizing
   ├─ Apply correlation limits
   └─ Check concurrent position cap
   ↓
6. Execute or Reject
```

### 5.2 Hard Guardrails (Non-Negotiable Rules)

These rules ALWAYS override AI recommendations:

| Rule ID | Condition | Action | Rationale |
| --------- | ----------- | -------- | ----------- |
| GR-01 | AI confidence < 60 | BLOCK | Low conviction = no trade |
| GR-02 | BTC regime = DOWNTREND AND beta > 1.2 | BLOCK | High beta coins crash harder |
| GR-03 | Funding rate > 0.01% per 8h | BLOCK LONG | Too expensive to hold |
| GR-04 | Volume spike < 1.2x on breakout | BLOCK | Weak breakout, likely fakeout |
| GR-05 | Distance from EMA50 > 10% | BLOCK or QUARTER | Overextended, high reversal risk |
| GR-06 | Concurrent positions >= 3 | BLOCK | Diversification limit |
| GR-07 | Correlation with existing position > 0.85 | BLOCK | Avoid double risk |
| GR-08 | ATR > 8% | QUARTER size max | Extreme volatility |
| GR-09 | Market regime = CHOP | BLOCK | No clear trend |
| GR-10 | AI response timeout > 10s | Use statistical-only | System reliability |

### 5.3 Soft Guardrails (Downgrade Rules)

These rules reduce position size but don't block entirely:

| Rule ID | Condition | Action | Rationale |
| --------- | ----------- | -------- | ----------- |
| SG-01 | AI says FULL but beta > 1.5 | DOWNGRADE to HALF | High volatility risk |
| SG-02 | AI says FULL but correlation > 0.8 | DOWNGRADE to HALF | Highly correlated to BTC |
| SG-03 | BTC regime = SIDEWAYS | DOWNGRADE one level | Uncertain macro |
| SG-04 | Volume spike 1.2-1.5x | DOWNGRADE one level | Moderate confirmation |
| SG-05 | Distance from EMA50 5-10% | DOWNGRADE one level | Somewhat overextended |

### 5.4 Position Size Mapping

```python
# AI recommendation → Final size after guardrails
{
    "BLOCK": {
        "after_soft_rules": "BLOCK",
        "position_pct": 0.0
    },
    "QUARTER": {
        "after_soft_rules": "QUARTER or BLOCK",
        "position_pct": 0.25  # 25% of normal size
    },
    "HALF": {
        "after_soft_rules": "HALF or QUARTER",
        "position_pct": 0.50  # 50% of normal size
    },
    "FULL": {
        "after_soft_rules": "FULL or HALF",
        "position_pct": 1.00  # 100% of normal size
    }
}

# Final position calculation:
# base_risk = 1% of total equity
# stop_distance = entry_price - stop_loss_price
# position_size = (base_risk * position_pct) / stop_distance
```

---

## 6. Execution Engine Modifications

### 6.1 Smart Order Execution

**Current Behavior:** Market order on signal
**New Behavior:** Intelligent order placement

#### 6.1.1 Entry Strategy Logic

```
IF entry_strategy == "MARKET":
    - Execute immediately at market price
    - Use only when: Volume spike > 2x AND breakout is strong
    
ELIF entry_strategy == "LIMIT_RETEST":
    - Place limit order at: EMA20 or breakout level * 0.995
    - Time to live (TTL): 2 candles (2 hours for 1H timeframe)
    - If not filled: Cancel and wait for next signal
    
ELIF entry_strategy == "WAIT_PULLBACK":
    - Place limit order at: EMA50 or support level
    - TTL: 4 candles (4 hours)
    - If not filled: Cancel
```

#### 6.1.2 Order Sizing Validation

Before placing order:

1. Check order book depth at intended price
2. Calculate slippage: `slippage_pct = (intended_size / order_book_depth_1pct) * 100`
3. If slippage_pct > 0.5%:
   - Reduce position size by 50%, OR
   - Split into 3 smaller orders (TWAP over 15 minutes)

### 6.2 Stop-Loss Strategy

**Current Behavior:** Fixed percentage or ATR-based
**New Behavior:** Adaptive, close-based trailing stop

#### 6.2.1 Initial Stop-Loss Placement

```
IF stop_loss_strategy == "TIGHT":
    stop_distance = 1.0 * ATR(14)
    
ELIF stop_loss_strategy == "NORMAL":
    stop_distance = 2.0 * ATR(14)
    
ELIF stop_loss_strategy == "WIDE":
    stop_distance = 3.0 * ATR(14)

stop_loss_price = entry_price - stop_distance
```

#### 6.2.2 Trailing Stop Logic

**Type:** Close-based (not wick-based)

```
# Update trailing stop every candle close
highest_since_entry = max(all_highs_since_entry)
trailing_stop = highest_since_entry - (2.0 * current_ATR)

# Only trigger on candle CLOSE below trailing stop
IF candle_close < trailing_stop:
    EXECUTE stop-loss (market order)
ELSE:
    HOLD position (ignore wicks)
```

**Rationale:** Crypto markets have frequent "scam wicks" that trigger stops and reverse. Close-based stops avoid this.

### 6.3 Take-Profit Strategy

**Philosophy:** No fixed take-profit. Let winners run with trailing stop.

**Optional Profit Lock:**

- If position profit > 3x risk (3R):
  - Move stop-loss to breakeven (entry price)
  - Guarantees no loss on this trade

### 6.4 Concurrent Position Management

**Limit:** Maximum 3 concurrent LONG positions

**Selection Logic:**

1. Rank all signals by: `(AI_confidence * volume_spike) / beta`
2. Take top 3 signals
3. Reject any new signals while at capacity

**Correlation Check:**

- Before opening new position:
  - Calculate correlation with all existing positions
  - If correlation > 0.85 with any position:
    - Reject new position (treat as same risk)

---

## 7. Data Flow & Integration Points

### 7.1 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    EXCHANGE API                              │
│  (Binance/Bybit/OKX)                                        │
│  - OHLCV streams (141 symbols)                              │
│  - Funding rates                                            │
│  - Order book snapshots                                     │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────┐
│                 DATA ENGINE (Existing)                       │
│  - Data normalization                                       │
│  - Candle aggregation                                       │
│  - Database storage (PostgreSQL/TimescaleDB)                │
──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
─────────────────────────────────────────────────────────────┐
│          STATISTICAL FEATURE ENGINE (NEW)                    │
│  - Calculate beta, correlation, ATR, volume metrics         │
│  - Update on every candle close                             │
│  - Store in Redis cache (TTL: 1 hour)                       │
──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
─────────────────────────────────────────────────────────────┐
│            REGIME CLASSIFIER (Enhanced)                      │
│  - Per-symbol regime detection                              │
│  - BTC global regime                                        │
│  - Output: TREND/RANGE/CHOP per symbol                      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
─────────────────────────────────────────────────────────────┐
│          HYBRID DECISION ENGINE (NEW)                        │
│  ├─ Statistical guardrails check                            │
│  ├─ AI decision engine call (if needed)                     │
│  ├─ Apply hybrid rules                                      │
│  └─ Output: {action: LONG/SHORT/BLOCK, size: %, reasoning}  │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────┐
│           SIZING PIPELINE (Refactored)                       │
│  - Volatility-based position calculation                    │
│  - Apply position size from hybrid decision                 │
│  - Check concurrent position limits                         │
│  - Output: Exact position size in quote currency            │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────
│          EXECUTION ENGINE (Enhanced)                         │
│  - Smart order placement (limit vs market)                  │
│  - Stop-loss and trailing stop management                   │
│  - Order reconciliation                                     │
│  - Database logging                                         │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────┐
│              MONITORING & LOGGING                            │
│  - Trade logs (strategy_trades table)                       │
│  - AI reasoning logs (ai_decisions table)                   │
│  - Performance metrics (win rate, Sharpe, drawdown)         │
│  - Real-time alerts (Telegram/Discord)                      │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 Integration Points with Existing Code

#### 7.2.1 Edge Family Integration

**File:** `src/edges/trend_continuation.py`

**Current:** Heuristic scoring with multipliers
**New:** Statistical feature-based scoring

```python
# Pseudocode for integration
class TrendContinuationEdge:
    def calculate_score(self, symbol: str) -> Dict:
        # Get statistical features
        features = self.feature_engine.get_features(symbol)
        
        # Check basic conditions
        if not self._check_breakout_confirmed(features):
            return {"active": False, "score": 0}
        
        if not self._check_volume_confirmed(features):
            return {"active": False, "score": 0}
        
        # Calculate base score from statistical strength
        base_score = self._calculate_statistical_score(features)
        
        return {
            "active": True,
            "score": base_score,
            "features": features,
            "metadata": {
                "breakout_strength": features['distance_from_ema50_pct'],
                "volume_confirmation": features['volume_spike_ratio'],
                "beta": features['beta_30d']
            }
        }
```

#### 7.2.2 Score Composer Integration

**File:** `src/scoring/composer.py`

**Current:** Pick highest score across all families
**New:** Regime-based family selection

```python
# Pseudocode for integration
class ScoreComposer:
    def compose(self, symbol: str) -> Dict:
        # Get regime
        regime = self.regime_classifier.get_regime(symbol)
        
        # Select appropriate edge families based on regime
        if regime == "TREND":
            active_families = ["TrendContinuation"]
        elif regime == "RANGE":
            active_families = ["MeanReversion"]
        else:  # CHOP
            active_families = []
        
        # Evaluate only active families
        scores = []
        for family in active_families:
            score = self.edge_families[family].calculate_score(symbol)
            scores.append(score)
        
        # Return best score from active families
        if not scores:
            return {"active": False}
        
        best = max(scores, key=lambda x: x['score'])
        return best
```

#### 7.2.3 Sizing Pipeline Integration

**File:** `src/sizing/pipeline.py`

**Current:** Multiplier soup (7 layers)
**New:** Volatility-based sizing with AI input

```python
# Pseudocode for integration
class SizingPipeline:
    def calculate(self, symbol: str, ai_decision: Dict) -> Decimal:
        # Get statistical features
        features = self.feature_engine.get_features(symbol)
        
        # Base risk: 1% of equity
        base_risk = self.account_equity * Decimal("0.01")
        
        # Apply AI position size recommendation
        size_multiplier = self._get_size_multiplier(ai_decision['position_size'])
        adjusted_risk = base_risk * size_multiplier
        
        # Calculate stop distance
        stop_distance = self._calculate_stop_distance(
            features['atr_pct'],
            ai_decision['stop_loss_strategy']
        )
        
        # Position size = risk / stop_distance
        position_size = adjusted_risk / stop_distance
        
        # Apply hard limits
        position_size = self._apply_limits(position_size, features)
        
        return position_size
```

---

## 8. Configuration & Parameters

### 8.1 Configuration File Structure

**File:** `config/hybrid_trading.yaml`

```yaml
# Statistical Feature Engine
statistical_features:
  beta_window_days: 30
  correlation_windows:
    short_term_hours: 24
    medium_term_days: 7
  atr_period: 14
  volume_sma_period: 20
  ema_periods: [20, 50, 200]

# AI Decision Engine
ai_engine:
  provider: "openai"  # or "anthropic" or "local"
  model: "gpt-4-turbo"
  temperature: 0.3
  max_tokens: 500
  timeout_seconds: 10
  max_retries: 3
  cache_ttl_hours: 4
  
  # API keys (use environment variables in production)
  openai_api_key: "${OPENAI_API_KEY}"
  anthropic_api_key: "${ANTHROPIC_API_KEY}"

# Hybrid Decision Rules
hybrid_rules:
  # Hard guardrails
  hard_guardrails:
    min_ai_confidence: 60
    max_beta_in_btc_downtrend: 1.2
    max_funding_rate_pct: 0.01
    min_volume_spike: 1.2
    max_distance_from_ema50_pct: 10.0
    max_concurrent_positions: 3
    max_correlation_with_existing: 0.85
    max_atr_pct: 8.0
    
  # Soft guardrails (downgrade rules)
  soft_guardrails:
    downgrade_if_beta_above: 1.5
    downgrade_if_correlation_above: 0.8
    downgrade_in_btc_sideways: true
    
  # Position size mapping
  position_size_mapping:
    BLOCK: 0.0
    QUARTER: 0.25
    HALF: 0.50
    FULL: 1.00

# Execution Engine
execution:
  # Order execution
  default_slippage_limit_pct: 0.5
  limit_order_ttl_candles: 2
  max_order_split_count: 3
  
  # Stop-loss
  trailing_stop_atr_multiplier: 2.0
  breakeven_trigger_profit_multiple: 3.0
  
  # Risk management
  base_risk_per_trade_pct: 1.0
  max_portfolio_risk_pct: 5.0

# Monitoring
monitoring:
  log_ai_decisions: true
  log_statistical_features: true
  metrics_collection:
    - win_rate
    - avg_profit_per_trade
    - sharpe_ratio
    - max_drawdown
    - fakeout_count
```

### 8.2 Environment Variables

```bash
# Exchange API
EXCHANGE_API_KEY=your_api_key
EXCHANGE_API_SECRET=your_api_secret

# AI Providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/karsa_db
REDIS_URL=redis://localhost:6379

# Bot Configuration
BOT_MODE=shadow  # or "live"
ACCOUNT_ID=your_account_id
```

---

## 9. Testing & Validation Strategy

### 9.1 Unit Testing Requirements

**Coverage Target:** 80% minimum

#### 9.1.1 Statistical Feature Engine Tests

- [ ] Test beta calculation with known data (verify against manual calculation)
- [ ] Test correlation calculation edge cases (zero variance, perfect correlation)
- [ ] Test ATR calculation accuracy
- [ ] Test volume spike detection
- [ ] Test feature output structure validation

#### 9.1.2 AI Decision Engine Tests

- [ ] Test prompt generation (verify all features included)
- [ ] Test JSON parsing (valid and invalid responses)
- [ ] Test fallback logic on timeout
- [ ] Test fallback logic on parsing error
- [ ] Test caching mechanism

#### 9.1.3 Hybrid Decision Engine Tests

- [ ] Test hard guardrail enforcement (all 10 rules)
- [ ] Test soft guardrail downgrades (all 5 rules)
- [ ] Test position size mapping
- [ ] Test AI recommendation override scenarios
- [ ] Test concurrent position limit enforcement

#### 9.1.4 Execution Engine Tests

- [ ] Test limit order price calculation
- [ ] Test trailing stop update logic
- [ ] Test close-based vs wick-based stop trigger
- [ ] Test slippage calculation and order splitting

### 9.2 Integration Testing

**Shadow Mode Testing:**

1. Deploy to shadow container
2. Run for minimum 2 weeks
3. Collect data on:
   - Number of signals generated
   - AI decision accuracy (confidence vs actual outcome)
   - Guardrail trigger frequency
   - Win rate and profit factor
4. Compare shadow performance vs hypothetical backtest

**Test Scenarios:**

- [ ] BTC uptrend + altcoin breakout
- [ ] BTC downtrend + altcoin high beta
- [ ] BTC sideways + altcoin low correlation
- [ ] Volume spike without price breakout (fakeout)
- [ ] Multiple simultaneous signals (concurrent position test)
- [ ] API timeout/failure scenarios

### 9.3 Performance Metrics

**Track from Day 1:**

| Metric | Target | Calculation |
| -------- | -------- | ------------- |
| Win Rate | > 45% | Winning trades / Total trades |
| Profit Factor | > 1.5 | Gross profit / Gross loss |
| Sharpe Ratio | > 1.0 | (Return - Risk-free) / Std Dev |
| Max Drawdown | < 15% | Largest peak-to-trough decline |
| Average Win/Loss Ratio | > 2.0 | Avg win size / Avg loss size |
| Fakeout Rate | < 30% | Breakouts that reverse within 3 candles |
| AI Accuracy | > 60% | High confidence (>70) trades that profit |

### 9.4 Validation Checklist Before Live Deployment

- [ ] All 15 existing test failures resolved
- [ ] New unit tests passing (80% coverage)
- [ ] Shadow mode running for 2+ weeks
- [ ] Shadow mode win rate > 40%
- [ ] No critical bugs in execution engine
- [ ] AI API costs within budget
- [ ] Database logging working correctly
- [ ] Alert system tested (Telegram/Discord)
- [ ] Emergency stop mechanism tested
- [ ] Documentation updated

---

## 10. Implementation Phases

### Phase 1: Foundation (Week 1-2)

**Objective:** Fix existing issues and build statistical foundation

**Tasks:**

1. [ ] Fix all 15 failing tests in current codebase
2. [ ] Implement `StatisticalFeatureEngine` class
   - Beta calculation
   - Correlation calculation
   - ATR and volatility metrics
   - Volume analysis
3. [ ] Add unit tests for statistical engine (target: 90% coverage)
4. [ ] Integrate feature engine with existing data layer
5. [ ] Deploy to shadow container for data collection

**Deliverables:**

- Working statistical feature calculator
- Test suite passing
- Shadow container collecting features

**Success Criteria:**

- All features calculate correctly (verified against manual calculations)
- No performance degradation (features calculated in < 1 second per symbol)
- Shadow container running stable for 48 hours

---

### Phase 2: AI Integration (Week 3)

**Objective:** Integrate LLM decision-making

**Tasks:**

1. [ ] Implement `AIDecisionEngine` class
   - Prompt template construction
   - API integration (OpenAI/Anthropic)
   - JSON parsing and validation
   - Caching mechanism
2. [ ] Create AI prompt templates
   - System prompt
   - User prompt with feature injection
3. [ ] Implement fallback logic
   - Timeout handling
   - Error handling
   - Default decision on failure
4. [ ] Add unit tests for AI engine
5. [ ] Test AI responses with historical data (dry run)

**Deliverables:**

- Working AI decision engine
- Prompt templates optimized
- Fallback mechanisms tested

**Success Criteria:**

- AI responds within 10 seconds consistently
- JSON parsing success rate > 95%
- Fallback logic triggers correctly on errors
- AI reasoning is coherent and actionable

---

### Phase 3: Hybrid Decision Logic (Week 4)

**Objective:** Combine statistical + AI with guardrails

**Tasks:**

1. [ ] Implement `HybridDecisionEngine` class
   - Hard guardrail enforcement
   - Soft guardrail downgrades
   - Position size mapping
2. [ ] Implement guardrail rules (10 hard, 5 soft)
3. [ ] Integrate with existing `ScoreComposer`
   - Replace "pick highest score" logic
   - Add regime-based family selection
4. [ ] Add unit tests for hybrid logic
5. [ ] Deploy to shadow container

**Deliverables:**

- Working hybrid decision engine
- All guardrails implemented and tested
- Integration with scoring system

**Success Criteria:**

- Hard guardrails block trades as expected
- Soft guardrails downgrade positions correctly
- Shadow container shows reasonable decision distribution
- No trades violate hard guardrails

---

### Phase 4: Execution Enhancements (Week 5)

**Objective:** Smart order execution and risk management

**Tasks:**

1. [ ] Refactor `SizingPipeline`
   - Remove multiplier soup
   - Implement volatility-based sizing
   - Integrate AI position size recommendation
2. [ ] Enhance `ExecutionEngine`
   - Smart order placement (limit vs market)
   - Close-based trailing stop
   - Slippage protection
3. [ ] Implement concurrent position manager
   - Max 3 positions limit
   - Correlation checking
4. [ ] Add unit tests for execution logic
5. [ ] Deploy to shadow container

**Deliverables:**

- Refactored sizing pipeline
- Smart execution engine
- Position management system

**Success Criteria:**

- Position sizing matches volatility targets
- Limit orders placed correctly
- Trailing stop updates on candle close
- Concurrent position limit enforced

---

### Phase 5: Monitoring & Optimization (Week 6-7)

**Objective:** Observability and performance tuning

**Tasks:**

1. [ ] Implement comprehensive logging
   - AI decisions with reasoning
   - Statistical features snapshot
   - Guardrail triggers
   - Execution details
2. [ ] Add performance metrics collection
   - Win rate per family
   - AI confidence vs actual outcome
   - Guardrail effectiveness
3. [ ] Create monitoring dashboard
   - Real-time position status
   - PnL tracking
   - AI decision history
4. [ ] Set up alerts
   - Trade execution alerts
   - Guardrail trigger alerts
   - System health alerts
5. [ ] Run shadow mode for 2 weeks
6. [ ] Analyze results and tune parameters

**Deliverables:**

- Full monitoring system
- Performance metrics dashboard
- Alert system
- 2 weeks of shadow mode data

**Success Criteria:**

- All metrics collected and visible
- Alerts trigger correctly
- Shadow mode win rate > 40%
- No critical bugs discovered

---

### Phase 6: Live Deployment (Week 8)

**Objective:** Gradual live deployment with risk controls

**Tasks:**

1. [ ] Final validation checklist
   - All tests passing
   - Shadow mode metrics acceptable
   - Documentation complete
2. [ ] Canary deployment
   - Start with 10% position size
   - Monitor first 5 trades closely
3. [ ] Gradual scale-up
   - Week 1: 10% size
   - Week 2: 25% size (if no issues)
   - Week 3: 50% size (if metrics good)
   - Week 4: 100% size (full deployment)
4. [ ] Continuous monitoring
   - Daily performance review
   - Weekly parameter tuning
5. [ ] Post-deployment optimization
   - Analyze losing trades
   - Tune AI prompts if needed
   - Adjust guardrail thresholds

**Deliverables:**

- Live bot running
- Performance tracking
- Optimization loop established

**Success Criteria:**

- No catastrophic losses
- Win rate maintains > 40%
- Drawdown < 10%
- System stable for 30 days

---

## Appendix A: Glossary

| Term | Definition |
| ------ | ------------ |
| **Beta** | Measure of coin's sensitivity to BTC price movements |
| **Correlation** | Measure of how synchronized two assets' price movements are |
| **ATR** | Average True Range - measure of volatility |
| **EMA** | Exponential Moving Average |
| **VWAP** | Volume Weighted Average Price |
| **Guardrail** | Hard rule that overrides AI recommendations |
| **Shadow Mode** | Running bot with fake money to test logic |
| **Fakeout** | Breakout that immediately reverses |
| **Scam Wick** | Brief price spike that triggers stops before reversing |
| **Concurrent Positions** | Number of open trades at the same time |

---

## Appendix B: Risk Warnings

1. **AI Hallucination:** LLMs can generate incorrect or nonsensical reasoning. Always validate with statistical guardrails.
2. **API Costs:** AI calls cost money. Budget approximately $50-200/month for GPT-4 usage depending on trade frequency.
3. **Overfitting:** Don't optimize parameters too much on historical data. Markets change.
4. **Correlation Risk:** During market crashes, all correlations go to 1.0. Diversification may fail.
5. **Slippage:** In volatile markets, expected fill prices may not be achieved.
6. **Exchange Risk:** API downtime, rate limits, or exchange issues can prevent trade execution.
7. **Smart Contract Risk:** If trading on DEXs, smart contract bugs can cause total loss.
8. **Regulatory Risk:** Crypto regulations may change and affect trading.

---

## Appendix C: Success Metrics Definition

**Win Rate:**

```
Win Rate = (Number of Winning Trades / Total Trades) * 100
Target: > 45%
```

**Profit Factor:**

```
Profit Factor = Gross Profit / Gross Loss
Target: > 1.5
```

**Sharpe Ratio:**

```
Sharpe = (Portfolio Return - Risk-Free Rate) / Portfolio Std Dev
Annualized: Sharpe * sqrt(365) for daily, sqrt(252*24) for hourly
Target: > 1.0
```

**Maximum Drawdown:**

```
Max Drawdown = (Peak Equity - Lowest Trough) / Peak Equity * 100
Target: < 15%
```

**Average Win/Loss Ratio:**

```
Win/Loss Ratio = Average Winning Trade Size / Average Losing Trade Size
Target: > 2.0
```

---

**Document Version:** 1.0  
**Last Updated:** 2025-01-15  
**Author:** Quant Trading System Design  
**Status:** Ready for Implementation
