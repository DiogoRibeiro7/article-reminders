"""Terminal output helpers.

Plain text, aligned columns, no dependencies. Colour is used only when the output
is a terminal and ``NO_COLOR`` is unset.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime

from article_reminders.domain.enums import ReminderSeverity
from article_reminders.domain.models import Paper
from article_reminders.domain.timeutils import days_between

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
COLOURS = {
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "blue": "\033[34m",
    "grey": "\033[90m",
}

SEVERITY_COLOUR = {
    ReminderSeverity.CRITICAL: "red",
    ReminderSeverity.WARNING: "yellow",
    ReminderSeverity.INFO: "grey",
}


def supports_colour(stream: object | None = None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    target = stream or sys.stdout
    return bool(getattr(target, "isatty", lambda: False)())


def colour(text: str, name: str, *, enabled: bool | None = None) -> str:
    if enabled is None:
        enabled = supports_colour()
    if not enabled or name not in COLOURS:
        return text
    return f"{COLOURS[name]}{text}{RESET}"


def bold(text: str, *, enabled: bool | None = None) -> str:
    if enabled is None:
        enabled = supports_colour()
    return f"{BOLD}{text}{RESET}" if enabled else text


def dim(text: str, *, enabled: bool | None = None) -> str:
    if enabled is None:
        enabled = supports_colour()
    return f"{DIM}{text}{RESET}" if enabled else text


def truncate(text: str, width: int) -> str:
    """Collapse whitespace and clip to ``width``.

    ASCII only: a Windows console in cp1252 raises on a horizontal ellipsis, and
    the CLI has to work on the platform this repository is maintained from.
    """
    text = " ".join(text.split())
    if width <= 1 or len(text) <= width:
        return text
    if width <= 4:
        return text[:width]
    return text[: width - 3] + "..."


def terminal_width(default: int = 100) -> int:
    try:
        return max(60, shutil.get_terminal_size((default, 24)).columns)
    except OSError:  # pragma: no cover - environments without a terminal
        return default


def table(
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    *,
    max_widths: Sequence[int] | None = None,
) -> str:
    """Render aligned columns, truncating cells that exceed ``max_widths``."""
    materialised = [[str(cell) for cell in row] for row in rows]
    if not materialised:
        return ""
    limits = list(max_widths or [0] * len(headers))
    while len(limits) < len(headers):
        limits.append(0)

    widths: list[int] = []
    for index, header in enumerate(headers):
        longest = max([len(header), *(len(row[index]) for row in materialised)])
        widths.append(min(longest, limits[index]) if limits[index] else longest)

    def render(cells: Sequence[str]) -> str:
        return "  ".join(
            truncate(cell, widths[index]).ljust(widths[index])
            for index, cell in enumerate(cells)
        ).rstrip()

    lines = [render(headers), render(["-" * width for width in widths])]
    lines.extend(render(row) for row in materialised)
    return "\n".join(lines)


def format_date(value: datetime | None, *, empty: str = "-") -> str:
    return empty if value is None else value.date().isoformat()


def relative_days(value: datetime | None, reference: datetime) -> str:
    """``in 6 days`` / ``4 days ago`` / ``today``."""
    if value is None:
        return "-"
    delta = days_between(reference, value)
    rounded = round(delta)
    if rounded == 0:
        return "today"
    if rounded > 0:
        return f"in {rounded} day{'s' if rounded != 1 else ''}"
    return f"{abs(rounded)} day{'s' if abs(rounded) != 1 else ''} ago"


def paper_line(paper: Paper) -> str:
    """A one-line identification of a paper, for confirmations."""
    return f"{paper.title} [{paper.slug}] ({paper.status.value})"
