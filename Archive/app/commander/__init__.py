"""karsa-commander — Telegram bot control plane.

Lightweight container: only starts the Telegram bot, reads Redis/Postgres
for dashboards, and publishes risk/hot-reload commands back to Redis.
No trading loops, no WebSocket connections, no exchange API calls beyond
BybitClient (wallet balance display only).
"""
