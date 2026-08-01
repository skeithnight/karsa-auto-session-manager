#!/usr/bin/env python3
"""Fetch and display current Bybit open positions (USDT linear)."""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from pybit.unified_trading import HTTP


def safe_decimal(value, default="0") -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(default)
    except Exception:
        return Decimal(default)


def main() -> None:
    api_key = os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET")
    testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    if not api_key or not api_secret:
        print("ERROR: BYBIT_API_KEY / BYBIT_API_SECRET not set in .env")
        sys.exit(1)

    session = HTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet,
    )

    result = session.get_positions(category="linear", settleCoin="USDT")
    positions = [
        p
        for p in result.get("result", {}).get("list", [])
        if safe_decimal(p.get("size")) > 0
    ]

    if not positions:
        print("No open positions.")
        return

    header = (
        f"{'Symbol':<14} {'Side':<6} {'Size':>10} {'Entry':>12} "
        f"{'Mark':>12} {'uPnL':>12} {'uPnL%':>8} {'Liq':>12} "
        f"{'Lev':>5} {'Value':>12}"
    )
    print(header)
    print("-" * len(header))

    total_pnl = Decimal("0")
    total_value = Decimal("0")
    for p in positions:
        symbol = p["symbol"]
        side = p.get("side", "")
        size = safe_decimal(p.get("size"))
        entry = safe_decimal(p.get("avgPrice"))
        mark = safe_decimal(p.get("markPrice"))
        upnl = safe_decimal(p.get("unrealisedPnl"))
        liq = safe_decimal(p.get("liqPrice"))
        leverage = p.get("leverage", "-")
        value = safe_decimal(p.get("positionValue"))
        total_pnl += upnl
        total_value += value

        # uPnL % relative to entry value
        if entry > 0 and size > 0:
            entry_value = entry * size
            pnl_pct = (upnl / entry_value) * Decimal("100")
        else:
            pnl_pct = Decimal("0")

        pnl_sign = "+" if upnl >= 0 else ""
        liq_str = f"{liq}" if liq > 0 else "-"

        print(
            f"{symbol:<14} {side:<6} {size:>10} {entry:>12} "
            f"{mark:>12} {pnl_sign}{upnl:>11} {pnl_sign}{pnl_pct:>7.2f}% {liq_str:>12} "
            f"{leverage:>5} {value:>12}"
        )

    print("-" * len(header))
    print(
        f"Positions: {len(positions)}   "
        f"Total Value: {total_value}   "
        f"Total uPnL: {'+' if total_pnl >= 0 else ''}{total_pnl}"
    )


if __name__ == "__main__":
    main()
