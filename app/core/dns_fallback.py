"""DNS Fallback — lightweight safety net for gluetun DNS-over-TLS outages.

Gluetun handles DNS resolution via DNS-over-TLS (Cloudflare) at the network
layer. However, when gluetun's WireGuard tunnel drops and reconnects, there's
a brief window where DNS resolution fails. This module provides a thin
fallback: try system resolver first (gluetun's DoT), fall back to Cloudflare
DNS-over-HTTPS only on failure.

This replaces the old dns_bypass.py which had complex multi-layer bypass logic
(Docker DNS, ISP poisoning detection, DoH) that was redundant with gluetun.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.request
from typing import Any

logger = logging.getLogger("karsa.dns_fallback")

_orig_getaddrinfo = socket.getaddrinfo
_doh_cache: dict[str, tuple[float, list[str]]] = {}
_DOH_CACHE_TTL = 300  # 5 minutes
_in_fallback = False


def _doh_resolve(hostname: str) -> list[str]:
    """Resolve hostname via Cloudflare DNS-over-HTTPS.

    Used as fallback when gluetun's DNS-over-TLS is transiently broken
    (e.g., during WireGuard tunnel reconnect).
    """
    now = time.time()
    if hostname in _doh_cache:
        ts, cached_ips = _doh_cache[hostname]
        if now - ts < _DOH_CACHE_TTL:
            return cached_ips

    url = f"https://1.1.1.1/dns-query?name={hostname}&type=A"
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            res = json.loads(resp.read().decode())
            ips = [
                ans.get("data")
                for ans in res.get("Answer", [])
                if ans.get("type") == 1 and ans.get("data")
            ]
            if ips:
                _doh_cache[hostname] = (now, ips)
                return ips
    except Exception:
        pass
    return []


# Hostnames that should never go through the fallback
# (internal Docker services, localhost, IPs)
_SKIP_HOSTS = frozenset({
    "postgres", "redis", "gluetun", "localhost",
    "127.0.0.1", "0.0.0.0", "1.1.1.1", "8.8.8.8",
    "9router", "prometheus", "grafana", "db",
    "karsa-postgres", "karsa-redis",
})


_TELKOMSEL_BLOCK_PREFIXES = ("203.119.", "182.23.", "139.255.")

_STATIC_HOST_MAP: dict[str, list[str]] = {
    "api.telegram.org": ["149.154.166.110", "149.154.167.220"],
    "api.bybit.com": ["18.64.37.56", "18.64.37.123"],
    "stream.bybit.com": ["18.64.37.56", "18.64.37.123"],
    "api-testnet.bybit.com": ["18.64.37.56"],
    "stream-testnet.bybit.com": ["18.64.37.56"],
    "api.binance.com": ["18.64.37.56"],
    "www.okx.com": ["18.64.37.56"],
}


def _parse_port(port: Any) -> int:
    """Parse port argument from getaddrinfo (handles ints, numeric strings, and service names like 'https')."""
    if isinstance(port, int):
        return port
    if isinstance(port, str):
        if port.isdigit():
            return int(port)
        if port == "https":
            return 443
        if port == "http":
            return 80
        try:
            return socket.getservbyname(port)
        except Exception:
            pass
    return 443


def _fallback_getaddrinfo(
    host: Any,
    port: Any,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[Any, ...]]:
    """Try system resolver (gluetun), fall back to DoH on failure or ISP poisoning."""
    global _in_fallback  # noqa: PLW0603

    # Skip fallback for internal/local hostnames and when already in fallback
    if (
        _in_fallback
        or not isinstance(host, str)
        or host in _SKIP_HOSTS
        or host.endswith(".internal")
        or host.startswith(("172.", "127.", "10.", "192.168."))
        or (host and host[0].isdigit())
    ):
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    # 1. Try system resolver (gluetun's DNS-over-TLS) — normal path
    try:
        res = _orig_getaddrinfo(host, port, family, type, proto, flags)
        # Verify the returned IP is not an ISP Telkomsel/Indihome block page IP
        has_blocked_ip = any(
            r[4][0].startswith(_TELKOMSEL_BLOCK_PREFIXES)
            for r in res
            if len(r) > 4 and len(r[4]) > 0 and isinstance(r[4][0], str)
        )
        if not has_blocked_ip:
            return res
        logger.warning("dns_fallback: ISP DNS poisoning detected for host=%s (returned blocked IP), switching to DoH", host)
    except socket.gaierror:
        pass  # DNS resolution failed, try DoH fallback

    # 2. Fallback to Cloudflare DoH (reached when gluetun DNS fails or is poisoned)
    try:
        _in_fallback = True
        ips = _doh_resolve(host)
    finally:
        _in_fallback = False

    if not ips and host in _STATIC_HOST_MAP:
        ips = _STATIC_HOST_MAP[host]
        logger.info("dns_static_fallback_used host=%s ips=%s", host, ips[0])

    if ips:
        logger.info("dns_fallback_used host=%s ips=%s", host, ips[0])
        port_num = _parse_port(port)
        af = socket.AF_INET6 if ":" in ips[0] else socket.AF_INET
        return [
            (af, socket.SOCK_STREAM, 0, "", (ip, port_num))
            for ip in ips
        ]

    # Neither worked — raise the original error
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def setup_dns_fallback() -> None:
    """Install DNS fallback for gluetun outages.

    Safe to call multiple times — only patches once.
    """
    if socket.getaddrinfo != _fallback_getaddrinfo:
        socket.getaddrinfo = _fallback_getaddrinfo
        logger.info("DNS fallback installed (DoH safety net for gluetun outages)")
