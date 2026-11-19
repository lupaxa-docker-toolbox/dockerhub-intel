"""Default configuration files."""

from __future__ import annotations

from pathlib import Path

__all__ = ["generate_default_files", "write_default_files"]

_CONFIG = """\
# Docker Hub Intel configuration.
# Pass --token or set the variable named by token_env. The token is not stored.

version: 1

dockerhub:
  token_env: DOCKERHUB_TOKEN
  api_url: https://hub.docker.com/v2
  registry_url: https://registry-1.docker.io

files:
  profiles: profiles.yml
  repositories: repositories.yml

defaults:
  profile: standard

sync:
  concurrency: 10
  timeout: 15
  retries: 3
  page_size: 100

cache:
  enabled: true
  metadata_ttl: 3600
  stats_ttl: 3600
  tags_ttl: 3600
  manifests_ttl: 21600

history:
  enabled: true
  retention_days: 0

logging:
  level: INFO

checks:
  stale:
    enabled: true
    repository_days: 180
    tag_days: 90
"""

_PROFILES = """\
profiles:
  base:
    metadata: true
    stats: true
    tags: false
    digests: false
    architectures: false
    image_details: false
    stale: false

  standard:
    extends: base
    tags: true

  detailed:
    extends: standard
    digests: true
    architectures: true
    image_details: true

  watchlist:
    extends: standard
    digests: true
"""

_REPOSITORIES = """\
repositories: []
"""


def write_default_files(directory: Path) -> Path:
    """Create the default files when config.yml is absent. Existing files stay."""
    config = directory / "config.yml"
    generate_default_files(
        (config, directory / "profiles.yml", directory / "repositories.yml"),
        force=False,
    )
    return config


def generate_default_files(
    paths: tuple[Path, Path, Path],
    *,
    force: bool,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Write config, profiles, and repositories. Return paths written, then kept."""
    directory = paths[0].parent
    directory.mkdir(parents=True, exist_ok=True)
    _restrict(directory)
    written: list[Path] = []
    kept: list[Path] = []
    for path, text in zip(paths, (_CONFIG, _PROFILES, _REPOSITORIES), strict=True):
        if path.exists() and not force:
            kept.append(path)
            continue
        path.write_text(text, encoding="utf-8")
        _restrict(path)
        written.append(path)
    return tuple(written), tuple(kept)


def _restrict(path: Path) -> None:
    if path.is_dir():
        path.chmod(0o700)
    else:
        path.chmod(0o600)
