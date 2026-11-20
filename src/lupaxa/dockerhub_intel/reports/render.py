"""JSON, CSV, and Markdown renderers. Human text uses a Rich table."""

from __future__ import annotations

from collections.abc import Sequence
import csv
import io
import json
from typing import IO, Literal

from rich.console import Console
from rich.table import Table
from rich.text import Text

__all__ = ["column_heading", "paint_cell", "render", "table_fields", "write_text"]

_RIGHT = frozenset({"pulls", "stars", "size", "rows", "percent", "growth"})
_LABELS = {
    "repository": "Repository",
    "visibility": "Visibility",
    "group": "Group",
    "profile": "Profile",
    "enabled": "Enabled",
    "pulls": "Pulls",
    "stars": "Stars",
    "updated": "Updated",
    "status": "Status",
    "stale": "Stale",
    "error": "Error",
    "tag": "Tag",
    "digest": "Digest",
    "platforms": "Platforms",
    "growth": "Growth",
    "percent": "Percent",
    "check": "Check",
    "result": "Result",
    "detail": "Detail",
    "setting": "Setting",
    "value": "Value",
    "path": "Path",
    "table": "Table",
    "rows": "Rows",
    "source": "Source",
    "extends": "Extends",
}


def render(rows: Sequence[dict[str, object]], fmt: str, *, title: str) -> str:
    """Render rows as json, csv, markdown, or a Rich table."""
    if fmt == "json":
        return json.dumps(list(rows), indent=2, default=str) + "\n"
    if fmt == "csv":
        return _csv(rows)
    if fmt == "markdown":
        return _markdown(rows, title)
    buffer = io.StringIO()
    write_text(rows, title, buffer)
    return buffer.getvalue()


def write_text(
    rows: Sequence[dict[str, object]],
    title: str,
    stream: IO[str],
) -> None:
    """Write a boxed table. A terminal gets colour; a file stays plain."""
    if not rows:
        stream.write("No rows.\n")
        return
    fields = table_fields(rows)
    table = Table(title=title, header_style="bold cyan")
    for field in fields:
        justify: Literal["left", "right"] = "right" if field in _RIGHT else "left"
        table.add_column(_label(field), justify=justify)
    failed = {id(row) for row in rows if _plain(row.get("error"))}
    for row in rows:
        marked = id(row) in failed
        table.add_row(*(_cell(field, row, mark_error=marked) for field in fields))
    interactive = bool(getattr(stream, "isatty", lambda: False)())
    console = Console(
        file=stream,
        force_terminal=interactive,
        width=None if interactive else _width(rows, fields),
        soft_wrap=True,
    )
    console.print(table)


def _csv(rows: Sequence[dict[str, object]]) -> str:
    buffer = io.StringIO()
    fields = list(rows[0]) if rows else ["value"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {key: "" if value is None else value for key, value in row.items()}
        )
    return buffer.getvalue()


def _markdown(rows: Sequence[dict[str, object]], title: str) -> str:
    lines = [f"# {title}", ""]
    if not rows:
        lines.append("No rows.")
        return "\n".join(lines) + "\n"
    fields = list(rows[0])
    lines.append("| " + " | ".join(fields) + " |")
    lines.append("| " + " | ".join("---" for _ in fields) + " |")
    for row in rows:
        cells = [
            "" if row.get(field) is None else str(row.get(field)) for field in fields
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def table_fields(rows: Sequence[dict[str, object]]) -> list[str]:
    """Return the columns a text table shows, omitting columns that are all empty."""
    if not rows:
        return []
    return _fields(rows)


def column_heading(field: str) -> str | Text:
    """Return the heading used by the text table, right-aligned when the values are."""
    label = _label(field)
    if field in _RIGHT:
        return Text(label, justify="right")
    return label


def paint_cell(field: str, row: dict[str, object]) -> str | Text:
    """Paint one cell with the same text and colour as the text table."""
    text = _plain(row.get(field))
    cell = _cell(field, row, mark_error=bool(_plain(row.get("error"))))
    if field not in _RIGHT or not text:
        return cell
    if isinstance(cell, Text):
        cell.justify = "right"
        return cell
    return Text(text, justify="right")


def _fields(rows: Sequence[dict[str, object]]) -> list[str]:
    fields = list(rows[0])
    visible = [field for field in fields if any(_plain(row.get(field)) for row in rows)]
    return visible or fields


def _width(rows: Sequence[dict[str, object]], fields: Sequence[str]) -> int:
    total = 0
    for field in fields:
        label = len(_label(field))
        values = max(len(_plain(row.get(field))) for row in rows)
        total += max(label, values)
    return max(80, total + 3 * len(fields) + 4)


def _cell(field: str, row: dict[str, object], *, mark_error: bool) -> str | Text:
    value = row.get(field)
    text = _plain(value)
    if field == "repository" and mark_error and text:
        return Text(text, style="red")
    style = _style(field, text)
    if style and text:
        return Text(text, style=style)
    return text


def _plain(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def _label(field: str) -> str:
    if field in _LABELS:
        return _LABELS[field]
    return field.replace("_", " ").title()


def _style(field: str, text: str) -> str:
    folded = text.strip().casefold()
    if field == "error" and text:
        return "red"
    if field == "visibility":
        return {"public": "green", "private": "magenta"}.get(folded, "")
    if field == "enabled":
        return {"yes": "green", "no": "dim"}.get(folded, "")
    if field == "stale":
        return {
            "current": "green",
            "stale": "yellow",
            "unknown": "dim",
            "skipped": "dim",
        }.get(folded, "")
    if field == "growth":
        if text.startswith("+"):
            return "green"
        if text.startswith("-"):
            return "yellow"
        return "dim"
    if field in {"status", "result"}:
        if folded in {"active", "current", "ok", "set", "yes"}:
            return "green"
        if folded in {"stale", "partial", "unavailable"}:
            return "yellow"
        if folded in {"error", "absent", "failed"}:
            return "red"
    return ""
