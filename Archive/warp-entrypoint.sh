#!/bin/bash
set -e

# Start WARP daemon in background
warp-svc &
WARP_SVC_PID=$!

# Wait for daemon socket to be ready
echo "Waiting for WARP daemon..."
for i in $(seq 1 30); do
    if warp-cli --accept-tos status >/dev/null 2>&1; then
        echo "WARP daemon ready"
        break
    fi
    sleep 1
done

# Register device with Cloudflare (accept TOS automatically)
warp-cli --accept-tos registration new || true

# Set proxy mode (SOCKS5 on port 1080)
warp-cli --accept-tos mode proxy || true
warp-cli --accept-tos proxy port 1080 || true

# Connect to WARP
warp-cli --accept-tos connect

echo "WARP proxy connected"

# Forward 0.0.0.0:1081 -> 127.0.0.1:1080 (WARP binds localhost only)
socat TCP-LISTEN:1081,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:1080 &
SOCAT_PID=$!

echo "WARP proxy forwarding on 0.0.0.0:1081 -> 127.0.0.1:1080"

# Wait for either process to exit
wait $WARP_SVC_PID $SOCAT_PID
