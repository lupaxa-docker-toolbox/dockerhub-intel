"""CLI help and version."""

from __future__ import annotations

import re
import sys

import pytest
from typer.testing import CliRunner

from lupaxa.dockerhub_intel.cli import _version, app, main
from lupaxa.dockerhub_intel.constants import EXIT_SUCCESS, PROGRAM_VERSION_STRING

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    output = _plain(result.stdout)
    assert result.exit_code == EXIT_SUCCESS
    assert "dockerhub-intel" in output
    assert "--version" in output


def test_version_exits_zero() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == EXIT_SUCCESS
    assert PROGRAM_VERSION_STRING in _plain(result.stdout)


def test_version_callback_false_is_a_no_op() -> None:
    _version(False)


def test_no_arguments_print_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == EXIT_SUCCESS
    assert "--version" in _plain(result.stdout)


def test_main_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["dockerhub-intel", "--version"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == EXIT_SUCCESS
