### 1. Preventing Distributed State Desync (Orphans & Phantoms)

**The Root Cause:** The system relies on **time-based polling** (a 60s/120s grace period) to reconcile local state (Redis) with exchange state (Bybit). Time-based reconciliation is inherently fragile because exchange API latency is non-deterministic (it can be 1 second or 5 minutes during high volatility).

**Architectural Prevention: Idempotent Order Tracking & Event-Driven State**
Stop guessing when a position is closed based on time. Track the exact lifecycle of the closing order.

* **Enhancement:** When the Active Position Manager (APM) decides to close a position, it must not just "fire and forget." It should record a `CloseIntent` in Redis containing the specific `order_id` returned by the exchange.
* **Why this prevents the issue:** It completely eliminates the "off-by-one" API latency bug. The system only updates state when the exchange cryptographically confirms the specific close order is filled.

---

### 2. Preventing Concurrency Race Conditions (Max Positions Breach)

**The Root Cause:** The system used a **"Check-Then-Act"** anti-pattern. It checked the position count in Redis, and if it was < 3, it executed the trade. In an `asyncio` environment, multiple signals can pass the "check" simultaneously before any of them complete the "act" (writing the new position to Redis).

**Architectural Prevention: The Actor Model (Sequential Execution Queue)**
Trading execution should never be highly concurrent. The decision to enter a trade is a critical section that must be serialized.

* **Enhancement:** Replace `asyncio.create_task(_on_signal_live(...))` with an `asyncio.Queue`. A single "Execution Actor" task reads from this queue and processes signals strictly one by one.
* **Why this prevents the issue:** It mathematically eliminates race conditions in the execution funnel. You no longer need distributed locks; the architecture itself enforces sequential processing for critical state mutations.

---

### 3. Preventing Data Boundary Leaks (Decimal Conversion Errors)

**The Root Cause:** The system trusted raw, unvalidated data from external APIs (CCXT) and handled type errors deep inside the business logic (Trade Memory Store) using defensive wrappers (`_safe_decimal`).

**Architectural Prevention: Strict Boundary Contracts (Fail-Fast Validation)**
Data from exchanges is inherently dirty. It should never reach the core domain logic without being strictly validated at the boundary.

* **Enhancement:** Implement **Pydantic models** at the exact boundary where data enters the system (e.g., in the `MarketDataIngestor` or `PositionStore` adapter). If a required field is missing or empty, the system should reject the data *at the door*, not crash deep in the database layer.
* **Why this prevents the issue:** It shifts error handling from "defensive coding everywhere" to "strict validation at the edge." The core business logic can now safely assume all data is perfectly typed, eliminating `ConversionSyntax` errors entirely.

---

### 4. Preventing Context Loss (Empty `entry_regime` on Orphans)

**The Root Cause:** When an orphan position was synced, the system assigned the *current* market regime to it. This is factually incorrect (the position was entered in the past, under a different regime) and causes downstream logic (like the Regime Kill Switch) to behave unpredictably.

**Architectural Prevention: Historical Reconstruction or Explicit "Unknown" State**
Never guess the historical context of a position. Either reconstruct it accurately or explicitly flag it as unknown.

* **Enhancement:** When an orphan is detected, query the exchange's trade history (`fetch_my_trades`) to find the exact timestamp of the entry. Use that timestamp to look up the historical regime. If historical data is unavailable, assign a special `Regime.UNKNOWN` state and exempt it from regime-based kill switches.
* **Why this prevents the issue:** It prevents the system from making false assumptions. By explicitly modeling "Unknown" states, you prevent downstream logic from firing incorrectly based on guessed data.

---

### 5. Preventing Lifecycle Stalls (Phantoms waiting for SL)

**The Root Cause:** "Phantom" was treated as a boolean check (`is_phantom = True/False`) rather than a distinct phase in the position lifecycle. Because it wasn't a formal state, the system didn't know it needed to trigger an immediate cleanup.

**Architectural Prevention: Formal Position State Machine**
Positions should not just be "open" or "closed." They should move through a strict, enumerated state machine.

* **Enhancement:** Define explicit states: `PENDING`, `OPEN`, `CLOSING`, `ORPHANED`, `PHANTOM`, `CLOSED`. When the reconciliation loop detects a phantom, it transitions the position to the `PHANTOM` state. The state machine dictates that a transition to `PHANTOM` immediately triggers the `purge_from_redis()` action.
* **Why this prevents the issue:** It decouples the *detection* of an anomaly from the *action* taken. The state machine guarantees that every state has a defined cleanup path, ensuring no position gets "stuck" in a limbo state waiting for a Stop Loss.

---

### Summary: The Evolution of the System

| Issue | Previous Tactical Fix (Reactive) | Status | Architectural Prevention (Proactive) | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Orphan/Phantom Desync** | Increase sleep timer to 120s | ✅ **DONE** | **Idempotent Order Tracking** (Wait for specific `order_id` fill) | ⏳ Future |
| **Max Pos Breach** | Add `if count >= max` check | ✅ **DONE** | **Actor Model** (Sequential execution queue eliminates race conditions) | ✅ **DONE** |
| **Decimal Errors** | `_safe_decimal()` wrapper | ✅ **DONE** | **Pydantic Boundary Contracts** (Fail-fast validation at data ingestion) | ⏳ Future |
| **Empty Entry Regime** | Assign `UNKNOWN` regime to orphan | ✅ **DONE** | **Historical Reconstruction** (Query trade history) | ⏳ Future |
| **Phantom Cleanup Delay** | Immediate `_store.remove()` on detection | ✅ **DONE** | **Formal State Machine** (Transition to `PHANTOM` state auto-triggers purge) | ⏳ Future |

### Implementation Log (2026-07-25)

| Fix | File | Change |
|:----|:-----|:-------|
| Orphan grace period 60→120s | `position_manager.py` | `ORPHAN_RE_ENTRY_GRACE_S = 120` |
| Immediate phantom cleanup | `position_manager.py` | Added `_store.remove()` in `_reconcile_position` when phantom detected |
| UNKNOWN regime for orphans | `position_manager.py` | Orphan sync sets `entry_regime="UNKNOWN"`, kill switch exempts UNKNOWN |
| Actor Model (single worker) | `live_loop.py` | `WORKER_COUNT` default changed from 10 → 1 |
