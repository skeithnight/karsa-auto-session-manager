#!/bin/sh
# Karsa ASM — container entrypoint
# Sets DNS to gluetun's resolver (bypasses ISP DNS poisoning).
# Dispatches to the correct Python module based on KARSA_ROLE.

if [ "$KARSA_ROLE" != "backtest" ] && [ "$KARSA_ROLE" != "commander" ]; then
  echo "ENTRYPOINT: resolv.conf contains:" >&2
  cat /etc/resolv.conf >&2
fi
echo "ENTRYPOINT: KARSA_ROLE=$KARSA_ROLE" >&2

if [ "$KARSA_ROLE" = "commander" ]; then
  echo "ENTRYPOINT: running database migrations..." >&2
  python -m app.core.migrate
  MIGRATE_EXIT=$?
  if [ "$MIGRATE_EXIT" -ne 0 ]; then
    echo "ENTRYPOINT: migrations FAILED (exit $MIGRATE_EXIT) — continuing for data-engine (polling role)" >&2
  fi
  echo "ENTRYPOINT: migrations done" >&2
else
  # Prevent race conditions by giving the commander container time to initialize DB schemas
  sleep 5
fi

case "$KARSA_ROLE" in
  data-engine)
    exec python -u -m app.data_engine.main "$@"
    ;;
  live)
    exec python -u -m app.consumer.live_loop "$@"
    ;;
  shadow)
    exec python -u -m app.consumer.shadow_loop "$@"
    ;;
  9router)
    exec python -u scripts/nine_router_proxy.py "$@"
    ;;
  backtest)
    exec python -u -m app.backtest.worker "$@"
    ;;
  commander)
    exec python -u -m app.commander.main "$@"
    ;;
  *)
    # Unknown roles fall through to app.main (legacy)
    exec python -u -m app.main "$@"
    ;;
esac
