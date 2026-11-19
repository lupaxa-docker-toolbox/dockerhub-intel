"""Stale-image evaluation. A stable image may legitimately update rarely."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = ["stale_state"]


def stale_state(
    updated_at: datetime | None,
    *,
    enabled: bool,
    threshold_days: int,
    now: datetime | None = None,
) -> str:
    """Return Current, Stale, Unknown, or Skipped."""
    if not enabled:
        return "Skipped"
    if updated_at is None:
        return "Unknown"
    moment = now or datetime.now(UTC)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    if moment - updated_at > timedelta(days=threshold_days):
        return "Stale"
    return "Current"
