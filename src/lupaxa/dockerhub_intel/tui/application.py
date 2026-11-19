"""
Textual interface.

The widgets call the application service. They do not call Docker Hub themselves.
"""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from lupaxa.dockerhub_intel.app import Service
from lupaxa.dockerhub_intel.errors import ConfigError, HubError
from lupaxa.dockerhub_intel.reports.progress import status_line
from lupaxa.dockerhub_intel.reports.render import (
    column_heading,
    paint_cell,
    table_fields,
)

__all__ = ["Dashboard", "RepoDetail", "run_dashboard"]


class RepoDetail(ModalScreen[None]):
    """Popup of one repository. Escape closes it."""

    BINDINGS = [Binding("escape", "dismiss", "Close", priority=True)]

    def __init__(self, shown: dict[str, object]) -> None:
        """Store the repository record."""
        super().__init__()
        self.shown = shown

    def compose(self) -> ComposeResult:
        """Build the field and value list."""
        title = str(self.shown.get("repository") or "")
        with VerticalScroll(id="dialog"):
            yield Static(title, id="detail-title")
            with Vertical(id="detail"):
                for field, value in _detail_pairs(self.shown):
                    with Horizontal(classes="detail-row"):
                        yield Static(field, classes="detail-field")
                        yield Static(value, classes="detail-value")


class Dashboard(App[None]):
    """Repository browser backed by the local cache."""

    TITLE = "Docker Hub Intel"
    CSS = """
    #summary { height: auto; padding: 0 1; }
    #search { margin: 0 1; }
    DataTable { height: 1fr; }
    DataTable > .datatable--header {
        color: cyan;
        text-style: bold;
    }
    DataTable > .datatable--header-cursor {
        color: cyan;
    }
    RepoDetail { align: center middle; }
    #dialog {
        width: 90%;
        max-width: 120;
        height: auto;
        max-height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
        overflow-x: hidden;
    }
    #detail-title { text-style: bold; padding: 0 0 1 0; height: auto; }
    #detail { height: auto; }
    .detail-row { height: auto; width: 100%; }
    .detail-field { width: 20; height: auto; color: cyan; text-style: bold; }
    .detail-value { width: 1fr; height: auto; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", key_display="control + q", priority=True),
        Binding("r", "sync", "Sync"),
        Binding("slash", "search", "Search"),
        Binding("p", "profiles", "Profiles"),
        Binding("c", "config", "Config"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, service: Service) -> None:
        """Remember the service. The database is opened when the interface starts."""
        super().__init__()
        self.service = service
        self._rows: list[dict[str, object]] = []
        self._fields: list[str] = []
        self._mode = "repos"
        self._loaded = False
        self._sync_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        """Build the header, summary, search box, table, and footer."""
        yield Header()
        yield Static("Loading cache…", id="summary")
        yield Input(placeholder="Search repositories...", id="search")
        yield DataTable(id="table")
        yield Footer()

    async def on_mount(self) -> None:
        """Load cached repositories without contacting Docker Hub."""
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        try:
            await self.service.open()
        except ConfigError as exc:
            self._summary(str(exc))
            return
        await self._reload()
        self._loaded = True
        table.focus()

    async def on_unmount(self) -> None:
        """Close the database when the interface exits."""
        await self.service.close()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the repository table as the search box changes."""
        if self._mode == "repos":
            self._fill(event.value)

    def action_search(self) -> None:
        """Focus the search box."""
        self.query_one("#search", Input).focus()

    async def action_sync(self) -> None:
        """Start synchronisation without blocking the interface."""
        if not self._loaded:
            return
        if self._sync_task is not None and not self._sync_task.done():
            return
        self._sync_task = asyncio.create_task(self._run_sync())

    def action_cancel(self) -> None:
        """Cancel synchronisation, or return to the repository table."""
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
            return
        if self._mode != "repos":
            self._show_repositories()
            return
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""

    def action_profiles(self) -> None:
        """Show profiles and how many repositories use each one."""
        if not self._loaded or self.service.loaded is None:
            return
        self._mode = "profiles"
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("Profile", "Extends", "Repositories")
        counts: dict[str, int] = {}
        for row in self._rows:
            name = str(row.get("profile") or "")
            counts[name] = counts.get(name, 0) + 1
        for item in self.service.profile_rows():
            profile = str(item["profile"])
            table.add_row(profile, str(item["extends"]), str(counts.get(profile, 0)))
        self._summary("Profiles. Press escape to return.")

    def action_config(self) -> None:
        """Show configuration paths. The token is not included."""
        loaded = self.service.loaded
        database = self.service.database
        if loaded is None or database is None:
            return
        settings = loaded.app
        root = loaded.path.parent
        self._summary(
            "\n".join(
                [
                    f"Config: {loaded.path}",
                    f"Profiles: {root / settings.files.profiles}",
                    f"Repositories: {root / settings.files.repositories}",
                    f"Concurrency: {settings.sync.concurrency}",
                    f"Token variable: {settings.dockerhub.token_env}",
                    f"Cache: {database.path}",
                    "",
                    "Press control + q to quit.",
                ]
            )
        )

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open the selected repository."""
        if self._mode != "repos" or not self._loaded:
            return
        repo = str(event.row_key.value)
        try:
            shown = await self.service.show(repo)
        except HubError as exc:
            self.notify(str(exc), severity="error")
            return
        self.push_screen(RepoDetail(shown))

    async def _run_sync(self) -> None:
        if self.service.offline:
            self._summary("Offline mode does not synchronise.")
            return
        self._summary("Synchronising…")
        try:
            report = await self.service.sync(
                self.service.repositories(enabled_only=True),
                force=False,
                concurrency=None,
                on_progress=lambda item: self._summary(status_line(item)),
            )
        except asyncio.CancelledError:
            await self._reload()
            self._summary("Synchronisation cancelled.")
            return
        except ConfigError as exc:
            self._summary(str(exc))
            return
        await self._reload()
        self._summary(f"Successful {report.succeeded}  Failed {report.failed}")

    async def _reload(self) -> None:
        self._rows = await self.service.repo_rows(self.service.repositories())
        self._show_repositories()

    def _show_repositories(self) -> None:
        self._mode = "repos"
        self._fields = table_fields(self._rows)
        table = self.query_one(DataTable)
        table.clear(columns=True)
        for field in self._fields:
            table.add_column(column_heading(field), key=field)
        self._fill(self.query_one("#search", Input).value)
        self._summary(_counts(self._rows))

    def _fill(self, query: str) -> None:
        folded = query.casefold()
        table = self.query_one(DataTable)
        table.clear()
        rows = sorted(
            self._rows,
            key=lambda row: str(row.get("repository") or "").casefold(),
        )
        for row in rows:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in ("repository", "group", "profile", "status", "stale")
            )
            if folded and folded not in haystack.casefold():
                continue
            cells = [paint_cell(field, row) for field in self._fields]
            table.add_row(*cells, key=str(row.get("repository") or ""))

    def _summary(self, text: str) -> None:
        self.query_one("#summary", Static).update(text)


def run_dashboard(service: Service) -> None:
    """Open the dashboard. The caller has not left an event loop running."""
    Dashboard(service).run()


def _counts(rows: list[dict[str, object]]) -> str:
    total = len(rows)
    stale = sum(1 for row in rows if row.get("stale") == "Stale")
    return f"Repositories {total}    Stale {stale}"


def _painted(key: str, row: dict[str, object]) -> str | Text:
    return paint_cell(key, row)


def _plain(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def _detail_pairs(shown: dict[str, object]) -> list[tuple[str, str]]:
    growth = shown.get("growth")
    tags = shown.get("tags")
    labels = shown.get("labels")
    pairs = [
        ("Repository", _plain(shown.get("repository"))),
        ("URL", _plain(shown.get("url"))),
        ("Group", _plain(shown.get("group"))),
        ("Profile", _plain(shown.get("profile"))),
        ("Labels", ", ".join(labels) if isinstance(labels, list) else ""),
        ("Enabled", _plain(shown.get("enabled"))),
        ("Description", _plain(shown.get("description"))),
        ("Visibility", _plain(shown.get("visibility"))),
        ("Status", _plain(shown.get("status"))),
        ("Pulls", _plain(shown.get("pulls"))),
        ("Stars", _plain(shown.get("stars"))),
        ("Updated", _plain(shown.get("updated"))),
    ]
    if isinstance(growth, dict):
        for name in ("previous", "24h", "7d", "30d", "90d"):
            pairs.append((f"Growth {name}", _plain(growth.get(name))))
    pairs.append(("Stale", _plain(shown.get("stale"))))
    pairs.append(("Tags", ", ".join(tags) if isinstance(tags, list) else ""))
    pairs.append(("Error", _plain(shown.get("error"))))
    return [(field, value) for field, value in pairs if value]
