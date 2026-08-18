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
import ssl
import time
import urllib.request
from typing import Any

logger = logging.getLogger("karsa.dns_fallback")

_orig_getaddrinfo = socket.getaddrinfo
_doh_cache: dict[str, tuple[float, list[str]]] = {}
_DOH_CACHE_TTL = 300  # 5 minutes
_in_fallback = False

_DOH_ENDPOINTS = (
    "https://8.8.8.8/resolve?name={host}&type=A",
    "https://1.1.1.1/dns-query?name={host}&type=A",
    "https://dns.google/resolve?name={host}&type=A",
    "https://cloudflare-dns.com/dns-query?name={host}&type=A",
)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _doh_resolve(hostname: str) -> list[str]:
    """Resolve hostname via DNS-over-HTTPS (Google & Cloudflare).

    Used as fallback when local DNS is poisoned or broken.
    """
    now = time.time()
    if hostname in _doh_cache:
        ts, cached_ips = _doh_cache[hostname]
        if now - ts < _DOH_CACHE_TTL:
            return cached_ips

    for endpoint_tmpl in _DOH_ENDPOINTS:
        url = endpoint_tmpl.format(host=hostname)
        req = urllib.request.Request(
            url,
            headers={
                "accept": "application/dns-json",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        try:
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=3.5) as resp:
                res = json.loads(resp.read().decode())
                ips = [
                    ans.get("data")
                    for ans in res.get("Answer", [])
                    if ans.get("type") == 1 and ans.get("data")
                ]
                if ips:
                    _doh_cache[hostname] = (now, ips)
                    return ips
        except Exception as exc:
            logger.debug("doh_endpoint_failed endpoint=%s host=%s error=%s", url, hostname, exc)

    return []


# Hostnames that should never go through the fallback
# (internal Docker services, localhost, IPs)
_SKIP_HOSTS = frozenset({
    "postgres", "redis", "gluetun", "localhost",
    "127.0.0.1", "0.0.0.0", "1.1.1.1", "8.8.8.8",
    "9router", "prometheus", "grafana", "db",
    "karsa-postgres", "karsa-redis", "karsa-gluetun", "karsa-9router",
})

# Indonesian ISP block page / redirect prefixes
_ISP_BLOCK_PREFIXES = (
    "202.169.",  # Biznet / CBN / Kominfo
    "203.119.",  # Telkom / IndiHome
    "182.23.",   # Indosat
    "139.255.",  # XL Axiata
    "103.28.",   # TrustPositif
    "103.136.",  # TrustPositif
    "118.98.",   # Telkom
    "36.86.",    # Telkom
    "36.88.",    # Telkom
    "114.124.",  # Telkomsel
    "114.125.",  # Telkomsel
)
_TELKOMSEL_BLOCK_PREFIXES = _ISP_BLOCK_PREFIXES

_STATIC_HOST_MAP: dict[str, list[str]] = {
    "api.telegram.org": ["149.154.166.110", "149.154.167.220"],
    "api.bybit.com": ["18.64.37.123", "18.64.37.129", "18.64.37.106", "18.64.37.56"],
    "stream.bybit.com": ["18.64.37.123", "18.64.37.129", "18.64.37.106", "18.64.37.56"],
    "api-testnet.bybit.com": ["18.64.37.123", "18.64.37.129"],
    "stream-testnet.bybit.com": ["18.64.37.123", "18.64.37.129"],
    "api.binance.com": ["18.64.37.123", "18.64.37.129"],
    "www.okx.com": ["18.64.37.123", "18.64.37.129"],
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
    """Try system resolver, fall back to DoH on failure or ISP poisoning."""
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

    # 1. Try system resolver first
    try:
        res = _orig_getaddrinfo(host, port, family, type, proto, flags)
        # Verify the returned IP is not an ISP block page IP
        has_blocked_ip = any(
            r[4][0].startswith(_ISP_BLOCK_PREFIXES)
            for r in res
            if len(r) > 4 and len(r[4]) > 0 and isinstance(r[4][0], str)
        )
        if not has_blocked_ip:
            return res
        logger.warning("dns_fallback: ISP DNS poisoning detected for host=%s (returned blocked IP), switching to DoH", host)
    except (socket.gaierror, Exception):
        pass  # DNS resolution failed, try DoH fallback

    # 2. Fallback to DoH (Google & Cloudflare direct IPs)
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
        sock_type = type if type != 0 else socket.SOCK_STREAM
        proto_num = proto if proto != 0 else (socket.IPPROTO_TCP if sock_type == socket.SOCK_STREAM else socket.IPPROTO_UDP)
        results = []
        for ip in ips:
            af = socket.AF_INET6 if ":" in ip else socket.AF_INET
            if family != 0 and family != af:
                continue
            sockaddr = (ip, port_num, 0, 0) if af == socket.AF_INET6 else (ip, port_num)
            results.append((af, sock_type, proto_num, "", sockaddr))
        if results:
            return results

    # Neither worked — raise the original error
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def setup_dns_fallback() -> None:
    """Install DNS fallback for gluetun outages.

    Safe to call multiple times — only patches once.
    """
    if socket.getaddrinfo != _fallback_getaddrinfo:
        socket.getaddrinfo = _fallback_getaddrinfo
        logger.info("DNS fallback installed (DoH safety net for gluetun outages)")
