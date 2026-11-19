"""Canonical Docker Hub repository names."""

from __future__ import annotations

import re

_PART = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")

__all__ = ["canonical_repository", "split_repository", "web_url"]


def canonical_repository(value: str) -> str:
    """
    Return ``namespace/repository``, prefixing official images with ``library``.

    Args:
        value: A repository name or official-image shorthand.

    Returns:
        The canonical identifier.

    Raises:
        ValueError: The name is blank or is not a Docker Hub repository.

    """

    text = value.strip().lower()
    if text == "":
        message = "repo must not be blank."
        raise ValueError(message)
    parts = text.split("/")
    if len(parts) == 1:
        parts = ["library", parts[0]]
    if len(parts) != 2 or any(part == "" for part in parts):
        message = f"Invalid repository name: {value}."
        raise ValueError(message)
    if any(_PART.fullmatch(part) is None for part in parts):
        message = f"Invalid repository name: {value}."
        raise ValueError(message)
    return f"{parts[0]}/{parts[1]}"


def split_repository(repo: str) -> tuple[str, str]:
    """Return the namespace and repository of a canonical name."""
    namespace, name = repo.split("/", 1)
    return namespace, name


def web_url(repo: str) -> str:
    """Return the Docker Hub page for a canonical repository."""
    namespace, name = split_repository(repo)
    if namespace == "library":
        return f"https://hub.docker.com/_/{name}"
    return f"https://hub.docker.com/r/{namespace}/{name}"
