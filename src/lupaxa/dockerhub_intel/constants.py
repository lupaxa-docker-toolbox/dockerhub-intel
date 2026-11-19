"""
Project-wide constants for Docker Hub Intel.

This module contains stable names, version text, and process exit codes.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CHECK_NAMES",
    "DEFAULT_PROFILE",
    "EXIT_AUTH",
    "EXIT_CHECKS",
    "EXIT_CONFIG",
    "EXIT_ERROR",
    "EXIT_NOT_FOUND",
    "EXIT_SUCCESS",
    "EXIT_SYNC",
    "PROGRAM_NAME",
    "PROGRAM_VERSION",
    "PROGRAM_VERSION_STRING",
    "PROJECT_NAME",
    "UNGROUPED",
]

PROGRAM_NAME: Final[str] = "dockerhub-intel"
PROJECT_NAME: Final[str] = "lupaxa-dockerhub-intel"
PROGRAM_VERSION: Final[str] = "0.0.0"
PROGRAM_VERSION_STRING: Final[str] = f"{PROGRAM_NAME} v{PROGRAM_VERSION}"

DEFAULT_PROFILE: Final[str] = "standard"
UNGROUPED: Final[str] = "ungrouped"

CHECK_NAMES: Final[tuple[str, ...]] = (
    "metadata",
    "stats",
    "tags",
    "digests",
    "architectures",
    "image_details",
    "stale",
)

EXIT_SUCCESS: Final[int] = 0
EXIT_ERROR: Final[int] = 1
EXIT_CONFIG: Final[int] = 2
EXIT_AUTH: Final[int] = 3
EXIT_SYNC: Final[int] = 4
EXIT_NOT_FOUND: Final[int] = 5
EXIT_CHECKS: Final[int] = 6
