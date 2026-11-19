"""Command-line interface for Docker Hub Intel."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
from pathlib import Path
import sys
from typing import Any

import typer

from lupaxa.dockerhub_intel import auth
from lupaxa.dockerhub_intel.app import Service
from lupaxa.dockerhub_intel.auth import TOKEN_ENV, resolve_token, use_token
from lupaxa.dockerhub_intel.config.defaults import generate_default_files
from lupaxa.dockerhub_intel.config.loader import LoadedConfig
from lupaxa.dockerhub_intel.config.models import RepositorySpec
from lupaxa.dockerhub_intel.config.paths import config_directory, data_directory
from lupaxa.dockerhub_intel.constants import (
    EXIT_CONFIG,
    PROGRAM_NAME,
    PROGRAM_VERSION_STRING,
)
from lupaxa.dockerhub_intel.errors import (
    AuthError,
    ChecksFailed,
    ConfigError,
    HubError,
    NotFound,
    SyncIncomplete,
)
from lupaxa.dockerhub_intel.logging_setup import configure_logging
from lupaxa.dockerhub_intel.reports.progress import print_sync, syncing
from lupaxa.dockerhub_intel.reports.render import render, write_text
from lupaxa.dockerhub_intel.sync.engine import SyncReport
from lupaxa.dockerhub_intel.tui.application import run_dashboard

__all__ = ["app", "main"]

app = typer.Typer(
    name=PROGRAM_NAME,
    help="Monitor an explicit list of Docker Hub repositories.",
    no_args_is_help=False,
    invoke_without_command=True,
    pretty_exceptions_enable=False,
)
repo_app = typer.Typer(help="Manage the local repository list.", no_args_is_help=True)
profile_app = typer.Typer(help="Inspect profiles.", no_args_is_help=True)
config_app = typer.Typer(
    help="Show, validate, and create configuration.",
    no_args_is_help=True,
)
cache_app = typer.Typer(help="Inspect the local cache.", no_args_is_help=True)
app.add_typer(repo_app, name="repo")
app.add_typer(profile_app, name="profile")
app.add_typer(config_app, name="config")
app.add_typer(cache_app, name="cache")


class _Options:
    """Process options shared by every command."""

    config: Path | None = None
    offline: bool = False
    verbose: bool = False
    debug: bool = False
    token: str | None = None


_options = _Options()


def _version(value: bool) -> None:
    """Print the version and exit."""
    if value:
        typer.echo(PROGRAM_VERSION_STRING)
        raise typer.Exit


@app.callback()
def _callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version,
        is_eager=True,
        help="Print the version and exit.",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help=f"Docker Hub access token. Otherwise {TOKEN_ENV} is used.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Main configuration file. The default is the user config directory.",
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Read the cache only. Synchronisation and online checks are refused.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", help="Log informational messages."
    ),
    debug: bool = typer.Option(False, "--debug", help="Log debug messages."),
) -> None:
    """Monitor an explicit list of Docker Hub repositories."""
    if version:
        return
    _options.token = token
    _options.config = config
    _options.offline = offline
    _options.verbose = verbose
    _options.debug = debug
    use_token(token)
    if ctx.invoked_subcommand is not None:
        return
    if sys.stdin.isatty() and sys.stdout.isatty():
        _start_dashboard()
        raise typer.Exit
    typer.echo(ctx.get_help())
    raise typer.Exit


@repo_app.command("list")
def repo_list(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    fmt: str = typer.Option("text", "--format", help="text, json, csv, or markdown."),
    output: Path | None = typer.Option(None, "--output"),
    fail_on: str | None = typer.Option(
        None, "--fail-on", help="Exit 6 when a row is stale."
    ),
) -> None:
    """List configured repositories and their cached values."""
    _run(_repo_list(repo, group, profile, label, fmt, output, fail_on))


@app.command("list")
def list_command(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    fmt: str = typer.Option("text", "--format", help="text, json, csv, or markdown."),
    output: Path | None = typer.Option(None, "--output"),
    fail_on: str | None = typer.Option(
        None, "--fail-on", help="Exit 6 when a row is stale."
    ),
) -> None:
    """List configured repositories and their cached values."""
    repo_list(repo, group, profile, label, fmt, output, fail_on)


@repo_app.command("add")
def repo_add(
    repo: str = typer.Argument(...),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: list[str] | None = typer.Option(None, "--label"),
) -> None:
    """Add a repository to the local list."""
    _run(_repo_add(repo, group, profile, label or []))


@repo_app.command("remove")
def repo_remove(repo: str = typer.Argument(...)) -> None:
    """Remove a repository from the local list. History is kept."""
    _run(_repo_remove(repo))


@repo_app.command("show")
def repo_show(
    repo: str = typer.Argument(...),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show one repository, its growth, and its cached tags."""
    _run(_repo_show(repo, fmt, output))


@repo_app.command("enable")
def repo_enable(repo: str = typer.Argument(...)) -> None:
    """Include a repository in synchronisation."""
    _run(_repo_flag(repo, enabled=True))


@repo_app.command("disable")
def repo_disable(repo: str = typer.Argument(...)) -> None:
    """Keep a repository configured and skip synchronisation."""
    _run(_repo_flag(repo, enabled=False))


@repo_app.command("set-group")
def repo_set_group(
    repo: str = typer.Argument(...), group: str = typer.Argument(...)
) -> None:
    """Change the group stored for a repository."""
    _run(_repo_set_group(repo, group))


@repo_app.command("set-profile")
def repo_set_profile(
    repo: str = typer.Argument(...),
    profile: str = typer.Argument(...),
) -> None:
    """Change the profile stored for a repository."""
    _run(_repo_set_profile(repo, profile))


@profile_app.command("list")
def profile_list(
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """List profile names and the profile each one extends."""
    _run(_profile_list(fmt, output))


@profile_app.command("show")
def profile_show(
    name: str = typer.Argument(...),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show the resolved checks for one profile."""
    _run(_profile_show(name, fmt, output))


@profile_app.command("tree")
def profile_tree(
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show each profile and its parent."""
    _run(_profile_list(fmt, output))


@profile_app.command("validate")
def profile_validate() -> None:
    """Validate profile inheritance."""
    _run(_validate("profiles"))


@config_app.command("init")
def config_init(
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing files with the defaults.",
    ),
) -> None:
    """Write config.yml, profiles.yml, and repositories.yml filled with defaults."""
    written, kept = generate_default_files(_default_paths(_options.config), force=force)
    for path in written:
        typer.echo(f"Wrote {path}.")
    for path in kept:
        typer.echo(f"Kept {path}. Use --force to overwrite.")
    if not written:
        raise typer.Exit(code=EXIT_CONFIG)


def _default_paths(config: Path | None) -> tuple[Path, Path, Path]:
    if config is None:
        directory = config_directory()
        main = directory / "config.yml"
    else:
        main = config
        directory = config.parent
    return main, directory / "profiles.yml", directory / "repositories.yml"


@config_app.command("show")
def config_show(
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show effective settings. The token is not included."""
    _run(_config_show(fmt, output))


@config_app.command("validate")
def config_validate() -> None:
    """Validate the configuration files."""
    _run(_validate("configuration"))


@config_app.command("paths")
def config_paths(
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show the configuration directory and the database directory."""
    rows: list[dict[str, object]] = [
        {"path": "config", "value": str(_options.config or config_directory())},
        {"path": "data", "value": str(data_directory())},
    ]
    _emit(rows, fmt, "Paths", output)


@app.command("sync")
def sync_command(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    force: bool = typer.Option(
        False, "--force", help="Refresh even when the cache is fresh."
    ),
    concurrency: int | None = typer.Option(None, "--concurrency"),
    stats: bool = typer.Option(
        False, "--stats", help="Print pull statistics after a sync."
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Omit the summary on stderr."),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Refresh the selected repositories from Docker Hub."""
    _run(
        _sync(
            repo, group, profile, label, force, concurrency, stats, quiet, fmt, output
        )
    )


@app.command("stats")
def stats_command(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    period: str | None = typer.Option(None, "--period", help="24h, 7d, 30d, or 90d."),
    sort: str = typer.Option(
        "pulls", "--sort", help="pulls, growth, percent, updated, or least-updated."
    ),
    top: int | None = typer.Option(None, "--top"),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show cached pull counts and observed growth."""
    _run(_stats(repo, group, profile, label, period, sort, top, fmt, output))


@app.command("tags")
def tags_command(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show cached image tags."""
    _run(_tags(repo, group, profile, label, fmt, output, digest=False, platforms=False))


@app.command("digests")
def digests_command(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show cached tag digests."""
    _run(_tags(repo, group, profile, label, fmt, output, digest=True, platforms=False))


@app.command("architectures")
def architectures_command(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show cached image platforms."""
    _run(_tags(repo, group, profile, label, fmt, output, digest=False, platforms=True))


@app.command("changes")
def changes_command(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    since: str | None = typer.Option(None, "--since", help="24h, 7d, 30d, or 90d."),
    change: str | None = typer.Option(None, "--type"),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show stored tag and digest changes."""
    _run(_changes(repo, group, profile, label, since, change, fmt, output))


@cache_app.command("info")
def cache_info(
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Show how many rows are stored."""
    _run(_cache_info(fmt, output))


@cache_app.command("clear")
def cache_clear() -> None:
    """Delete cached rows. Configuration is kept."""
    _run(_cache_clear())


@cache_app.command("vacuum")
def cache_vacuum() -> None:
    """Reclaim free pages in the database."""
    _run(_cache_vacuum())


@app.command("report")
def report_command(
    repo: str | None = typer.Option(None, "--repo"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    label: str | None = typer.Option(None, "--label"),
    fmt: str = typer.Option("markdown", "--format"),
    output: Path | None = typer.Option(None, "--output"),
    fail_on: str | None = typer.Option(None, "--fail-on"),
) -> None:
    """Write a repository report from the cache."""
    _run(_repo_list(repo, group, profile, label, fmt, output, fail_on))


@app.command("tui")
def tui_command() -> None:
    """Open the terminal dashboard from the cache."""
    _start_dashboard()


@app.command("doctor")
def doctor_command(
    online: bool = typer.Option(
        False, "--online", help="Request library/nginx from Docker Hub."
    ),
    fmt: str = typer.Option("text", "--format"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Check configuration, the database, and whether a token is set."""
    _run(_doctor(online, fmt, output))


def main() -> None:
    """Run the command-line interface."""
    app()


def _run(awaitable: Coroutine[Any, Any, None]) -> None:
    try:
        asyncio.run(awaitable)
    except HubError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc


async def _open() -> Service:
    service = Service(_options.config, offline=_options.offline, token=None)
    loaded = await service.open()
    service.token = resolve_token(
        _options.token,
        env_name=loaded.app.dockerhub.token_env,
    )
    auth.active_token = service.token
    configure_logging(
        loaded.app.logging.level,
        verbose=_options.verbose,
        debug=_options.debug,
    )
    return service


def _start_dashboard() -> None:
    service = Service(_options.config, offline=_options.offline, token=None)
    try:
        loaded = asyncio.run(_prepare_dashboard(service))
    except HubError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    if not loaded.repositories.repositories:
        typer.echo("No repositories are configured.")
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        typer.echo("The dashboard needs a terminal.", err=True)
        raise typer.Exit(code=ConfigError.exit_code)
    run_dashboard(service)


async def _prepare_dashboard(service: Service) -> LoadedConfig:
    loaded = await service.open()
    service.token = resolve_token(
        _options.token,
        env_name=loaded.app.dockerhub.token_env,
    )
    auth.active_token = service.token
    configure_logging(
        loaded.app.logging.level,
        verbose=_options.verbose,
        debug=_options.debug,
    )
    await service.close()
    return loaded


async def _selected(
    service: Service,
    repo: str | None,
    group: str | None,
    profile: str | None,
    label: str | None,
    *,
    enabled_only: bool = False,
) -> list[RepositorySpec]:
    specs = service.repositories(
        repo=repo,
        group=group,
        profile=profile,
        label=label,
        enabled_only=enabled_only,
    )
    if repo is not None and not specs:
        raise NotFound(f"Repository not found: {repo}.")
    return specs


async def _repo_list(
    repo: str | None,
    group: str | None,
    profile: str | None,
    label: str | None,
    fmt: str,
    output: Path | None,
    fail_on: str | None,
) -> None:
    service = await _open()
    try:
        specs = await _selected(service, repo, group, profile, label)
        rows = await service.repo_rows(specs)
    finally:
        await service.close()
    if (
        not rows
        and output is None
        and fmt == "text"
        and repo is None
        and group is None
        and profile is None
        and label is None
    ):
        typer.echo("No repositories are configured.")
        return
    _emit(rows, fmt, "Repositories", output)
    _fail_if_stale(rows, fail_on)


async def _repo_add(
    repo: str,
    group: str | None,
    profile: str | None,
    labels: Sequence[str],
) -> None:
    service = await _open()
    try:
        name = service.add(repo, group=group, profile=profile, labels=list(labels))
    finally:
        await service.close()
    _emit([{"repository": name, "result": "added"}], "text", "Repository", None)


async def _repo_remove(repo: str) -> None:
    service = await _open()
    try:
        name = service.remove(repo)
    finally:
        await service.close()
    _emit([{"repository": name, "result": "removed"}], "text", "Repository", None)


async def _repo_show(repo: str, fmt: str, output: Path | None) -> None:
    service = await _open()
    try:
        shown = await service.show(repo)
    finally:
        await service.close()
    growth = shown.pop("growth")
    tags = shown.pop("tags")
    labels = shown.pop("labels")
    flat = dict(shown)
    if isinstance(growth, dict):
        for key, value in growth.items():
            flat[f"growth_{key}"] = value
    flat["labels"] = ", ".join(labels) if isinstance(labels, list) else ""
    flat["tags"] = ", ".join(tags) if isinstance(tags, list) else ""
    _emit([flat], fmt, "Repository", output)


async def _repo_flag(repo: str, *, enabled: bool) -> None:
    service = await _open()
    try:
        name = service.enable(repo) if enabled else service.disable(repo)
    finally:
        await service.close()
    result = "enabled" if enabled else "disabled"
    _emit([{"repository": name, "result": result}], "text", "Repository", None)


async def _repo_set_group(repo: str, group: str) -> None:
    service = await _open()
    try:
        name = service.set_group(repo, group)
    finally:
        await service.close()
    _emit([{"repository": name, "group": group}], "text", "Repository", None)


async def _repo_set_profile(repo: str, profile: str) -> None:
    service = await _open()
    try:
        name = service.set_profile(repo, profile)
    finally:
        await service.close()
    _emit([{"repository": name, "profile": profile}], "text", "Repository", None)


async def _profile_list(fmt: str, output: Path | None) -> None:
    service = await _open()
    try:
        rows = service.profile_rows()
    finally:
        await service.close()
    _emit(rows, fmt, "Profiles", output)


async def _profile_show(name: str, fmt: str, output: Path | None) -> None:
    service = await _open()
    try:
        rows = service.profile_tree(name)
    finally:
        await service.close()
    _emit(rows, fmt, name, output)


async def _validate(kind: str) -> None:
    service = await _open()
    await service.close()
    _emit([{"check": kind, "result": "ok"}], "text", "Validate", None)


async def _config_show(fmt: str, output: Path | None) -> None:
    service = await _open()
    try:
        loaded = service.loaded
        if loaded is None:
            raise ConfigError("Configuration is not loaded.")
        settings = loaded.app
        rows: list[dict[str, object]] = [
            {"setting": "profile", "value": settings.defaults.profile},
            {"setting": "concurrency", "value": settings.sync.concurrency},
            {"setting": "token_env", "value": settings.dockerhub.token_env},
            {"setting": "retention_days", "value": settings.history.retention_days},
            {"setting": "page_size", "value": settings.sync.page_size},
        ]
    finally:
        await service.close()
    _emit(rows, fmt, "Configuration", output)


async def _sync(
    repo: str | None,
    group: str | None,
    profile: str | None,
    label: str | None,
    force: bool,
    concurrency: int | None,
    stats: bool,
    quiet: bool,
    fmt: str,
    output: Path | None,
) -> None:
    service = await _open()
    try:
        specs = await _selected(service, repo, group, profile, label, enabled_only=True)
        if quiet:
            report = await service.sync(specs, force=force, concurrency=concurrency)
        else:
            with syncing() as on_progress:
                report = await service.sync(
                    specs,
                    force=force,
                    concurrency=concurrency,
                    on_progress=on_progress,
                )
        rows = (
            await service.stats_rows(specs, period=None, sort="pulls", top=None)
            if stats
            else []
        )
    finally:
        await service.close()
    if fmt == "text" and output is None:
        if not quiet:
            print_sync(report)
    else:
        _emit([_summary(report)], fmt, "Sync", output)
    if stats:
        _emit(rows, fmt, "Statistics", None)
    _raise_for_report(report)


async def _stats(
    repo: str | None,
    group: str | None,
    profile: str | None,
    label: str | None,
    period: str | None,
    sort: str,
    top: int | None,
    fmt: str,
    output: Path | None,
) -> None:
    service = await _open()
    try:
        specs = await _selected(service, repo, group, profile, label)
        rows = await service.stats_rows(specs, period=period, sort=sort, top=top)
    finally:
        await service.close()
    _emit(rows, fmt, "Statistics", output)


async def _tags(
    repo: str | None,
    group: str | None,
    profile: str | None,
    label: str | None,
    fmt: str,
    output: Path | None,
    *,
    digest: bool,
    platforms: bool,
) -> None:
    service = await _open()
    try:
        specs = await _selected(service, repo, group, profile, label)
        rows = await service.tag_rows(specs)
    finally:
        await service.close()
    if digest:
        rows = [
            {
                "repository": row["repository"],
                "tag": row["tag"],
                "digest": row["digest"],
            }
            for row in rows
        ]
    elif platforms:
        rows = [
            {
                "repository": row["repository"],
                "tag": row["tag"],
                "platforms": row["platforms"],
            }
            for row in rows
        ]
    _emit(rows, fmt, "Tags", output)


async def _changes(
    repo: str | None,
    group: str | None,
    profile: str | None,
    label: str | None,
    since: str | None,
    change: str | None,
    fmt: str,
    output: Path | None,
) -> None:
    service = await _open()
    try:
        specs = await _selected(service, repo, group, profile, label)
        rows = await service.change_rows(specs, since=since, change=change)
    finally:
        await service.close()
    _emit(rows, fmt, "Changes", output)


async def _cache_info(fmt: str, output: Path | None) -> None:
    service = await _open()
    try:
        counts = await service.database.counts() if service.database else {}
    finally:
        await service.close()
    rows = [{"table": name, "rows": count} for name, count in counts.items()]
    _emit(rows, fmt, "Cache", output)


async def _cache_clear() -> None:
    service = await _open()
    try:
        if service.database is not None:
            await service.database.clear()
    finally:
        await service.close()
    _emit([{"result": "cleared"}], "text", "Cache", None)


async def _cache_vacuum() -> None:
    service = await _open()
    try:
        if service.database is not None:
            await service.database.vacuum()
    finally:
        await service.close()
    _emit([{"result": "vacuumed"}], "text", "Cache", None)


async def _doctor(online: bool, fmt: str, output: Path | None) -> None:
    service = await _open()
    try:
        rows = await service.doctor(online=online)
    finally:
        await service.close()
    _emit(rows, fmt, "Doctor", output)


def _emit(
    rows: Sequence[dict[str, object]],
    fmt: str,
    title: str,
    output: Path | None,
) -> None:
    if fmt not in {"text", "json", "csv", "markdown"}:
        raise ConfigError("format must be text, json, csv, or markdown.")
    if fmt == "text" and output is None:
        write_text(rows, title, sys.stdout)
        return
    text = render(rows, fmt, title=title)
    if output is None:
        typer.echo(text, nl=False)
        return
    output.write_text(text, encoding="utf-8")


def _summary(report: SyncReport) -> dict[str, object]:
    return {
        "repositories": report.repositories,
        "succeeded": report.succeeded,
        "failed": report.failed,
        "cancelled": report.cancelled,
        "requests": report.requests,
        "cache_hits": report.cache_hits,
        "elapsed": report.elapsed,
        "rate_pauses": report.rate_pauses,
    }


def _raise_for_report(report: SyncReport) -> None:
    if (
        report.auth_failed
        and report.succeeded == 0
        and report.auth_failed == report.failed
    ):
        raise AuthError("; ".join(report.errors) or "Authentication failed.")
    if report.failed or report.cancelled:
        raise SyncIncomplete("; ".join(report.errors) or "Synchronisation incomplete.")


def _fail_if_stale(rows: Sequence[dict[str, object]], fail_on: str | None) -> None:
    if fail_on is None:
        return
    if fail_on != "stale":
        raise ConfigError("fail-on must be stale.")
    stale = [str(row.get("repository")) for row in rows if row.get("stale") == "Stale"]
    if stale:
        raise ChecksFailed("Stale repositories: " + ", ".join(stale) + ".")
