"""AI Circuit Breaker (Sprint 5).

Protects the trading engine from AI service outages.
"""
from __future__ import annotations

import time

from loguru import logger


class AICircuitBreaker:
    """State machine for degrading gracefully when AI is down."""

    def __init__(self, failure_threshold: int = 3, reset_timeout_seconds: int = 300) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout_seconds

        self.failures = 0
        self.state = "CLOSED" # CLOSED (healthy), OPEN (failing), HALF_OPEN (testing recovery)
        self.last_failure_time = 0.0

    def allow_request(self) -> bool:
        """Check if a request should be allowed to proceed."""
        if self.state == "CLOSED":
            return True

        if self.state == "OPEN":
            now = time.time()
            if now - self.last_failure_time > self.reset_timeout:
                logger.info("AICircuitBreaker: Entering HALF_OPEN state")
                self.state = "HALF_OPEN"
                return True
            return False

        if self.state == "HALF_OPEN":
            # Only allow one request to test the waters
            return True

        return True

    def record_success(self) -> None:
        """Record a successful request."""
        if self.state != "CLOSED":
            logger.info("AICircuitBreaker: Service recovered, entering CLOSED state")
        self.failures = 0
        self.state = "CLOSED"

    def record_failure(self) -> None:
        """Record a failed request."""
        self.failures += 1
        self.last_failure_time = time.time()

        if self.state == "HALF_OPEN" or self.failures >= self.failure_threshold:
            if self.state != "OPEN":
                logger.warning(f"AICircuitBreaker: Threshold reached ({self.failures}), entering OPEN state")
            self.state = "OPEN"
