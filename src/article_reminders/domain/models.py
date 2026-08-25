"""The core model: one research paper and the value objects hanging off it.

Everything is a frozen dataclass. A paper is never mutated in place; services
produce a new one with :meth:`Paper.evolve` and hand it to the repository, which
makes "what changed" explicit and keeps the event log honest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from article_reminders.domain.enums import (
    BoardColumn,
    DecisionOutcome,
    LifecycleStatus,
    Priority,
    ReminderKind,
    ReminderSeverity,
)
from article_reminders.domain.errors import ValidationError
from article_reminders.domain.ids import PaperId, Slug, is_valid_slug, new_paper_id, slugify
from article_reminders.domain.timeutils import (
    ensure_aware,
    format_datetime,
    now,
    parse_optional_datetime,
)

_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Fields serialised as ISO-8601 datetimes on the Paper itself.
_PAPER_TIMESTAMPS: tuple[str, ...] = (
    "created_at",
    "updated_at",
    "started_at",
    "draft_started_at",
    "submitted_at",
    "decision_received_at",
    "revision_due_at",
    "accepted_at",
    "published_at",
    "conference_deadline",
    "internal_review_deadline",
    "last_repository_activity_at",
    "last_manuscript_activity_at",
    "last_analysis_activity_at",
)


def _without_empties(data: dict[str, Any]) -> dict[str, Any]:
    """Drop keys with no value, so a stored record shows only what is known."""
    return {key: value for key, value in data.items() if value is not None}


def _clean(value: object, *, field_name: str) -> str:
    """Coerce a scalar to a stripped string, rejecting structured values."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    raise ValidationError(f"{field_name} must be a string; got {type(value).__name__}.")


def _clean_optional(value: object, *, field_name: str) -> str | None:
    text = _clean(value, field_name=field_name)
    return text or None


def _string_tuple(values: object, *, field_name: str) -> tuple[str, ...]:
    """Normalise a list-ish of strings, dropping blanks and preserving order."""
    if values is None:
        return ()
    if isinstance(values, str):
        candidates: Sequence[object] = list(values.split(","))
    elif isinstance(values, Sequence):
        candidates = list(values)
    else:
        raise ValidationError(f"{field_name} must be a list of strings.")

    out: list[str] = []
    for candidate in candidates:
        text = _clean(candidate, field_name=field_name)
        if text and text not in out:
            out.append(text)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    """Where the work actually lives."""

    slug: str
    provider: str = "github"
    branch: str | None = None
    paper_path: str | None = None

    def __post_init__(self) -> None:
        slug = self.slug.strip()
        if not slug:
            raise ValidationError("A repository reference needs a slug such as owner/name.")
        if self.provider == "github" and not _REPO_SLUG_RE.match(slug):
            raise ValidationError(f"A GitHub repository must be owner/name; got {self.slug!r}.")
        object.__setattr__(self, "slug", slug)
        object.__setattr__(self, "provider", self.provider.strip().lower() or "github")
        object.__setattr__(self, "branch", (self.branch or "").strip() or None)
        object.__setattr__(self, "paper_path", (self.paper_path or "").strip() or None)

    @property
    def owner(self) -> str:
        return self.slug.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.slug.split("/", 1)[-1]

    @property
    def url(self) -> str:
        if self.provider == "github":
            return f"https://github.com/{self.slug}"
        return self.slug

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.slug,
            "repository_provider": self.provider,
            "repository_branch": self.branch,
            "paper_path": self.paper_path,
        }


@dataclass(frozen=True, slots=True)
class NextAction:
    """Concrete next action required to advance a research project."""

    description: str
    due_at: datetime | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Next-action description cannot be empty.")
        object.__setattr__(self, "description", self.description.strip())
        if self.due_at is not None:
            object.__setattr__(self, "due_at", ensure_aware(self.due_at, field="due_at"))
        if self.created_at is not None:
            object.__setattr__(
                self, "created_at", ensure_aware(self.created_at, field="created_at")
            )

    def to_dict(self) -> dict[str, Any]:
        return _without_empties(
            {
                "description": self.description,
                "due_at": format_datetime(self.due_at),
                "created_at": format_datetime(self.created_at),
            }
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> NextAction:
        description = _clean(raw.get("description"), field_name="next_action.description")
        if not description:
            raise ValidationError("next_action.description cannot be empty.")
        return cls(
            description=description,
            due_at=parse_optional_datetime(raw.get("due_at"), field="next_action.due_at"),
            created_at=parse_optional_datetime(
                raw.get("created_at"), field="next_action.created_at"
            ),
        )


@dataclass(frozen=True, slots=True)
class StatusTransition:
    """One recorded move through the lifecycle."""

    to_status: LifecycleStatus
    occurred_at: datetime
    from_status: LifecycleStatus | None = None
    forced: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "occurred_at", ensure_aware(self.occurred_at, field="transition.occurred_at")
        )

    def to_dict(self) -> dict[str, Any]:
        return _without_empties(
            {
                "from_status": None if self.from_status is None else self.from_status.value,
                "to_status": self.to_status.value,
                "occurred_at": format_datetime(self.occurred_at),
                "forced": self.forced,
                "note": self.note or None,
            }
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> StatusTransition:
        occurred_at = parse_optional_datetime(
            raw.get("occurred_at"), field="transition.occurred_at"
        )
        if occurred_at is None:
            raise ValidationError("A status transition needs an occurred_at timestamp.")
        source = raw.get("from_status")
        return cls(
            to_status=coerce_status(raw.get("to_status"), field_name="transition.to_status"),
            occurred_at=occurred_at,
            from_status=(
                None
                if source in (None, "")
                else coerce_status(source, field_name="transition.from_status")
            ),
            forced=bool(raw.get("forced", False)),
            note=_clean(raw.get("note"), field_name="transition.note"),
        )


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    """One round at one venue."""

    venue: str
    submitted_at: datetime
    decision: DecisionOutcome = DecisionOutcome.PENDING
    decision_at: datetime | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.venue.strip():
            raise ValidationError("A submission needs a venue.")
        object.__setattr__(self, "venue", self.venue.strip())
        object.__setattr__(
            self, "submitted_at", ensure_aware(self.submitted_at, field="submitted_at")
        )
        if self.decision_at is not None:
            object.__setattr__(
                self, "decision_at", ensure_aware(self.decision_at, field="decision_at")
            )

    @property
    def is_resolved(self) -> bool:
        return self.decision is not DecisionOutcome.PENDING

    def to_dict(self) -> dict[str, Any]:
        return _without_empties(
            {
                "venue": self.venue,
                "submitted_at": format_datetime(self.submitted_at),
                "decision": self.decision.value,
                "decision_at": format_datetime(self.decision_at),
                "notes": self.notes or None,
            }
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SubmissionRecord:
        submitted_at = parse_optional_datetime(
            raw.get("submitted_at"), field="submission.submitted_at"
        )
        if submitted_at is None:
            raise ValidationError("A submission needs a submitted_at timestamp.")
        decision_raw = _clean(raw.get("decision"), field_name="submission.decision")
        try:
            decision = DecisionOutcome(decision_raw) if decision_raw else DecisionOutcome.PENDING
        except ValueError as exc:
            raise ValidationError(
                f"Unknown submission decision {decision_raw!r}. Valid: "
                f"{', '.join(sorted(item.value for item in DecisionOutcome))}."
            ) from exc
        return cls(
            venue=_clean(raw.get("venue"), field_name="submission.venue"),
            submitted_at=submitted_at,
            decision=decision,
            decision_at=parse_optional_datetime(
                raw.get("decision_at"), field="submission.decision_at"
            ),
            notes=_clean(raw.get("notes"), field_name="submission.notes"),
        )


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    """What a repository looked like the last time it was observed.

    Produced by the GitHub layer, merged into the paper by the activity service.
    """

    repository_slug: str
    observed_at: datetime
    last_repository_activity_at: datetime | None = None
    last_manuscript_activity_at: datetime | None = None
    last_analysis_activity_at: datetime | None = None
    last_data_activity_at: datetime | None = None
    default_branch: str | None = None
    open_issue_count: int | None = None
    closed_issue_count: int | None = None
    open_pull_request_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_slug": self.repository_slug,
            "observed_at": format_datetime(self.observed_at),
            "last_repository_activity_at": format_datetime(self.last_repository_activity_at),
            "last_manuscript_activity_at": format_datetime(self.last_manuscript_activity_at),
            "last_analysis_activity_at": format_datetime(self.last_analysis_activity_at),
            "last_data_activity_at": format_datetime(self.last_data_activity_at),
            "default_branch": self.default_branch,
            "open_issue_count": self.open_issue_count,
            "closed_issue_count": self.closed_issue_count,
            "open_pull_request_count": self.open_pull_request_count,
        }


@dataclass(frozen=True, slots=True)
class Reminder:
    """A structured reason to look at a paper today."""

    project_id: PaperId
    kind: ReminderKind
    severity: ReminderSeverity
    message: str
    created_at: datetime
    due_at: datetime | None = None
    paper_title: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValidationError("A reminder needs a message.")
        object.__setattr__(
            self, "created_at", ensure_aware(self.created_at, field="reminder.created_at")
        )
        if self.due_at is not None:
            object.__setattr__(self, "due_at", ensure_aware(self.due_at, field="reminder.due_at"))

    @property
    def sort_key(self) -> tuple[int, float]:
        """Most severe first, then soonest due."""
        due = self.due_at.timestamp() if self.due_at else float("inf")
        return (-self.severity.rank, due)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "paper_title": self.paper_title,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "message": self.message,
            "created_at": format_datetime(self.created_at),
            "due_at": format_datetime(self.due_at),
            "context": dict(self.context),
        }


def coerce_status(value: object, *, field_name: str = "status") -> LifecycleStatus:
    """Parse a lifecycle status, with a message that lists the vocabulary."""
    if isinstance(value, LifecycleStatus):
        return value
    text = _clean(value, field_name=field_name).lower().replace("-", "_").replace(" ", "_")
    try:
        return LifecycleStatus(text)
    except ValueError as exc:
        raise ValidationError(
            f"Unknown {field_name} {text!r}. Valid: "
            f"{', '.join(item.value for item in LifecycleStatus)}."
        ) from exc


def coerce_priority(value: object, *, field_name: str = "priority") -> Priority:
    """Parse a priority, defaulting to medium only when nothing was supplied."""
    if isinstance(value, Priority):
        return value
    text = _clean(value, field_name=field_name).lower()
    if not text:
        return Priority.MEDIUM
    try:
        return Priority(text)
    except ValueError as exc:
        raise ValidationError(
            f"Unknown {field_name} {text!r}. Valid: "
            f"{', '.join(item.value for item in Priority)}."
        ) from exc


@dataclass(frozen=True, slots=True)
class Paper:
    """One research paper or publication project.

    The identity fields (``id``, ``title``, ``slug``, ``status``, ``priority``) are
    always present. Everything else is optional, because a paper that exists only
    as an idea has almost nothing to record yet and should still be trackable.
    """

    id: PaperId
    title: str
    slug: Slug
    status: LifecycleStatus = LifecycleStatus.IDEA
    priority: Priority = Priority.MEDIUM

    research_question: str = ""
    description: str = ""
    abstract: str = ""
    notes: str = ""

    repository: RepositoryRef | None = None
    target_journal: str | None = None
    target_conference: str | None = None

    authors: tuple[str, ...] = ()
    corresponding_author: str | None = None
    tags: tuple[str, ...] = ()
    research_programme: str | None = None

    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)

    started_at: datetime | None = None
    draft_started_at: datetime | None = None
    submitted_at: datetime | None = None
    decision_received_at: datetime | None = None
    revision_due_at: datetime | None = None
    accepted_at: datetime | None = None
    published_at: datetime | None = None
    conference_deadline: datetime | None = None
    internal_review_deadline: datetime | None = None

    next_action: NextAction | None = None

    doi: str | None = None
    preprint_url: str | None = None
    publication_url: str | None = None

    last_repository_activity_at: datetime | None = None
    last_manuscript_activity_at: datetime | None = None
    last_analysis_activity_at: datetime | None = None

    waiting_for: str | None = None

    submissions: tuple[SubmissionRecord, ...] = ()
    transitions: tuple[StatusTransition, ...] = ()

    github_issue_number: int | None = None
    # Anything a legacy record or a future version carried that this version does
    # not model. Round-tripped verbatim so a migration never loses information.
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValidationError("A paper needs an id.")
        title = self.title.strip()
        if not title:
            raise ValidationError("A paper needs a title.")
        object.__setattr__(self, "title", title)

        slug = str(self.slug).strip()
        if not is_valid_slug(slug):
            slug = str(slugify(slug or title))
        if not slug:
            raise ValidationError(f"Could not derive a slug from the title {title!r}.")
        object.__setattr__(self, "slug", Slug(slug))

        object.__setattr__(self, "created_at", ensure_aware(self.created_at, field="created_at"))
        object.__setattr__(self, "updated_at", ensure_aware(self.updated_at, field="updated_at"))
        for name in _PAPER_TIMESTAMPS:
            value = getattr(self, name)
            if isinstance(value, datetime):
                object.__setattr__(self, name, ensure_aware(value, field=name))

        # A revision deadline before the submission that produced it is a typo,
        # not a workflow: dates like this quietly poison every staleness metric.
        if (
            self.revision_due_at is not None
            and self.submitted_at is not None
            and self.revision_due_at < self.submitted_at
        ):
            raise ValidationError("revision_due_at is earlier than submitted_at; check the dates.")
        if (
            self.published_at is not None
            and self.accepted_at is not None
            and self.published_at < self.accepted_at
        ):
            raise ValidationError("published_at is earlier than accepted_at; check the dates.")

    # -- derived state ----------------------------------------------------

    @property
    def next_action_due_at(self) -> datetime | None:
        """Deadline of the current next action, if it has one."""
        return None if self.next_action is None else self.next_action.due_at

    @property
    def is_active(self) -> bool:
        """Whether the paper is still moving through the lifecycle."""
        from article_reminders.domain.rules import is_active_status

        return is_active_status(self.status)

    @property
    def is_waiting(self) -> bool:
        """Whether progress currently depends on somebody else."""
        from article_reminders.domain.rules import is_waiting_status

        return bool(self.waiting_for) or is_waiting_status(self.status)

    @property
    def board_column(self) -> BoardColumn:
        from article_reminders.domain.rules import board_column_for

        return board_column_for(self.status)

    @property
    def venue(self) -> str | None:
        """Target journal if set, otherwise the target conference."""
        return self.target_journal or self.target_conference

    @property
    def repository_slug(self) -> str | None:
        return None if self.repository is None else self.repository.slug

    @property
    def paper_path(self) -> str | None:
        return None if self.repository is None else self.repository.paper_path

    def last_activity_at(self) -> datetime | None:
        """The most recent evidence of any activity, recorded or observed."""
        candidates = [
            self.last_manuscript_activity_at,
            self.last_analysis_activity_at,
            self.last_repository_activity_at,
            self.updated_at,
        ]
        known = [value for value in candidates if value is not None]
        return max(known) if known else None

    def deadlines(self) -> tuple[tuple[str, str, datetime], ...]:
        """Every dated commitment, as ``(kind, label, when)`` sorted by date."""
        entries: list[tuple[str, str, datetime]] = []
        if self.next_action is not None and self.next_action.due_at is not None:
            entries.append(("next_action", self.next_action.description, self.next_action.due_at))
        if self.revision_due_at is not None:
            entries.append(("revision", "Revision due", self.revision_due_at))
        if self.conference_deadline is not None:
            entries.append(
                (
                    "conference",
                    self.target_conference or "Conference deadline",
                    self.conference_deadline,
                )
            )
        if self.internal_review_deadline is not None:
            entries.append(("internal_review", "Internal review", self.internal_review_deadline))
        return tuple(sorted(entries, key=lambda item: item[2]))

    def evolve(self, **changes: Any) -> Paper:
        """Return a copy with ``changes`` applied; validation runs again."""
        return replace(self, **changes)

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """A flat, human-readable record. Empty values are omitted."""
        data: dict[str, Any] = {
            "id": str(self.id),
            "title": self.title,
            "slug": str(self.slug),
            "status": self.status.value,
            "priority": self.priority.value,
            "research_question": self.research_question or None,
            "description": self.description or None,
            "abstract": self.abstract or None,
            "notes": self.notes or None,
            "target_journal": self.target_journal,
            "target_conference": self.target_conference,
            "authors": list(self.authors) or None,
            "corresponding_author": self.corresponding_author,
            "tags": list(self.tags) or None,
            "research_programme": self.research_programme,
            "next_action": None if self.next_action is None else self.next_action.to_dict(),
            "doi": self.doi,
            "preprint_url": self.preprint_url,
            "publication_url": self.publication_url,
            "waiting_for": self.waiting_for,
            "github_issue_number": self.github_issue_number,
            "submissions": [item.to_dict() for item in self.submissions] or None,
            "transitions": [item.to_dict() for item in self.transitions] or None,
        }
        if self.repository is not None:
            data.update(self.repository.to_dict())
        for name in _PAPER_TIMESTAMPS:
            data[name] = format_datetime(getattr(self, name))
        for key, value in self.extra.items():
            data.setdefault(key, value)
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Paper:
        """Rebuild a paper from stored data, validating at the boundary."""
        if not isinstance(raw, Mapping):
            raise ValidationError(f"A paper record must be an object; got {type(raw).__name__}.")

        title = _clean(raw.get("title"), field_name="title")
        if not title:
            raise ValidationError("A paper record needs a title.")

        repository_slug = _clean_optional(raw.get("repository"), field_name="repository")
        repository = (
            RepositoryRef(
                slug=repository_slug,
                provider=_clean(raw.get("repository_provider"), field_name="repository_provider")
                or "github",
                branch=_clean_optional(
                    raw.get("repository_branch"), field_name="repository_branch"
                ),
                paper_path=_clean_optional(raw.get("paper_path"), field_name="paper_path"),
            )
            if repository_slug
            else None
        )

        next_action_raw = raw.get("next_action")
        next_action: NextAction | None = None
        if isinstance(next_action_raw, Mapping):
            next_action = NextAction.from_dict(next_action_raw)
        elif isinstance(next_action_raw, str) and next_action_raw.strip():
            next_action = NextAction(
                description=next_action_raw.strip(),
                due_at=parse_optional_datetime(
                    raw.get("next_action_due_at"), field="next_action_due_at"
                ),
            )

        timestamps: dict[str, datetime | None] = {
            name: parse_optional_datetime(raw.get(name), field=name) for name in _PAPER_TIMESTAMPS
        }
        created_at = timestamps.pop("created_at") or now()
        updated_at = timestamps.pop("updated_at") or created_at

        issue_number_raw = raw.get("github_issue_number")
        issue_number: int | None
        if issue_number_raw in (None, ""):
            issue_number = None
        else:
            try:
                issue_number = int(str(issue_number_raw))
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    f"github_issue_number must be an integer; got {issue_number_raw!r}."
                ) from exc

        extra = {key: value for key, value in raw.items() if key not in _KNOWN_KEYS}

        return cls(
            id=PaperId(_clean(raw.get("id"), field_name="id") or str(new_paper_id())),
            title=title,
            slug=Slug(_clean(raw.get("slug"), field_name="slug") or str(slugify(title))),
            status=coerce_status(raw.get("status") or LifecycleStatus.IDEA),
            priority=coerce_priority(raw.get("priority")),
            research_question=_clean(raw.get("research_question"), field_name="research_question"),
            description=_clean(raw.get("description"), field_name="description"),
            abstract=_clean(raw.get("abstract"), field_name="abstract"),
            notes=_clean(raw.get("notes"), field_name="notes"),
            repository=repository,
            target_journal=_clean_optional(raw.get("target_journal"), field_name="target_journal"),
            target_conference=_clean_optional(
                raw.get("target_conference"), field_name="target_conference"
            ),
            authors=_string_tuple(raw.get("authors"), field_name="authors"),
            corresponding_author=_clean_optional(
                raw.get("corresponding_author"), field_name="corresponding_author"
            ),
            tags=_string_tuple(raw.get("tags"), field_name="tags"),
            research_programme=_clean_optional(
                raw.get("research_programme"), field_name="research_programme"
            ),
            created_at=created_at,
            updated_at=updated_at,
            next_action=next_action,
            doi=_clean_optional(raw.get("doi"), field_name="doi"),
            preprint_url=_clean_optional(raw.get("preprint_url"), field_name="preprint_url"),
            publication_url=_clean_optional(
                raw.get("publication_url"), field_name="publication_url"
            ),
            waiting_for=_clean_optional(raw.get("waiting_for"), field_name="waiting_for"),
            submissions=_submissions_from(raw.get("submissions")),
            transitions=_transitions_from(raw.get("transitions")),
            github_issue_number=issue_number,
            extra=extra,
            **timestamps,
        )


def _submissions_from(value: object) -> tuple[SubmissionRecord, ...]:
    if value is None:
        return ()
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        raise ValidationError("submissions must be a list of submission objects.")
    return tuple(
        SubmissionRecord.from_dict(item) if isinstance(item, Mapping) else _reject(item)
        for item in value
    )


def _transitions_from(value: object) -> tuple[StatusTransition, ...]:
    if value is None:
        return ()
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        raise ValidationError("transitions must be a list of transition objects.")
    return tuple(
        StatusTransition.from_dict(item) if isinstance(item, Mapping) else _reject(item)
        for item in value
    )


def _reject(item: object) -> Any:
    raise ValidationError(f"Expected an object, found {type(item).__name__}.")


_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "title",
        "slug",
        "status",
        "priority",
        "research_question",
        "description",
        "abstract",
        "notes",
        "repository",
        "repository_provider",
        "repository_branch",
        "paper_path",
        "target_journal",
        "target_conference",
        "authors",
        "corresponding_author",
        "tags",
        "research_programme",
        "next_action",
        "next_action_due_at",
        "doi",
        "preprint_url",
        "publication_url",
        "waiting_for",
        "submissions",
        "transitions",
        "github_issue_number",
        *_PAPER_TIMESTAMPS,
    }
)

__all__ = [
    "ActivitySnapshot",
    "NextAction",
    "Paper",
    "Reminder",
    "RepositoryRef",
    "StatusTransition",
    "SubmissionRecord",
    "coerce_priority",
    "coerce_status",
]
