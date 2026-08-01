import json

with open("grafana/dashboards/asm-core-operations.json") as f:
    dashboard = json.load(f)

# Find the Analysis Result panel
analysis_panel = None
for p in dashboard.get("panels", []):
    if p.get("id") == 6:
        analysis_panel = p
        break

if analysis_panel:
    # Resize the AI panel
    analysis_panel["gridPos"]["w"] = 10
    analysis_panel["title"] = "AI Decisions"

    # Create the Signal Confidence panel
    confidence_panel = {
        "id": 10,
        "title": "Signal Confidence",
        "type": "table",
        "gridPos": { "h": 7, "w": 8, "x": 16, "y": 7 },
        "datasource": { "type": "prometheus", "uid": "prometheus" }, # Standard uid or PBFA97CFB590B2093? Let's check executive-overview.json uid earlier, it was PBFA97CFB590B2093. I will just use prometheus datasource default.
        "targets": [
            {
                "expr": "sum(karsa_signal_confidence_sum) by (symbol) / sum(karsa_signal_confidence_count) by (symbol)",
                "format": "table",
                "instant": True,
                "refId": "A"
            }
        ],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": { "Time": True },
                    "renameByName": { "symbol": "Symbol", "Value": "Confidence" }
                }
            }
        ],
        "fieldConfig": {
            "defaults": { "custom": { "align": "center" } },
            "overrides": []
        }
    }
    dashboard["panels"].insert(dashboard["panels"].index(analysis_panel) + 1, confidence_panel)

    with open("grafana/dashboards/asm-core-operations.json", "w") as f:
        json.dump(dashboard, f, indent=2)
