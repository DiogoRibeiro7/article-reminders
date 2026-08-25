"""Domain layer: the model, the vocabulary, and the rules.

Nothing here imports from ``application``, ``infrastructure``, ``cli`` or ``web``.
"""

from __future__ import annotations

from article_reminders.domain.enums import (
    ActivityKind,
    BoardColumn,
    DecisionOutcome,
    LifecycleStatus,
    Priority,
    ProjectEventType,
    ReminderKind,
    ReminderSeverity,
)
from article_reminders.domain.errors import (
    AmbiguousPaperError,
    DomainError,
    InvalidTransitionError,
    PaperNotFoundError,
    ValidationError,
)
from article_reminders.domain.events import ProjectEvent
from article_reminders.domain.ids import PaperId, Slug, new_paper_id, slugify
from article_reminders.domain.models import (
    ActivitySnapshot,
    NextAction,
    Paper,
    Reminder,
    RepositoryRef,
    StatusTransition,
    SubmissionRecord,
)

__all__ = [
    "ActivityKind",
    "ActivitySnapshot",
    "AmbiguousPaperError",
    "BoardColumn",
    "DecisionOutcome",
    "DomainError",
    "InvalidTransitionError",
    "LifecycleStatus",
    "NextAction",
    "Paper",
    "PaperId",
    "PaperNotFoundError",
    "Priority",
    "ProjectEvent",
    "ProjectEventType",
    "Reminder",
    "ReminderKind",
    "ReminderSeverity",
    "RepositoryRef",
    "Slug",
    "StatusTransition",
    "SubmissionRecord",
    "ValidationError",
    "new_paper_id",
    "slugify",
]
