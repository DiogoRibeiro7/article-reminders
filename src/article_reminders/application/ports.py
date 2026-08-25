"""The interfaces the application layer depends on.

Everything the services need from the outside world is one of these protocols.
Concrete implementations live in ``infrastructure``; tests substitute fakes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from article_reminders.domain.events import ProjectEvent
from article_reminders.domain.ids import PaperId
from article_reminders.domain.models import ActivitySnapshot, Paper


@runtime_checkable
class Clock(Protocol):
    """The current time, injected so business logic stays deterministic."""

    def now(self) -> datetime: ...


class PaperRepository(Protocol):
    """Persistent storage for papers."""

    def load(self) -> list[Paper]: ...

    def all(self) -> tuple[Paper, ...]: ...

    def get(self, paper_id: PaperId) -> Paper: ...

    def find(self, reference: str) -> Paper: ...

    def save(self, paper: Paper) -> Paper: ...

    def save_all(self, papers: Iterable[Paper], *, generated_at: datetime | None = ...) -> None: ...

    def delete(self, paper_id: PaperId) -> Paper: ...


class EventLog(Protocol):
    """Append-only project history."""

    def append(self, event: ProjectEvent) -> ProjectEvent: ...

    def extend(self, events: Iterable[ProjectEvent]) -> int: ...

    def all(self) -> list[ProjectEvent]: ...

    def for_project(self, project_id: PaperId) -> list[ProjectEvent]: ...


@dataclass(frozen=True, slots=True)
class IssueRef:
    """The parts of a GitHub issue this application cares about."""

    number: int
    title: str
    body: str
    state: str = "open"
    labels: tuple[str, ...] = ()

    @property
    def is_open(self) -> bool:
        return self.state != "closed"


@dataclass(frozen=True, slots=True)
class IssuePayload:
    """What to write to an issue."""

    title: str
    body: str
    labels: tuple[str, ...] = ()
    state: str | None = None


@dataclass(frozen=True, slots=True)
class RepositoryActivityRequest:
    """One repository to inspect, and the paths that matter within it."""

    slug: str
    branch: str | None = None
    manuscript_paths: tuple[str, ...] = ()
    analysis_paths: tuple[str, ...] = ()
    data_paths: tuple[str, ...] = ()
    include_issue_counts: bool = False


class ActivityGateway(Protocol):
    """A source of evidence about what a research repository has been doing."""

    def repository_activity(self, request: RepositoryActivityRequest) -> ActivitySnapshot | None:
        """The latest activity for one repository, or ``None`` if unreachable."""


class IssueGateway(Protocol):
    """The issue tracker used as a notification channel."""

    def list_managed_issues(self) -> list[IssueRef]: ...

    def ensure_labels(self, labels: Sequence[str]) -> None: ...

    def create_issue(self, payload: IssuePayload) -> IssueRef: ...

    def update_issue(self, number: int, payload: IssuePayload) -> IssueRef: ...

    def comment(self, number: int, body: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """What one issue-synchronisation run did."""

    created: tuple[str, ...] = field(default_factory=tuple)
    updated: tuple[str, ...] = field(default_factory=tuple)
    closed: tuple[str, ...] = field(default_factory=tuple)
    reopened: tuple[str, ...] = field(default_factory=tuple)
    skipped: tuple[str, ...] = field(default_factory=tuple)
    orphans_closed: tuple[int, ...] = field(default_factory=tuple)
    dry_run: bool = False

    def summary(self) -> str:
        head = "Would sync" if self.dry_run else "Synced"
        return (
            f"{head} issues: {len(self.created)} created, {len(self.updated)} updated, "
            f"{len(self.closed)} closed, {len(self.reopened)} reopened, "
            f"{len(self.orphans_closed)} orphaned closed, {len(self.skipped)} skipped."
        )
