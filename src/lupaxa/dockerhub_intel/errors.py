"""Application errors and the exit codes they map to."""

from __future__ import annotations

from lupaxa.dockerhub_intel.constants import (
    EXIT_AUTH,
    EXIT_CHECKS,
    EXIT_CONFIG,
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_SYNC,
)

__all__ = [
    "AuthError",
    "ChecksFailed",
    "ConfigError",
    "HubError",
    "NotFound",
    "SyncIncomplete",
]


class HubError(Exception):
    """Base error for failures reported to the operator."""

    exit_code = EXIT_ERROR


class ConfigError(HubError):
    """The configuration or command arguments are invalid."""

    exit_code = EXIT_CONFIG


class AuthError(HubError):
    """Docker Hub rejected the credentials used for the API."""

    exit_code = EXIT_AUTH


class SyncIncomplete(HubError):
    """Synchronisation finished with failures or was cancelled."""

    exit_code = EXIT_SYNC


class NotFound(HubError):
    """The named repository is not in the configuration."""

    exit_code = EXIT_NOT_FOUND


class ChecksFailed(HubError):
    """An explicit failure option found a monitoring condition."""

    exit_code = EXIT_CHECKS
