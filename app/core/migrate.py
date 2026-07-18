"""Database migration runner — runs alembic upgrade head on startup.

Called by entrypoint.sh BEFORE any microservice starts.
Handles connection errors gracefully so services fail-fast with clear logs.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("karsa.migrate")


def run_migrations() -> bool:
    """Run alembic upgrade head. Returns True on success."""
    from alembic.config import Config

    from alembic import command

    try:
        alembic_cfg = Config("/app/alembic.ini")
        logger.info("Running database migrations...")
        command.upgrade(alembic_cfg, "head")
        logger.info("Migrations complete")
        return True
    except Exception as e:
        logger.error(f"Migrations failed: {e}")
        return False


def main() -> int:
    """CLI entrypoint — runs migrations and exits."""
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    success = run_migrations()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
