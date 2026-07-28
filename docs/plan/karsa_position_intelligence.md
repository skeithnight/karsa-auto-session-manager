# Karsa v3 Proposal

# Execution Intelligence Architecture

### From Rule-Based Execution to Probabilistic Position Intelligence

---

# Vision

Karsa's current execution stack is already highly resilient and feature-rich.

```
Decision Engine
      │
      ▼
Portfolio Risk
      │
      ▼
Smart Order Router
      │
      ▼
Active Position Manager
      │
      ▼
Exchange
```

However, the intelligence layer is still distributed across multiple execution components.

Examples:

- SOR decides routing strategy based on market regime.
- APM decides when to scale out.
- APM decides when to trail.
- APM decides when to move breakeven.
- APM decides moon bag allocation.

These decisions are currently encoded as deterministic rules.

This creates three major problems:

- Strategy logic is duplicated.
- Execution and intelligence are tightly coupled.
- Every optimization requires code changes.

The goal of Karsa v3 is to separate **decision making** from **execution**.

---

# New Architecture

```
                     Decision Engine
                           │
                  Position Intent
                           │
                           ▼
          Position Intelligence Engine (PIE)
          ================================
          Continuous Probabilistic Reasoning
                           │
        ┌──────────────────┴──────────────────┐
        ▼                                     ▼
 Smart Order Router                     Active Position Manager
 Execution Optimizer                     Lifecycle Executor
        │                                     │
        └──────────────────┬──────────────────┘
                           ▼
                       Exchange
```

---

# Core Philosophy

Instead of

> Execution components deciding strategy

the architecture becomes

> Intelligence decides.
>
> Execution executes.

This separation enables:

- Better maintainability
- Better explainability
- Better experimentation
- Better learning
- Better optimization

---

# Component Responsibilities

| Component | Responsibility | Never Responsible For |
| ------------ | --------------- | ----------------------- |
| Decision Engine | Generate trade opportunities | Position management |
| Position Intelligence Engine | Continuous reasoning and recommendation | Exchange execution |
| Smart Order Router | Optimize entry execution | Trading strategy |
| Active Position Manager | Execute lifecycle actions | Strategy decisions |

---

# Smart Order Router v3

## Current State

Current routing flow:

```
Post Only

↓

Reprice

↓

Market
```

Routing policy depends on:

- regime
- spread
- imbalance

This means SOR currently contains trading logic.

---

# Proposed Responsibility

SOR should become a pure execution optimizer.

It receives an execution objective.

It does **not** know:

- Trend
- Range
- ATR
- Alpha
- Confidence Model

Instead it receives

```
ExecutionIntent
```

---

# ExecutionIntent

```python
ExecutionIntent

symbol

side

quantity

urgency

maker_bias

max_slippage

expected_edge

confidence

time_budget

expiry

cancel_policy
```

Example

```
Urgency

HIGH

Maker Bias

70%

Max Slippage

0.35%

Expected Edge

4.2R

Time Budget

12 seconds
```

SOR chooses execution strategy.

---

# Queue Intelligence

Current

```
Place order

↓

Wait

↓

Cancel

↓

Reprice
```

New

Estimate queue state.

```
Queue Size

Contracts Ahead

Estimated Fill Time

Fill Probability

Expected Queue Decay
```

Example

```
Best Bid

100.50

Queue Ahead

840 BTC

Expected Fill

18 seconds
```

Decision

```
Keep

Cancel

Reprice

Cross Spread
```

---

# Expected Fill Model

For every resting order calculate

```
P(fill within 1 sec)

P(fill within 5 sec)

P(fill within 10 sec)

Expected Waiting Cost

Expected Adverse Selection
```

Routing becomes

```
Maximum Expected Value
```

instead of

```
Retry Counter
```

---

# Opportunity Cost Model

Waiting for maker fill has cost.

Calculate

```
Maker Fee Savings

vs

Missed Opportunity
```

Example

```
Maker Fee Saved

0.02%

Expected Price Move

0.90%

Decision

Cross Spread
```

---

# Dynamic Repricing

Instead of

```
0.02%

0.02%

0.02%
```

Optimize using

- volatility
- queue position
- spread
- orderbook imbalance
- latency

Output

```
0.007%

0.013%

0.081%
```

---

# Latency Intelligence

Continuously measure

```
REST Latency

Websocket Delay

Redis Delay

Exchange ACK

Fill Latency
```

Execution policy changes automatically when latency increases.

---

# Execution Quality Metrics

SOR should publish

```
Implementation Shortfall

Maker Ratio

Average Queue Time

Average Fill Delay

Average Slippage

Market Impact

Cancelled Ratio

Opportunity Cost

Fill Probability Accuracy
```

---

# Active Position Manager v3

Current APM

```
Rule Engine
```

Future APM

```
Execution Engine
```

APM should never decide strategy.

It only executes recommendations.

---

# Current Rules

Examples

```
+0.25R

↓

Move BE
```

```
+2R

↓

Moon Bag
```

```
ATR x2.5

↓

Trail
```

These are deterministic.

---

# Proposed Flow

```
Position Intelligence

↓

Recommendation

↓

APM executes

↓

Exchange
```

---

# Dynamic Scale-Out

Current

```
33%

33%

33%
```

Future

Optimize

Inputs

```
Expected Remaining EV

Liquidity

Trend Strength

Volatility

Portfolio Risk
```

Output

```
Reduce 12%

Reduce 41%

Reduce 78%

Hold
```

---

# Dynamic Trailing Width

Current

```
ATR x2.5
```

Future

Optimizer inputs

```
ATR

Funding

Volatility

Trend Persistence

Liquidity

Orderbook Pressure
```

Output

```
1.8 ATR

2.4 ATR

4.1 ATR

Disabled
```

---

# Adaptive Breakeven

Instead of

```
0.25R
```

Compute

```
Optimal Breakeven Timing
```

Based on

- expected trend continuation
- volatility
- probability of pullback
- remaining edge

---

# Residual Position Optimizer

Replace Moon Bag.

Current

```
Close 80%

Leave 20%
```

Future

Optimize

```
Tail Probability

Remaining EV

Trend Strength

Risk Budget
```

Output

```
Leave

12%

34%

57%

82%
```

---

# Position Intelligence Engine (PIE)

## Purpose

The Position Intelligence Engine becomes the brain of every open position.

Every evaluation cycle

```
Collect Evidence

↓

Update Belief

↓

Estimate Expected Value

↓

Optimize Action

↓

Send Recommendation
```

---

# PIE Pipeline

```
Evidence Collector

↓

Belief Engine

↓

Expected Value Engine

↓

Risk Evaluator

↓

Action Optimizer

↓

Execution Recommendation
```

---

# Evidence Collector

Collect

```
Funding

Open Interest

CVD

Volume

Liquidity

Spread

Orderbook

Liquidations

Macro Events

Portfolio Exposure

News

AI Evidence

Exchange Health
```

---

# Belief Engine

Maintain

```
Posterior Confidence

Posterior Regime

Posterior Trend

Posterior Volatility

Posterior Risk
```

Confidence becomes dynamic.

Example

```
Entry

82%

↓

86%

↓

90%

↓

74%

↓

58%
```

Instead of remaining fixed forever.

---

# Expected Value Engine

Estimate

```
Expected Remaining R

Expected Drawdown

Tail Probability

Expected Holding Time

Risk Adjusted Return

Convexity

Opportunity Cost
```

---

# Action Optimizer

Produces recommendations only.

```
Hold

Reduce

Exit

Increase

Trail

Freeze

Hedge
```

No exchange logic.

No API calls.

Pure reasoning.

---

# Position Lifecycle

Replace

```
OPEN
```

With

```
NEW

↓

DISCOVERY

↓

ACCELERATION

↓

MATURITY

↓

DISTRIBUTION

↓

EXIT

↓

CLOSED
```

Every state has different execution behavior.

---

# Continuous Bayesian Updating

Instead of

```
Confidence

82%

Forever
```

PIE continuously updates

```
Evidence

↓

Posterior Confidence

↓

Expected Value

↓

Recommended Action
```

---

# Learning Engine

Every completed trade becomes training data.

```
Trade

↓

Replay

↓

Counterfactual Analysis

↓

Policy Evaluation

↓

Parameter Optimization

↓

Knowledge Base
```

Questions answered automatically

```
Would

ATR 3.1

perform better

than

ATR 2.5?
```

```
Would

holding 15 minutes longer

increase expectancy?
```

---

# Counterfactual Simulator

Replay every trade using multiple policies.

Example

```
Trade #4182

Policy A

Current

PnL

+2.1R

Policy B

Dynamic Trail

+3.6R

Policy C

No Scale Out

+5.4R
```

Automatically identify superior execution policies.

---

# Decision Trace

Every recommendation should be explainable.

Example

```
Position

BTCUSDT

Current Confidence

91%

Expected Remaining EV

+2.8R

Recommendation

Reduce 25%

Reason

Increasing liquidation pressure
Funding deteriorating
Orderbook imbalance
```

---

# Component Interaction

```
Decision Engine

↓

Generate Position Intent

↓

Position Intelligence Engine

↓

Recommend Action

↓

Smart Order Router

↓

Execute Entry

↓

Exchange

↓

Active Position Manager

↓

Execute Lifecycle

↓

Exchange
```

---

# Success Metrics

## SOR

- Maker Fill Ratio
- Average Slippage
- Fill Probability Calibration
- Implementation Shortfall
- Queue Prediction Accuracy
- Opportunity Cost

---

## PIE

- Recommendation Accuracy
- Confidence Calibration
- Expected Value Prediction Error
- Exit Quality
- Scale-Out Effectiveness
- Position Lifetime Optimization

---

## APM

- Execution Success Rate
- Stop Update Latency
- Partial Exit Accuracy
- Recovery Success
- State Synchronization
- Exchange Consistency

---

# Roadmap

## Phase 1

Architecture Separation

- Introduce Position Intelligence Engine
- Introduce ExecutionIntent
- Remove strategy from SOR
- Remove intelligence from APM

---

## Phase 2

Execution Optimization

- Queue Intelligence
- Fill Probability Model
- Opportunity Cost Model
- Adaptive Repricing
- Execution Quality Metrics

---

## Phase 3

Position Intelligence

- Bayesian Confidence Update
- Expected Value Engine
- Dynamic Scale-Out
- Adaptive Trailing
- Residual Position Optimizer
- Position State Machine

---

## Phase 4

Learning Platform

- Replay Engine
- Counterfactual Simulator
- Policy Evaluation
- Online Parameter Optimization
- Execution Attribution
- Continuous Learning

---

# End Goal

Karsa evolves from a rule-based execution framework into an adaptive Decision Intelligence Platform.

The execution stack no longer asks

> "Has a rule been triggered?"

Instead, every evaluation cycle asks

> "Given everything currently known about the market, portfolio, execution environment, and position state, what action maximizes expected value while respecting the defined risk constraints?"

This transforms execution from a deterministic workflow into a continuously optimized decision process, allowing Karsa to improve over time through evidence, replay, attribution, and learning rather than relying on static thresholds and handcrafted heuristics.
