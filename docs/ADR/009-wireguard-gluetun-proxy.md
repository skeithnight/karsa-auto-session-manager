# ADR-009: Self-Hosted WireGuard VPN via Gluetun Sidecar (Replacing Cloudflare WARP)

**Status:** Accepted
**Date:** 2026-07-17
**Deciders:** Core team

---

## Context

Bybit REST and Private WebSocket endpoints are geo-restricted. Previously, Cloudflare WARP SOCKS5 proxy was used for egress traffic. However, Cloudflare WARP introduced inconsistent proxy latency spikes, dynamic IP rotation issues, and connection instability on Bybit endpoints.

---

## Decision

Migrate proxy infrastructure from Cloudflare WARP to a **self-hosted WireGuard VPN tunnel** running via a `gluetun` Docker sidecar container (pointing to a dedicated DigitalOcean Sydney droplet). All outbound Bybit REST and Private WebSocket traffic (plus 9router AI proxy calls) route strictly through the `gluetun` sidecar network stack (`network_mode: "service:gluetun"`).

---

## Consequences

### Positive
- **Deterministic Egress IP:** Fixed, dedicated IP avoids rate-limiting, captcha triggers, and regional geo-fencing blocks from Bybit.
- **Lower & Stable Latency:** Dedicated WireGuard tunnel reduces jitter compared to public WARP SOCKS5 routing.
- **Unified Sidecar Container:** Managed cleanly in `docker-compose.yml` via standard `gluetun` healthchecks and automatic reconnection mechanisms.

### Negative
- **Infrastructure Dependency:** Self-hosted droplet requires hosting maintenance ($5-10/mo) and droplet monitoring.
- **Single Failure Vector:** If the WireGuard endpoint or gluetun sidecar drops, outbound Bybit calls fail until re-established. Mitigated by `RISK_AND_RUNBOOK.md` §3 ("Proxy Degradation Protocol") and resting exchange-side stop-losses.

---

## Alternatives Considered

1. **Cloudflare WARP (Original):** Deprecated due to connection resets and latency variance.
2. **Commercial SOCKS5 Proxy Services:** Rejected due to shared IP noise, security concerns with private API keys, and dynamic IP rotation.

---

## References

- `docs/SETUP.md` (Gluetun & WireGuard environment configuration)
- `CONTEXT.md` §7 Issue #12
- `RISK_AND_RUNBOOK.md` §3
