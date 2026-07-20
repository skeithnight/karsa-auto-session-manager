#!/bin/sh
# Karsa ASM — container entrypoint
# Sets DNS to gluetun's resolver (bypasses ISP DNS poisoning).
# Dispatches to the correct Python module based on KARSA_ROLE.

if [ "$KARSA_ROLE" != "backtest" ]; then
  echo "nameserver 127.0.0.1" > /etc/resolv.conf
  echo "ENTRYPOINT: resolv.conf set to:" >&2
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
    exec python -m app.data_engine.main "$@"
    ;;
  live)
    exec python -m app.consumer.live_loop "$@"
    ;;
  shadow)
    exec python -m app.consumer.shadow_loop "$@"
    ;;
  backtest)
    exec python -m app.backtest.worker "$@"
    ;;
  commander)
    exec python -m app.commander.main "$@"
    ;;
  *)
    # Unknown roles fall through to app.main (legacy)
    exec python -m app.main "$@"
    ;;
esac
