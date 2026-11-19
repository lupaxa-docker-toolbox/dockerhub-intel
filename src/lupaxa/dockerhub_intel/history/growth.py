"""Pull-count growth from stored observations. Missing baselines are N/A."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["Growth", "PERIODS", "growth_for", "parse_period"]

PERIODS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


@dataclass(frozen=True)
class Observation:
    """One stored pull-count snapshot."""

    observed_at: datetime
    pull_count: int | None
    anomaly: bool = False


@dataclass(frozen=True)
class Growth:
    """A comparison between two real observations."""

    label: str
    change: int | None
    baseline_at: datetime | None
    current_at: datetime | None
    baseline_count: int | None
    current_count: int | None

    @property
    def text(self) -> str:
        """Return the change, or N/A when a baseline is missing."""
        if self.change is None:
            return "N/A"
        return f"{self.change:+d}"


def parse_period(value: str) -> int:
    """Return the number of days in ``24h``, ``7d``, ``30d``, or ``90d``."""
    days = PERIODS.get(value.strip().lower())
    if days is None:
        message = "period must be 24h, 7d, 30d, or 90d."
        raise ValueError(message)
    return days


def growth_for(
    observations: list[Observation],
    *,
    days: int | None = None,
    label: str = "previous",
) -> Growth:
    """
    Compare the latest observation with a stored baseline.

    The baseline for a period is the newest observation at or before
    ``latest - days``. Previous means the observation immediately before
    the latest. No value is invented for a time that was not collected.
    """

    usable = [item for item in observations if item.pull_count is not None]
    usable.sort(key=lambda item: item.observed_at)
    if not usable:
        return Growth(label, None, None, None, None, None)
    current = usable[-1]
    baseline = _baseline(usable, current, days)
    if baseline is None or baseline.pull_count is None or current.pull_count is None:
        return Growth(
            label,
            None,
            None if baseline is None else baseline.observed_at,
            current.observed_at,
            None if baseline is None else baseline.pull_count,
            current.pull_count,
        )
    return Growth(
        label,
        current.pull_count - baseline.pull_count,
        baseline.observed_at,
        current.observed_at,
        baseline.pull_count,
        current.pull_count,
    )


def _baseline(
    usable: list[Observation],
    current: Observation,
    days: int | None,
) -> Observation | None:
    earlier = [item for item in usable if item.observed_at < current.observed_at]
    if not earlier:
        return None
    if days is None:
        return earlier[-1]
    cutoff = current.observed_at - timedelta(days=days)
    candidates = [item for item in earlier if item.observed_at <= cutoff]
    if not candidates:
        return None
    return candidates[-1]
