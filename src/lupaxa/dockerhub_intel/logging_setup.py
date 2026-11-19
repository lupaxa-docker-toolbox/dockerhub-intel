"""Logging configuration. The access token is removed from log records."""

from __future__ import annotations

import logging
import sys

from lupaxa.dockerhub_intel import auth

__all__ = ["configure_logging"]


def configure_logging(level: str, *, verbose: bool, debug: bool) -> None:
    """Send diagnostics to stderr. Debug and verbose raise the level."""
    chosen = "DEBUG" if debug else "INFO" if verbose else level
    logging.basicConfig(
        level=chosen,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )
    root = logging.getLogger()
    root.filters.clear()
    root.addFilter(_Redact())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class _Redact(logging.Filter):
    """Drop the active token from a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return true after replacing the token."""
        token = auth.active_token
        if token:
            record.msg = str(_hide(record.getMessage(), token))
            record.args = ()
        return True


def _hide(value: object, token: str) -> object:
    if isinstance(value, str):
        return value.replace(token, "***")
    return value
