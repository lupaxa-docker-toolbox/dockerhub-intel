"""SQLite storage. Configuration remains in repositories.yml."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import aiosqlite

__all__ = ["Database", "Snapshot", "StoredTag"]

_EVENT_QUERIES = {
    (False, False, False): (
        "SELECT * FROM tag_events ORDER BY observed_at DESC, id DESC"
    ),
    (True, False, False): (
        "SELECT * FROM tag_events WHERE repo = ? ORDER BY observed_at DESC, id DESC"
    ),
    (False, True, False): (
        "SELECT * FROM tag_events WHERE change = ? ORDER BY observed_at DESC, id DESC"
    ),
    (False, False, True): (
        "SELECT * FROM tag_events WHERE observed_at >= ? "
        "ORDER BY observed_at DESC, id DESC"
    ),
    (True, True, False): (
        "SELECT * FROM tag_events WHERE repo = ? AND change = ? "
        "ORDER BY observed_at DESC, id DESC"
    ),
    (True, False, True): (
        "SELECT * FROM tag_events WHERE repo = ? AND observed_at >= ? "
        "ORDER BY observed_at DESC, id DESC"
    ),
    (False, True, True): (
        "SELECT * FROM tag_events WHERE change = ? AND observed_at >= ? "
        "ORDER BY observed_at DESC, id DESC"
    ),
    (True, True, True): (
        "SELECT * FROM tag_events WHERE repo = ? AND change = ? AND observed_at >= ? "
        "ORDER BY observed_at DESC, id DESC"
    ),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repositories (
    repo TEXT PRIMARY KEY,
    description TEXT,
    visibility TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT,
    pull_count INTEGER,
    star_count INTEGER,
    web_url TEXT,
    metadata_at TEXT,
    metadata_error TEXT
);
CREATE TABLE IF NOT EXISTS repository_snapshots (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    pull_count INTEGER,
    star_count INTEGER,
    anomaly INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tags (
    repo TEXT NOT NULL,
    name TEXT NOT NULL,
    updated_at TEXT,
    full_size INTEGER,
    digest TEXT,
    media_type TEXT,
    platforms TEXT,
    PRIMARY KEY (repo, name)
);
CREATE TABLE IF NOT EXISTS tag_events (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    name TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    change TEXT NOT NULL,
    old_digest TEXT,
    new_digest TEXT
);
CREATE TABLE IF NOT EXISTS manifest_snapshots (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    tag TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    digest TEXT,
    platforms TEXT,
    media_type TEXT,
    full_size INTEGER
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_results (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    repo TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS cache_entries (
    repo TEXT NOT NULL,
    category TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    complete INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (repo, category)
);
"""


@dataclass(frozen=True)
class Snapshot:
    """One pull-count observation."""

    observed_at: datetime
    pull_count: int | None
    star_count: int | None
    anomaly: bool


@dataclass(frozen=True)
class StoredTag:
    """The latest known tag row."""

    name: str
    updated_at: str | None
    full_size: int | None
    digest: str | None
    media_type: str | None
    platforms: tuple[str, ...]


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class Database:
    """One SQLite file for observations, events, and cache freshness."""

    def __init__(self, path: Path) -> None:
        """Remember the database path. The file is created on open."""
        self.path = path
        self._connection: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the database, enable WAL, and create tables."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.executescript(_SCHEMA)
        await self._connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (version, applied_at)
            VALUES (1, ?)
            """,
            (_iso(utc_now()),),
        )
        await self._connection.commit()

    async def close(self) -> None:
        """Close the connection."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def _db(self) -> aiosqlite.Connection:
        if self._connection is None:
            message = "Database is not open."
            raise RuntimeError(message)
        return self._connection

    async def fresh(self, repo: str, category: str, ttl: int, *, enabled: bool) -> bool:
        """Return whether a category is still inside its TTL."""
        if not enabled or ttl == 0:
            return False
        cursor = await self._db().execute(
            """
            SELECT fetched_at, complete FROM cache_entries
            WHERE repo = ? AND category = ?
            """,
            (repo, category),
        )
        row = await cursor.fetchone()
        if row is None or not row["complete"]:
            return False
        fetched = _parse(str(row["fetched_at"]))
        if fetched is None:
            return False
        return utc_now() - fetched < timedelta(seconds=ttl)

    async def mark_fresh(self, repo: str, category: str, *, complete: bool) -> None:
        """Record a fetch time."""
        await self._db().execute(
            """
            INSERT INTO cache_entries (repo, category, fetched_at, complete)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(repo, category) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                complete = excluded.complete
            """,
            (repo, category, _iso(utc_now()), int(complete)),
        )
        await self._db().commit()

    async def save_repository(
        self,
        *,
        repo: str,
        description: str | None,
        visibility: str | None,
        status: str | None,
        created_at: str | None,
        updated_at: str | None,
        pull_count: int | None,
        star_count: int | None,
        web_url: str,
        error: str | None,
    ) -> None:
        """Store the latest metadata without turning missing numbers into zero."""
        await self._db().execute(
            """
            INSERT INTO repositories (
                repo, description, visibility, status, created_at, updated_at,
                pull_count, star_count, web_url, metadata_at, metadata_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo) DO UPDATE SET
                description = excluded.description,
                visibility = excluded.visibility,
                status = excluded.status,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                pull_count = excluded.pull_count,
                star_count = excluded.star_count,
                web_url = excluded.web_url,
                metadata_at = excluded.metadata_at,
                metadata_error = excluded.metadata_error
            """,
            (
                repo,
                description,
                visibility,
                status,
                created_at,
                updated_at,
                pull_count,
                star_count,
                web_url,
                _iso(utc_now()),
                error,
            ),
        )
        await self._db().commit()

    async def add_snapshot(
        self,
        repo: str,
        pull_count: int | None,
        star_count: int | None,
    ) -> bool:
        """
        Record a statistics observation.

        Returns:
            True when the new pull count is lower than the previous one.

        """
        previous = await self.latest_snapshot(repo)
        anomaly = False
        if (
            previous is not None
            and previous.pull_count is not None
            and pull_count is not None
            and pull_count < previous.pull_count
        ):
            anomaly = True
        await self._db().execute(
            """
            INSERT INTO repository_snapshots
                (repo, observed_at, pull_count, star_count, anomaly)
            VALUES (?, ?, ?, ?, ?)
            """,
            (repo, _iso(utc_now()), pull_count, star_count, int(anomaly)),
        )
        await self._db().commit()
        return anomaly

    async def latest_snapshot(self, repo: str) -> Snapshot | None:
        """Return the newest statistics observation."""
        rows = await self.snapshots(repo)
        if not rows:
            return None
        return rows[-1]

    async def snapshots(self, repo: str) -> list[Snapshot]:
        """Return statistics observations from oldest to newest."""
        cursor = await self._db().execute(
            """
            SELECT observed_at, pull_count, star_count, anomaly
            FROM repository_snapshots WHERE repo = ? ORDER BY observed_at
            """,
            (repo,),
        )
        rows = await cursor.fetchall()
        return [
            Snapshot(
                observed_at=_parse(str(row["observed_at"])) or utc_now(),
                pull_count=row["pull_count"],
                star_count=row["star_count"],
                anomaly=bool(row["anomaly"]),
            )
            for row in rows
        ]

    async def repository_row(self, repo: str) -> dict[str, object] | None:
        """Return the cached metadata row."""
        cursor = await self._db().execute(
            "SELECT * FROM repositories WHERE repo = ?", (repo,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def tag_map(self, repo: str) -> dict[str, str | None]:
        """Return the latest digest for each known tag."""
        cursor = await self._db().execute(
            "SELECT name, digest FROM tags WHERE repo = ?",
            (repo,),
        )
        rows = await cursor.fetchall()
        return {str(row["name"]): row["digest"] for row in rows}

    async def tags_for(self, repo: str) -> list[StoredTag]:
        """Return the latest tag rows."""
        cursor = await self._db().execute(
            "SELECT * FROM tags WHERE repo = ? ORDER BY name",
            (repo,),
        )
        rows = await cursor.fetchall()
        result: list[StoredTag] = []
        for row in rows:
            platforms = json.loads(row["platforms"] or "[]")
            result.append(
                StoredTag(
                    name=str(row["name"]),
                    updated_at=row["updated_at"],
                    full_size=row["full_size"],
                    digest=row["digest"],
                    media_type=row["media_type"],
                    platforms=tuple(platforms),
                )
            )
        return result

    async def replace_tags(
        self,
        repo: str,
        tags: list[StoredTag],
        *,
        complete: bool,
    ) -> None:
        """Upsert tags. Delete missing tags only after a complete listing."""
        for tag in tags:
            await self._db().execute(
                """
                INSERT INTO tags
                    (repo, name, updated_at, full_size, digest, media_type, platforms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo, name) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    full_size = excluded.full_size,
                    digest = excluded.digest,
                    media_type = excluded.media_type,
                    platforms = excluded.platforms
                """,
                (
                    repo,
                    tag.name,
                    tag.updated_at,
                    tag.full_size,
                    tag.digest,
                    tag.media_type,
                    json.dumps(list(tag.platforms)),
                ),
            )
        if complete:
            names = {tag.name for tag in tags}
            cursor = await self._db().execute(
                "SELECT name FROM tags WHERE repo = ?",
                (repo,),
            )
            stored = [str(row["name"]) for row in await cursor.fetchall()]
            for name in stored:
                if name not in names:
                    await self._db().execute(
                        "DELETE FROM tags WHERE repo = ? AND name = ?",
                        (repo, name),
                    )
        await self._db().commit()

    async def add_events(
        self, repo: str, events: list[tuple[str, str, str | None, str | None]]
    ) -> None:
        """Store tag change events."""
        moment = _iso(utc_now())
        await self._db().executemany(
            """
            INSERT INTO tag_events
                (repo, name, observed_at, change, old_digest, new_digest)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (repo, name, moment, change, old, new)
                for name, change, old, new in events
            ],
        )
        await self._db().commit()

    async def events(
        self,
        *,
        repo: str | None = None,
        change: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, object]]:
        """Return change events, newest first."""
        values: list[object] = []
        if repo is not None:
            values.append(repo)
        if change is not None:
            values.append(change)
        if since is not None:
            values.append(_iso(since))
        key = (repo is not None, change is not None, since is not None)
        statement = _EVENT_QUERIES[key]
        cursor = await self._db().execute(statement, values)
        return [dict(row) for row in await cursor.fetchall()]

    async def tag_listing_was_complete(self, repo: str) -> bool:
        """Return whether the previous tag fetch finished."""
        cursor = await self._db().execute(
            """
            SELECT complete FROM cache_entries
            WHERE repo = ? AND category = 'tags'
            """,
            (repo,),
        )
        row = await cursor.fetchone()
        return row is not None and bool(row["complete"])

    async def start_run(self) -> int:
        """Open a synchronisation session."""
        cursor = await self._db().execute(
            "INSERT INTO sync_runs (started_at, status) VALUES (?, 'running')",
            (_iso(utc_now()),),
        )
        await self._db().commit()
        return int(cursor.lastrowid or 0)

    async def finish_run(self, run_id: int, status: str) -> None:
        """Close a synchronisation session."""
        await self._db().execute(
            "UPDATE sync_runs SET finished_at = ?, status = ? WHERE id = ?",
            (_iso(utc_now()), status, run_id),
        )
        await self._db().commit()

    async def add_result(
        self,
        run_id: int,
        repo: str,
        check_name: str,
        status: str,
        error: str | None,
    ) -> None:
        """Record one check outcome."""
        await self._db().execute(
            """
            INSERT INTO sync_results (run_id, repo, check_name, status, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, repo, check_name, status, error),
        )
        await self._db().commit()

    async def record_error(self, repo: str, error: str) -> None:
        """Keep the last good metadata and store the latest error beside it."""
        await self._db().execute(
            """
            INSERT INTO repositories (repo, web_url, metadata_error)
            VALUES (?, '', ?)
            ON CONFLICT(repo) DO UPDATE SET metadata_error = excluded.metadata_error
            """,
            (repo, error),
        )
        await self._db().commit()

    async def apply_retention(self, days: int) -> None:
        """Delete old snapshots except the newest row for each repository."""
        if days <= 0:
            return
        cutoff = _iso(utc_now() - timedelta(days=days))
        await self._db().execute(
            """
            DELETE FROM repository_snapshots
            WHERE observed_at < ?
              AND id NOT IN (
                SELECT MAX(id) FROM repository_snapshots GROUP BY repo
              )
            """,
            (cutoff,),
        )
        await self._db().commit()

    async def replace_manifests(self, repo: str, tags: list[StoredTag]) -> None:
        """Replace stored manifest rows for one repository."""
        moment = _iso(utc_now())
        await self._db().execute(
            "DELETE FROM manifest_snapshots WHERE repo = ?", (repo,)
        )
        await self._db().executemany(
            """
            INSERT INTO manifest_snapshots (
                repo, tag, observed_at, digest, platforms, media_type, full_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    repo,
                    tag.name,
                    moment,
                    tag.digest,
                    json.dumps(list(tag.platforms)),
                    tag.media_type,
                    tag.full_size,
                )
                for tag in tags
            ],
        )
        await self._db().commit()

    async def clear(self) -> None:
        """Delete collected rows. The schema stays."""
        for statement in (
            "DELETE FROM sync_results",
            "DELETE FROM sync_runs",
            "DELETE FROM tag_events",
            "DELETE FROM manifest_snapshots",
            "DELETE FROM tags",
            "DELETE FROM repository_snapshots",
            "DELETE FROM repositories",
            "DELETE FROM cache_entries",
        ):
            await self._db().execute(statement)
        await self._db().commit()

    async def vacuum(self) -> None:
        """Reclaim free pages."""
        await self._db().execute("VACUUM")

    async def counts(self) -> dict[str, int]:
        """Return row counts used by cache info."""
        result: dict[str, int] = {}
        statements = {
            "repositories": "SELECT COUNT(*) AS n FROM repositories",
            "repository_snapshots": "SELECT COUNT(*) AS n FROM repository_snapshots",
            "tags": "SELECT COUNT(*) AS n FROM tags",
            "tag_events": "SELECT COUNT(*) AS n FROM tag_events",
        }
        for table, statement in statements.items():
            cursor = await self._db().execute(statement)
            row = await cursor.fetchone()
            result[table] = int(row["n"]) if row is not None else 0
        return result
