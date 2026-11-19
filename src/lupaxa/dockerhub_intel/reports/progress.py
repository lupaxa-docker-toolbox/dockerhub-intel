"""Synchronisation progress. Machine-readable output never uses this module."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import sys
from typing import IO

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from lupaxa.dockerhub_intel.sync.engine import SyncReport

__all__ = ["print_sync", "status_line", "syncing"]


@contextmanager
def syncing() -> Iterator[Callable[[SyncReport], None]]:
    """Show synchronisation progress on stderr."""
    console = Console(stderr=True)
    if not console.is_terminal:
        seen = {"done": -1, "wait": -1}

        def emit(report: SyncReport) -> None:
            done = report.succeeded + report.failed
            waiting = int(report.rate_wait)
            if done == seen["done"] and waiting == seen["wait"]:
                return
            seen["done"] = done
            seen["wait"] = waiting
            console.print(status_line(report))

        yield emit
        return

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task = progress.add_task("Synchronising", total=None)

        def update(report: SyncReport) -> None:
            done = report.succeeded + report.failed
            progress.update(
                task,
                completed=done,
                total=report.repositories or None,
                description=status_line(report),
            )

        yield update


def print_sync(report: SyncReport, stream: IO[str] | None = None) -> None:
    """Print the completed run in the same shape as GitHub Intel."""
    title = (
        "Synchronisation cancelled" if report.cancelled else "Synchronisation completed"
    )
    lines = [
        title,
        "",
        f"Repositories: {report.repositories}",
        f"Successful: {report.succeeded}",
        f"Failed: {report.failed}",
        "",
        f"API requests: {report.requests}",
        f"Cache hits: {report.cache_hits}",
        "",
        f"Elapsed: {report.elapsed:.1f}s",
    ]
    if report.rate_pauses:
        lines.extend(["", f"Rate-limit pauses: {report.rate_pauses}"])
    console = Console(file=stream or sys.stdout)
    console.print("\n".join(lines), markup=False)
    if report.errors:
        errors = Console(stderr=True)
        for error in report.errors:
            errors.print(error, markup=False)


def status_line(report: SyncReport) -> str:
    """One line of repository, request, and rate-limit progress."""
    done = report.succeeded + report.failed
    name = report.latest or "Synchronising"
    line = (
        f"{name}  {done}/{report.repositories}"
        f"  requests {report.requests}"
        f"  cache {report.cache_hits}"
        f"  failed {report.failed}"
    )
    if report.rate_wait > 0:
        return (
            f"{line}  waiting for Docker Hub rate limit, "
            f"requests resume in {_wait_label(report.rate_wait)}"
        )
    if report.rate_pauses:
        return f"{line}  rate limit pauses {report.rate_pauses}"
    return line


def _wait_label(seconds: float) -> str:
    remaining = max(0, int(seconds + 0.5))
    minutes, secs = divmod(remaining, 60)
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
