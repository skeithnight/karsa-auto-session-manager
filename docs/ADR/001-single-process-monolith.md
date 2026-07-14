# ADR-001: Single-Process Monolith

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** Core team

---

## Context

The system must execute trades on Bybit through a Cloudflare WARP SOCKS5 proxy, which adds 100-300ms of network latency. Traditional microservice architectures (separate Orchestrator and Bot containers communicating via Redis pub/sub) would add additional internal latency and create state synchronization risks.

---

## Decision

Build the entire system as a **single Python `asyncio` process** — merging the signal generator (Orchestrator) and order executor (Bot) into one application.

---

## Consequences

### Positive
- **Latency:** In-process signal passing takes <0.01ms vs 5-10ms for Redis pub/sub IPC.
- **State consistency:** No two-phase commit needed — the Executor updates in-memory state immediately and synchronously after fills.
- **Simpler deployment:** One Docker container, one process, one log stream.
- **No message broker dependency:** Eliminates Redis pub/sub as a failure mode.

### Negative
- **Single point of failure:** If the process crashes, all components stop. Mitigated by exchange-side stop-losses and Dead Man's Switch.
- **Scaling limitation:** Cannot scale individual components independently. Acceptable for single-operator trading bot.
- **Memory pressure:** All components share one process memory space. Mitigated by efficient data structures (deque, in-memory cache with TTL).

---

## Alternatives Considered

1. **Microservices with Redis pub/sub:** Rejected — adds 5-10ms internal latency on top of 100-300ms proxy latency, and introduces state divergence risk on partial failure.
2. **Microservices with gRPC:** Rejected — same latency concern, plus operational complexity for a single-operator system.
3. **Thread-based separation within single process:** Rejected — `asyncio` is more efficient for I/O-bound work (hundreds of concurrent WebSockets).

---

## References

- `ARCHITECTURE.md` §3 "The Single Process Mandate"
- `CONTEXT.md` §4 "Key Architectural Decisions"
