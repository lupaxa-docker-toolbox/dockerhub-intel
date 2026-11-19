"""Edge cases for configuration, the client, synchronisation, and commands."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import io
import json
from pathlib import Path
import runpy
import sys

import httpx
import pytest
import respx
from typer.testing import CliRunner

from lupaxa.dockerhub_intel.checks.stale import stale_state
from lupaxa.dockerhub_intel.cli import app
from lupaxa.dockerhub_intel.config.defaults import write_default_files
from lupaxa.dockerhub_intel.config.editor import (
    add_repository,
    set_group,
    set_profile,
)
from lupaxa.dockerhub_intel.config.loader import load_config
from lupaxa.dockerhub_intel.config.models import AppConfig, RepositoryFile
from lupaxa.dockerhub_intel.config.paths import config_directory, default_config_file
from lupaxa.dockerhub_intel.constants import (
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_SUCCESS,
    EXIT_SYNC,
)
from lupaxa.dockerhub_intel.dockerhub.client import HubClient
from lupaxa.dockerhub_intel.dockerhub.names import canonical_repository
from lupaxa.dockerhub_intel.dockerhub.rate_limit import RateLimit
from lupaxa.dockerhub_intel.errors import ConfigError, HubError
from lupaxa.dockerhub_intel.history.changes import compare_tags
from lupaxa.dockerhub_intel.history.growth import Observation, growth_for, parse_period
from lupaxa.dockerhub_intel.reports.progress import print_sync, status_line, syncing
from lupaxa.dockerhub_intel.reports.render import render
from lupaxa.dockerhub_intel.storage.database import Database
from lupaxa.dockerhub_intel.sync.engine import SyncReport, synchronise

runner = CliRunner()
NOW = "2026-11-19T00:00:00+00:00"


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config = tmp_path / "config"
    monkeypatch.setenv("DOCKERHUB_INTEL_CONFIG_DIR", str(config))
    monkeypatch.setenv("DOCKERHUB_INTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DOCKERHUB_INTEL_CONFIG", raising=False)
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
    monkeypatch.delenv("DOCKERHUB_INTEL_CONCURRENCY", raising=False)
    monkeypatch.delenv("DOCKERHUB_INTEL_LOG_LEVEL", raising=False)
    write_default_files(config)
    return config / "config.yml"


def test_names_and_periods_reject_bad_values() -> None:
    with pytest.raises(ValueError, match="blank"):
        canonical_repository("  ")
    with pytest.raises(ValueError, match="Invalid repository name"):
        canonical_repository("library/nginx/extra")
    assert parse_period(" 24H ") == 1
    with pytest.raises(ValueError, match="period must be"):
        parse_period("1y")
    earlier = Observation(datetime(2026, 11, 1, tzinfo=UTC), 10)
    later = Observation(datetime(2026, 11, 19, tzinfo=UTC), 10)
    assert growth_for([later, earlier], days=7, label="7d").change == 0
    assert growth_for([later], days=7).text == "N/A"


def test_configuration_rejects_bad_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yml")
    config.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(config)
    config.write_text("version: [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Cannot read"):
        load_config(config)
    config.unlink()
    write_default_files(config.parent)
    monkeypatch.setenv("DOCKERHUB_INTEL_CONCURRENCY", "nope")
    with pytest.raises(ConfigError, match="DOCKERHUB_INTEL_CONCURRENCY"):
        load_config(config)
    monkeypatch.setenv("DOCKERHUB_INTEL_CONCURRENCY", "4")
    monkeypatch.setenv("DOCKERHUB_INTEL_LOG_LEVEL", "debug")
    loaded = load_config(config)
    assert loaded.app.sync.concurrency == 4
    assert loaded.app.logging.level == "DEBUG"
    assert loaded.select(group="missing", label="none", enabled_only=True) == []


def test_model_rules() -> None:
    with pytest.raises(ValueError, match="https"):
        AppConfig.model_validate(
            {"version": 1, "dockerhub": {"api_url": "http://example"}}
        )
    with pytest.raises(ValueError, match="version must be 1"):
        AppConfig.model_validate({"version": 2})
    with pytest.raises(ValueError, match="Duplicate"):
        RepositoryFile.model_validate({"repositories": ["nginx", "library/nginx"]})
    with pytest.raises(ValueError, match="Unknown check"):
        RepositoryFile.model_validate(
            {"repositories": [{"repo": "nginx", "checks": {"nope": True}}]}
        )


def test_editor_updates_json_and_rejects_unknown_profiles(tmp_path: Path) -> None:
    write_default_files(tmp_path)
    config = tmp_path / "config.yml"
    text = config.read_text(encoding="utf-8").replace(
        "repositories: repositories.yml",
        "repositories: repositories.json",
    )
    config.write_text(text, encoding="utf-8")
    (tmp_path / "repositories.json").write_text(
        '{"repositories": []}\n',
        encoding="utf-8",
    )
    loaded = load_config(config)
    add_repository(loaded, "nginx")
    loaded = load_config(config)
    set_group(loaded, "nginx", "web")
    with pytest.raises(ConfigError, match="Unknown profile"):
        set_profile(loaded, "nginx", "missing")
    saved = json.loads((tmp_path / "repositories.json").read_text(encoding="utf-8"))
    assert saved["repositories"][0]["group"] == "web"


def test_directory_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DOCKERHUB_INTEL_CONFIG_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert config_directory().name
    monkeypatch.setattr(sys, "platform", "darwin")
    assert config_directory().name
    empty = tmp_path / "empty"
    empty.mkdir()
    assert default_config_file(empty).name == "config.yml"
    (empty / "config.yaml").write_text("version: 1\n", encoding="utf-8")
    assert default_config_file(empty).name == "config.yaml"


async def test_client_retries_pages_and_bad_payloads() -> None:
    attempts = {"repository": 0}

    def repository_handler(request: httpx.Request) -> httpx.Response:
        attempts["repository"] += 1
        if attempts["repository"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            json={"description": "web", "is_private": False, "pull_count": 4},
        )

    transport = httpx.MockTransport(repository_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token=None,
            timeout=5,
            retries=1,
            page_size=1,
            client=http,
        )
        info = await client.repository("library/nginx")
    assert info is not None
    assert info.pull_count == 4

    def tags_handler(request: httpx.Request) -> httpx.Response:
        if "page=2" in str(request.url):
            return httpx.Response(200, text="not-json")
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "latest",
                        "digest": "sha256:one",
                        "images": [
                            {"os": "linux", "architecture": "arm64", "variant": "v8"}
                        ],
                    },
                    "skip",
                ],
                "next": "https://hub.docker.com/v2/repositories/library/nginx/tags?page=2",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(tags_handler)) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token=None,
            timeout=5,
            retries=0,
            page_size=1,
            client=http,
        )
        page = await client.tags("library/nginx")
    assert page.complete is False
    assert page.tags[0].platforms == ("linux/arm64/v8",)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    ) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token=None,
            timeout=5,
            retries=0,
            page_size=1,
            client=http,
        )
        with pytest.raises(HubError):
            await client.repository("library/nginx")


async def test_rate_limit_waits_for_a_shared_pause() -> None:
    limiter = RateLimit()
    await limiter.pause(0)
    await limiter.wait()
    assert limiter.backoff(0, 1.5) == 1.5
    assert limiter.backoff(1, None) > 0
    seen: list[float] = []
    limiter.set_listener(seen.append)
    assert limiter.observe(429, {"Retry-After": "0"}) == 0
    await limiter.wait()
    assert limiter.pauses == 0
    exhausted = limiter.observe(
        200,
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0.05"},
    )
    assert exhausted == 0.05
    await limiter.wait()
    assert limiter.pauses == 1
    assert seen[-1] == 0
    registry = limiter.observe(
        200,
        {"RateLimit-Remaining": "0;w=3600", "RateLimit-Reset": "0"},
    )
    assert registry == 0


def test_sync_progress_names_the_repository_and_a_rate_limit_wait() -> None:
    report = SyncReport(
        repositories=2,
        succeeded=1,
        latest="library/nginx",
        requests=3,
        cache_hits=1,
        rate_wait=65,
        rate_pauses=2,
    )
    text = status_line(report)
    assert "library/nginx" in text
    assert "1/2" in text
    assert "requests resume in 1m 05s" in text
    with syncing() as update:
        update(report)
        update(report)
    buffer = io.StringIO()
    print_sync(
        SyncReport(repositories=1, succeeded=1, requests=2, elapsed=1.25),
        buffer,
    )
    summary = buffer.getvalue()
    assert "Synchronisation completed" in summary
    assert "Successful: 1" in summary
    assert "API requests: 2" in summary
    assert "Elapsed: 1.2s" in summary
    assert "┃" not in summary


async def test_sync_failure_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    profiles = config.parent / "profiles.yml"
    profiles.write_text(
        """
profiles:
  base:
    metadata: true
    stats: true
    tags: true
    digests: true
    architectures: true
    image_details: true
    stale: true
  standard:
    extends: base
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (config.parent / "repositories.yml").write_text(
        "repositories:\n  - library/nginx\n  - library/missing\n",
        encoding="utf-8",
    )
    loaded = load_config(config)
    database = Database(tmp_path / "data" / "cache.sqlite")
    await database.open()
    with respx.mock(assert_all_called=False) as router:
        router.get("https://hub.docker.com/v2/repositories/library/nginx/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "description": "web",
                    "is_private": False,
                    "status_description": "active",
                    "last_updated": "2000-01-01T00:00:00Z",
                    "pull_count": 3,
                    "star_count": 1,
                },
            )
        )
        router.get(url__regex=r".*/library/nginx/tags.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "latest",
                            "digest": "sha256:one",
                            "full_size": 10,
                            "images": [{"os": "linux", "architecture": "amd64"}],
                        }
                    ],
                    "next": None,
                },
            )
        )
        router.get("https://hub.docker.com/v2/repositories/library/missing/").mock(
            return_value=httpx.Response(404, json={"message": "object not found"})
        )
        router.get(url__regex=r".*/library/missing/tags.*").mock(
            return_value=httpx.Response(404, json={"message": "object not found"})
        )
        report = await synchronise(
            loaded,
            database,
            loaded.repositories.repositories,
            token=None,
            force=True,
        )
        cached = await synchronise(
            loaded,
            database,
            loaded.select(repo="library/nginx"),
            token=None,
            force=False,
        )
    assert report.succeeded == 2
    assert cached.succeeded == 1
    empty = await synchronise(loaded, database, [], token=None, force=True)
    assert empty.repositories == 0
    counts = await database.counts()
    await database.close()
    assert counts["tags"] == 1


async def test_authentication_failure_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    (config.parent / "repositories.yml").write_text(
        "repositories:\n  - library/nginx\n",
        encoding="utf-8",
    )
    loaded = load_config(config)
    database = Database(tmp_path / "data" / "cache.sqlite")
    await database.open()
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://hub.docker.com/.*").mock(
            return_value=httpx.Response(401, json={"message": "no"})
        )
        report = await synchronise(
            loaded,
            database,
            loaded.repositories.repositories,
            token="secret",
            force=True,
        )
    await database.close()
    assert report.auth_failed == 1


async def test_retention_keeps_the_newest_snapshot(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    await database.open()
    await database.add_snapshot("library/nginx", 1, 1)
    await database.add_snapshot("library/nginx", 2, 1)
    connection = database._connection
    assert connection is not None
    await connection.execute(
        """
        UPDATE repository_snapshots
        SET observed_at = '2000-01-01T00:00:00+00:00'
        WHERE id = (SELECT MIN(id) FROM repository_snapshots)
        """
    )
    await connection.commit()
    await database.apply_retention(1)
    remaining = await database.snapshots("library/nginx")
    await database.vacuum()
    await database.close()
    assert len(remaining) == 1
    assert remaining[0].pull_count == 2


def test_command_formats_sync_and_failure_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    output = tmp_path / "report.md"
    listed = runner.invoke(
        app,
        ["--config", str(config), "repo", "list", "--output", str(output)],
    )
    assert listed.exit_code == EXIT_SUCCESS
    assert output.read_text(encoding="utf-8").startswith("No rows.")
    invalid = runner.invoke(
        app,
        ["--config", str(config), "repo", "list", "--format", "xml"],
    )
    assert invalid.exit_code == EXIT_CONFIG
    with respx.mock(assert_all_called=False) as router:
        router.get("https://hub.docker.com/v2/repositories/library/nginx/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "pull_count": 8,
                    "star_count": 1,
                    "is_private": False,
                    "last_updated": "2000-01-01T00:00:00Z",
                },
            )
        )
        router.get(url__regex=r".*/tags.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [{"name": "latest", "digest": "sha256:abc"}],
                    "next": None,
                },
            )
        )
        runner.invoke(app, ["--config", str(config), "repo", "add", "nginx"])
        synced = runner.invoke(
            app,
            [
                "--config",
                str(config),
                "sync",
                "--repo",
                "nginx",
                "--format",
                "json",
                "--stats",
            ],
        )
        doctor = runner.invoke(app, ["--config", str(config), "doctor", "--online"])
    assert synced.exit_code == EXIT_SUCCESS
    assert "succeeded" in synced.stdout
    assert doctor.exit_code == EXIT_SUCCESS
    changed = runner.invoke(
        app,
        ["--config", str(config), "changes", "--since", "24h", "--type", "tag-added"],
    )
    assert changed.exit_code == EXIT_SUCCESS
    bad_period = runner.invoke(
        app,
        ["--config", str(config), "stats", "--period", "1y"],
    )
    assert bad_period.exit_code == EXIT_CONFIG
    profiles = (config.parent / "profiles.yml").read_text(encoding="utf-8")
    (config.parent / "profiles.yml").write_text(
        profiles.replace("stale: false", "stale: true", 1),
        encoding="utf-8",
    )
    failed = runner.invoke(
        app,
        ["--config", str(config), "repo", "list", "--fail-on", "stale"],
    )
    assert failed.exit_code == 6
    unknown = runner.invoke(
        app,
        ["--config", str(config), "repo", "list", "--fail-on", "missing"],
    )
    assert unknown.exit_code == EXIT_CONFIG


def test_module_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["dockerhub-intel", "--version"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("lupaxa.dockerhub_intel.__main__", run_name="__main__")
    assert exc.value.code == EXIT_SUCCESS


def test_sync_command_reports_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    runner.invoke(app, ["--config", str(config), "repo", "add", "nginx"])
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://hub.docker.com/.*").mock(
            return_value=httpx.Response(401)
        )
        result = runner.invoke(
            app,
            ["--config", str(config), "--token", "secret", "sync"],
        )
    assert result.exit_code == EXIT_AUTH


def test_sync_command_reports_an_incomplete_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace("retries: 3", "retries: 0"),
        encoding="utf-8",
    )
    runner.invoke(app, ["--config", str(config), "repo", "add", "nginx"])
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://hub.docker.com/.*").mock(
            return_value=httpx.Response(500)
        )
        result = runner.invoke(
            app,
            ["--config", str(config), "sync", "--quiet", "--concurrency", "1"],
        )
    assert result.exit_code == EXIT_SYNC


async def test_cancelled_sync_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _isolate(monkeypatch, tmp_path)
    (config.parent / "repositories.yml").write_text(
        "repositories:\n  - library/nginx\n",
        encoding="utf-8",
    )
    loaded = load_config(config)
    database = Database(tmp_path / "data" / "cache.sqlite")
    await database.open()

    async def _hang(self: HubClient, repo: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(HubClient, "repository", _hang)
    task = asyncio.create_task(
        synchronise(
            loaded,
            database,
            loaded.repositories.repositories,
            token=None,
            force=True,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await database.close()


def test_smaller_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    moment = datetime(2026, 11, 19, tzinfo=UTC)
    previous = {"latest": "sha256:one"}
    added = compare_tags(
        previous,
        {"latest": "sha256:one", "new": "sha256:two"},
        complete=True,
        had_complete_baseline=True,
    )
    assert added[0].change == "new"
    close = [
        Observation(moment, 5),
        Observation(datetime(2026, 11, 18, tzinfo=UTC), 4),
    ]
    assert growth_for(close, days=7).text == "N/A"
    naive = datetime(2020, 1, 1)
    assert stale_state(naive, enabled=True, threshold_days=1, now=moment) == "Stale"
    assert "None" not in render([{"name": None}], "text", title="Empty")

    config = _isolate(monkeypatch, tmp_path)
    loaded = load_config(config)
    add_repository(loaded, "nginx", profile="standard", labels=["web"])
    loaded = load_config(config)
    with pytest.raises(ConfigError, match="Duplicate"):
        add_repository(loaded, "library/nginx")
    with pytest.raises(ConfigError, match="Unknown profile"):
        add_repository(loaded, "redis", profile="missing")
    chosen = loaded.select(repo="library/nginx", profile="standard", label="web")
    assert chosen[0].repo == "library/nginx"
    assert loaded.select(enabled_only=True, group="ungrouped")

    monkeypatch.setenv("DOCKERHUB_INTEL_CONFIG", str(config))
    assert load_config(None).path == config
    monkeypatch.setenv("DOCKERHUB_INTEL_LOG_LEVEL", "NOPE")
    with pytest.raises(ConfigError, match="logging.level"):
        load_config(config)


async def test_network_error_then_success_and_forbidden() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"description": "web", "pull_count": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token=None,
            timeout=5,
            retries=1,
            page_size=1,
            client=http,
        )
        info = await client.repository("library/nginx")
    assert info is not None

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token="secret",
            timeout=5,
            retries=0,
            page_size=1,
            client=http,
        )
        with pytest.raises(HubError, match="Permission"):
            await client.repository("library/nginx")

    limiter = RateLimit()
    await limiter.pause(0.01)
    await limiter.wait()
