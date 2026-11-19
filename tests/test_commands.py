"""Command behaviour against a temporary configuration and cache."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
import respx
from textual.widgets import DataTable, Input, Static
from typer.testing import CliRunner

from lupaxa.dockerhub_intel import auth
from lupaxa.dockerhub_intel.app import Service
from lupaxa.dockerhub_intel.cli import app
from lupaxa.dockerhub_intel.config.defaults import write_default_files
from lupaxa.dockerhub_intel.config.editor import add_repository, remove_repository
from lupaxa.dockerhub_intel.config.loader import load_config
from lupaxa.dockerhub_intel.constants import EXIT_CONFIG, EXIT_SUCCESS
from lupaxa.dockerhub_intel.errors import ConfigError, NotFound
from lupaxa.dockerhub_intel.logging_setup import configure_logging
from lupaxa.dockerhub_intel.sync.engine import SyncReport
from lupaxa.dockerhub_intel.tui.application import (
    Dashboard,
    RepoDetail,
    _detail_pairs,
    _painted,
    run_dashboard,
)

runner = CliRunner()


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config = tmp_path / "config"
    monkeypatch.setenv("DOCKERHUB_INTEL_CONFIG_DIR", str(config))
    monkeypatch.setenv("DOCKERHUB_INTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DOCKERHUB_INTEL_CONFIG", raising=False)
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
    write_default_files(config)
    return config / "config.yml"


def test_repository_commands_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    added = runner.invoke(
        app, ["--config", str(config), "repo", "add", "nginx", "--label", "web"]
    )
    assert added.exit_code == EXIT_SUCCESS
    assert "library/nginx" in added.stdout
    blocked = runner.invoke(app, ["--config", str(config), "tui"])
    assert blocked.exit_code == EXIT_CONFIG
    assert "terminal" in blocked.stderr
    listed = runner.invoke(
        app,
        ["--config", str(config), "repo", "list", "--format", "json"],
    )
    assert listed.exit_code == EXIT_SUCCESS
    payload = json.loads(listed.stdout)
    assert payload[0]["repository"] == "library/nginx"
    assert payload[0]["stale"] == "Skipped"
    shown = runner.invoke(
        app, ["--config", str(config), "repo", "show", "nginx", "--format", "json"]
    )
    assert shown.exit_code == EXIT_SUCCESS
    assert json.loads(shown.stdout)[0]["growth_24h"] == "N/A"
    grouped = runner.invoke(
        app,
        ["--config", str(config), "repo", "set-group", "nginx", "web"],
    )
    assert grouped.exit_code == EXIT_SUCCESS
    disabled = runner.invoke(app, ["--config", str(config), "repo", "disable", "nginx"])
    assert disabled.exit_code == EXIT_SUCCESS
    enabled = runner.invoke(app, ["--config", str(config), "repo", "enable", "nginx"])
    assert enabled.exit_code == EXIT_SUCCESS
    profiled = runner.invoke(
        app,
        ["--config", str(config), "repo", "set-profile", "nginx", "watchlist"],
    )
    assert profiled.exit_code == EXIT_SUCCESS
    removed = runner.invoke(app, ["--config", str(config), "repo", "remove", "nginx"])
    assert removed.exit_code == EXIT_SUCCESS
    missing = runner.invoke(app, ["--config", str(config), "repo", "show", "nginx"])
    assert missing.exit_code == 5


def test_offline_sync_does_not_call_docker_hub(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    runner.invoke(app, ["--config", str(config), "repo", "add", "nginx"])
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r"https://hub.docker.com/.*").mock(
            return_value=httpx.Response(500)
        )
        result = runner.invoke(app, ["--config", str(config), "--offline", "sync"])
    assert result.exit_code == EXIT_CONFIG
    assert route.called is False


def test_config_profile_cache_and_report_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    validated = runner.invoke(app, ["--config", str(config), "config", "validate"])
    assert validated.exit_code == EXIT_SUCCESS
    shown = runner.invoke(
        app, ["--config", str(config), "config", "show", "--format", "json"]
    )
    assert "DOCKERHUB_TOKEN" in shown.stdout
    assert "secret" not in shown.stdout
    profiles = runner.invoke(
        app, ["--config", str(config), "profile", "show", "standard"]
    )
    assert profiles.exit_code == EXIT_SUCCESS
    assert "tags" in profiles.stdout
    tree = runner.invoke(
        app, ["--config", str(config), "profile", "tree", "--format", "csv"]
    )
    assert "standard" in tree.stdout
    checked = runner.invoke(app, ["--config", str(config), "profile", "validate"])
    assert checked.exit_code == EXIT_SUCCESS
    info = runner.invoke(
        app, ["--config", str(config), "cache", "info", "--format", "json"]
    )
    assert json.loads(info.stdout)[0]["rows"] == 0
    cleared = runner.invoke(app, ["--config", str(config), "cache", "clear"])
    assert cleared.exit_code == EXIT_SUCCESS
    vacuumed = runner.invoke(app, ["--config", str(config), "cache", "vacuum"])
    assert vacuumed.exit_code == EXIT_SUCCESS
    report = runner.invoke(
        app,
        ["--config", str(config), "report", "--format", "markdown"],
    )
    assert report.stdout.startswith("# Repositories")
    doctor = runner.invoke(app, ["--config", str(config), "doctor"])
    assert doctor.exit_code == EXIT_SUCCESS
    assert "absent" in doctor.stdout
    stats = runner.invoke(app, ["--config", str(config), "stats"])
    assert "No rows." in stats.stdout
    changes = runner.invoke(app, ["--config", str(config), "changes"])
    assert changes.exit_code == EXIT_SUCCESS
    tags = runner.invoke(app, ["--config", str(config), "tags"])
    digests = runner.invoke(app, ["--config", str(config), "digests"])
    architectures = runner.invoke(app, ["--config", str(config), "architectures"])
    assert (
        tags.exit_code == digests.exit_code == architectures.exit_code == EXIT_SUCCESS
    )
    dashboard = runner.invoke(app, ["--config", str(config), "tui"])
    assert dashboard.exit_code == EXIT_SUCCESS
    assert "No repositories are configured." in dashboard.stdout
    listed = runner.invoke(app, ["--config", str(config), "list"])
    assert listed.exit_code == EXIT_SUCCESS
    assert "No repositories are configured." in listed.stdout


def test_editor_adds_and_removes_without_deleting_history(tmp_path: Path) -> None:
    write_default_files(tmp_path)
    loaded = load_config(tmp_path / "config.yml")
    add_repository(loaded, "nginx", group="web", profile="standard", labels=["edge"])
    loaded = load_config(tmp_path / "config.yml")
    assert loaded.repositories.repositories[0].labels == ["edge"]
    remove_repository(loaded, "library/nginx")
    loaded = load_config(tmp_path / "config.yml")
    assert loaded.repositories.repositories == []


async def test_sync_records_pulls_tags_and_a_digest_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    repositories = config.parent / "repositories.yml"
    repositories.write_text("repositories:\n  - library/nginx\n", encoding="utf-8")
    with respx.mock(assert_all_called=False) as router:
        router.get("https://hub.docker.com/v2/repositories/library/nginx/").mock(
            return_value=httpx.Response(200, json=_repository(20))
        )
        router.get(url__regex=r".*/library/nginx/tags.*").mock(
            return_value=httpx.Response(200, json=_tags("sha256:one"))
        )
        service = Service(config, offline=False, token=None)
        await service.open(create=False)
        report = await service.sync(service.repositories(), force=True, concurrency=2)
        await service.close()
    assert report.succeeded == 1
    with respx.mock(assert_all_called=False) as router:
        router.get("https://hub.docker.com/v2/repositories/library/nginx/").mock(
            return_value=httpx.Response(200, json=_repository(25))
        )
        router.get(url__regex=r".*/library/nginx/tags.*").mock(
            return_value=httpx.Response(200, json=_tags("sha256:two"))
        )
        service = Service(config, offline=False, token=None)
        await service.open(create=False)
        await service.sync(
            service.repositories(profile="standard"),
            force=True,
            concurrency=None,
        )
        changes = await service.change_rows(
            service.repositories(), since=None, change="digest"
        )
        rows = await service.stats_rows(
            service.repositories(), period=None, sort="growth", top=1
        )
        await service.close()
    assert changes[0]["new"] == "sha256:two"
    assert rows[0]["growth"] == "+5"


async def test_dashboard_searches_and_opens_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    add_repository(load_config(config), "nginx")
    dashboard = Dashboard(Service(config, offline=True, token=None))
    async with dashboard.run_test() as pilot:
        await pilot.pause()
        table = pilot.app.query_one(DataTable)
        assert table.row_count == 1
        assert _headers(table) == [
            "Repository",
            "Group",
            "Profile",
            "Enabled",
            "Stale",
        ]
        assert any(binding.key == "ctrl+q" for binding in Dashboard.BINDINGS)
        await pilot.press("/")
        await pilot.press("z", "z")
        await pilot.pause()
        assert table.row_count == 0
        await pilot.press("backspace", "backspace")
        await pilot.pause()
        assert table.row_count == 1
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(pilot.app.screen, RepoDetail)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        summary = str(pilot.app.query_one("#summary", Static).content)
        assert "config.yml" in summary
        assert "control + q" in summary
        await pilot.press("r")
        await pilot.pause()
        summary = str(pilot.app.query_one("#summary", Static).content)
        assert "Offline mode does not synchronise." in summary


async def test_dashboard_profiles_sync_and_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    add_repository(load_config(config), "nginx", group="web")
    service = Service(config, offline=False, token=None)
    started = asyncio.Event()

    async def hang(*_args: object, **_kwargs: object) -> SyncReport:
        started.set()
        await asyncio.Event().wait()
        return SyncReport()

    monkeypatch.setattr(service, "sync", hang)
    dashboard = Dashboard(service)
    async with dashboard.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        summary = str(pilot.app.query_one("#summary", Static).content)
        assert "Profiles" in summary
        await pilot.press("escape")
        await pilot.pause()
        assert pilot.app.query_one(DataTable).row_count == 1
        await pilot.press("r")
        await started.wait()
        await pilot.press("r")
        await pilot.press("escape")
        await pilot.pause()
        summary = str(pilot.app.query_one("#summary", Static).content)
        assert "cancelled" in summary

    async def finished(*_args: object, **_kwargs: object) -> SyncReport:
        return SyncReport(succeeded=1, failed=0)

    monkeypatch.setattr(service, "sync", finished)
    dashboard = Dashboard(service)
    async with dashboard.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        summary = str(pilot.app.query_one("#summary", Static).content)
        assert "Successful 1" in summary

    async def broken(*_args: object, **_kwargs: object) -> SyncReport:
        raise ConfigError("Synchronisation refused.")

    monkeypatch.setattr(service, "sync", broken)
    dashboard = Dashboard(service)
    async with dashboard.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        summary = str(pilot.app.query_one("#summary", Static).content)
        assert "Synchronisation refused." in summary


async def test_dashboard_reports_a_missing_configuration(tmp_path: Path) -> None:
    service = Service(tmp_path / "missing.yml", offline=True, token=None)
    dashboard = Dashboard(service)
    async with dashboard.run_test() as pilot:
        await pilot.pause()
        summary = str(pilot.app.query_one("#summary", Static).content)
        assert "not found" in summary
        await pilot.press("r", "p", "c")
        await pilot.pause()
        assert "not found" in str(pilot.app.query_one("#summary", Static).content)


async def test_dashboard_reports_a_missing_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    add_repository(load_config(config), "nginx")
    service = Service(config, offline=True, token=None)

    async def missing(_repo: str) -> dict[str, object]:
        raise NotFound("Repository not found: library/nginx.")

    monkeypatch.setattr(service, "show", missing)
    dashboard = Dashboard(service)
    async with dashboard.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#search", Input).value = "library"
        await pilot.pause()
        assert pilot.app.query_one(DataTable).row_count == 1
        pilot.app.action_cancel()
        await pilot.pause()
        assert pilot.app.query_one("#search", Input).value == ""
        pilot.app.query_one(DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, RepoDetail)


def _headers(table: DataTable) -> list[str]:
    labels: list[str] = []
    for column in table.columns.values():
        label = column.label
        labels.append(label.plain if hasattr(label, "plain") else str(label))
    return labels


def test_dashboard_paints_status_and_detail_fields() -> None:
    row = {
        "repository": "library/nginx",
        "pulls": 1200,
        "stars": 4,
        "status": "active",
        "stale": "Stale",
        "error": "timed out",
        "enabled": True,
    }
    name = _painted("repository", row)
    pulls = _painted("pulls", row)
    status = _painted("status", row)
    stale = _painted("stale", row)
    assert "red" in repr(name)
    assert getattr(pulls, "plain", pulls) == "1,200"
    assert "green" in repr(status)
    assert "yellow" in repr(stale)
    assert "yellow" in repr(_painted("status", {"status": "unavailable"}))
    assert "green" in repr(_painted("stale", {"stale": "Current"}))
    assert "dim" in repr(_painted("stale", {"stale": "Skipped"}))
    assert _painted("group", {"group": None}) == ""
    enabled = _painted("enabled", {"enabled": False})
    assert "No" in repr(enabled)
    assert "dim" in repr(enabled)
    pairs = _detail_pairs(
        {
            "repository": "library/nginx",
            "url": "https://hub.docker.com/_/nginx",
            "group": "",
            "profile": "standard",
            "labels": ["web"],
            "enabled": True,
            "description": "Proxy",
            "visibility": "public",
            "status": "active",
            "pulls": 1200,
            "stars": 4,
            "updated": "2026-01-01",
            "growth": {"previous": "+1", "24h": "", "7d": "+2", "30d": "", "90d": ""},
            "stale": "Current",
            "tags": ["latest"],
            "error": "",
        }
    )
    fields = [field for field, _value in pairs]
    assert "Growth previous" in fields
    assert "Growth 24h" not in fields
    assert "Labels" in fields
    assert "Error" not in fields


def test_run_dashboard_starts_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[Service] = []

    def fake_run(self: Dashboard) -> None:
        started.append(self.service)

    monkeypatch.setattr(Dashboard, "run", fake_run)
    service = Service(None, offline=True, token=None)
    run_dashboard(service)
    assert started == [service]


def test_log_records_do_not_keep_the_token() -> None:
    auth.active_token = "super-secret"
    configure_logging("WARNING", verbose=True, debug=False)
    record = logging.LogRecord(
        "lupaxa.dockerhub_intel",
        logging.INFO,
        __file__,
        1,
        "token %s",
        ("super-secret",),
        None,
    )
    logging.getLogger().filter(record)
    rendered = record.getMessage()
    assert "super-secret" not in rendered
    assert "***" in rendered
    auth.active_token = None
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


async def test_offline_service_refuses_synchronisation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DOCKERHUB_INTEL_DATA_DIR", str(tmp_path / "data"))
    write_default_files(tmp_path)
    service = Service(tmp_path / "config.yml", offline=True, token=None)
    await service.open(create=False)
    try:
        with pytest.raises(ConfigError, match="Offline"):
            await service.sync([], force=False, concurrency=1)
    finally:
        await service.close()


def _repository(pulls: int) -> dict[str, object]:
    return {
        "description": "web",
        "is_private": False,
        "status_description": "active",
        "date_registered": "2014-01-01T00:00:00Z",
        "last_updated": "2020-01-01T00:00:00Z",
        "pull_count": pulls,
        "star_count": 2,
    }


def _tags(digest: str) -> dict[str, object]:
    return {
        "count": 1,
        "next": None,
        "results": [
            {
                "name": "latest",
                "last_updated": "2020-01-01T00:00:00Z",
                "full_size": 100,
                "digest": digest,
                "media_type": "application/vnd.oci.image.index.v1+json",
                "images": [
                    {
                        "os": "linux",
                        "architecture": "amd64",
                        "digest": "sha256:platform",
                        "size": 10,
                    },
                    {"os": "unknown", "architecture": "unknown"},
                ],
            }
        ],
    }
