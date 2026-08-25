"""Turning repository evidence into statements about a paper.

Two ideas carry this module. First, classification is path-based: a commit counts
as manuscript work because it touched the manuscript, not because its message said
"writing". Second, the manuscript and the analysis are tracked separately, which is
what makes it possible to notice that one is moving while the other is not.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from article_reminders.application.ports import ActivityGateway, RepositoryActivityRequest
from article_reminders.application.services import PortfolioService
from article_reminders.domain.enums import ActivityKind
from article_reminders.domain.models import ActivitySnapshot, Paper
from article_reminders.domain.rules import ANALYSIS_STATUSES, WRITING_STATUSES
from article_reminders.domain.timeutils import days_between
from article_reminders.infrastructure.configuration.settings import ActivityPaths, Settings

logger = logging.getLogger(__name__)

#: Ties are broken in this order, so a path configured under two headings counts
#: as the more specific kind of work.
_KIND_PRIORITY: Mapping[ActivityKind, int] = {
    ActivityKind.MANUSCRIPT: 3,
    ActivityKind.ANALYSIS: 2,
    ActivityKind.DATA: 1,
    ActivityKind.OTHER: 0,
}


def _normalise(prefix: str) -> str:
    return prefix.strip().lstrip("./").lower()


def classify_path(
    path: str,
    activity_paths: ActivityPaths,
    *,
    manuscript_extra: Sequence[str] = (),
) -> ActivityKind:
    """What kind of work a changed path represents.

    The longest matching prefix wins, so ``paper/analysis/`` configured as a
    manuscript path is not stolen by a shorter ``analysis/`` analysis prefix.
    """
    candidate = _normalise(path)
    if not candidate:
        return ActivityKind.OTHER

    groups: tuple[tuple[ActivityKind, Sequence[str]], ...] = (
        (ActivityKind.MANUSCRIPT, [*manuscript_extra, *activity_paths.manuscript]),
        (ActivityKind.ANALYSIS, activity_paths.analysis),
        (ActivityKind.DATA, activity_paths.data),
    )

    best_kind = ActivityKind.OTHER
    best_length = 0
    for kind, prefixes in groups:
        for prefix in prefixes:
            normalised = _normalise(prefix)
            if not normalised:
                continue
            matches = candidate == normalised.rstrip("/") or candidate.startswith(normalised)
            if not matches:
                continue
            length = len(normalised)
            if length > best_length or (
                length == best_length and _KIND_PRIORITY[kind] > _KIND_PRIORITY[best_kind]
            ):
                best_kind, best_length = kind, length
    return best_kind


def classify_paths(
    paths: Iterable[str],
    activity_paths: ActivityPaths,
    *,
    manuscript_extra: Sequence[str] = (),
) -> dict[ActivityKind, tuple[str, ...]]:
    """Group changed paths by what they represent."""
    buckets: dict[ActivityKind, list[str]] = {kind: [] for kind in ActivityKind}
    for path in paths:
        buckets[classify_path(path, activity_paths, manuscript_extra=manuscript_extra)].append(path)
    return {kind: tuple(values) for kind, values in buckets.items()}


def manuscript_paths_for(paper: Paper, settings: Settings) -> tuple[str, ...]:
    """The manuscript prefixes to watch for one paper.

    A paper's own ``paper_path`` is the sharpest signal available and comes first;
    the configured defaults are the fallback for papers that never set one.
    """
    own = paper.paper_path
    if own:
        return (own if own.endswith("/") or "." in own.rsplit("/", 1)[-1] else f"{own}/",)
    return settings.activity_paths.manuscript


def activity_request_for(
    paper: Paper, settings: Settings, *, include_issue_counts: bool = False
) -> RepositoryActivityRequest | None:
    """What to ask the gateway about this paper, or ``None`` if it has no repository."""
    if paper.repository is None:
        return None
    return RepositoryActivityRequest(
        slug=paper.repository.slug,
        branch=paper.repository.branch,
        manuscript_paths=manuscript_paths_for(paper, settings),
        analysis_paths=settings.activity_paths.analysis,
        data_paths=settings.activity_paths.data,
        include_issue_counts=include_issue_counts,
    )


@dataclass(frozen=True, slots=True)
class StagnationFinding:
    """Analysis is moving; the manuscript is not."""

    paper_id: str
    paper_title: str
    repository_age_days: float
    manuscript_age_days: float | None
    analysis_age_days: float | None
    message: str

    @property
    def manuscript_never_touched(self) -> bool:
        return self.manuscript_age_days is None


def detect_stagnation(
    paper: Paper, reference: datetime, settings: Settings
) -> StagnationFinding | None:
    """Report a paper whose repository is alive while its manuscript is not.

    The rule is ``A_r < repository_active_within_days`` and
    ``A_m > manuscript_idle_days``, where ``A_r`` is the age of the newest
    repository or analysis activity and ``A_m`` the age of the newest manuscript
    activity. Both thresholds are configurable.
    """
    if not paper.is_active:
        return None

    thresholds = settings.stagnation
    repository_at = paper.last_repository_activity_at
    analysis_at = paper.last_analysis_activity_at
    seen = [value for value in (repository_at, analysis_at) if value is not None]
    freshest = max(seen) if seen else None
    if freshest is None:
        return None

    repository_age = days_between(freshest, reference)
    if repository_age >= thresholds.repository_active_within_days:
        return None

    manuscript_at = paper.last_manuscript_activity_at
    analysis_age = days_between(analysis_at, reference) if analysis_at is not None else None

    if manuscript_at is None:
        # Nothing has ever touched the manuscript. Only worth reporting once the
        # paper claims to be at a stage where a manuscript should exist.
        if paper.status not in (WRITING_STATUSES | ANALYSIS_STATUSES):
            return None
        return StagnationFinding(
            paper_id=str(paper.id),
            paper_title=paper.title,
            repository_age_days=repository_age,
            manuscript_age_days=None,
            analysis_age_days=analysis_age,
            message=(
                f"The repository was active {repository_age:.0f} days ago, but no manuscript "
                f"activity has ever been detected under "
                f"{paper.paper_path or 'the configured manuscript paths'}."
            ),
        )

    manuscript_age = days_between(manuscript_at, reference)
    if manuscript_age <= thresholds.manuscript_idle_days:
        return None

    return StagnationFinding(
        paper_id=str(paper.id),
        paper_title=paper.title,
        repository_age_days=repository_age,
        manuscript_age_days=manuscript_age,
        analysis_age_days=analysis_age,
        message=(
            f"Analysis remains active, but the manuscript has not changed for "
            f"{manuscript_age:.0f} days."
        ),
    )


@dataclass(frozen=True, slots=True)
class ActivitySyncReport:
    """What one activity synchronisation run learned."""

    checked: int = 0
    updated: tuple[str, ...] = ()
    unreachable: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    def summary(self) -> str:
        return (
            f"Checked {self.checked} repositor{'y' if self.checked == 1 else 'ies'}: "
            f"{len(self.updated)} paper(s) updated, {len(self.unreachable)} unreachable, "
            f"{len(self.skipped)} without a repository."
        )


class ActivityService:
    """Fetch repository evidence and fold it into the portfolio."""

    def __init__(
        self,
        portfolio: PortfolioService,
        gateway: ActivityGateway,
        settings: Settings,
    ) -> None:
        self._portfolio = portfolio
        self._gateway = gateway
        self._settings = settings

    def sync(self, papers: Sequence[Paper] | None = None) -> ActivitySyncReport:
        """Refresh activity timestamps for every paper with a repository.

        Repositories are fetched once each, so several papers living in one
        monorepo cost one request between them.
        """
        targets = list(papers if papers is not None else self._portfolio.list_papers())
        cache: dict[tuple[str, tuple[str, ...]], ActivitySnapshot | None] = {}
        updated: list[str] = []
        unreachable: list[str] = []
        skipped: list[str] = []
        checked = 0

        for paper in targets:
            request = activity_request_for(paper, self._settings)
            if request is None:
                skipped.append(paper.title)
                continue

            key = (request.slug, request.manuscript_paths)
            if key not in cache:
                checked += 1
                try:
                    cache[key] = self._gateway.repository_activity(request)
                except Exception:  # one bad repository must not stop a portfolio-wide run
                    logger.exception("failed to read activity for %s", request.slug)
                    cache[key] = None
            snapshot = cache[key]

            if snapshot is None:
                unreachable.append(paper.title)
                continue
            refreshed = self._portfolio.apply_activity(paper, snapshot)
            if refreshed != paper:
                updated.append(paper.title)

        return ActivitySyncReport(
            checked=checked,
            updated=tuple(updated),
            unreachable=tuple(unreachable),
            skipped=tuple(skipped),
        )
