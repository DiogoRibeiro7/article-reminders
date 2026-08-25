"""Research pipeline analytics.

Deliberately small, and deliberately willing to say "unknown". A portfolio that
has never recorded a submission cannot report a submission-to-decision time, and
inventing one from the data that happens to be present would be worse than an
empty cell.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from article_reminders.domain.enums import DecisionOutcome, LifecycleStatus
from article_reminders.domain.events import ProjectEvent
from article_reminders.domain.models import Paper
from article_reminders.domain.rules import is_active_status
from article_reminders.domain.timeutils import days_between

S = LifecycleStatus

#: The intervals reported for the pipeline, as ``(key, label, start, end)`` where
#: start and end name attributes on :class:`Paper`.
STAGE_INTERVALS: tuple[tuple[str, str, str, str], ...] = (
    ("idea_to_draft", "Idea to draft", "created_at", "draft_started_at"),
    ("draft_to_submission", "Draft to submission", "draft_started_at", "submitted_at"),
    ("submission_to_decision", "Submission to decision", "submitted_at", "decision_received_at"),
    ("acceptance_to_publication", "Acceptance to publication", "accepted_at", "published_at"),
    ("idea_to_publication", "Idea to publication", "created_at", "published_at"),
)


@dataclass(frozen=True, slots=True)
class Duration:
    """A measured interval, or an explicit statement that it is unknown."""

    key: str
    label: str
    days: float | None

    @property
    def known(self) -> bool:
        return self.days is not None

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "days": self.days}


@dataclass(frozen=True, slots=True)
class PaperDurations:
    """Every measurable interval for one paper."""

    paper_id: str
    title: str
    durations: tuple[Duration, ...]

    def get(self, key: str) -> float | None:
        for duration in self.durations:
            if duration.key == key:
                return duration.days
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "durations": [item.to_dict() for item in self.durations],
        }


@dataclass(frozen=True, slots=True)
class Statistic:
    """A summary statistic that knows how much data it rests on."""

    key: str
    label: str
    median_days: float | None
    sample_size: int

    @property
    def available(self) -> bool:
        return self.median_days is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "median_days": self.median_days,
            "sample_size": self.sample_size,
        }


@dataclass(frozen=True, slots=True)
class PortfolioAnalytics:
    """The whole analytics page."""

    generated_at: datetime
    total: int
    active: int
    stalled: int
    paused: int
    abandoned: int
    submissions: int
    decisions: int
    acceptances: int
    publications: int
    rejections: int
    revisions_requested: int
    by_status: Mapping[str, int]
    stage_durations: tuple[Statistic, ...]
    time_in_current_stage: tuple[Statistic, ...]
    per_paper: tuple[PaperDurations, ...]

    @property
    def acceptance_rate(self) -> float | None:
        """Accepted over decided, or ``None`` when nothing has been decided."""
        decided = self.acceptances + self.rejections
        return None if decided == 0 else self.acceptances / decided

    def statistic(self, key: str) -> Statistic | None:
        for item in self.stage_durations:
            if item.key == key:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "total": self.total,
            "active": self.active,
            "stalled": self.stalled,
            "paused": self.paused,
            "abandoned": self.abandoned,
            "submissions": self.submissions,
            "decisions": self.decisions,
            "acceptances": self.acceptances,
            "publications": self.publications,
            "rejections": self.rejections,
            "revisions_requested": self.revisions_requested,
            "acceptance_rate": self.acceptance_rate,
            "by_status": dict(self.by_status),
            "stage_durations": [item.to_dict() for item in self.stage_durations],
            "time_in_current_stage": [item.to_dict() for item in self.time_in_current_stage],
        }


def paper_durations(paper: Paper) -> PaperDurations:
    """Measure every interval this paper has both endpoints for."""
    durations: list[Duration] = []
    for key, label, start_field, end_field in STAGE_INTERVALS:
        start = getattr(paper, start_field)
        end = getattr(paper, end_field)
        days: float | None = None
        if isinstance(start, datetime) and isinstance(end, datetime) and end >= start:
            days = days_between(start, end)
        durations.append(Duration(key=key, label=label, days=days))

    durations.append(
        Duration(
            key="revision_to_resubmission",
            label="Revision to resubmission",
            days=_revision_to_resubmission(paper),
        )
    )
    return PaperDurations(paper_id=str(paper.id), title=paper.title, durations=tuple(durations))


def _revision_to_resubmission(paper: Paper) -> float | None:
    """Days between a revision decision and the submission that followed it.

    Needs the submission history: the flat timestamps only ever describe the
    current round.
    """
    resolved = [item for item in paper.submissions if item.decision_at is not None]
    for index, submission in enumerate(resolved):
        if submission.decision not in (
            DecisionOutcome.MAJOR_REVISION,
            DecisionOutcome.MINOR_REVISION,
        ):
            continue
        later = [
            item
            for item in paper.submissions[index + 1 :]
            if submission.decision_at is not None and item.submitted_at >= submission.decision_at
        ]
        if later and submission.decision_at is not None:
            return days_between(submission.decision_at, later[0].submitted_at)
    return None


def _summarise(key: str, label: str, values: Sequence[float]) -> Statistic:
    if not values:
        return Statistic(key=key, label=label, median_days=None, sample_size=0)
    return Statistic(
        key=key, label=label, median_days=round(median(values), 1), sample_size=len(values)
    )


def build_analytics(
    papers: Sequence[Paper],
    reference: datetime,
    *,
    events: Iterable[ProjectEvent] = (),
    stalled_ids: Iterable[str] = (),
) -> PortfolioAnalytics:
    """Summarise the research pipeline.

    ``events`` refines the counts where the history is richer than the current
    record: a paper that was submitted, rejected, and is now a draft again still
    counts as one submission.
    """
    per_paper = tuple(paper_durations(paper) for paper in papers)
    stalled = set(stalled_ids)

    submissions = 0
    decisions = 0
    acceptances = 0
    rejections = 0
    revisions = 0
    publications = 0

    for paper in papers:
        submissions += len(paper.submissions) or (1 if paper.submitted_at else 0)
        for record in paper.submissions:
            if record.is_resolved:
                decisions += 1
            if record.decision is DecisionOutcome.ACCEPT:
                acceptances += 1
            elif record.decision in (DecisionOutcome.REJECT, DecisionOutcome.DESK_REJECT):
                rejections += 1
            elif record.decision in (
                DecisionOutcome.MAJOR_REVISION,
                DecisionOutcome.MINOR_REVISION,
            ):
                revisions += 1
        if not paper.submissions:
            if paper.accepted_at is not None:
                acceptances += 1
                decisions += 1
            if paper.status is S.REVISION:
                revisions += 1
        if paper.published_at is not None or paper.status is S.PUBLISHED:
            publications += 1

    event_list = list(events)
    if event_list:
        from article_reminders.domain.enums import ProjectEventType

        counted = sum(
            1 for event in event_list if event.event_type is ProjectEventType.SUBMISSION_RECORDED
        )
        submissions = max(submissions, counted)

    reported = [
        *((key, label) for key, label, _, _ in STAGE_INTERVALS),
        ("revision_to_resubmission", "Revision to resubmission"),
    ]
    stage_durations = tuple(
        _summarise(
            key,
            label,
            [value for value in (item.get(key) for item in per_paper) if value is not None],
        )
        for key, label in reported
    )

    by_status: dict[str, int] = {}
    for paper in papers:
        by_status[paper.status.value] = by_status.get(paper.status.value, 0) + 1

    current_stage = _time_in_current_stage(papers, reference)

    return PortfolioAnalytics(
        generated_at=reference,
        total=len(papers),
        active=sum(1 for paper in papers if is_active_status(paper.status)),
        stalled=sum(1 for paper in papers if str(paper.id) in stalled),
        paused=sum(1 for paper in papers if paper.status is S.PAUSED),
        abandoned=sum(1 for paper in papers if paper.status is S.ABANDONED),
        submissions=submissions,
        decisions=decisions,
        acceptances=acceptances,
        publications=publications,
        rejections=rejections,
        revisions_requested=revisions,
        by_status=by_status,
        stage_durations=stage_durations,
        time_in_current_stage=current_stage,
        per_paper=per_paper,
    )


def _time_in_current_stage(
    papers: Sequence[Paper], reference: datetime
) -> tuple[Statistic, ...]:
    """Median days each stage's occupants have been sitting in it.

    Measured from the recorded transition into the stage, so it is only available
    for papers that have moved at least once inside the application.
    """
    buckets: dict[LifecycleStatus, list[float]] = {}
    for paper in papers:
        entered = _entered_current_stage(paper)
        if entered is None:
            continue
        buckets.setdefault(paper.status, []).append(days_between(entered, reference))
    return tuple(
        _summarise(status.value, status.label, sorted(values))
        for status, values in sorted(buckets.items(), key=lambda item: item[0].value)
    )


def _entered_current_stage(paper: Paper) -> datetime | None:
    for transition in reversed(paper.transitions):
        if transition.to_status is paper.status:
            return transition.occurred_at
    return None
