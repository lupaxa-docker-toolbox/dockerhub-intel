"""Tag and digest changes. A partial listing never reports a removal."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TagChange", "compare_tags"]


@dataclass(frozen=True)
class TagChange:
    """One detected tag change."""

    name: str
    change: str
    old_digest: str | None
    new_digest: str | None


def compare_tags(
    previous: dict[str, str | None] | None,
    current: dict[str, str | None],
    *,
    complete: bool,
    had_complete_baseline: bool,
) -> list[TagChange]:
    """
    Compare tag digests with the previous complete snapshot.

    Removals are emitted only when both listings completed. The first
    complete listing establishes a baseline and emits no events.
    """

    if not had_complete_baseline or previous is None:
        return []
    events: list[TagChange] = []
    for name in sorted(current):
        if name not in previous:
            events.append(TagChange(name, "new", None, current[name]))
            continue
        old = previous[name]
        new = current[name]
        if old != new:
            events.append(TagChange(name, "updated", old, new))
    if complete:
        for name in sorted(set(previous) - set(current)):
            events.append(TagChange(name, "removed", previous[name], None))
    return events
