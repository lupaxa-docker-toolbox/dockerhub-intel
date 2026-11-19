"""
Resolve an optional Docker Hub token.

The command-line value wins. Otherwise the named environment variable is
used. A missing or blank value means unauthenticated access.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Final

__all__ = ["TOKEN_ENV", "active_token", "resolve_token", "use_token"]

TOKEN_ENV: Final[str] = "DOCKERHUB_TOKEN"  # noqa: S105
active_token: str | None = None


def resolve_token(
    cli_token: str | None,
    *,
    environ: Mapping[str, str] | None = None,
    env_name: str = TOKEN_ENV,
) -> str | None:
    """Return the CLI token, otherwise the environment token, otherwise none."""
    source = os.environ if environ is None else environ
    for candidate in (cli_token, source.get(env_name)):
        if candidate is None:
            continue
        text = candidate.strip()
        if text:
            return text
    return None


def use_token(cli_token: str | None) -> str | None:
    """Resolve the process token and remember it for later commands."""
    global active_token
    active_token = resolve_token(cli_token)
    return active_token
