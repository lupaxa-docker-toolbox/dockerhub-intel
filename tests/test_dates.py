"""Configured date formatting for the Updated column."""

from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
import pytest

from lupaxa.dockerhub_intel.app import Service
from lupaxa.dockerhub_intel.config.defaults import write_default_files
from lupaxa.dockerhub_intel.config.models import AppConfig
from lupaxa.dockerhub_intel.dates import (
    format_date,
    format_timestamp,
    normalize_instant,
)
from lupaxa.dockerhub_intel.errors import ConfigError
from lupaxa.dockerhub_intel.storage.database import StoredTag


def test_default_date_format_includes_the_clock() -> None:
    config = AppConfig.model_validate({"version": 1})
    assert config.display.date_format == "DD/MM/YYYY HH:mm UTC"
    assert (
        format_date("2026-11-18T23:06:41Z", config.display.date_format)
        == "18/11/2026 23:06 UTC"
    )


def test_date_format_accepts_another_token_order() -> None:
    config = AppConfig.model_validate(
        {"version": 1, "display": {"date_format": "yyyy-mm-dd hh:mm"}},
    )
    assert config.display.date_format == "YYYY-MM-DD HH:mm"
    assert format_date("2026-11-18T23:06:41+01:00", config.display.date_format) == (
        "2026-11-18 22:06"
    )


def test_date_format_can_include_seconds() -> None:
    config = AppConfig.model_validate(
        {"version": 1, "display": {"date_format": "DD/MM/YYYY HH:MM:SS"}},
    )
    assert config.display.date_format == "DD/MM/YYYY HH:mm:ss"
    assert format_date("2026-11-18T23:06:41Z", config.display.date_format) == (
        "18/11/2026 23:06:41"
    )


def test_date_format_can_omit_the_clock() -> None:
    config = AppConfig.model_validate(
        {"version": 1, "display": {"date_format": "DD/MM/YYYY"}},
    )
    assert config.display.date_format == "DD/MM/YYYY"
    assert format_date("2026-11-18T23:06:41Z", config.display.date_format) == (
        "18/11/2026"
    )


def test_date_format_rejects_an_incomplete_pattern() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"version": 1, "display": {"date_format": "DD/MM/YY"}},
        )
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"version": 1, "display": {"date_format": "DD/MM/YYYY HH"}},
        )


def test_a_date_without_a_clock_leaves_the_time_blank() -> None:
    assert format_date("2026-11-18", "DD/MM/YYYY HH:mm UTC") == "18/11/2026 --:-- UTC"


def test_timestamp_uses_the_date_pattern() -> None:
    moment = datetime(2026, 11, 18, 23, 6, tzinfo=UTC)
    assert format_timestamp(moment.timestamp(), "DD/MM/YYYY HH:mm UTC") == (
        "18/11/2026 23:06 UTC"
    )


def test_normalize_instant_keeps_the_clock_in_utc() -> None:
    assert normalize_instant("2026-11-18T23:06:41+01:00") == "2026-11-18T22:06:41Z"
    assert normalize_instant("2026-11-18") == "2026-11-18"
    assert normalize_instant("later") == ""


def test_default_files_include_the_date_format(tmp_path: Path) -> None:
    write_default_files(tmp_path)
    text = (tmp_path / "config.yml").read_text(encoding="utf-8")
    assert "date_format: DD/MM/YYYY HH:mm UTC" in text


async def test_invalid_date_format_is_rejected(tmp_path: Path) -> None:
    write_default_files(tmp_path)
    path = tmp_path / "config.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "date_format: DD/MM/YYYY HH:mm UTC",
            "date_format: DD/MM/YY",
        ),
        encoding="utf-8",
    )
    service = Service(path, offline=True, token=None)
    with pytest.raises(ConfigError, match="date_format"):
        await service.open(create=False)


async def test_updated_follows_the_configured_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DOCKERHUB_INTEL_DATA_DIR", str(tmp_path / "data"))
    write_default_files(tmp_path)
    path = tmp_path / "config.yml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "date_format: DD/MM/YYYY HH:mm UTC",
            "date_format: DD/MM/YYYY",
        ),
        encoding="utf-8",
    )
    (tmp_path / "repositories.yml").write_text(
        "repositories:\n  - library/nginx\n  - library/redis\n",
        encoding="utf-8",
    )
    service = Service(path, offline=True, token=None)
    await service.open(create=False)
    try:
        await _save(service, "library/nginx", "2020-01-15T12:00:00Z")
        await _save(service, "library/redis", "2020-02-01T08:30:00Z")
        listed = await service.repo_rows(service.repositories())
        rows = {row["repository"]: row for row in listed}
        assert rows["library/nginx"]["updated"] == "15/01/2020"
        assert rows["library/redis"]["updated"] == "01/02/2020"
        shown = await service.show("library/nginx")
        assert shown["updated"] == "15/01/2020"
        stats = await service.stats_rows(
            service.repositories(),
            period=None,
            sort="least-updated",
            top=None,
        )
        assert [row["repository"] for row in stats] == [
            "library/nginx",
            "library/redis",
        ]
        assert stats[0]["updated"] == "15/01/2020"
        await service._db().replace_tags(
            "library/nginx",
            [
                StoredTag(
                    name="latest",
                    updated_at="2020-01-15T12:00:00.5Z",
                    full_size=10,
                    digest="sha256:abc",
                    media_type=None,
                    platforms=(),
                )
            ],
            complete=True,
        )
        tags = await service.tag_rows(service.repositories(repo="library/nginx"))
        assert tags[0]["updated"] == "15/01/2020"
    finally:
        await service.close()


async def _save(service: Service, repo: str, updated: str) -> None:
    await service._db().save_repository(
        repo=repo,
        description="web",
        visibility="public",
        status="active",
        created_at=None,
        updated_at=updated,
        pull_count=1,
        star_count=1,
        web_url=f"https://hub.docker.com/r/{repo}",
        error=None,
    )
