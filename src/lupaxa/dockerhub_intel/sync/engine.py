"""Synchronise enabled repositories. One failure does not stop the others."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import time

from lupaxa.dockerhub_intel.checks.stale import stale_state
from lupaxa.dockerhub_intel.config.loader import LoadedConfig
from lupaxa.dockerhub_intel.config.models import RepositorySpec
from lupaxa.dockerhub_intel.dockerhub.client import HubClient, TagInfo
from lupaxa.dockerhub_intel.dockerhub.names import web_url
from lupaxa.dockerhub_intel.dockerhub.rate_limit import RateLimit
from lupaxa.dockerhub_intel.errors import AuthError
from lupaxa.dockerhub_intel.history.changes import compare_tags
from lupaxa.dockerhub_intel.storage.database import Database, StoredTag

__all__ = ["SyncReport", "synchronise"]


@dataclass
class SyncReport:
    """Summary of one synchronisation run."""

    repositories: int = 0
    succeeded: int = 0
    failed: int = 0
    auth_failed: int = 0
    cancelled: bool = False
    requests: int = 0
    cache_hits: int = 0
    latest: str = ""
    rate_pauses: int = 0
    rate_wait: float = 0.0
    elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)
    on_progress: Callable[[SyncReport], None] | None = field(default=None, repr=False)


async def synchronise(
    loaded: LoadedConfig,
    database: Database,
    repos: list[RepositorySpec],
    *,
    token: str | None,
    force: bool,
    concurrency: int | None = None,
    on_progress: Callable[[SyncReport], None] | None = None,
) -> SyncReport:
    """Refresh stale checks for the selected repositories."""
    report = SyncReport(repositories=len(repos))
    report.on_progress = on_progress
    if not repos:
        return report
    limiter = RateLimit()
    client = HubClient(
        api_url=loaded.app.dockerhub.api_url,
        registry_url=loaded.app.dockerhub.registry_url,
        token=token,
        timeout=loaded.app.sync.timeout,
        retries=loaded.app.sync.retries,
        page_size=loaded.app.sync.page_size,
        limiter=limiter,
    )
    run_id = await database.start_run()
    limit = concurrency or loaded.app.sync.concurrency
    semaphore = asyncio.Semaphore(max(1, min(limit, 50)))

    def waiting(_seconds: float) -> None:
        _publish(report, client)

    client.hub_limit.set_listener(waiting)
    client.registry_limit.set_listener(waiting)
    client.on_request = lambda: _publish(report, client)
    _publish(report, client)
    started = time.perf_counter()
    try:
        await asyncio.gather(
            *[
                _one(loaded, database, client, semaphore, run_id, spec, force, report)
                for spec in repos
            ]
        )
    except asyncio.CancelledError:
        report.cancelled = True
        await database.finish_run(run_id, "cancelled")
        raise
    finally:
        report.elapsed = time.perf_counter() - started
        await client.aclose()
    if loaded.app.history.enabled:
        await database.apply_retention(loaded.app.history.retention_days)
    status = "incomplete" if report.failed or report.cancelled else "ok"
    await database.finish_run(run_id, status)
    return report


async def _one(
    loaded: LoadedConfig,
    database: Database,
    client: HubClient,
    semaphore: asyncio.Semaphore,
    run_id: int,
    spec: RepositorySpec,
    force: bool,
    report: SyncReport,
) -> None:
    async with semaphore:
        profile = loaded.resolved(spec)
        report.latest = spec.repo
        _publish(report, client)
        try:
            if profile.enabled["metadata"] or profile.enabled["stats"]:
                await _metadata(
                    loaded,
                    database,
                    client,
                    run_id,
                    spec,
                    profile.enabled,
                    force,
                    report,
                )
            if _wants_tags(profile.enabled):
                await _tags(
                    loaded,
                    database,
                    client,
                    run_id,
                    spec,
                    profile.enabled,
                    force,
                    report,
                )
            if profile.enabled["stale"] and loaded.app.checks.stale.enabled:
                await _stale(loaded, database, run_id, spec)
            report.succeeded += 1
        except AuthError as exc:
            report.failed += 1
            report.auth_failed += 1
            report.errors.append(f"{spec.repo}: {exc}")
            await database.record_error(spec.repo, str(exc))
            await database.add_result(run_id, spec.repo, "auth", "error", str(exc))
        except Exception as exc:
            report.failed += 1
            report.errors.append(f"{spec.repo}: {exc}")
            await database.record_error(spec.repo, str(exc))
            await database.add_result(run_id, spec.repo, "sync", "error", str(exc))
        _publish(report, client)


def _wants_tags(enabled: dict[str, bool]) -> bool:
    names = ("tags", "digests", "architectures", "image_details", "stale")
    return any(enabled[name] for name in names)


async def _metadata(
    loaded: LoadedConfig,
    database: Database,
    client: HubClient,
    run_id: int,
    spec: RepositorySpec,
    enabled: dict[str, bool],
    force: bool,
    report: SyncReport,
) -> None:
    cache = loaded.app.cache
    category = "stats" if enabled["stats"] else "metadata"
    ttl = cache.stats_ttl if category == "stats" else cache.metadata_ttl
    if not force and await database.fresh(
        spec.repo, category, ttl, enabled=cache.enabled
    ):
        report.cache_hits += 1
        await database.add_result(run_id, spec.repo, category, "cached", None)
        return
    info = await client.repository(spec.repo)
    if info is None:
        await database.record_error(spec.repo, "Repository not found.")
        await database.add_result(
            run_id, spec.repo, category, "unavailable", "Repository not found."
        )
        return
    visibility = (
        None if info.private is None else ("private" if info.private else "public")
    )
    await database.save_repository(
        repo=spec.repo,
        description=info.description,
        visibility=visibility,
        status=info.status,
        created_at=info.created_at,
        updated_at=info.updated_at,
        pull_count=info.pull_count,
        star_count=info.star_count,
        web_url=web_url(spec.repo),
        error=None,
    )
    if enabled["stats"]:
        await database.add_snapshot(spec.repo, info.pull_count, info.star_count)
    await database.mark_fresh(spec.repo, "metadata", complete=True)
    if enabled["stats"]:
        await database.mark_fresh(spec.repo, "stats", complete=True)
    await database.add_result(run_id, spec.repo, category, "ok", None)


async def _tags(
    loaded: LoadedConfig,
    database: Database,
    client: HubClient,
    run_id: int,
    spec: RepositorySpec,
    enabled: dict[str, bool],
    force: bool,
    report: SyncReport,
) -> None:
    cache = loaded.app.cache
    if not force and await database.fresh(
        spec.repo, "tags", cache.tags_ttl, enabled=cache.enabled
    ):
        report.cache_hits += 1
        await database.add_result(run_id, spec.repo, "tags", "cached", None)
        return
    page = await client.tags(spec.repo)
    previous = (
        await database.tag_map(spec.repo)
        if await database.tag_listing_was_complete(spec.repo)
        else None
    )
    had = previous is not None
    current = {tag.name: tag.digest for tag in page.tags}
    events = compare_tags(
        previous,
        current,
        complete=page.complete,
        had_complete_baseline=had,
    )
    stored = [_stored(tag) for tag in page.tags]
    await database.replace_tags(spec.repo, stored, complete=page.complete)
    if events:
        await database.add_events(
            spec.repo,
            [
                (event.name, event.change, event.old_digest, event.new_digest)
                for event in events
            ],
        )
    await database.mark_fresh(spec.repo, "tags", complete=page.complete)
    if enabled["digests"] or enabled["architectures"] or enabled["image_details"]:
        await database.mark_fresh(spec.repo, "manifests", complete=page.complete)
    if enabled["image_details"]:
        await database.replace_manifests(spec.repo, stored)
    status = "ok" if page.complete else "partial"
    await database.add_result(run_id, spec.repo, "tags", status, page.error)
    for check in ("digests", "architectures", "image_details"):
        if enabled[check]:
            await database.add_result(run_id, spec.repo, check, status, page.error)


async def _stale(
    loaded: LoadedConfig,
    database: Database,
    run_id: int,
    spec: RepositorySpec,
) -> None:
    row = await database.repository_row(spec.repo)
    updated = None if row is None else _moment(row.get("updated_at"))
    state = stale_state(
        updated,
        enabled=True,
        threshold_days=loaded.app.checks.stale.repository_days,
    )
    await database.add_result(run_id, spec.repo, "stale", state.lower(), None)


def _publish(report: SyncReport, client: HubClient) -> None:
    report.requests = client.requests
    report.rate_pauses = client.hub_limit.pauses + client.registry_limit.pauses
    report.rate_wait = max(client.hub_limit.remaining, client.registry_limit.remaining)
    if report.on_progress is not None:
        report.on_progress(report)


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _stored(tag: TagInfo) -> StoredTag:
    return StoredTag(
        name=tag.name,
        updated_at=tag.updated_at,
        full_size=tag.full_size,
        digest=tag.digest,
        media_type=tag.media_type,
        platforms=tag.platforms,
    )
