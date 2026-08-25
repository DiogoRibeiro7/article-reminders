"""Append-only project history.

The primary model stays state-based: a paper is a record, not a fold over its
events. Events exist so the detail page can show what happened and the analytics
layer can measure how long each stage took.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from article_reminders.domain.enums import ProjectEventType
from article_reminders.domain.errors import ValidationError
from article_reminders.domain.ids import PaperId
from article_reminders.domain.timeutils import ensure_aware, format_datetime, parse_datetime


@dataclass(frozen=True, slots=True)
class ProjectEvent:
    """Something that happened to one paper, at one instant."""

    project_id: PaperId
    event_type: ProjectEventType
    occurred_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.project_id).strip():
            raise ValidationError("An event needs a project id.")
        object.__setattr__(
            self, "occurred_at", ensure_aware(self.occurred_at, field="event.occurred_at")
        )

    @property
    def summary(self) -> str:
        """One line describing the event, for timelines."""
        described = _SUMMARIES.get(self.event_type)
        if described is None:
            return self.event_type.value.replace("_", " ").capitalize()
        return described(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "event_type": self.event_type.value,
            "occurred_at": format_datetime(self.occurred_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProjectEvent:
        try:
            event_type = ProjectEventType(str(raw.get("event_type", "")))
        except ValueError as exc:
            raise ValidationError(f"Unknown event type {raw.get('event_type')!r}.") from exc
        metadata = raw.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise ValidationError("event.metadata must be an object.")
        occurred = raw.get("occurred_at")
        if not isinstance(occurred, (str, datetime)):
            raise ValidationError("event.occurred_at must be an ISO-8601 timestamp.")
        return cls(
            project_id=PaperId(str(raw.get("project_id", ""))),
            event_type=event_type,
            occurred_at=parse_datetime(occurred, field="event.occurred_at"),
            metadata=dict(metadata),
        )


#: How one event type turns its metadata into a sentence.
Describe = Callable[[Mapping[str, Any]], str]


def _describe_status(metadata: Mapping[str, Any]) -> str:
    source = metadata.get("from") or "nothing"
    target = metadata.get("to") or "unknown"
    forced = " (forced)" if metadata.get("forced") else ""
    return f"Status changed from {source} to {target}{forced}."


def _describe_next_action(metadata: Mapping[str, Any]) -> str:
    description = metadata.get("description")
    if not description:
        return "Next action cleared."
    due = metadata.get("due_at")
    return f"Next action set: {description}" + (f" (due {str(due)[:10]})" if due else "")


def _describe_deadline(metadata: Mapping[str, Any]) -> str:
    field_name = str(metadata.get("field", "deadline")).replace("_", " ")
    value = metadata.get("value")
    return f"{field_name} set to {str(value)[:10]}." if value else f"{field_name} cleared."


def _describe_activity(label: str) -> Describe:
    def describe(metadata: Mapping[str, Any]) -> str:
        when = str(metadata.get("at", ""))[:10]
        return f"{label} activity detected{f' on {when}' if when else ''}."

    return describe


def _describe_submission(metadata: Mapping[str, Any]) -> str:
    venue = metadata.get("venue") or "a venue"
    return f"Submitted to {venue}."


def _describe_decision(metadata: Mapping[str, Any]) -> str:
    outcome = str(metadata.get("decision", "a decision")).replace("_", " ")
    venue = metadata.get("venue")
    return f"Decision received from {venue}: {outcome}." if venue else f"Decision: {outcome}."


_SUMMARIES: Mapping[ProjectEventType, Describe] = {
    ProjectEventType.STATUS_CHANGED: _describe_status,
    ProjectEventType.NEXT_ACTION_CHANGED: _describe_next_action,
    ProjectEventType.DEADLINE_CHANGED: _describe_deadline,
    ProjectEventType.REPOSITORY_ACTIVITY_DETECTED: _describe_activity("Repository"),
    ProjectEventType.MANUSCRIPT_ACTIVITY_DETECTED: _describe_activity("Manuscript"),
    ProjectEventType.ANALYSIS_ACTIVITY_DETECTED: _describe_activity("Analysis"),
    ProjectEventType.SUBMISSION_RECORDED: _describe_submission,
    ProjectEventType.DECISION_RECORDED: _describe_decision,
}
