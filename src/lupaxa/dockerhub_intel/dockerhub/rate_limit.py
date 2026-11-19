"""Shared pause so workers honour one Docker Hub or registry limit together."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import random
import time

__all__ = ["RateLimit"]


class RateLimit:
    """Coordinate throttling across concurrent requests."""

    def __init__(self) -> None:
        """Start with no pause scheduled."""
        self._until = 0.0
        self._rate_until = 0.0
        self._lock = asyncio.Lock()
        self.pauses = 0
        self._on_wait: Callable[[float], None] | None = None

    def set_listener(self, listener: Callable[[float], None] | None) -> None:
        """Report the remaining quota wait. The callback must not await."""
        self._on_wait = listener

    @property
    def remaining(self) -> float:
        """Seconds left on a quota pause. Backoff for server errors is not included."""
        return max(0.0, self._rate_until - time.monotonic())

    def observe(self, status: int, headers: Mapping[str, str]) -> float | None:
        """Pause when a 429 or an exhausted quota says so. None means no instruction."""
        delay = _limit_delay(status, headers)
        if delay is not None and delay > 0:
            self.hold(delay)
        return delay

    def hold(self, seconds: float) -> None:
        """Pause every worker for a quota limit."""
        moment = time.monotonic() + max(seconds, 0)
        self._until = max(self._until, moment)
        self._rate_until = max(self._rate_until, moment)

    async def wait(self) -> None:
        """Sleep while a shared pause is active."""
        announced = False
        while True:
            async with self._lock:
                now = time.monotonic()
                delay = self._until - now
                rate = self._rate_until - now
                if rate > 0 and not announced:
                    self.pauses += 1
                    announced = True
            if delay <= 0:
                if announced and self._on_wait is not None:
                    self._on_wait(0.0)
                return
            if rate > 0 and self._on_wait is not None:
                self._on_wait(rate)
            await asyncio.sleep(min(1.0, delay))

    async def pause(self, seconds: float) -> None:
        """Extend the shared pause for a transient error that is not a quota limit."""
        async with self._lock:
            self._until = max(self._until, time.monotonic() + max(seconds, 0))

    @staticmethod
    def backoff(attempt: int, retry_after: float | None) -> float:
        """Return Retry-After, or exponential backoff with jitter."""
        if retry_after is not None:
            return retry_after
        base = min(30.0, 0.5 * (2**attempt))
        return float(base + random.uniform(0, base * 0.1))  # noqa: S311


def _limit_delay(status: int, headers: Mapping[str, str]) -> float | None:
    folded = {str(key).lower(): str(value) for key, value in headers.items()}
    if status == 429 and "retry-after" in folded:
        return _retry_after(folded.get("retry-after")) or 0.0
    remaining = _first_number(
        folded.get("x-ratelimit-remaining") or folded.get("ratelimit-remaining")
    )
    reset = _first_number(
        folded.get("x-ratelimit-reset") or folded.get("ratelimit-reset")
    )
    if remaining == 0 and reset is not None:
        return _reset_delay(reset)
    if status == 429:
        return None
    return None


def _retry_after(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


def _reset_delay(reset: float) -> float:
    if reset > 1_000_000_000:
        return max(0.0, reset - time.time())
    return max(0.0, reset)


def _first_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    token = raw.split(";", 1)[0].strip()
    if token == "":
        return None
    try:
        return float(token)
    except ValueError:
        return None
