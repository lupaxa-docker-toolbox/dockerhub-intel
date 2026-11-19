"""Single-parent profile inheritance. An explicit false overrides an inherited true."""

from __future__ import annotations

from dataclasses import dataclass

from lupaxa.dockerhub_intel.config.models import ProfileSpec
from lupaxa.dockerhub_intel.constants import CHECK_NAMES
from lupaxa.dockerhub_intel.errors import ConfigError

__all__ = ["ResolvedProfile", "resolve_profile"]


@dataclass(frozen=True)
class ResolvedProfile:
    """Effective check flags and the profile that set each one."""

    name: str
    chain: tuple[str, ...]
    enabled: dict[str, bool]
    sources: dict[str, str]


def resolve_profile(
    profiles: dict[str, ProfileSpec],
    name: str,
    overrides: dict[str, bool] | None = None,
) -> ResolvedProfile:
    """
    Resolve ``name``, then apply repository overrides.

    Args:
        profiles: Named profile definitions.
        name: Profile to resolve.
        overrides: Repository check overrides applied last.

    Returns:
        Effective flags. The chain runs from the named profile to the root.

    Raises:
        ConfigError: The profile is missing, a parent is missing, or inheritance cycles.

    """

    chain = _inheritance_chain(profiles, name)
    enabled = dict.fromkeys(CHECK_NAMES, False)
    sources = dict.fromkeys(CHECK_NAMES, "default")
    for profile_name in reversed(chain):
        _apply_profile(profiles[profile_name], profile_name, enabled, sources)
    for check, value in (overrides or {}).items():
        if check not in CHECK_NAMES:
            message = f"Unknown check names: {check}."
            raise ConfigError(message)
        enabled[check] = value
        sources[check] = "repository"
    return ResolvedProfile(name=name, chain=chain, enabled=enabled, sources=sources)


def _inheritance_chain(
    profiles: dict[str, ProfileSpec],
    name: str,
) -> tuple[str, ...]:
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = name
    while current is not None:
        if current in seen:
            cycle = " -> ".join([*chain, current])
            raise ConfigError(f"Circular profile inheritance: {cycle}.")
        spec = profiles.get(current)
        if spec is None:
            raise ConfigError(f"Unknown profile: {current}.")
        seen.add(current)
        chain.append(current)
        current = spec.extends
    return tuple(chain)


def _apply_profile(
    spec: ProfileSpec,
    profile_name: str,
    enabled: dict[str, bool],
    sources: dict[str, str],
) -> None:
    for check in CHECK_NAMES:
        value = getattr(spec, check)
        if isinstance(value, bool):
            enabled[check] = value
            sources[check] = profile_name
