"""Resolve configuration and cache directories with platformdirs."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from platformdirs import PlatformDirs
from platformdirs.unix import Unix

__all__ = ["config_directory", "data_directory", "default_config_file"]


def config_directory() -> Path:
    """Return the configuration directory.

    ``DOCKERHUB_INTEL_CONFIG_DIR`` overrides the platform directory.
    """
    return _configured_directory("DOCKERHUB_INTEL_CONFIG_DIR", "user_config_dir")


def data_directory() -> Path:
    """Return the database directory. ``DOCKERHUB_INTEL_DATA_DIR`` overrides it."""
    return _configured_directory("DOCKERHUB_INTEL_DATA_DIR", "user_data_dir")


def default_config_file(directory: Path | None = None) -> Path:
    """Return the main configuration file, preferring one that already exists."""
    root = config_directory() if directory is None else directory
    for name in ("config.yml", "config.yaml", "config.json"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return root / "config.yml"


def _configured_directory(env_name: str, attribute: str) -> Path:
    override = os.environ.get(env_name)
    if override is not None and override.strip() != "":
        return Path(override).expanduser()
    dirs: PlatformDirs | Unix
    if sys.platform == "win32":
        dirs = PlatformDirs(appname="dockerhub-intel", appauthor=False)
    else:
        dirs = Unix(appname="dockerhub-intel", appauthor=False)
    return Path(getattr(dirs, attribute))
