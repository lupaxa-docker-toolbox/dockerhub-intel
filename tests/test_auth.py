"""Optional Docker Hub token resolution."""

from __future__ import annotations

from pathlib import Path
import re

import pytest
from typer.testing import CliRunner

from lupaxa.dockerhub_intel import auth
from lupaxa.dockerhub_intel.auth import TOKEN_ENV, resolve_token
from lupaxa.dockerhub_intel.cli import app
from lupaxa.dockerhub_intel.config.defaults import write_default_files
from lupaxa.dockerhub_intel.constants import EXIT_SUCCESS

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def test_missing_token_is_anonymous() -> None:
    assert resolve_token(None, environ={}) is None


def test_environment_token_is_used() -> None:
    assert resolve_token(None, environ={TOKEN_ENV: "from-env"}) == "from-env"


def test_command_line_token_wins() -> None:
    resolved = resolve_token("from-cli", environ={TOKEN_ENV: "from-env"})
    assert resolved == "from-cli"


def test_blank_command_line_falls_through_to_environment() -> None:
    assert resolve_token("  ", environ={TOKEN_ENV: "from-env"}) == "from-env"


def test_blank_values_are_anonymous() -> None:
    assert resolve_token("  ", environ={TOKEN_ENV: "  "}) is None


def test_callback_keeps_the_token_without_printing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("DOCKERHUB_INTEL_CONFIG_DIR", str(config))
    monkeypatch.setenv("DOCKERHUB_INTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DOCKERHUB_INTEL_CONFIG", raising=False)
    write_default_files(config)
    result = runner.invoke(
        app,
        ["--token", "from-cli", "config", "validate"],
    )
    assert result.exit_code == EXIT_SUCCESS
    assert auth.active_token == "from-cli"
    assert "from-cli" not in _plain(result.stdout)
    assert "from-cli" not in _plain(result.stderr)


def test_help_names_the_token_option_without_its_value(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "from-env")
    result = runner.invoke(app, ["--token", "from-cli", "--help"])
    output = _plain(result.stdout)
    assert result.exit_code == EXIT_SUCCESS
    assert "--token" in output
    assert TOKEN_ENV in output
    assert "from-cli" not in output
    assert "from-env" not in output
