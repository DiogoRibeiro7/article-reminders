"""The controlled vocabularies of the research lifecycle.

Every enum here is a :class:`StrEnum`, so values serialise to plain strings and a
JSON portfolio file stays readable without the application.
"""

from __future__ import annotations

from enum import StrEnum


class LifecycleStatus(StrEnum):
    """Where a paper sits in the idea-to-publication lifecycle."""

    IDEA = "idea"
    PLANNED = "planned"
    RESEARCH = "research"
    DATA_COLLECTION = "data_collection"
    ANALYSIS = "analysis"
    DRAFT = "draft"
    INTERNAL_REVIEW = "internal_review"
    READY_TO_SUBMIT = "ready_to_submit"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    REVISION = "revision"
    RESUBMITTED = "resubmitted"
    ACCEPTED = "accepted"
    PUBLISHED = "published"
    PAUSED = "paused"
    ABANDONED = "abandoned"

    @property
    def label(self) -> str:
        """Human-facing name, for tables and templates."""
        return self.value.replace("_", " ").capitalize()


class Priority(StrEnum):
    """How much the researcher cares, independent of urgency."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Sort key: higher is more important."""
        return _PRIORITY_RANK[self]

    @property
    def label(self) -> str:
        return self.value.capitalize()


_PRIORITY_RANK: dict[Priority, int] = {
    Priority.LOW: 0,
    Priority.MEDIUM: 1,
    Priority.HIGH: 2,
    Priority.CRITICAL: 3,
}


class BoardColumn(StrEnum):
    """Kanban columns.

    Deliberately coarser than :class:`LifecycleStatus`: sixteen columns is a
    spreadsheet, not a board.
    """

    IDEAS = "ideas"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    WRITING = "writing"
    SUBMISSION_READY = "submission_ready"
    UNDER_REVIEW = "under_review"
    REVISION = "revision"
    ACCEPTED = "accepted"
    PUBLISHED = "published"
    PAUSED = "paused"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class ActivityKind(StrEnum):
    """What a changed path in a research repository represents.

    Classification is path-based and deterministic; commit messages are evidence
    of nothing in particular.
    """

    MANUSCRIPT = "manuscript"
    ANALYSIS = "analysis"
    DATA = "data"
    OTHER = "other"


class ReminderKind(StrEnum):
    """Why the engine is telling the researcher something."""

    DEADLINE_UPCOMING = "deadline_upcoming"
    DEADLINE_OVERDUE = "deadline_overdue"
    MISSING_NEXT_ACTION = "missing_next_action"
    MISSING_TARGET_VENUE = "missing_target_venue"
    ANALYSIS_WITHOUT_DRAFT = "analysis_without_draft"
    MANUSCRIPT_STAGNATION = "manuscript_stagnation"
    MANUSCRIPT_INACTIVITY = "manuscript_inactivity"
    REPOSITORY_INACTIVITY = "repository_inactivity"
    PROJECT_INACTIVITY = "project_inactivity"
    STAGE_STALE = "stage_stale"
    AWAITING_EXTERNAL = "awaiting_external"


class ReminderSeverity(StrEnum):
    """How loudly a reminder should be shown."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @property
    def label(self) -> str:
        return self.value.capitalize()


_SEVERITY_RANK: dict[ReminderSeverity, int] = {
    ReminderSeverity.INFO: 0,
    ReminderSeverity.WARNING: 1,
    ReminderSeverity.CRITICAL: 2,
}


class DecisionOutcome(StrEnum):
    """What a venue said about a submission."""

    PENDING = "pending"
    DESK_REJECT = "desk_reject"
    REJECT = "reject"
    MAJOR_REVISION = "major_revision"
    MINOR_REVISION = "minor_revision"
    ACCEPT = "accept"
    WITHDRAWN = "withdrawn"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


class ProjectEventType(StrEnum):
    """Append-only history of what happened to a paper."""

    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    STATUS_CHANGED = "status_changed"
    NEXT_ACTION_CHANGED = "next_action_changed"
    NEXT_ACTION_COMPLETED = "next_action_completed"
    DEADLINE_CHANGED = "deadline_changed"
    REPOSITORY_ACTIVITY_DETECTED = "repository_activity_detected"
    MANUSCRIPT_ACTIVITY_DETECTED = "manuscript_activity_detected"
    ANALYSIS_ACTIVITY_DETECTED = "analysis_activity_detected"
    SUBMISSION_RECORDED = "submission_recorded"
    DECISION_RECORDED = "decision_recorded"
    REVISION_REQUESTED = "revision_requested"
    PAPER_ACCEPTED = "paper_accepted"
    PAPER_PUBLISHED = "paper_published"
    ISSUE_SYNCHRONISED = "issue_synchronised"
    MIGRATED_FROM_LEGACY = "migrated_from_legacy"
    NOTE_ADDED = "note_added"
