"""Load YAML or JSON configuration and resolve profiles."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from lupaxa.dockerhub_intel.config.models import (
    AppConfig,
    ProfileFile,
    RepositoryFile,
    RepositorySpec,
)
from lupaxa.dockerhub_intel.config.paths import config_directory, default_config_file
from lupaxa.dockerhub_intel.config.profiles import ResolvedProfile, resolve_profile
from lupaxa.dockerhub_intel.errors import ConfigError

__all__ = ["LoadedConfig", "load_config"]

ModelT = TypeVar("ModelT", AppConfig, ProfileFile, RepositoryFile)


@dataclass(frozen=True)
class LoadedConfig:
    """Application settings, profiles, and the repository list."""

    path: Path
    app: AppConfig
    profiles: ProfileFile
    repositories: RepositoryFile

    def resolved(self, spec: RepositorySpec) -> ResolvedProfile:
        """Return the effective profile for one repository."""
        name = spec.profile or self.app.defaults.profile
        return resolve_profile(self.profiles.profiles, name, spec.checks)

    def select(
        self,
        *,
        repo: str | None = None,
        group: str | None = None,
        profile: str | None = None,
        label: str | None = None,
        enabled_only: bool = False,
    ) -> list[RepositorySpec]:
        """Return repositories matching every supplied filter."""
        chosen: list[RepositorySpec] = []
        for spec in self.repositories.repositories:
            if enabled_only and not spec.enabled:
                continue
            if repo is not None and spec.repo != repo:
                continue
            if group is not None and spec.group != group:
                continue
            if (
                profile is not None
                and (spec.profile or self.app.defaults.profile) != profile
            ):
                continue
            if label is not None and label not in spec.labels:
                continue
            chosen.append(spec)
        return chosen


def load_config(path: Path | None = None) -> LoadedConfig:
    """
    Load the main file and the profile and repository files it names.

    Relative paths resolve from the main file. The result is validated,
    including profile inheritance, before it is returned.
    """

    config_path = _main_path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}.")
    app = _model(AppConfig, _read_mapping(config_path), config_path)
    app = _apply_environment(app)
    root = config_path.parent
    profiles_path = _resolve(root, app.files.profiles)
    repositories_path = _resolve(root, app.files.repositories)
    profiles = _model(ProfileFile, _read_mapping(profiles_path), profiles_path)
    repositories = _model(
        RepositoryFile,
        _read_mapping(repositories_path),
        repositories_path,
    )
    _validate_graph(app, profiles, repositories)
    return LoadedConfig(config_path, app, profiles, repositories)


def _main_path(path: Path | None) -> Path:
    if path is not None:
        return path.expanduser()
    env = os.environ.get("DOCKERHUB_INTEL_CONFIG")
    if env is not None and env.strip() != "":
        return Path(env).expanduser()
    return default_config_file(config_directory())


def _resolve(root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _read_mapping(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}.")
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() == ".json":
            loaded = json.loads(text or "{}")
        else:
            loaded = YAML(typ="safe").load(text)
            if loaded is None:
                loaded = {}
    except (OSError, YAMLError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}.") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a mapping.")
    return loaded


def _model(kind: type[ModelT], payload: dict[str, object], path: Path) -> ModelT:
    try:
        return kind.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(_validation_message(path, exc)) from exc


def _validate_graph(
    app: AppConfig,
    profiles: ProfileFile,
    repositories: RepositoryFile,
) -> None:
    try:
        AppConfig.model_validate(app.model_dump())
        for name in profiles.profiles:
            resolve_profile(profiles.profiles, name)
        if app.defaults.profile not in profiles.profiles:
            raise ConfigError(f"Unknown profile: {app.defaults.profile}.")
        for spec in repositories.repositories:
            name = spec.profile or app.defaults.profile
            resolve_profile(profiles.profiles, name, spec.checks)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
    except ConfigError:
        raise


def _apply_environment(app: AppConfig) -> AppConfig:
    data = app.model_dump()
    concurrency = os.environ.get("DOCKERHUB_INTEL_CONCURRENCY")
    if concurrency is not None and concurrency.strip() != "":
        try:
            data["sync"]["concurrency"] = int(concurrency)
        except ValueError as exc:
            raise ConfigError(
                "DOCKERHUB_INTEL_CONCURRENCY must be an integer."
            ) from exc
    level = os.environ.get("DOCKERHUB_INTEL_LOG_LEVEL")
    if level is not None and level.strip() != "":
        data["logging"]["level"] = level.strip().upper()
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_message(Path("environment"), exc)) from exc


def _validation_message(path: Path, exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "value"
        parts.append(f"{location}: {error['msg']}")
    detail = "; ".join(parts) if parts else str(exc)
    return f"Invalid configuration in {path}: {detail}."
