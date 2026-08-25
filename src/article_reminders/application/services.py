"""The service layer both interfaces sit on.

Every business rule that changes a paper lives here exactly once. The CLI and the
web application call these methods; neither one re-implements a rule, and neither
one writes to storage directly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from article_reminders.application.ports import Clock, EventLog, PaperRepository
from article_reminders.domain.enums import (
    BoardColumn,
    DecisionOutcome,
    LifecycleStatus,
    Priority,
    ProjectEventType,
)
from article_reminders.domain.errors import InvalidTransitionError, ValidationError
from article_reminders.domain.events import ProjectEvent
from article_reminders.domain.ids import PaperId, Slug, new_paper_id, slugify
from article_reminders.domain.models import (
    ActivitySnapshot,
    NextAction,
    Paper,
    RepositoryRef,
    StatusTransition,
    SubmissionRecord,
    coerce_priority,
    coerce_status,
)
from article_reminders.domain.rules import (
    COLUMN_DEFAULT_STATUS,
    STATUS_TIMESTAMP_FIELD,
    is_transition_allowed,
)
from article_reminders.domain.timeutils import format_datetime, parse_optional_datetime
from article_reminders.infrastructure.clock import SystemClock

logger = logging.getLogger(__name__)

#: Fields a caller may set through :meth:`PortfolioService.update`. Status and the
#: next action have their own methods because they carry side effects.
EDITABLE_TEXT_FIELDS: tuple[str, ...] = (
    "title",
    "research_question",
    "description",
    "abstract",
    "notes",
    "target_journal",
    "target_conference",
    "corresponding_author",
    "research_programme",
    "doi",
    "preprint_url",
    "publication_url",
    "waiting_for",
)

EDITABLE_DATE_FIELDS: tuple[str, ...] = (
    "started_at",
    "draft_started_at",
    "submitted_at",
    "decision_received_at",
    "revision_due_at",
    "accepted_at",
    "published_at",
    "conference_deadline",
    "internal_review_deadline",
)

#: Text fields that mean "" rather than None when cleared, so the model keeps its
#: str type instead of drifting to Optional for no reason.
_CLEARS_TO_EMPTY_STRING: frozenset[str] = frozenset(
    {"research_question", "description", "abstract", "notes"}
)

#: Fields whose change is worth an entry in the event log on its own.
DEADLINE_FIELDS: frozenset[str] = frozenset(
    {"revision_due_at", "conference_deadline", "internal_review_deadline"}
)


@dataclass(frozen=True, slots=True)
class PaperFilter:
    """A saved view over the portfolio."""

    statuses: frozenset[LifecycleStatus] = frozenset()
    priorities: frozenset[Priority] = frozenset()
    tags: frozenset[str] = frozenset()
    programme: str | None = None
    query: str | None = None
    active_only: bool = False
    needs_next_action: bool = False

    def matches(self, paper: Paper) -> bool:
        if self.statuses and paper.status not in self.statuses:
            return False
        if self.priorities and paper.priority not in self.priorities:
            return False
        if self.tags and not self.tags.intersection({tag.lower() for tag in paper.tags}):
            return False
        if self.programme and (paper.research_programme or "").lower() != self.programme.lower():
            return False
        if self.active_only and not paper.is_active:
            return False
        if self.needs_next_action and paper.next_action is not None:
            return False
        if self.query:
            needle = self.query.lower()
            haystack = " ".join(
                [
                    paper.title,
                    paper.research_question,
                    paper.description,
                    paper.abstract,
                    paper.notes,
                    paper.repository_slug or "",
                    " ".join(paper.tags),
                ]
            ).lower()
            if needle not in haystack:
                return False
        return True


class PortfolioService:
    """Read and change the research portfolio."""

    def __init__(
        self,
        repository: PaperRepository,
        events: EventLog,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._events = events
        self._clock = clock or SystemClock()

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def events(self) -> EventLog:
        return self._events

    @property
    def repository(self) -> PaperRepository:
        return self._repository

    # -- reading ----------------------------------------------------------

    def list_papers(self, criteria: PaperFilter | None = None) -> tuple[Paper, ...]:
        """Papers matching ``criteria``, most urgent first."""
        papers = self._repository.all()
        if criteria is not None:
            papers = tuple(paper for paper in papers if criteria.matches(paper))
        return tuple(sorted(papers, key=_portfolio_sort_key))

    def get(self, reference: str | PaperId) -> Paper:
        """One paper, by id, slug, title, or unique title fragment."""
        return self._repository.find(str(reference))

    def timeline(self, paper: Paper) -> list[ProjectEvent]:
        """The paper's history, oldest first."""
        return self._events.for_project(paper.id)

    # -- creating and editing --------------------------------------------

    def create(
        self,
        title: str,
        *,
        status: LifecycleStatus | str = LifecycleStatus.IDEA,
        priority: Priority | str = Priority.MEDIUM,
        repository: str | None = None,
        paper_path: str | None = None,
        repository_branch: str | None = None,
        repository_provider: str = "github",
        next_action: str | None = None,
        next_action_due_at: datetime | str | None = None,
        tags: Sequence[str] = (),
        authors: Sequence[str] = (),
        paper_id: str | None = None,
        **fields: Any,
    ) -> Paper:
        """Add a paper to the portfolio.

        ``paper_id`` is for importers that need a stable, reproducible id; left
        alone, a fresh opaque one is minted.
        """
        stamp = self._clock.now()
        if not title.strip():
            raise ValidationError("A paper needs a title.")

        existing_slugs = {str(paper.slug) for paper in self._repository.all()}
        slug = _unique_slug(title, existing_slugs)

        due = parse_optional_datetime(next_action_due_at, field="next_action_due_at")
        action = (
            NextAction(description=next_action, due_at=due, created_at=stamp)
            if next_action and next_action.strip()
            else None
        )

        paper = Paper(
            id=PaperId(paper_id) if paper_id else new_paper_id(),
            title=title.strip(),
            slug=slug,
            status=coerce_status(status),
            priority=coerce_priority(priority),
            repository=(
                RepositoryRef(
                    slug=repository,
                    provider=repository_provider,
                    branch=repository_branch,
                    paper_path=paper_path,
                )
                if repository
                else None
            ),
            tags=tuple(tags),
            authors=tuple(authors),
            created_at=stamp,
            updated_at=stamp,
            next_action=action,
            **_normalise_fields(fields),
        )
        paper = _stamp_status_timestamp(paper, paper.status, stamp)
        paper = paper.evolve(
            transitions=(StatusTransition(to_status=paper.status, occurred_at=stamp),)
        )

        self._repository.save(paper)
        self._record(paper, ProjectEventType.PROJECT_CREATED, stamp, {"title": paper.title})
        if action is not None:
            self._record(
                paper,
                ProjectEventType.NEXT_ACTION_CHANGED,
                stamp,
                {"description": action.description, "due_at": format_datetime(action.due_at)},
            )
        logger.info("created paper %s (%s)", paper.id, paper.slug)
        return paper

    def update(self, reference: str | Paper, **changes: Any) -> Paper:
        """Change plain fields on a paper.

        Status and next action are deliberately not settable here: they have their
        own methods because they record history.
        """
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        stamp = self._clock.now()

        for reserved in ("status", "next_action", "id", "created_at"):
            if reserved in changes:
                raise ValidationError(
                    f"{reserved!r} cannot be set through update(); use the dedicated method."
                )

        updates = _normalise_fields(changes)
        if not updates:
            return paper

        repository_fields = {
            key: updates.pop(key)
            for key in ("repository", "paper_path", "repository_branch", "repository_provider")
            if key in updates
        }
        if repository_fields:
            updates["repository"] = _merge_repository(paper.repository, repository_fields)

        updated = paper.evolve(**updates, updated_at=stamp)
        self._repository.save(updated)

        changed_deadlines = {
            key: value for key, value in updates.items() if key in DEADLINE_FIELDS
        }
        for key, value in changed_deadlines.items():
            self._record(
                updated,
                ProjectEventType.DEADLINE_CHANGED,
                stamp,
                {"field": key, "value": format_datetime(value) if value else None},
            )
        if set(updates) - DEADLINE_FIELDS:
            self._record(
                updated,
                ProjectEventType.PROJECT_UPDATED,
                stamp,
                {"fields": sorted(set(updates) - DEADLINE_FIELDS)},
            )
        return updated

    def delete(self, reference: str | Paper) -> Paper:
        """Remove a paper from the portfolio. Its events are kept."""
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        return self._repository.delete(paper.id)

    # -- lifecycle --------------------------------------------------------

    def set_status(
        self,
        reference: str | Paper,
        status: LifecycleStatus | str,
        *,
        force: bool = False,
        note: str = "",
    ) -> Paper:
        """Move a paper through the lifecycle.

        Non-canonical moves are refused unless ``force`` is set, and a forced move
        is recorded as forced so the history says a rule was overridden.
        """
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        target = coerce_status(status)
        stamp = self._clock.now()

        if target is paper.status:
            return paper
        if not force and not is_transition_allowed(paper.status, target):
            raise InvalidTransitionError(paper.status.value, target.value)

        transition = StatusTransition(
            to_status=target,
            occurred_at=stamp,
            from_status=paper.status,
            forced=force and not is_transition_allowed(paper.status, target),
            note=note,
        )
        updated = paper.evolve(
            status=target,
            updated_at=stamp,
            transitions=(*paper.transitions, transition),
        )
        updated = _stamp_status_timestamp(updated, target, stamp)
        self._repository.save(updated)

        self._record(
            updated,
            ProjectEventType.STATUS_CHANGED,
            stamp,
            {
                "from": paper.status.value,
                "to": target.value,
                "forced": transition.forced,
                "note": note or None,
            },
        )
        if target is LifecycleStatus.ACCEPTED:
            self._record(updated, ProjectEventType.PAPER_ACCEPTED, stamp, {})
        elif target is LifecycleStatus.PUBLISHED:
            self._record(updated, ProjectEventType.PAPER_PUBLISHED, stamp, {})
        elif target is LifecycleStatus.REVISION:
            self._record(updated, ProjectEventType.REVISION_REQUESTED, stamp, {})
        logger.info("paper %s: %s -> %s", updated.id, paper.status.value, target.value)
        return updated

    def move_to_column(
        self, reference: str | Paper, column: BoardColumn | str, *, force: bool = True
    ) -> Paper:
        """Move a paper to the status a Kanban column stands for.

        Board moves default to forced: dragging a card is an explicit human
        decision, and refusing it silently would make the board lie.
        """
        target_column = BoardColumn(str(column))
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        if paper.board_column is target_column:
            return paper
        return self.set_status(paper, COLUMN_DEFAULT_STATUS[target_column], force=force)

    # -- the next action --------------------------------------------------

    def set_next_action(
        self,
        reference: str | Paper,
        description: str,
        *,
        due_at: datetime | str | None = None,
    ) -> Paper:
        """Set the one concrete thing that moves this paper forward."""
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        stamp = self._clock.now()
        action = NextAction(
            description=description,
            due_at=parse_optional_datetime(due_at, field="next_action_due_at"),
            created_at=stamp,
        )
        updated = paper.evolve(next_action=action, updated_at=stamp)
        self._repository.save(updated)
        self._record(
            updated,
            ProjectEventType.NEXT_ACTION_CHANGED,
            stamp,
            {"description": action.description, "due_at": format_datetime(action.due_at)},
        )
        return updated

    def clear_next_action(self, reference: str | Paper) -> Paper:
        """Remove the next action without completing it."""
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        if paper.next_action is None:
            return paper
        stamp = self._clock.now()
        updated = paper.evolve(next_action=None, updated_at=stamp)
        self._repository.save(updated)
        self._record(updated, ProjectEventType.NEXT_ACTION_CHANGED, stamp, {"description": None})
        return updated

    def complete_next_action(
        self,
        reference: str | Paper,
        *,
        follow_up: str | None = None,
        follow_up_due_at: datetime | str | None = None,
    ) -> Paper:
        """Mark the current next action done, optionally naming the next one."""
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        stamp = self._clock.now()
        completed = paper.next_action
        updated = paper.evolve(next_action=None, updated_at=stamp)
        self._repository.save(updated)
        if completed is not None:
            self._record(
                updated,
                ProjectEventType.NEXT_ACTION_COMPLETED,
                stamp,
                {"description": completed.description},
            )
        if follow_up:
            return self.set_next_action(updated, follow_up, due_at=follow_up_due_at)
        return updated

    # -- submissions ------------------------------------------------------

    def record_submission(
        self,
        reference: str | Paper,
        venue: str,
        *,
        submitted_at: datetime | str | None = None,
        advance_status: bool = True,
    ) -> Paper:
        """Record that the manuscript went out to a venue."""
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        stamp = self._clock.now()
        when = parse_optional_datetime(submitted_at, field="submitted_at") or stamp
        record = SubmissionRecord(venue=venue, submitted_at=when)

        updated = paper.evolve(
            submissions=(*paper.submissions, record),
            submitted_at=when,
            target_journal=paper.target_journal or venue,
            decision_received_at=None,
            waiting_for=f"{venue} decision",
            next_action=None,
            updated_at=stamp,
        )
        self._repository.save(updated)
        self._record(
            updated,
            ProjectEventType.SUBMISSION_RECORDED,
            when,
            {"venue": venue, "submitted_at": format_datetime(when)},
        )
        if advance_status and updated.status is not LifecycleStatus.SUBMITTED:
            return self.set_status(updated, LifecycleStatus.SUBMITTED, force=True)
        return updated

    def record_decision(
        self,
        reference: str | Paper,
        decision: DecisionOutcome | str,
        *,
        decided_at: datetime | str | None = None,
        revision_due_at: datetime | str | None = None,
        notes: str = "",
        advance_status: bool = True,
    ) -> Paper:
        """Record what the venue said, and where that leaves the paper."""
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        stamp = self._clock.now()
        outcome = DecisionOutcome(str(decision))
        when = parse_optional_datetime(decided_at, field="decision_received_at") or stamp
        due = parse_optional_datetime(revision_due_at, field="revision_due_at")

        submissions = list(paper.submissions)
        venue: str | None = None
        for index in range(len(submissions) - 1, -1, -1):
            if not submissions[index].is_resolved:
                submissions[index] = SubmissionRecord(
                    venue=submissions[index].venue,
                    submitted_at=submissions[index].submitted_at,
                    decision=outcome,
                    decision_at=when,
                    notes=notes,
                )
                venue = submissions[index].venue
                break

        updated = paper.evolve(
            submissions=tuple(submissions),
            decision_received_at=when,
            revision_due_at=due if due is not None else paper.revision_due_at,
            waiting_for=None,
            updated_at=stamp,
        )
        self._repository.save(updated)
        self._record(
            updated,
            ProjectEventType.DECISION_RECORDED,
            when,
            {"decision": outcome.value, "venue": venue, "notes": notes or None},
        )
        if not advance_status:
            return updated
        return self._apply_decision_status(updated, outcome)

    def _apply_decision_status(self, paper: Paper, outcome: DecisionOutcome) -> Paper:
        target = _DECISION_STATUS.get(outcome)
        if target is None or paper.status is target:
            return paper
        return self.set_status(paper, target, force=True)

    # -- activity ---------------------------------------------------------

    def apply_activity(self, paper: Paper, snapshot: ActivitySnapshot) -> Paper:
        """Fold observed repository activity into a paper.

        Timestamps only ever move forward: a snapshot that saw less than a previous
        one (a shallower scan, a narrower token) must not erase evidence.
        """
        stamp = self._clock.now()
        merged = paper.evolve(
            last_repository_activity_at=_latest(
                paper.last_repository_activity_at, snapshot.last_repository_activity_at
            ),
            last_manuscript_activity_at=_latest(
                paper.last_manuscript_activity_at, snapshot.last_manuscript_activity_at
            ),
            last_analysis_activity_at=_latest(
                paper.last_analysis_activity_at, snapshot.last_analysis_activity_at
            ),
        )
        if merged == paper:
            return paper

        self._repository.save(merged)
        pairs = (
            (
                ProjectEventType.REPOSITORY_ACTIVITY_DETECTED,
                paper.last_repository_activity_at,
                merged.last_repository_activity_at,
            ),
            (
                ProjectEventType.MANUSCRIPT_ACTIVITY_DETECTED,
                paper.last_manuscript_activity_at,
                merged.last_manuscript_activity_at,
            ),
            (
                ProjectEventType.ANALYSIS_ACTIVITY_DETECTED,
                paper.last_analysis_activity_at,
                merged.last_analysis_activity_at,
            ),
        )
        for event_type, before, after in pairs:
            if after is not None and after != before:
                self._record(
                    merged, event_type, stamp, {"at": format_datetime(after), "observed": True}
                )
        return merged

    def record_issue_sync(self, paper: Paper, issue_number: int, action: str) -> Paper:
        """Remember which GitHub issue belongs to a paper."""
        stamp = self._clock.now()
        updated = paper
        if paper.github_issue_number != issue_number:
            updated = paper.evolve(github_issue_number=issue_number, updated_at=stamp)
            self._repository.save(updated)
        self._record(
            updated,
            ProjectEventType.ISSUE_SYNCHRONISED,
            stamp,
            {"issue": issue_number, "action": action},
        )
        return updated

    def add_note(self, reference: str | Paper, note: str) -> Paper:
        """Append a dated note to the paper's notes field and its history."""
        paper = reference if isinstance(reference, Paper) else self.get(reference)
        text = note.strip()
        if not text:
            raise ValidationError("A note cannot be empty.")
        stamp = self._clock.now()
        stamped = f"{stamp.date().isoformat()}: {text}"
        combined = f"{paper.notes}\n{stamped}".strip() if paper.notes else stamped
        updated = paper.evolve(notes=combined, updated_at=stamp)
        self._repository.save(updated)
        self._record(updated, ProjectEventType.NOTE_ADDED, stamp, {"note": text})
        return updated

    # -- internals --------------------------------------------------------

    def _record(
        self,
        paper: Paper,
        event_type: ProjectEventType,
        occurred_at: datetime,
        metadata: Mapping[str, Any],
    ) -> ProjectEvent:
        event = ProjectEvent(
            project_id=paper.id,
            event_type=event_type,
            occurred_at=occurred_at,
            metadata={key: value for key, value in metadata.items() if value is not None},
        )
        self._events.append(event)
        return event


_DECISION_STATUS: Mapping[DecisionOutcome, LifecycleStatus | None] = {
    DecisionOutcome.ACCEPT: LifecycleStatus.ACCEPTED,
    DecisionOutcome.MAJOR_REVISION: LifecycleStatus.REVISION,
    DecisionOutcome.MINOR_REVISION: LifecycleStatus.REVISION,
    DecisionOutcome.REJECT: LifecycleStatus.DRAFT,
    DecisionOutcome.DESK_REJECT: LifecycleStatus.DRAFT,
    DecisionOutcome.WITHDRAWN: LifecycleStatus.PAUSED,
    DecisionOutcome.PENDING: None,
}


def _latest(*values: datetime | None) -> datetime | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _stamp_status_timestamp(paper: Paper, status: LifecycleStatus, stamp: datetime) -> Paper:
    """Fill in the lifecycle timestamp a status implies, if it is still empty."""
    field_name = STATUS_TIMESTAMP_FIELD.get(status)
    if field_name is None or getattr(paper, field_name) is not None:
        return paper
    return paper.evolve(**{field_name: stamp})


def _merge_repository(
    current: RepositoryRef | None, changes: Mapping[str, Any]
) -> RepositoryRef | None:
    slug = changes.get("repository", current.slug if current else None)
    if not slug:
        return None
    return RepositoryRef(
        slug=str(slug),
        provider=str(
            changes.get("repository_provider", current.provider if current else "github")
        ),
        branch=changes.get("repository_branch", current.branch if current else None),
        paper_path=changes.get("paper_path", current.paper_path if current else None),
    )


def _normalise_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and coerce the free-form keyword arguments of create/update."""
    allowed = {
        *EDITABLE_TEXT_FIELDS,
        *EDITABLE_DATE_FIELDS,
        "tags",
        "authors",
        "priority",
        "repository",
        "paper_path",
        "repository_branch",
        "repository_provider",
        "github_issue_number",
    }
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ValidationError(
            f"Unknown field(s): {', '.join(unknown)}. Editable: {', '.join(sorted(allowed))}."
        )

    out: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None and key in EDITABLE_TEXT_FIELDS:
            out[key] = "" if key in _CLEARS_TO_EMPTY_STRING else None
            continue
        if key in EDITABLE_DATE_FIELDS:
            out[key] = parse_optional_datetime(value, field=key)
        elif key in ("tags", "authors"):
            out[key] = tuple(_as_sequence(value))
        elif key == "priority":
            out[key] = coerce_priority(value)
        elif key == "github_issue_number":
            out[key] = None if value in (None, "") else int(value)
        else:
            out[key] = value
    return out


def _as_sequence(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValidationError(f"Expected a list of strings; got {type(value).__name__}.")


def _unique_slug(title: str, taken: set[str]) -> Slug:
    base = str(slugify(title))
    if base not in taken:
        return Slug(base)
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return Slug(candidate)
    raise ValidationError(f"Could not derive a unique slug for {title!r}.")


def _portfolio_sort_key(paper: Paper) -> tuple[int, float, int, str]:
    """Active work first, then by deadline, then by priority, then by title."""
    due = paper.next_action_due_at or paper.revision_due_at
    return (
        0 if paper.is_active else 1,
        due.timestamp() if due else float("inf"),
        -paper.priority.rank,
        paper.title.lower(),
    )
