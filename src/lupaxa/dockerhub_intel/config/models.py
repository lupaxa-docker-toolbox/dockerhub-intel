"""Configuration models. Unknown keys and invalid types are rejected."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lupaxa.dockerhub_intel.constants import CHECK_NAMES, DEFAULT_PROFILE, UNGROUPED
from lupaxa.dockerhub_intel.dockerhub.names import canonical_repository

__all__ = [
    "AppConfig",
    "CacheSettings",
    "DefaultsSettings",
    "DockerHubSettings",
    "FileSettings",
    "HistorySettings",
    "LoggingSettings",
    "ProfileFile",
    "ProfileSpec",
    "RepositoryFile",
    "RepositorySpec",
    "StaleSettings",
    "SyncSettings",
]

_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DockerHubSettings(_StrictModel):
    """Docker Hub location. The token itself is never stored here."""

    token_env: str = "DOCKERHUB_TOKEN"  # noqa: S105
    api_url: str = "https://hub.docker.com/v2"
    registry_url: str = "https://registry-1.docker.io"

    @field_validator("token_env")
    @classmethod
    def _token_env(cls, value: str) -> str:
        text = value.strip()
        if text == "":
            message = "dockerhub.token_env must not be blank."
            raise ValueError(message)
        return text

    @field_validator("api_url", "registry_url")
    @classmethod
    def _https(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not text.startswith("https://"):
            message = "Docker Hub URLs must use https."
            raise ValueError(message)
        return text


class FileSettings(_StrictModel):
    """Paths of the profile and repository files, relative to the main file."""

    profiles: str = "profiles.yml"
    repositories: str = "repositories.yml"


class CacheSettings(_StrictModel):
    """Per-category freshness windows, in seconds."""

    enabled: bool = True
    metadata_ttl: int = Field(default=3600, ge=0)
    stats_ttl: int = Field(default=3600, ge=0)
    tags_ttl: int = Field(default=3600, ge=0)
    manifests_ttl: int = Field(default=21600, ge=0)


class SyncSettings(_StrictModel):
    """Bounded synchronisation settings."""

    concurrency: int = Field(default=10, ge=1, le=50)
    timeout: float = Field(default=15, gt=0)
    retries: int = Field(default=3, ge=0)
    page_size: int = Field(default=100, ge=1, le=100)


class HistorySettings(_StrictModel):
    """How long observations are kept. Zero retains them indefinitely."""

    enabled: bool = True
    retention_days: int = Field(default=0, ge=0)


class LoggingSettings(_StrictModel):
    """Logging level stored in configuration."""

    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        text = value.strip().upper()
        if text not in _LOG_LEVELS:
            message = "logging.level must be CRITICAL, ERROR, WARNING, INFO, or DEBUG."
            raise ValueError(message)
        return text


class StaleSettings(_StrictModel):
    """Age thresholds for the stale check, in days."""

    enabled: bool = True
    repository_days: int = Field(default=180, ge=1)
    tag_days: int = Field(default=90, ge=1)


class CheckSettings(_StrictModel):
    """Global check settings. Profile flags still decide what runs."""

    stale: StaleSettings = Field(default_factory=StaleSettings)


class DefaultsSettings(_StrictModel):
    """Profile used when a repository does not name one."""

    profile: str = DEFAULT_PROFILE

    @field_validator("profile")
    @classmethod
    def _profile(cls, value: str) -> str:
        text = value.strip()
        if text == "":
            message = "defaults.profile must not be blank."
            raise ValueError(message)
        return text


class ProfileSpec(_StrictModel):
    """One monitoring profile. Omitted checks are inherited."""

    extends: str | None = None
    metadata: bool | None = None
    stats: bool | None = None
    tags: bool | None = None
    digests: bool | None = None
    architectures: bool | None = None
    image_details: bool | None = None
    stale: bool | None = None

    @field_validator("extends")
    @classmethod
    def _extends(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if text == "":
            message = "extends must not be blank."
            raise ValueError(message)
        return text


class RepositorySpec(_StrictModel):
    """One configured repository."""

    repo: str
    group: str = UNGROUPED
    profile: str | None = None
    labels: list[str] = Field(default_factory=list)
    enabled: bool = True
    checks: dict[str, bool] = Field(default_factory=dict)

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str) -> str:
        try:
            return canonical_repository(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("group", "profile")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if text == "":
            message = "group and profile must not be blank."
            raise ValueError(message)
        return text

    @field_validator("labels")
    @classmethod
    def _labels(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for label in value:
            text = label.strip()
            if text == "":
                message = "labels must not contain a blank value."
                raise ValueError(message)
            cleaned.append(text)
        return cleaned

    @field_validator("checks")
    @classmethod
    def _checks(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = sorted(set(value) - set(CHECK_NAMES))
        if unknown:
            message = f"Unknown check names: {', '.join(unknown)}."
            raise ValueError(message)
        return value


class AppConfig(_StrictModel):
    """The main configuration file."""

    version: int = 1
    dockerhub: DockerHubSettings = Field(default_factory=DockerHubSettings)
    files: FileSettings = Field(default_factory=FileSettings)
    defaults: DefaultsSettings = Field(default_factory=DefaultsSettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    checks: CheckSettings = Field(default_factory=CheckSettings)

    @field_validator("version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            message = "config version must be 1."
            raise ValueError(message)
        return value


class ProfileFile(_StrictModel):
    """The profiles file."""

    profiles: dict[str, ProfileSpec] = Field(default_factory=dict)

    @field_validator("profiles")
    @classmethod
    def _names(cls, value: dict[str, ProfileSpec]) -> dict[str, ProfileSpec]:
        for name in value:
            if name.strip() == "":
                message = "profile names must not be blank."
                raise ValueError(message)
        return value


class RepositoryFile(_StrictModel):
    """The repository list. Strings and objects may be mixed."""

    repositories: list[RepositorySpec] = Field(default_factory=list)

    @field_validator("repositories", mode="before")
    @classmethod
    def _entries(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        parsed: list[object] = []
        for item in value:
            if isinstance(item, str):
                parsed.append({"repo": item})
            else:
                parsed.append(item)
        return parsed

    @model_validator(mode="after")
    def _duplicates(self) -> RepositoryFile:
        seen: set[str] = set()
        for spec in self.repositories:
            if spec.repo in seen:
                message = f"Duplicate repository: {spec.repo}."
                raise ValueError(message)
            seen.add(spec.repo)
        return self
