"""Atomic edits of the repository list. Comments and key order are preserved."""

from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from lupaxa.dockerhub_intel.config.loader import LoadedConfig
from lupaxa.dockerhub_intel.dockerhub.names import canonical_repository
from lupaxa.dockerhub_intel.errors import ConfigError, NotFound

__all__ = [
    "add_repository",
    "disable_repository",
    "enable_repository",
    "remove_repository",
    "set_group",
    "set_profile",
]


def add_repository(
    loaded: LoadedConfig,
    repo: str,
    *,
    group: str | None = None,
    profile: str | None = None,
    labels: list[str] | None = None,
) -> str:
    """Append a repository. The canonical name is returned."""
    name = _canonical(repo)
    if any(item.repo == name for item in loaded.repositories.repositories):
        raise ConfigError(f"Duplicate repository: {name}.")
    if profile is not None and profile not in loaded.profiles.profiles:
        raise ConfigError(f"Unknown profile: {profile}.")
    document = _load(loaded)
    entry: object
    if group is None and profile is None and not labels:
        entry = name
    else:
        mapping = CommentedMap({"repo": name})
        if group is not None:
            mapping["group"] = group
        if profile is not None:
            mapping["profile"] = profile
        if labels:
            mapping["labels"] = labels
        entry = mapping
    document.append(entry)
    _save(loaded, document)
    return name


def remove_repository(loaded: LoadedConfig, repo: str) -> str:
    """Remove a repository from configuration. History is not deleted."""
    name = _require(loaded, repo)
    document = _load(loaded)
    kept = CommentedSeq(item for item in document if _name_of(item) != name)
    _save(loaded, kept)
    return name


def disable_repository(loaded: LoadedConfig, repo: str) -> str:
    """Keep a repository in configuration and skip it during synchronisation."""
    return enable_repository(loaded, repo, enabled=False)


def enable_repository(loaded: LoadedConfig, repo: str, *, enabled: bool) -> str:
    """Set ``enabled`` on one repository."""
    name = _require(loaded, repo)
    document = _load(loaded)
    for index, item in enumerate(document):
        if _name_of(item) == name:
            mapping = _as_mapping(item, name)
            mapping["enabled"] = enabled
            document[index] = mapping
            break
    _save(loaded, document)
    return name


def set_group(loaded: LoadedConfig, repo: str, group: str) -> str:
    """Set the primary group."""
    return _set_field(loaded, repo, "group", group.strip())


def set_profile(loaded: LoadedConfig, repo: str, profile: str) -> str:
    """Set the monitoring profile."""
    text = profile.strip()
    if text not in loaded.profiles.profiles:
        raise ConfigError(f"Unknown profile: {text}.")
    return _set_field(loaded, repo, "profile", text)


def _set_field(loaded: LoadedConfig, repo: str, field: str, value: str) -> str:
    if value == "":
        raise ConfigError(f"{field} must not be blank.")
    name = _require(loaded, repo)
    document = _load(loaded)
    for index, item in enumerate(document):
        if _name_of(item) == name:
            mapping = _as_mapping(item, name)
            mapping[field] = value
            document[index] = mapping
            break
    _save(loaded, document)
    return name


def _require(loaded: LoadedConfig, repo: str) -> str:
    name = _canonical(repo)
    if not any(item.repo == name for item in loaded.repositories.repositories):
        raise NotFound(f"Repository not found: {name}.")
    return name


def _canonical(repo: str) -> str:
    try:
        return canonical_repository(repo)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _repositories_path(loaded: LoadedConfig) -> Path:
    root = loaded.path.parent
    candidate = Path(loaded.app.files.repositories).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def _load(loaded: LoadedConfig) -> CommentedSeq:
    path = _repositories_path(loaded)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text or "{}")
        raw = payload.get("repositories", []) if isinstance(payload, dict) else []
        return CommentedSeq(raw if isinstance(raw, list) else [])
    parsed = _yaml().load(text) or {}
    raw = parsed.get("repositories") if isinstance(parsed, dict) else None
    if isinstance(raw, CommentedSeq):
        return raw
    if isinstance(raw, list):
        return CommentedSeq(raw)
    return CommentedSeq()


def _save(loaded: LoadedConfig, repositories: CommentedSeq) -> None:
    path = _repositories_path(loaded)
    if path.suffix.lower() == ".json":
        text = json.dumps({"repositories": list(repositories)}, indent=2) + "\n"
    else:
        yaml = _yaml()
        document = yaml.load(path.read_text(encoding="utf-8")) or CommentedMap()
        if not isinstance(document, CommentedMap):
            document = CommentedMap()
        document["repositories"] = repositories
        buffer = StringIO()
        yaml.dump(document, buffer)
        text = buffer.getvalue()
    _atomic(path, text)


def _as_mapping(item: object, name: str) -> CommentedMap:
    if isinstance(item, str):
        return CommentedMap({"repo": name})
    if isinstance(item, CommentedMap):
        item["repo"] = name
        return item
    if isinstance(item, dict):
        mapping = CommentedMap(item)
        mapping["repo"] = name
        return mapping
    raise ConfigError(f"Cannot edit repository entry for {name}.")


def _name_of(item: object) -> str | None:
    if isinstance(item, str):
        try:
            return canonical_repository(item)
        except ValueError:
            return None
    if isinstance(item, dict) and isinstance(item.get("repo"), str):
        try:
            return canonical_repository(str(item["repo"]))
        except ValueError:
            return None
    return None


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=2, offset=0)
    return yaml


def _atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)
