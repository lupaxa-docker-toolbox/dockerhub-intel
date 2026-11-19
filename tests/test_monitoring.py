"""Configuration, history, cache, and Docker Hub client behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import httpx
import pytest

from lupaxa.dockerhub_intel.checks.stale import stale_state
from lupaxa.dockerhub_intel.config.defaults import write_default_files
from lupaxa.dockerhub_intel.config.loader import load_config
from lupaxa.dockerhub_intel.config.models import ProfileSpec
from lupaxa.dockerhub_intel.config.profiles import resolve_profile
from lupaxa.dockerhub_intel.dockerhub.client import HubClient
from lupaxa.dockerhub_intel.dockerhub.names import canonical_repository, web_url
from lupaxa.dockerhub_intel.errors import AuthError, ConfigError
from lupaxa.dockerhub_intel.history.changes import compare_tags
from lupaxa.dockerhub_intel.history.growth import Observation, growth_for
from lupaxa.dockerhub_intel.reports.render import render
from lupaxa.dockerhub_intel.storage.database import Database

NOW = datetime(2026, 11, 19, tzinfo=UTC)


def test_official_name_and_web_url() -> None:
    assert canonical_repository("Nginx") == "library/nginx"
    assert web_url("library/nginx") == "https://hub.docker.com/_/nginx"
    assert web_url("bitnami/nginx") == "https://hub.docker.com/r/bitnami/nginx"
    with pytest.raises(ValueError, match="Invalid repository name"):
        canonical_repository("not a name")


def test_explicit_false_overrides_the_parent() -> None:
    profiles = {
        "base": ProfileSpec(metadata=True, stats=True, tags=True),
        "quiet": ProfileSpec(extends="base", tags=False),
    }
    resolved = resolve_profile(profiles, "quiet", {"stats": False})
    assert resolved.enabled["metadata"] is True
    assert resolved.enabled["tags"] is False
    assert resolved.enabled["stats"] is False
    assert resolved.sources["stats"] == "repository"


def test_profile_cycles_are_rejected() -> None:
    profiles = {
        "a": ProfileSpec(extends="b"),
        "b": ProfileSpec(extends="a"),
    }
    with pytest.raises(ConfigError, match="Circular profile inheritance"):
        resolve_profile(profiles, "a")


def test_growth_uses_a_real_baseline_and_keeps_a_decrease() -> None:
    observations = [
        Observation(NOW - timedelta(days=8), 100),
        Observation(NOW - timedelta(days=1), 80, anomaly=True),
        Observation(NOW, 90),
    ]
    week = growth_for(observations, days=7, label="7d")
    assert week.change == -10
    assert week.text == "-10"
    assert growth_for([], label="previous").text == "N/A"


def test_first_complete_listing_has_no_events() -> None:
    current = {"latest": "sha256:one"}
    assert compare_tags(None, current, complete=True, had_complete_baseline=False) == []


def test_partial_listing_does_not_report_removals() -> None:
    previous = {"latest": "sha256:one", "old": "sha256:old"}
    current = {"latest": "sha256:two"}
    events = compare_tags(previous, current, complete=False, had_complete_baseline=True)
    assert [event.change for event in events] == ["updated"]
    finished = compare_tags(
        previous, current, complete=True, had_complete_baseline=True
    )
    assert {event.change for event in finished} == {"updated", "removed"}


def test_stale_states() -> None:
    assert stale_state(None, enabled=False, threshold_days=180, now=NOW) == "Skipped"
    assert stale_state(None, enabled=True, threshold_days=180, now=NOW) == "Unknown"
    old = NOW - timedelta(days=181)
    assert stale_state(old, enabled=True, threshold_days=180, now=NOW) == "Stale"
    assert stale_state(NOW, enabled=True, threshold_days=180, now=NOW) == "Current"


def test_yaml_and_json_configuration_match(tmp_path: Path) -> None:
    write_default_files(tmp_path)
    assert (tmp_path.stat().st_mode & 0o777) == 0o700
    yaml_loaded = load_config(tmp_path / "config.yml")
    document = {
        "version": 1,
        "files": {"profiles": "profiles.yml", "repositories": "repositories.yml"},
    }
    (tmp_path / "config.json").write_text(json.dumps(document), encoding="utf-8")
    json_loaded = load_config(tmp_path / "config.json")
    assert json_loaded.app.defaults.profile == yaml_loaded.app.defaults.profile
    assert "standard" in json_loaded.profiles.profiles


def test_duplicate_repository_is_rejected(tmp_path: Path) -> None:
    write_default_files(tmp_path)
    (tmp_path / "repositories.yml").write_text(
        "repositories:\n  - nginx\n  - library/nginx\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="Duplicate repository"):
        load_config(tmp_path / "config.yml")


def test_unknown_configuration_key_is_rejected(tmp_path: Path) -> None:
    write_default_files(tmp_path)
    path = tmp_path / "config.yml"
    path.write_text(
        path.read_text(encoding="utf-8") + "extra: true\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="Invalid configuration"):
        load_config(path)


def test_reports_cover_each_format() -> None:
    rows = [{"repository": "library/nginx", "pulls": 10}]
    assert '"library/nginx"' in render(rows, "json", title="Repos")
    assert "repository,pulls" in render(rows, "csv", title="Repos")
    assert "| repository |" in render(rows, "markdown", title="Repos")
    table = render(rows, "text", title="Repositories")
    assert "library/nginx" in table
    assert "Repositories" in table
    assert "┃" in table
    assert render([], "text", title="Repos") == "No rows.\n"


async def test_pull_decrease_is_kept_as_an_anomaly(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    await database.open()
    try:
        await database.save_repository(
            repo="library/nginx",
            description="web",
            visibility="public",
            status="active",
            created_at=None,
            updated_at=None,
            pull_count=10,
            star_count=1,
            web_url="https://hub.docker.com/_/nginx",
            error=None,
        )
        assert await database.add_snapshot("library/nginx", 10, 1) is False
        assert await database.add_snapshot("library/nginx", 4, 1) is True
        assert (
            await database.fresh("library/nginx", "stats", 3600, enabled=True) is False
        )
        await database.mark_fresh("library/nginx", "stats", complete=True)
        assert (
            await database.fresh("library/nginx", "stats", 3600, enabled=True) is True
        )
        await database.apply_retention(0)
        counts = await database.counts()
    finally:
        await database.close()
    assert counts["repository_snapshots"] == 2


async def test_missing_repository_is_not_an_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "Authorization" not in request.headers
        return httpx.Response(404, json={"message": "object not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token=None,
            timeout=5,
            retries=0,
            page_size=10,
            client=http,
        )
        assert await client.repository("library/missing") is None


async def test_personal_access_token_uses_the_jwt_scheme() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"description": "web", "pull_count": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token="dckr_pat_example",
            timeout=5,
            retries=0,
            page_size=10,
            client=http,
        )
        info = await client.repository("library/nginx")
    assert info is not None
    assert seen == ["JWT dckr_pat_example"]


async def test_issued_jwt_uses_the_bearer_scheme() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"description": "web", "pull_count": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token="eyJhbGciOiJIUzI1NiJ9.payload.signature",
            timeout=5,
            retries=0,
            page_size=10,
            client=http,
        )
        await client.repository("library/nginx")
    assert seen == ["Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"]


async def test_rejected_token_is_an_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "incorrect"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token="secret-token",
            timeout=5,
            retries=0,
            page_size=10,
            client=http,
        )
        with pytest.raises(AuthError):
            await client.repository("library/nginx")


async def test_write_requests_are_rejected() -> None:
    async with httpx.AsyncClient() as http:
        client = HubClient(
            api_url="https://hub.docker.com/v2",
            registry_url="https://registry-1.docker.io",
            token=None,
            timeout=5,
            retries=0,
            page_size=10,
            client=http,
        )
        with pytest.raises(RuntimeError, match="Only read requests"):
            await client._json(
                "POST", "https://hub.docker.com/v2/repositories/library/nginx/"
            )
