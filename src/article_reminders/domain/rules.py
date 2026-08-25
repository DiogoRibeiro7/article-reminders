"""The rules of the research workflow.

Pure functions over the domain vocabulary: which transitions are canonical, which
statuses count as active, when a stage has gone stale, and when a paper needs
attention. Nothing here reads a file, a clock, or a network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from article_reminders.domain.enums import BoardColumn, LifecycleStatus
from article_reminders.domain.models import Paper
from article_reminders.domain.timeutils import days_between

S = LifecycleStatus

#: The canonical path from an idea to a publication. Everything else is a move a
#: researcher has to ask for explicitly, which keeps the graph readable without
#: making it a cage.
CANONICAL_TRANSITIONS: Mapping[LifecycleStatus, frozenset[LifecycleStatus]] = {
    S.IDEA: frozenset({S.PLANNED, S.RESEARCH}),
    S.PLANNED: frozenset({S.RESEARCH, S.DATA_COLLECTION, S.IDEA}),
    S.RESEARCH: frozenset({S.DATA_COLLECTION, S.ANALYSIS, S.PLANNED}),
    S.DATA_COLLECTION: frozenset({S.ANALYSIS, S.RESEARCH}),
    S.ANALYSIS: frozenset({S.DRAFT, S.DATA_COLLECTION, S.RESEARCH}),
    S.DRAFT: frozenset({S.INTERNAL_REVIEW, S.READY_TO_SUBMIT, S.ANALYSIS}),
    S.INTERNAL_REVIEW: frozenset({S.READY_TO_SUBMIT, S.DRAFT}),
    S.READY_TO_SUBMIT: frozenset({S.SUBMITTED, S.DRAFT, S.INTERNAL_REVIEW}),
    S.SUBMITTED: frozenset({S.UNDER_REVIEW, S.REVISION, S.ACCEPTED, S.READY_TO_SUBMIT}),
    S.UNDER_REVIEW: frozenset({S.REVISION, S.ACCEPTED, S.DRAFT}),
    S.REVISION: frozenset({S.RESUBMITTED, S.DRAFT, S.ANALYSIS}),
    S.RESUBMITTED: frozenset({S.UNDER_REVIEW, S.ACCEPTED, S.REVISION}),
    S.ACCEPTED: frozenset({S.PUBLISHED}),
    S.PUBLISHED: frozenset(),
    S.PAUSED: frozenset(),
    S.ABANDONED: frozenset(),
}

#: Reachable from anywhere: a paper can always be shelved or given up on, and a
#: shelved paper can always be picked up again wherever it left off.
ALWAYS_AVAILABLE: frozenset[LifecycleStatus] = frozenset({S.PAUSED, S.ABANDONED})

#: Statuses that still need the researcher's attention at some point.
ACTIVE_STATUSES: frozenset[LifecycleStatus] = frozenset(
    {
        S.IDEA,
        S.PLANNED,
        S.RESEARCH,
        S.DATA_COLLECTION,
        S.ANALYSIS,
        S.DRAFT,
        S.INTERNAL_REVIEW,
        S.READY_TO_SUBMIT,
        S.SUBMITTED,
        S.UNDER_REVIEW,
        S.REVISION,
        S.RESUBMITTED,
        S.ACCEPTED,
    }
)

#: Active, but the ball is in somebody else's court. A paper sitting with an editor
#: for six weeks is not stalled, and a next action is not owed for it.
WAITING_STATUSES: frozenset[LifecycleStatus] = frozenset(
    {S.SUBMITTED, S.UNDER_REVIEW, S.RESUBMITTED}
)

#: Statuses that imply a manuscript should exist.
WRITING_STATUSES: frozenset[LifecycleStatus] = frozenset(
    {S.DRAFT, S.INTERNAL_REVIEW, S.READY_TO_SUBMIT, S.REVISION}
)

#: Statuses that imply analysis work rather than writing.
ANALYSIS_STATUSES: frozenset[LifecycleStatus] = frozenset(
    {S.RESEARCH, S.DATA_COLLECTION, S.ANALYSIS}
)

BOARD_COLUMNS: Mapping[LifecycleStatus, BoardColumn] = {
    S.IDEA: BoardColumn.IDEAS,
    S.PLANNED: BoardColumn.IDEAS,
    S.RESEARCH: BoardColumn.RESEARCH,
    S.DATA_COLLECTION: BoardColumn.RESEARCH,
    S.ANALYSIS: BoardColumn.ANALYSIS,
    S.DRAFT: BoardColumn.WRITING,
    S.INTERNAL_REVIEW: BoardColumn.WRITING,
    S.READY_TO_SUBMIT: BoardColumn.SUBMISSION_READY,
    S.SUBMITTED: BoardColumn.UNDER_REVIEW,
    S.UNDER_REVIEW: BoardColumn.UNDER_REVIEW,
    S.RESUBMITTED: BoardColumn.UNDER_REVIEW,
    S.REVISION: BoardColumn.REVISION,
    S.ACCEPTED: BoardColumn.ACCEPTED,
    S.PUBLISHED: BoardColumn.PUBLISHED,
    S.PAUSED: BoardColumn.PAUSED,
    S.ABANDONED: BoardColumn.PAUSED,
}

#: The status a board column drops a paper into when it is dragged there. A column
#: covering several statuses picks the one that column is named after.
COLUMN_DEFAULT_STATUS: Mapping[BoardColumn, LifecycleStatus] = {
    BoardColumn.IDEAS: S.IDEA,
    BoardColumn.RESEARCH: S.RESEARCH,
    BoardColumn.ANALYSIS: S.ANALYSIS,
    BoardColumn.WRITING: S.DRAFT,
    BoardColumn.SUBMISSION_READY: S.READY_TO_SUBMIT,
    BoardColumn.UNDER_REVIEW: S.UNDER_REVIEW,
    BoardColumn.REVISION: S.REVISION,
    BoardColumn.ACCEPTED: S.ACCEPTED,
    BoardColumn.PUBLISHED: S.PUBLISHED,
    BoardColumn.PAUSED: S.PAUSED,
}

#: Days of silence after which a stage is stale, by stage. Overridden by settings;
#: these are the defaults, and they differ by an order of magnitude on purpose.
DEFAULT_STALENESS_DAYS: Mapping[LifecycleStatus, int] = {
    S.IDEA: 90,
    S.PLANNED: 60,
    S.RESEARCH: 30,
    S.DATA_COLLECTION: 21,
    S.ANALYSIS: 21,
    S.DRAFT: 14,
    S.INTERNAL_REVIEW: 21,
    S.READY_TO_SUBMIT: 14,
    S.SUBMITTED: 120,
    S.UNDER_REVIEW: 180,
    S.REVISION: 7,
    S.RESUBMITTED: 120,
    S.ACCEPTED: 60,
}

#: The timestamp each status sets when a paper first reaches it.
STATUS_TIMESTAMP_FIELD: Mapping[LifecycleStatus, str] = {
    S.RESEARCH: "started_at",
    S.DATA_COLLECTION: "started_at",
    S.DRAFT: "draft_started_at",
    S.SUBMITTED: "submitted_at",
    S.ACCEPTED: "accepted_at",
    S.PUBLISHED: "published_at",
}


def is_active_status(status: LifecycleStatus) -> bool:
    """Whether a paper in this status is still in flight."""
    return status in ACTIVE_STATUSES


def is_waiting_status(status: LifecycleStatus) -> bool:
    """Whether this status means somebody else owes the next move."""
    return status in WAITING_STATUSES


def board_column_for(status: LifecycleStatus) -> BoardColumn:
    """Which Kanban column a status belongs in."""
    return BOARD_COLUMNS[status]


def allowed_transitions(status: LifecycleStatus) -> frozenset[LifecycleStatus]:
    """Every status reachable from ``status`` without an explicit override."""
    forward = CANONICAL_TRANSITIONS.get(status, frozenset())
    if status in ALWAYS_AVAILABLE:
        # Resuming shelved work: back to anything still active.
        return frozenset(ACTIVE_STATUSES)
    return frozenset(forward | (ALWAYS_AVAILABLE - {status}))


def is_transition_allowed(source: LifecycleStatus, target: LifecycleStatus) -> bool:
    """Whether ``source -> target`` is canonical.

    A no-op transition counts as allowed so that re-saving a paper is never an
    error.
    """
    if source is target:
        return True
    return target in allowed_transitions(source)


def requires_next_action(paper: Paper) -> bool:
    """Whether this paper owes the researcher one concrete next action.

    Active work does; work parked with an editor, a co-author, or a data provider
    does not, and neither does a paused or published paper.
    """
    if not is_active_status(paper.status):
        return False
    if paper.waiting_for:
        return False
    return not is_waiting_status(paper.status)


def staleness_threshold_days(
    status: LifecycleStatus, overrides: Mapping[LifecycleStatus, int] | None = None
) -> int | None:
    """Days of silence tolerated in this stage, or ``None`` if the stage is untimed."""
    if overrides is not None and status in overrides:
        return overrides[status]
    return DEFAULT_STALENESS_DAYS.get(status)


@dataclass(frozen=True, slots=True)
class StalenessVerdict:
    """Why a paper is (or is not) considered stale."""

    is_stale: bool
    days_since_activity: float | None
    threshold_days: int | None
    last_activity_at: datetime | None

    @property
    def days_overdue(self) -> float | None:
        if self.days_since_activity is None or self.threshold_days is None:
            return None
        return self.days_since_activity - self.threshold_days


def evaluate_staleness(
    paper: Paper,
    reference: datetime,
    overrides: Mapping[LifecycleStatus, int] | None = None,
) -> StalenessVerdict:
    """Compare a paper's silence against the tolerance of its own stage.

    A paper waiting on a journal for 45 days and a draft untouched for 45 days are
    not the same situation, so the threshold comes from the stage rather than from
    a single global number.
    """
    threshold = staleness_threshold_days(paper.status, overrides)
    last_activity = paper.last_activity_at()
    if threshold is None or last_activity is None:
        return StalenessVerdict(False, None, threshold, last_activity)

    elapsed = days_between(last_activity, reference)
    return StalenessVerdict(elapsed > threshold, elapsed, threshold, last_activity)


def needs_attention_reasons(
    paper: Paper,
    reference: datetime,
    overrides: Mapping[LifecycleStatus, int] | None = None,
) -> tuple[str, ...]:
    """Short, human-readable reasons this paper is a workflow problem.

    The dashboard's "needs attention" bucket is exactly the set of papers for which
    this returns something.
    """
    reasons: list[str] = []

    if requires_next_action(paper) and paper.next_action is None:
        reasons.append("No next action defined.")

    due = paper.next_action_due_at
    if due is not None and due < reference:
        reasons.append("The next action is overdue.")

    if paper.revision_due_at is not None and paper.revision_due_at < reference:
        reasons.append("The revision deadline has passed.")

    if paper.status is S.READY_TO_SUBMIT and paper.venue is None:
        reasons.append("Marked ready to submit with no target venue.")

    verdict = evaluate_staleness(paper, reference, overrides)
    if verdict.is_stale and verdict.days_since_activity is not None:
        reasons.append(
            f"No activity for {verdict.days_since_activity:.0f} days "
            f"in the {paper.status.label.lower()} stage."
        )

    return tuple(reasons)
