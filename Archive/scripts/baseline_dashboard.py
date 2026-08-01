"""Baseline Metrics Dashboard (Sprint 0).

Parses observability logs and computes:
- Signal -> Execute Ratio
- Reject Reason Distribution
- Average Confidence
- Average Market Quality (if available)
- Regime Flip Frequency
"""

import json
import sys
from collections import defaultdict


def generate_dashboard(log_file: str) -> None:
    traces = []
    rejects = []
    regime_transitions = []

    with open(log_file) as f:
        for line in f:
            if "[OBSERVABILITY]" not in line:
                continue

            try:
                # Extract JSON part
                json_str = line.split("[OBSERVABILITY]")[1].strip()
                payload = json.loads(json_str)

                if payload["type"] == "decision_trace":
                    traces.append(payload)
                elif payload["type"] == "reject_reason":
                    rejects.append(payload)
                elif payload["type"] == "regime_transition":
                    regime_transitions.append(payload)
            except Exception:
                pass

    total_signals = len(traces) + len(rejects)
    total_executed = len(traces)

    print("=" * 50)
    print("BASELINE METRICS DASHBOARD")
    print("=" * 50)
    print(f"Total Signals Attempted: {total_signals}")
    print(f"Total Signals Executed:  {total_executed}")
    if total_signals > 0:
        print(f"Signal->Execute Ratio:   {total_executed / total_signals * 100:.2f}%")

    print("\nReject Reason Distribution:")
    reasons = defaultdict(int)
    for r in rejects:
        reasons[r["reason"]] += 1
    for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {reason}: {count} ({count/len(rejects)*100:.1f}%)" if len(rejects) > 0 else "")

    if traces:
        avg_conf = sum(t.get("confidence", 0) for t in traces) / len(traces)
        print(f"\nAverage Confidence (Executed): {avg_conf:.2f}")

    print(f"\nRegime Transitions Logged: {len(regime_transitions)}")
    if regime_transitions:
        transitions_map = defaultdict(int)
        for t in regime_transitions:
            transitions_map[f"{t['old_regime']} -> {t['new_regime']}"] += 1
        for trans, count in sorted(transitions_map.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {trans}: {count}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python baseline_dashboard.py <path_to_log_file>")
        sys.exit(1)
    generate_dashboard(sys.argv[1])
