"""DNS Bypass module — protects against ISP DNS poisoning (e.g. Telkomsel/Indihome).

Provides DoH (DNS-over-HTTPS) fallback and Docker/gluetun DNS resolution.
Safely patches socket.getaddrinfo at application startup.
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import time
import urllib.request
from typing import Any

logger = logging.getLogger("karsa.dns_bypass")

_orig_getaddrinfo = socket.getaddrinfo
_doh_cache: dict[str, tuple[float, list[str]]] = {}
_TELKOMSEL_BLOCK_PREFIXES = ("182.23.", "139.255.")
_in_doh = False


def _dns_query(server: str, hostname: str, timeout: float = 0.1) -> list[str]:
    """Query a DNS server directly via UDP. Returns list of IPs or empty list."""
    txid = b"\xaa\xbb"
    flags = b"\x01\x00"
    counts = struct.pack(">HHHH", 1, 0, 0, 0)
    question = b""
    for part in hostname.encode().split(b"."):
        question += bytes([len(part)]) + part
    question += b"\x00" + struct.pack(">HH", 1, 1)
    packet = txid + flags + counts + question
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(512)
    except Exception:
        return []
    finally:
        sock.close()

    try:
        offset = 12
        while data[offset] != 0:
            offset += data[offset] + 1
        offset += 5
        answers = struct.unpack(">H", data[6:8])[0]
        ips = []
        for _ in range(answers):
            if data[offset] & 0xC0:
                offset += 2
            else:
                while data[offset] != 0:
                    offset += data[offset] + 1
                offset += 1
            rtype, rclass, ttl, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
            offset += 10
            if rtype == 1 and rdlength == 4:
                ip = ".".join(str(b) for b in data[offset : offset + 4])
                ips.append(ip)
            offset += rdlength
        return ips
    except Exception:
        return []


def _doh_query(hostname: str) -> list[str]:
    """Query Cloudflare DNS-over-HTTPS (DoH) to bypass ISP DNS poisoning."""
    now = time.time()
    if hostname in _doh_cache:
        ts, cached_ips = _doh_cache[hostname]
        if now - ts < 300:  # 5 min TTL
            return cached_ips

    url = f"https://1.1.1.1/dns-query?name={hostname}&type=A"
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            res = json.loads(resp.read().decode())
            ips = [ans.get("data") for ans in res.get("Answer", []) if ans.get("type") == 1 and ans.get("data")]
            if ips:
                _doh_cache[hostname] = (now, ips)
                return ips
    except Exception:
        pass
    return []


def _bypass_getaddrinfo(
    host: Any,
    port: Any,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[Any, ...]]:
    """Override socket.getaddrinfo with DNS poisoning bypass."""
    global _in_doh  # noqa: PLW0603

    if (
        _in_doh
        or not isinstance(host, str)
        or host in {"postgres", "redis", "gluetun", "localhost", "127.0.0.1", "0.0.0.0", "1.1.1.1", "8.8.8.8", "9router", "prometheus", "grafana", "karsa-postgres", "karsa-redis"}
        or host.endswith(".internal")
        or host.startswith("172.")
        or host.startswith("127.")
        or (host and host[0].isdigit())
    ):
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    # 1. Try Docker internal DNS (127.0.0.11) with short 0.1s timeout
    try:
        ips = _dns_query("127.0.0.11", host, timeout=0.1)
        if ips and not ips[0].startswith(_TELKOMSEL_BLOCK_PREFIXES):
            af = socket.AF_INET6 if ":" in ips[0] else socket.AF_INET
            return [(af, socket.SOCK_STREAM, 0, "", (ips[0], port if isinstance(port, int) else 0))]
    except Exception:
        pass

    # 2. Try standard system resolver
    try:
        res = _orig_getaddrinfo(host, port, family, type, proto, flags)
        has_blocked = any(
            r[4][0].startswith(_TELKOMSEL_BLOCK_PREFIXES)
            for r in res
            if len(r) > 4 and len(r[4]) > 0 and isinstance(r[4][0], str)
        )
        if not has_blocked:
            return res
    except Exception:
        pass

    # 3. Fallback to Cloudflare DNS-over-HTTPS (DoH) for poisoned domain
    try:
        _in_doh = True
        ips = _doh_query(host)
    finally:
        _in_doh = False

    if ips:
        af = socket.AF_INET6 if ":" in ips[0] else socket.AF_INET
        return [(af, socket.SOCK_STREAM, 0, "", (ip, port if isinstance(port, int) else 0)) for ip in ips]

    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def setup_dns_bypass() -> None:
    """Monkey-patch socket.getaddrinfo with DNS bypass."""
    if socket.getaddrinfo != _bypass_getaddrinfo:
        socket.getaddrinfo = _bypass_getaddrinfo
        logger.info("DNS bypass initialized (DoH + Docker/gluetun support)")
