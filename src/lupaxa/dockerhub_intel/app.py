"""Services shared by the command line and the terminal interface."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lupaxa.dockerhub_intel.checks.stale import stale_state
from lupaxa.dockerhub_intel.config.defaults import write_default_files
from lupaxa.dockerhub_intel.config.editor import (
    add_repository,
    disable_repository,
    enable_repository,
    remove_repository,
    set_group,
    set_profile,
)
from lupaxa.dockerhub_intel.config.loader import LoadedConfig, load_config
from lupaxa.dockerhub_intel.config.models import RepositorySpec
from lupaxa.dockerhub_intel.config.paths import (
    config_directory,
    data_directory,
    default_config_file,
)
from lupaxa.dockerhub_intel.config.profiles import resolve_profile
from lupaxa.dockerhub_intel.dates import format_date
from lupaxa.dockerhub_intel.dockerhub.client import HubClient
from lupaxa.dockerhub_intel.dockerhub.names import canonical_repository, web_url
from lupaxa.dockerhub_intel.errors import ConfigError, NotFound
from lupaxa.dockerhub_intel.history.growth import Observation, growth_for, parse_period
from lupaxa.dockerhub_intel.storage.database import Database
from lupaxa.dockerhub_intel.sync.engine import SyncReport, synchronise

__all__ = ["Service"]


class Service:
    """Load configuration, read the cache, and synchronise repositories."""

    def __init__(
        self,
        config_path: Path | None,
        *,
        offline: bool,
        token: str | None,
    ) -> None:
        """Remember process options. Files are opened in ``open``."""
        self.config_path = config_path
        self.offline = offline
        self.token = token
        self.loaded: LoadedConfig | None = None
        self.database: Database | None = None

    async def open(self, *, create: bool = True) -> LoadedConfig:
        """Open configuration and the database."""
        path = self.config_path
        if path is None and create:
            write_default_files(config_directory())
            path = default_config_file()
        self.loaded = load_config(path)
        self.database = Database(data_directory() / "cache.sqlite")
        await self.database.open()
        return self.loaded

    async def close(self) -> None:
        """Close the database."""
        if self.database is not None:
            await self.database.close()
            self.database = None

    def _config(self) -> LoadedConfig:
        if self.loaded is None:
            raise ConfigError("Configuration is not loaded.")
        return self.loaded

    def _db(self) -> Database:
        if self.database is None:
            raise ConfigError("Database is not open.")
        return self.database

    def repositories(
        self,
        *,
        repo: str | None = None,
        group: str | None = None,
        profile: str | None = None,
        label: str | None = None,
        enabled_only: bool = False,
    ) -> list[RepositorySpec]:
        """Return configured repositories after optional filters."""
        canonical = canonical_repository(repo) if repo else None
        return self._config().select(
            repo=canonical,
            group=group,
            profile=profile,
            label=label,
            enabled_only=enabled_only,
        )

    def add(
        self,
        repo: str,
        *,
        group: str | None,
        profile: str | None,
        labels: list[str],
    ) -> str:
        """Add a repository to the local list."""
        return add_repository(
            self._config(),
            repo,
            group=group,
            profile=profile,
            labels=labels,
        )

    def remove(self, repo: str) -> str:
        """Remove a repository from the local list."""
        return remove_repository(self._config(), repo)

    def enable(self, repo: str) -> str:
        """Include a repository in synchronisation."""
        return enable_repository(self._config(), repo, enabled=True)

    def disable(self, repo: str) -> str:
        """Keep a repository configured and skip synchronisation."""
        return disable_repository(self._config(), repo)

    def set_group(self, repo: str, group: str) -> str:
        """Change a repository group."""
        return set_group(self._config(), repo, group)

    def set_profile(self, repo: str, profile: str) -> str:
        """Change a repository profile."""
        return set_profile(self._config(), repo, profile)

    async def sync(
        self,
        repos: list[RepositorySpec],
        *,
        force: bool,
        concurrency: int | None,
        on_progress: Callable[[SyncReport], None] | None = None,
    ) -> SyncReport:
        """Synchronise repositories. Offline mode refuses."""
        if self.offline:
            raise ConfigError("Offline mode does not synchronise.")
        return await synchronise(
            self._config(),
            self._db(),
            repos,
            token=self.token,
            force=force,
            concurrency=concurrency,
            on_progress=on_progress,
        )

    async def repo_rows(self, specs: list[RepositorySpec]) -> list[dict[str, object]]:
        """Return repository rows from configuration and the cache."""
        rows: list[dict[str, object]] = []
        for spec in specs:
            cached = await self._db().repository_row(spec.repo)
            profile = spec.profile or self._config().app.defaults.profile
            updated = None if cached is None else cached.get("updated_at")
            rows.append(
                {
                    "repository": spec.repo,
                    "visibility": None if cached is None else cached.get("visibility"),
                    "group": spec.group,
                    "profile": profile,
                    "enabled": spec.enabled,
                    "pulls": None if cached is None else cached.get("pull_count"),
                    "stars": None if cached is None else cached.get("star_count"),
                    "updated": self._shown_updated(updated),
                    "status": None if cached is None else cached.get("status"),
                    "stale": self._stale(spec, updated),
                    "error": None if cached is None else cached.get("metadata_error"),
                }
            )
        return rows

    async def show(self, repo: str) -> dict[str, object]:
        """Return one repository, including cache and growth."""
        name = canonical_repository(repo)
        matches = self.repositories(repo=name)
        if not matches:
            raise NotFound(f"Repository not found: {name}.")
        spec = matches[0]
        cached = await self._db().repository_row(name)
        snapshots = await self._db().snapshots(name)
        observations = [
            Observation(item.observed_at, item.pull_count, item.anomaly)
            for item in snapshots
        ]
        growth = {
            "previous": growth_for(observations, label="previous").text,
            "24h": growth_for(observations, days=1, label="24h").text,
            "7d": growth_for(observations, days=7, label="7d").text,
            "30d": growth_for(observations, days=30, label="30d").text,
            "90d": growth_for(observations, days=90, label="90d").text,
        }
        tags = await self._db().tags_for(name)
        updated = None if cached is None else cached.get("updated_at")
        stale = self._stale(spec, updated)
        return {
            "repository": name,
            "visibility": None if cached is None else cached.get("visibility"),
            "url": web_url(name),
            "group": spec.group,
            "profile": spec.profile or self._config().app.defaults.profile,
            "labels": spec.labels,
            "enabled": spec.enabled,
            "description": None if cached is None else cached.get("description"),
            "status": None if cached is None else cached.get("status"),
            "pulls": None if cached is None else cached.get("pull_count"),
            "stars": None if cached is None else cached.get("star_count"),
            "updated": self._shown_updated(updated),
            "growth": growth,
            "stale": stale,
            "tags": [tag.name for tag in tags],
            "error": None if cached is None else cached.get("metadata_error"),
        }

    async def stats_rows(
        self,
        specs: list[RepositorySpec],
        *,
        period: str | None,
        sort: str,
        top: int | None,
    ) -> list[dict[str, object]]:
        """Return pull statistics and observed growth."""
        days = _period_days(period)
        rows: list[dict[str, object]] = []
        for spec in specs:
            snapshots = await self._db().snapshots(spec.repo)
            observations = [
                Observation(item.observed_at, item.pull_count, item.anomaly)
                for item in snapshots
            ]
            growth = growth_for(
                observations,
                days=days,
                label=period or "previous",
            )
            latest = snapshots[-1] if snapshots else None
            cached = await self._db().repository_row(spec.repo)
            pulls = None if latest is None else latest.pull_count
            cached_pulls = None if cached is None else cached.get("pull_count")
            if pulls is None and isinstance(cached_pulls, int):
                pulls = cached_pulls
            percent = _percent(growth.change, growth.baseline_count)
            rows.append(
                {
                    "repository": spec.repo,
                    "pulls": pulls,
                    "growth": growth.text,
                    "baseline": None
                    if growth.baseline_at is None
                    else growth.baseline_at.isoformat(),
                    "current": None
                    if growth.current_at is None
                    else growth.current_at.isoformat(),
                    "percent": percent,
                    "anomaly": bool(latest and latest.anomaly),
                    "updated": None if cached is None else cached.get("updated_at"),
                    "_change": growth.change,
                }
            )
        rows.sort(key=lambda row: _sort_key(row, sort))
        if top is not None:
            rows = rows[:top]
        for row in rows:
            row.pop("_change", None)
            row["updated"] = self._shown_updated(row.get("updated"))
        return rows

    async def tag_rows(self, specs: list[RepositorySpec]) -> list[dict[str, object]]:
        """Return cached tags."""
        rows: list[dict[str, object]] = []
        for spec in specs:
            for tag in await self._db().tags_for(spec.repo):
                rows.append(
                    {
                        "repository": spec.repo,
                        "tag": tag.name,
                        "digest": tag.digest,
                        "updated": self._shown_updated(tag.updated_at),
                        "size": tag.full_size,
                        "platforms": ", ".join(tag.platforms),
                    }
                )
        return rows

    async def change_rows(
        self,
        specs: list[RepositorySpec],
        *,
        since: str | None,
        change: str | None,
    ) -> list[dict[str, object]]:
        """Return stored tag and digest changes."""
        allowed = {item.repo for item in specs}
        moment = None
        if since is not None:
            moment = datetime.now(UTC) - timedelta(days=_period_days(since) or 0)
        kind = None if change is None else _change_name(change)
        events = await self._db().events(since=moment, change=kind)
        return [
            {
                "repository": event["repo"],
                "tag": event["name"],
                "change": event["change"],
                "old": event["old_digest"],
                "new": event["new_digest"],
                "observed": event["observed_at"],
            }
            for event in events
            if event["repo"] in allowed
        ]

    def _shown_updated(self, value: object) -> object:
        if not isinstance(value, str) or value.strip() == "":
            return value
        return format_date(value, self._config().app.display.date_format)

    def _stale(self, spec: RepositorySpec, updated: object) -> str:
        settings = self._config().app.checks.stale
        enabled = self._config().resolved(spec).enabled["stale"] and settings.enabled
        return stale_state(
            _time(updated),
            enabled=enabled,
            threshold_days=settings.repository_days,
        )

    def profile_rows(self) -> list[dict[str, object]]:
        """Return profile names and parents."""
        return [
            {"profile": name, "extends": spec.extends or ""}
            for name, spec in sorted(self._config().profiles.profiles.items())
        ]

    def profile_tree(self, name: str) -> list[dict[str, object]]:
        """Return the resolved checks for one profile."""
        resolved = resolve_profile(self._config().profiles.profiles, name)
        return [
            {
                "check": check,
                "enabled": "Yes" if resolved.enabled[check] else "No",
                "source": resolved.sources[check],
            }
            for check in resolved.enabled
        ]

    async def doctor(self, *, online: bool) -> list[dict[str, object]]:
        """Report configuration, database access, and whether a token is set."""
        loaded = self._config()
        rows: list[dict[str, object]] = [
            {"check": "configuration", "result": "ok", "detail": str(loaded.path)},
            {"check": "database", "result": "ok", "detail": str(self._db().path)},
            {
                "check": "token",
                "result": "set" if self.token else "absent",
                "detail": loaded.app.dockerhub.token_env,
            },
        ]
        if online:
            if self.offline:
                raise ConfigError("Offline mode does not contact Docker Hub.")
            client = HubClient(
                api_url=loaded.app.dockerhub.api_url,
                registry_url=loaded.app.dockerhub.registry_url,
                token=self.token,
                timeout=loaded.app.sync.timeout,
                retries=0,
                page_size=1,
            )
            try:
                info = await client.repository("library/nginx")
                rows.append(
                    {
                        "check": "api",
                        "result": "ok" if info is not None else "unavailable",
                        "detail": "library/nginx",
                    }
                )
            finally:
                await client.aclose()
        return rows


def _period_days(period: str | None) -> int | None:
    if period is None:
        return None
    try:
        return parse_period(period)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _percent(change: int | None, baseline: int | None) -> str:
    if change is None or not isinstance(baseline, int) or baseline == 0:
        return "N/A"
    return f"{(change / baseline) * 100:.1f}%"


def _sort_key(row: dict[str, object], sort: str) -> tuple[int, float | str]:
    if sort == "growth":
        value = row.get("_change")
        if not isinstance(value, int) or value < 0:
            return (1, 0)
        return (0, -value)
    if sort == "percent":
        text = str(row.get("percent"))
        if text == "N/A":
            return (1, 0)
        return (0, -float(text.rstrip("%")))
    if sort == "least-updated":
        updated = row.get("updated")
        return (1, "") if updated is None else (0, str(updated))
    if sort == "updated":
        updated = row.get("updated")
        return (1, "") if updated is None else (0, _invert_text(str(updated)))
    pulls = row.get("pulls")
    if not isinstance(pulls, int):
        return (1, 0)
    return (0, -pulls)


def _invert_text(value: str) -> str:
    return "".join(chr(0x10FFFF - ord(char)) for char in value)


def _change_name(value: str) -> str:
    names = {
        "digest": "updated",
        "tag-added": "new",
        "tag-removed": "removed",
        "tag-updated": "updated",
        "new": "new",
        "removed": "removed",
        "updated": "updated",
    }
    try:
        return names[value]
    except KeyError as exc:
        raise ConfigError(
            "type must be digest, tag-added, tag-removed, or tag-updated."
        ) from exc
