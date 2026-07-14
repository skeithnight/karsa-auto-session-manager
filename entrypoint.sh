#!/bin/sh
# Override DNS to use gluetun's DNS (127.0.0.1) which resolves both
# Docker internal names and external names via Cloudflare 1.1.1.1.
# This bypasses ISP (Telkomsel) DNS poisoning of crypto exchange domains.
echo "nameserver 127.0.0.1" > /etc/resolv.conf
echo "ENTRYPOINT: resolv.conf set to:" >&2
cat /etc/resolv.conf >&2
exec python -m app.main "$@"
