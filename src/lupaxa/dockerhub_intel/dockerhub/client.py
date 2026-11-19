"""Read-only Docker Hub and registry client."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

import httpx

from lupaxa.dockerhub_intel.dockerhub.names import split_repository
from lupaxa.dockerhub_intel.dockerhub.rate_limit import RateLimit
from lupaxa.dockerhub_intel.errors import AuthError, HubError

__all__ = ["HubClient", "RepoInfo", "TagInfo", "TagPage"]

_USER_AGENT = "lupaxa-dockerhub-intel"


@dataclass(frozen=True)
class RepoInfo:
    """Repository metadata returned by Docker Hub."""

    description: str | None
    private: bool | None
    status: str | None
    created_at: str | None
    updated_at: str | None
    pull_count: int | None
    star_count: int | None


@dataclass(frozen=True)
class TagInfo:
    """One tag from a Hub tag listing."""

    name: str
    updated_at: str | None
    full_size: int | None
    digest: str | None
    media_type: str | None
    platforms: tuple[str, ...]


@dataclass(frozen=True)
class TagPage:
    """A tag listing and whether every page was retrieved."""

    tags: tuple[TagInfo, ...]
    complete: bool
    error: str | None = None


class HubClient:
    """HTTP client that never calls a write operation."""

    def __init__(
        self,
        *,
        api_url: str,
        registry_url: str,
        token: str | None,
        timeout: float,
        retries: int,
        page_size: int,
        limiter: RateLimit | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Store endpoint settings. A token is sent only when one was supplied."""
        self.api_url = api_url.rstrip("/")
        self.registry_url = registry_url.rstrip("/")
        self.token = token
        self.retries = retries
        self.page_size = page_size
        self.requests = 0
        self.on_request: Callable[[], None] | None = None
        self.hub_limit = limiter or RateLimit()
        self.registry_limit = RateLimit()
        self.limiter = self.hub_limit
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        self._client = client or httpx.AsyncClient(timeout=timeout, headers=headers)
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the client when this object created it."""
        if self._owns_client:
            await self._client.aclose()

    async def repository(self, repo: str) -> RepoInfo | None:
        """Return repository metadata, or None when Docker Hub returns 404."""
        namespace, name = split_repository(repo)
        status, payload, error = await self._json(
            "GET",
            f"{self.api_url}/repositories/{namespace}/{name}/",
        )
        if status == 404:
            return None
        if error is not None or not isinstance(payload, dict):
            raise HubError(
                error or "Docker Hub returned an unexpected repository response."
            )
        return RepoInfo(
            description=_text(payload.get("description")),
            private=payload.get("is_private")
            if isinstance(payload.get("is_private"), bool)
            else None,
            status=_text(payload.get("status_description")),
            created_at=_text(payload.get("date_registered")),
            updated_at=_text(payload.get("last_updated")),
            pull_count=_int(payload.get("pull_count")),
            star_count=_int(payload.get("star_count")),
        )

    async def tags(self, repo: str) -> TagPage:
        """Follow tag pages. A failed page leaves the listing incomplete."""
        namespace, name = split_repository(repo)
        url: str | None = (
            f"{self.api_url}/repositories/{namespace}/{name}/tags"
            f"?page_size={self.page_size}&page=1"
        )
        found: list[TagInfo] = []
        seen: set[str] = set()
        while url is not None:
            status, payload, error = await self._json("GET", url)
            if status == 404:
                return TagPage(
                    tuple(found), complete=False, error="Repository not found."
                )
            if error is not None or not isinstance(payload, dict):
                return TagPage(
                    tuple(found), complete=False, error=error or "Tag page failed."
                )
            results = payload.get("results")
            if not isinstance(results, list):
                return TagPage(
                    tuple(found), complete=False, error="Tag page has no results."
                )
            for item in results:
                if not isinstance(item, dict):
                    continue
                tag_name = item.get("name")
                if not isinstance(tag_name, str) or tag_name in seen:
                    continue
                seen.add(tag_name)
                found.append(_tag(item))
            nxt = payload.get("next")
            url = nxt if isinstance(nxt, str) and nxt else None
        return TagPage(tuple(found), complete=True)

    async def _json(
        self,
        method: str,
        url: str,
    ) -> tuple[int, object | None, str | None]:
        if method != "GET":
            message = "Only read requests are allowed."
            raise RuntimeError(message)
        last_error = "Request failed."
        status = 0
        limiter = self._limiter_for(url)
        for attempt in range(self.retries + 1):
            await limiter.wait()
            self.requests += 1
            if self.on_request is not None:
                self.on_request()
            try:
                response = await self._client.get(url, headers=self._auth_header())
            except httpx.HTTPError:
                last_error = "Network error."
                if attempt >= self.retries:
                    return 0, None, last_error
                await limiter.pause(limiter.backoff(attempt, None))
                continue
            status = response.status_code
            scheduled = limiter.observe(status, response.headers)
            if status in {401, 403}:
                raise AuthError(_auth_message(status))
            if status == 429 or status >= 500:
                last_error = f"HTTP {status}."
                if attempt >= self.retries:
                    return status, None, last_error
                if status == 429 and scheduled is None:
                    limiter.hold(limiter.backoff(attempt, None))
                elif status >= 500:
                    await limiter.pause(limiter.backoff(attempt, None))
                continue
            if status >= 400:
                return status, None, f"HTTP {status}."
            try:
                return status, response.json(), None
            except json.JSONDecodeError:
                return status, None, "Docker Hub returned invalid JSON."
        return status, None, last_error

    def _auth_header(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": _authorization(self.token)}

    def _limiter_for(self, url: str) -> RateLimit:
        if url.startswith(self.registry_url):
            return self.registry_limit
        return self.hub_limit


def _tag(item: dict[str, object]) -> TagInfo:
    images = item.get("images")
    platforms: list[str] = []
    if isinstance(images, list):
        for image in images:
            if not isinstance(image, dict):
                continue
            system = image.get("os")
            arch = image.get("architecture")
            if system in {None, "unknown"} or arch in {None, "unknown"}:
                continue
            variant = image.get("variant")
            label = f"{system}/{arch}"
            if isinstance(variant, str) and variant:
                label = f"{label}/{variant}"
            if label not in platforms:
                platforms.append(label)
    return TagInfo(
        name=str(item.get("name")),
        updated_at=_text(item.get("last_updated")),
        full_size=_int(item.get("full_size")),
        digest=_text(item.get("digest")),
        media_type=_text(item.get("media_type")),
        platforms=tuple(platforms),
    )


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


def _authorization(token: str) -> str:
    """Return the header value Hub accepts for this token."""
    if token.startswith("eyJ") and token.count(".") == 2:
        return f"Bearer {token}"
    return f"JWT {token}"


def _auth_message(status: int) -> str:
    if status == 401:
        return "Authentication failed."
    return "Permission denied."
