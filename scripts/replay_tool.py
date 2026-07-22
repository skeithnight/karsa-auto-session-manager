"""Replay Tool (Sprint 0).

Displays the full decision pipeline for a historical trade based on
observability logs.
"""

import json
import sys


def replay_trace(log_file: str, symbol_or_time: str) -> None:
    traces = []
    snapshots = []

    with open(log_file) as f:
        for line in f:
            if "[OBSERVABILITY]" not in line:
                continue
            try:
                json_str = line.split("[OBSERVABILITY]")[1].strip()
                payload = json.loads(json_str)
                if payload["type"] == "decision_trace":
                    traces.append(payload)
                elif payload["type"] == "feature_snapshot":
                    snapshots.append(payload)
            except Exception:
                pass

    print(f"Searching for traces matching '{symbol_or_time}'...")
    found_trace = None
    for t in traces:
        # Match by simple text in the JSON string for this basic prototype
        if symbol_or_time in json.dumps(t):
            found_trace = t
            break

    if not found_trace:
        print("No matching trace found.")
        return

    print("=" * 50)
    print("REPLAY PIPELINE")
    print("=" * 50)

    print(f"Time: {found_trace.get('timestamp')}")
    print(f"Decision: {found_trace.get('entry_decision')}")
    print(f"Strategy: {found_trace.get('strategy')}")
    print(f"Confidence: {found_trace.get('confidence')}")

    print("\nEvidence:")
    for ev in found_trace.get("evidence", []):
        print(f"  - {ev}")

    print("\nSnapshot features (if any):")
    for s in snapshots:
        # Assuming timestamps match roughly for the same event
        if abs(s.get("timestamp", 0) - found_trace.get("timestamp", 0)) < 1.0:
            print(json.dumps(s.get("feature_vector"), indent=2))
            break


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python replay_tool.py <path_to_log_file> <symbol_or_time_search>")
        sys.exit(1)
    replay_trace(sys.argv[1], sys.argv[2])
