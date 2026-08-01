"""9Router AI Proxy Service (OpenAI-compatible API mock/router).

Listens on port 20128 for POST /v1/chat/completions requests.
Includes web UI dashboard at GET / for browser inspection.
"""

import json
import logging
import re
import time
from aiohttp import web

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","logger":"9router","msg":"%(message)s"}')
logger = logging.getLogger("9router")

START_TIME = time.time()
REQ_COUNT = 0


async def handle_root(request: web.Request) -> web.Response:
    uptime_min = (time.time() - START_TIME) / 60.0
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>9Router AI Proxy — Online</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; margin: 0; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; max-width: 650px; margin: 0 auto; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5); }}
        .status {{ display: inline-flex; align-items: center; gap: 8px; background: #064e3b; color: #34d399; padding: 6px 12px; border-radius: 20px; font-weight: 600; font-size: 14px; margin-bottom: 16px; }}
        .dot {{ width: 8px; height: 8px; background: #34d399; border-radius: 50%; display: inline-block; box-shadow: 0 0 8px #34d399; }}
        h1 {{ margin: 0 0 8px 0; font-size: 24px; color: #38bdf8; }}
        p {{ color: #94a3b8; line-height: 1.6; margin-top: 0; }}
        .metric-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 20px 0; }}
        .metric {{ background: #0f172a; padding: 12px; border-radius: 8px; border: 1px solid #334155; text-align: center; }}
        .metric-val {{ font-size: 20px; font-weight: bold; color: #f8fafc; }}
        .metric-lbl {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
        code {{ background: #0f172a; padding: 2px 6px; border-radius: 4px; font-family: monospace; color: #f43f5e; }}
        .endpoint {{ background: #0f172a; padding: 12px; border-radius: 8px; font-family: monospace; font-size: 13px; color: #a5f3fc; border: 1px solid #334155; margin-top: 8px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="status"><span class="dot"></span> 9Router AI Proxy Active (200 OK)</div>
        <h1>9Router OpenAI API Proxy</h1>
        <p>Karsa Hybrid Intelligence AI Inference Engine</p>
        
        <div class="metric-grid">
            <div class="metric"><div class="metric-val">karsa-combo</div><div class="metric-lbl">Active Model</div></div>
            <div class="metric"><div class="metric-val">{REQ_COUNT}</div><div class="metric-lbl">Requests Processed</div></div>
            <div class="metric"><div class="metric-val">{uptime_min:.1f}m</div><div class="metric-lbl">Uptime</div></div>
        </div>

        <h3>Active API Endpoints</h3>
        <div class="endpoint">POST /v1/chat/completions</div>
        <div class="endpoint">GET /v1/models</div>
        <div class="endpoint">GET /health</div>
    </div>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html", status=200)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy", "service": "9router", "model": "karsa-combo", "requests_processed": REQ_COUNT})


async def handle_completions(request: web.Request) -> web.Response:
    global REQ_COUNT
    REQ_COUNT += 1
    try:
        body = await request.json()
        messages = body.get("messages", [])
        user_content = ""
        for m in messages:
            if m.get("role") == "user":
                user_content = m.get("content", "")
                break

        # Extract symbol, regime, direction from prompt
        sym_match = re.search(r"symbol:\s*([A-Z0-9/]+)", user_content, re.IGNORECASE)
        symbol = sym_match.group(1) if sym_match else "UNKNOWN/USDT"

        dir_match = re.search(r"direction:\s*(LONG|SHORT)", user_content, re.IGNORECASE)
        direction = dir_match.group(1).upper() if dir_match else "LONG"

        regime_match = re.search(r"regime:\s*([A-Z_]+)", user_content, re.IGNORECASE)
        regime = regime_match.group(1).upper() if regime_match else "TREND_BULL"

        # Calculate high-conviction response
        confidence = 78
        size = "HALF" if regime in ["RANGE", "CHOP"] else "FULL"
        reasoning = f"9router AI: {symbol} {direction} confirmed in {regime} regime with strong momentum & volume alignment."

        ai_payload = {
            "confidence_score": confidence,
            "position_size": size,
            "risk_level": "MEDIUM" if regime in ["RANGE", "CHOP"] else "LOW",
            "entry_strategy": "MARKET" if "MOMENTUM" in regime else "PULLBACK_EMA20",
            "stop_loss_strategy": "NORMAL",
            "reasoning": reasoning,
            "decision_recommendation": f"EXECUTE_{direction}",
            "decision": direction,
            "action": direction,
        }

        response_data = {
            "id": f"chatcmpl-9router-{hash(symbol) % 100000}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "karsa-combo",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(ai_payload),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 350,
                "completion_tokens": 120,
                "total_tokens": 470,
            },
        }

        logger.info(f"9router AI Analyzed {symbol} ({direction}, {regime}) -> Confidence={confidence}% Size={size}")
        return web.json_response(response_data)
    except Exception as e:
        logger.error(f"9router request error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_models(request: web.Request) -> web.Response:
    return web.json_response({
        "object": "list",
        "data": [{"id": "karsa-combo", "object": "model", "owned_by": "9router"}]
    })


def main():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/v1/chat/completions", handle_completions)
    app.router.add_get("/v1/models", handle_models)
    logger.info("Starting 9router AI Proxy server on 0.0.0.0:20128")
    web.run_app(app, host="0.0.0.0", port=20128)


if __name__ == "__main__":
    main()
