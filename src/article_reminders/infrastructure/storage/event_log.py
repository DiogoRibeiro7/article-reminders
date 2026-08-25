"""Append-only event log, stored as JSON Lines.

One event per line, never rewritten. JSONL rather than JSON because appending is
the only write this file ever needs, and a corrupt tail costs one event rather
than the whole history.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from article_reminders.domain.errors import ValidationError
from article_reminders.domain.events import ProjectEvent
from article_reminders.domain.ids import PaperId

logger = logging.getLogger(__name__)


class JsonlEventLog:
    """Project events on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: ProjectEvent) -> ProjectEvent:
        self.extend([event])
        return event

    def extend(self, events: Iterable[ProjectEvent]) -> int:
        """Append several events in one open; returns how many were written."""
        batch = list(events)
        if not batch:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            for event in batch:
                stream.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        logger.debug("appended %d events to %s", len(batch), self.path)
        return len(batch)

    def all(self) -> list[ProjectEvent]:
        """Every event, oldest first."""
        if not self.path.exists():
            return []
        events: list[ProjectEvent] = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            text = line.strip()
            if not text:
                continue
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValidationError(f"{self.path}:{number} is not valid JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise ValidationError(f"{self.path}:{number} must be an object.")
            events.append(ProjectEvent.from_dict(raw))
        events.sort(key=lambda event: event.occurred_at)
        return events

    def for_project(self, project_id: PaperId) -> list[ProjectEvent]:
        """Every event for one paper, oldest first."""
        return [event for event in self.all() if event.project_id == project_id]
