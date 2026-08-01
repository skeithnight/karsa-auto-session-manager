import sys

def replace_in_file(path, replacements):
    with open(path, 'r') as f:
        content = f.read()
    
    for old, new in replacements:
        if old not in content:
            print(f"WARN: Could not find text in {path}:\n{old[:50]}...")
            continue
        content = content.replace(old, new)
        
    with open(path, 'w') as f:
        f.write(content)

# 1. README.md
replace_in_file('README.md', [
    (
        '## 🏗 System Architecture (The 7 Keys)\n\nOur architecture is split into critical paths ensuring robustness and modularity:\n',
        '## 🏗 System Architecture (The 7 Keys)\n\n![Karsa E2E Workflow](assets/karsa_asm_e2e_workfow.png)\n\nOur architecture is split into critical paths ensuring robustness and modularity:\n'
    )
])

# 2. docs/E2E_WORKFLOW.md
replace_in_file('docs/E2E_WORKFLOW.md', [
    (
        '### Stage 1 — Universe Selection (`app/data/universe_scorer.py`)\nUniverseScorer runs every 4 hours. Scores all configured symbols 0–100:',
        '### Stage 1 — Universe Selection (`app/data/universe_scorer.py`)\nUniverseScorer runs every 4 hours. It dynamically fetches the top 150 Bybit USDT perps (>$250k volume) via `fetch_bybit_perps` as the **primary** universe source. The `.env` `SYMBOLS` list is strictly a *fallback* if the Bybit REST API is unreachable on startup. Scores all configured symbols 0–100:'
    ),
    (
        '4. **AI CryptoAnalyst** (`analyst.py`, MANDATORY): Structured prompt with TA indicators + trade memory context. Final confidence = `quant_confidence × 0.5 + ai_confidence × 0.5`. Gate: >= 0.65. **If AI call fails, signal is rejected.**',
        '4. **AI CryptoAnalyst** (`analyst.py`, MANDATORY): Structured prompt with TA indicators + trade memory context. Final confidence = `quant_confidence × 0.5 + ai_confidence × 0.5`. Gate: >= 0.65. **If the 9router AI call fails (timeout, parse error, circuit breaker), it returns `ai_confidence = 0`. This mathematically halves the final confidence score (`quant * 0.5 + 0 * 0.5`), guaranteeing it falls below the `0.65` threshold and is automatically rejected.** *AI failure never bypasses to a deterministic trade.*'
    ),
    (
        '│  6d. Regime Shift Kill Switch                                       │\n│      Every cycle: current_regime != entry_regime → force close      │',
        '│  6d. Regime Shift Kill Switch                                       │\n│      Requires **3 consecutive checks** of regime mismatch (hysteresis) before forcing exit to prevent noise whipsaws. │'
    ),
    (
        '│  6f. AI Position Judge (app/alpha/position_judge.py)                │\n│      - 2-tier: haiku (cheap) → sonnet (escalated)                   │\n│      - 3 consecutive HOLDs on loser → forced EXIT                   │',
        '│  6f. AI Position Judge (app/alpha/position_judge.py)                │\n│      - 2-tier: haiku (cheap) → sonnet (escalated)                   │\n│      - **3 consecutive HOLDs on a losing position automatically trigger a forced EXIT** to prevent infinite ambiguity loops. │'
    ),
    (
        '- **AI Position Judge** (`position_judge.py`): 2-tier escalation (haiku → sonnet). 3 consecutive HOLDs on losing position → forced EXIT.',
        '- **AI Position Judge** (`position_judge.py`): 2-tier escalation (haiku → sonnet). **3 consecutive HOLDs on a losing position automatically trigger a forced EXIT** to prevent infinite ambiguity loops.'
    ),
    (
        'Shadow mode skips startup reconciliation and position_reconciler.',
        'Shadow mode skips startup reconciliation and position_reconciler.\n\n**Shadow Mode Mathematical Fidelity**:\n1. **Fee Asymmetry**: Simulates realistic maker (0.02%) vs taker (0.055%) fees based on order type.\n2. **Wick Miss Prevention**: Uses `worst_price_seen` from Redis for SL detection, not just the current tick.\n3. **Funding Rate Drag**: Deducts 8-hour funding fees on held virtual positions.\n4. **Pending State Machine**: Virtual limit orders expire after 600s TTL if not filled.'
    )
])

# 3. docs/ARCHITECTURE.md
replace_in_file('docs/ARCHITECTURE.md', [
    (
        '### H. Dynamic Universe Scoring (`app/data/universe_scorer.py`)\n*   **Purpose:** Replace static symbol list with dynamic scoring based on market conditions. Adapts KCT\'s UniverseScorer pattern.',
        '### H. Dynamic Universe Scoring (`app/data/universe_scorer.py`)\n*   **Purpose:** Replace static symbol list with dynamic scoring based on market conditions. Adapts KCT\'s UniverseScorer pattern. This is the **Primary Universe Driver**. The fallback chain is: `Bybit API (Top 150)` → `Validated .env config` → `Hard Fail`.'
    ),
    (
        '*   **Pre-Entry CryptoAnalyst** (`app/alpha/analyst.py`): MANDATORY step in signal pipeline. Runs after deterministic signal generation, before risk gate. Fetches 200 1H candles, computes TA indicators (RSI, BB, MACD, ATR, EMA), sends structured prompt to AI via 9router. Final confidence = quant_confidence × 0.5 + ai_confidence × 0.5. Gate: final_confidence >= 0.65. If AI call fails, signal is **rejected** (not bypassed).',
        '*   **Pre-Entry CryptoAnalyst** (`app/alpha/analyst.py`): MANDATORY step in signal pipeline. Runs after deterministic signal generation, before risk gate. Fetches 200 1H candles, computes TA indicators (RSI, BB, MACD, ATR, EMA), sends structured prompt to AI via 9router. Final confidence = quant_confidence × 0.5 + ai_confidence × 0.5. Gate: final_confidence >= 0.65. **Fail-Safe Defaults**:\n    * Parse failure → `FLAT` / `EXIT` (never `HOLD`).\n    * AI unavailable/timeout → Conservative `HOLD` (don\'t exit blindly) or `REJECT` (for new entries), mathematically halving the final confidence score to ensure rejection.\n    * 3 consecutive `HOLD`s on a losing position → Forced `EXIT`.'
    ),
    (
        '**Key insight:** The data pipeline runs continuously regardless of ASM state. Signals are generated and queued. The executor only processes them when ASM is active. This means the system is always "warm" — no cold-start delay when launching a session.',
        '**Key insight (Warm Start Advantage):** Because Stages 1–4 run continuously regardless of ASM state, the bot is always "warm". When a user activates a session via Telegram, Stage 5 execution begins instantly with zero cold-start data delay. Furthermore, **Execution is strictly gated by `karsa:auto:state:active == "1"`**. If Redis is down or the key is missing, the system **blocks all trades**. It never defaults to "open".'
    )
])

# 4. docs/METRICS_DICTIONARY.md
replace_in_file('docs/METRICS_DICTIONARY.md', [
    (
        '| `karsa_shadow_limit_orders_unfilled_total` | Counter | `symbol` | Shadow post-only limit orders that expired unfilled (TTL 600s) |',
        '| `karsa_shadow_limit_orders_unfilled_total` | Counter | `symbol` | Shadow post-only limit orders that expired unfilled (TTL 600s) |\n| `karsa_shadow_live_entry_divergence_seconds` | Histogram | — | Shadow vs Live entry divergence in seconds |\n| `karsa_shadow_live_slippage_bps` | Histogram | — | Shadow vs Live slippage comparison in basis points |'
    ),
    (
        '| `karsa_position_lifecycle_duration_seconds` | Histogram | — | Time from position open to close |',
        '| `karsa_position_lifecycle_duration_seconds` | Histogram | — | Time from position open to close |\n| `karsa_risk_gate_reject` | Counter | `symbol`, `reason` | Signals rejected by risk gate |\n| `karsa_risk_gate_pass` | Counter | `symbol` | Signals passed by risk gate |\n| `karsa_positions_opened` | Counter | `symbol`, `side` | Positions successfully opened |\n| `karsa_positions_closed` | Counter | `symbol`, `side`, `exit_reason` | Positions closed and why |'
    ),
    (
        '| `karsa_state_divergence_detected_total` | Counter | — | Fires on Scenario C (Ghost Positions) — this should page a human, not just increment quietly |',
        '| `karsa_state_divergence_detected_total` | Counter | — | Fires on Scenario C (Ghost Positions) — this should page a human, not just increment quietly |\n| `karsa_trade_reconcile_discrepancies` | Counter | `kind` | Count of discrepancies found during reconciliation |\n| `karsa_trade_reconcile_repairs` | Counter | `kind` | Count of successful state repairs made |\n| `karsa_reconciler_stale_removed` | Counter | `symbol` | Count of stale keys cleaned up |'
    )
])

# 5. docs/DATA_MODEL.md
replace_in_file('docs/DATA_MODEL.md', [
    (
        '| `karsa:state:risk_profile` | String | None | `"conservative"` / `"semi_aggressive"` / `"aggressive"` | Active risk profile name |',
        '| `karsa:state:risk_profile` | String | None | `"conservative"` / `"semi_aggressive"` / `"aggressive"` | Active risk profile name |\n| `karsa:exit_alerted:{symbol}:{side}` | String | 60s | `"1"` | Deduplication guard for TP/SL Telegram alerts |\n| `karsa:settings:max_hyper_slots` | String | None | `"2"` | Maximum allowed concurrent HYPER regime positions |'
    ),
    (
        '| is_shadow | BOOLEAN | TRUE | Always TRUE for shadow trades |',
        '| is_shadow | BOOLEAN | TRUE | BOOLEAN DEFAULT TRUE. Always TRUE for shadow trades to prevent live contamination |'
    )
])

# 6. docs/TESTING_STRATEGY.md
replace_in_file('docs/TESTING_STRATEGY.md', [
    (
        '  - Position judge returns HOLD 3 times → forced EXIT\n  - Position judge returns EXIT on first call → immediate exit',
        '  - Position judge returns HOLD 3 times → forced EXIT\n  - Position judge returns EXIT on first call → immediate exit\n  - `test_ai_timeout_rejects_signal`: Assert that a 9router timeout returns `FLAT` with `0` confidence, dropping the blended score below `0.65`.\n  - `test_position_judge_3_hold_forced_exit`: Mock the Position Judge to return `HOLD` 3 times on a losing position; assert that `ActivePositionManager` triggers a market close.'
    ),
    (
        '    ├── test_proxy_failover.py\n    ├── test_reconciliation_scenarios.py\n    └── test_watchdog.py',
        '    ├── test_proxy_failover.py\n    ├── test_reconciliation_scenarios.py\n    ├── test_regime_hysteresis.py    # Assert 3 consecutive checks required\n    ├── test_shadow_refinements.py   # wick_miss_prevention, funding_drag, pending_ttl\n    └── test_watchdog.py'
    )
])

# 7. docs/SYSTEM_CONSTANTS.md
missing_constants = """
### 15.6 Other Important Constants

| Constant | Value | Unit | Source | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `REGIME_SHIFT_CONFIRM_COUNT` | `3` | count | `position_manager.py` | Consecutive checks required to trigger Regime Kill Switch |
| `SHADOW_PENDING_TTL` | `600` | seconds | `shadow.py` | Max time a virtual limit order remains `PENDING` |
| `MAX_STOP_ORDERS_PER_SYMBOL` | `9` | count | `live_loop.py` | Pre-check limit to ensure room for 1 new SL (Bybit limit is 10) |
| `APM_BREAKEVEN_ATR_MULT` | `1.0` | × ATR | `position_manager.py` | Price must move 1x ATR to trigger breakeven lock |
"""
with open('docs/SYSTEM_CONSTANTS.md', 'a') as f:
    f.write(missing_constants)

print("Done")
