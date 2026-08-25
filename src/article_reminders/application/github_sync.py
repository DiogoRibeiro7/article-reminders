"""Keeping one GitHub issue per active paper.

This replaces ``scripts/sync_article_issues.py`` with the same conventions, on
purpose: the same ``[article-reminder]`` title prefix, the same
``article-reminder`` label, and the same orphan-closing safety rules. A repository
that has been running the old script for months must not wake up to sixty-five
duplicate issues.

Matching is therefore tried three ways, newest convention first:

1. the hidden ``article-reminders:id`` marker this version writes;
2. the legacy reminder key, which the old script wrote into the body;
3. the issue title.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from article_reminders.application.ports import IssueGateway, IssuePayload, IssueRef, SyncOutcome
from article_reminders.application.reminders import ReminderEngine, group_by_paper
from article_reminders.application.services import PortfolioService
from article_reminders.domain.enums import LifecycleStatus, ReminderKind, ReminderSeverity
from article_reminders.domain.models import Paper, Reminder
from article_reminders.infrastructure.configuration.settings import Settings

logger = logging.getLogger(__name__)

MARKER_PREFIX = "article-reminders:id="
MARKER_RE = re.compile(r"<!--\s*article-reminders:id=([A-Za-z0-9_-]+)\s*-->")
LEGACY_KEY_RE = re.compile(r"Reminder key:\*{0,2}\s*`([^`]+)`")

#: Workflow labels applied on top of the two identity labels.
NEEDS_ACTION_LABEL = "needs-action"
STALLED_LABEL = "stalled"
SUBMISSION_LABEL = "submission"
REVISION_LABEL = "revision"

_SUBMISSION_STATUSES = frozenset(
    {LifecycleStatus.READY_TO_SUBMIT, LifecycleStatus.SUBMITTED, LifecycleStatus.RESUBMITTED}
)


def legacy_reminder_key(title: str, repository: str) -> str:
    """The slug the old sync script wrote into every issue body.

    Reproduced exactly so an issue created by the old script is recognised as
    belonging to the migrated paper.
    """
    base = f"{title}::{repository}".strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")


def issue_marker(paper: Paper) -> str:
    return f"<!-- {MARKER_PREFIX}{paper.id} -->"


def render_issue_body(
    paper: Paper,
    reminders: Sequence[Reminder] = (),
    *,
    generated_at: datetime | None = None,
) -> str:
    """The issue body: what the paper is, what is next, and what is wrong."""
    lines: list[str] = [
        issue_marker(paper),
        "This issue is maintained automatically by article-reminders. "
        "The portfolio file is the source of truth; edit it rather than this issue.",
        "",
        f"- **Stage:** `{paper.status.value}`",
        f"- **Priority:** `{paper.priority.value}`",
    ]
    if paper.repository is not None:
        lines.append(f"- **Repository:** {paper.repository.url}")
        lines.append(f"- **Manuscript path:** `{paper.paper_path or 'not set'}`")
    if paper.venue:
        lines.append(f"- **Target venue:** {paper.venue}")
    if paper.waiting_for:
        lines.append(f"- **Waiting for:** {paper.waiting_for}")
    key = legacy_reminder_key(paper.title, paper.repository_slug or "")
    lines.append(f"- **Reminder key:** `{key}`")
    lines.append("")

    lines.append("## Next action")
    if paper.next_action is None:
        lines.append("_None set._ An active paper without a next action is a workflow problem.")
    else:
        due = paper.next_action.due_at
        suffix = f" (due {due.date().isoformat()})" if due is not None else ""
        lines.append(f"{paper.next_action.description}{suffix}")
    lines.append("")

    if paper.research_question:
        lines.extend(["## Research question", paper.research_question, ""])
    if paper.abstract:
        lines.extend(["## Abstract", paper.abstract, ""])
    if paper.notes:
        lines.extend(["## Notes", paper.notes, ""])

    if reminders:
        lines.append("## Reminders")
        for reminder in reminders:
            lines.append(f"- **{reminder.severity.label}** — {reminder.message}")
        lines.append("")

    activity = [
        ("Repository", paper.last_repository_activity_at),
        ("Manuscript", paper.last_manuscript_activity_at),
        ("Analysis", paper.last_analysis_activity_at),
    ]
    known = [(label, value) for label, value in activity if value is not None]
    if known:
        lines.append("## Last observed activity")
        for label, value in known:
            lines.append(f"- {label}: {value.date().isoformat()}")
        lines.append("")

    if generated_at is not None:
        lines.append(f"_Last synchronised {generated_at.date().isoformat()}._")
    return "\n".join(lines).strip() + "\n"


def workflow_labels(paper: Paper, reminders: Sequence[Reminder]) -> tuple[str, ...]:
    """The labels that describe this paper's current workflow state."""
    labels: list[str] = []
    kinds = {reminder.kind for reminder in reminders}
    if ReminderKind.MISSING_NEXT_ACTION in kinds or any(
        reminder.severity is ReminderSeverity.CRITICAL for reminder in reminders
    ):
        labels.append(NEEDS_ACTION_LABEL)
    if {
        ReminderKind.STAGE_STALE,
        ReminderKind.MANUSCRIPT_STAGNATION,
        ReminderKind.MANUSCRIPT_INACTIVITY,
    } & kinds:
        labels.append(STALLED_LABEL)
    if paper.status in _SUBMISSION_STATUSES:
        labels.append(SUBMISSION_LABEL)
    if paper.status is LifecycleStatus.REVISION:
        labels.append(REVISION_LABEL)
    return tuple(labels)


class IssueSyncService:
    """Reconcile the portfolio with the issues that mirror it."""

    def __init__(
        self,
        portfolio: PortfolioService,
        gateway: IssueGateway,
        settings: Settings,
        *,
        engine: ReminderEngine | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._gateway = gateway
        self._settings = settings
        self._engine = engine or ReminderEngine(settings)

    def sync(
        self, papers: Sequence[Paper] | None = None, *, dry_run: bool = False
    ) -> SyncOutcome:
        """Create, update, and close issues so they match the portfolio."""
        targets = list(papers if papers is not None else self._portfolio.list_papers())
        reference = self._portfolio.clock.now()
        reminders = group_by_paper(self._engine.generate(targets, reference))

        github = self._settings.github
        identity_labels = [github.managed_label, github.paper_label]
        if not dry_run:
            labels = [*identity_labels]
            if github.workflow_labels:
                labels += [NEEDS_ACTION_LABEL, STALLED_LABEL, SUBMISSION_LABEL, REVISION_LABEL]
            self._gateway.ensure_labels(labels)

        issues = self._gateway.list_managed_issues()
        created: list[str] = []
        updated: list[str] = []
        closed: list[str] = []
        reopened: list[str] = []
        skipped: list[str] = []
        matched: set[int] = set()

        for paper in targets:
            issue = find_issue_for_paper(paper, issues)
            if issue is not None:
                matched.add(issue.number)

            paper_reminders = reminders.get(str(paper.id), [])
            payload = IssuePayload(
                title=f"{github.issue_prefix} {paper.title}",
                body=render_issue_body(paper, paper_reminders, generated_at=reference),
                labels=(
                    *identity_labels,
                    *(workflow_labels(paper, paper_reminders) if github.workflow_labels else ()),
                ),
            )

            if paper.is_active:
                if issue is None:
                    if not dry_run:
                        fresh = self._gateway.create_issue(payload)
                        self._portfolio.record_issue_sync(paper, fresh.number, "created")
                    created.append(paper.title)
                elif issue.is_open:
                    if not dry_run:
                        self._gateway.update_issue(issue.number, payload)
                        self._portfolio.record_issue_sync(paper, issue.number, "updated")
                    updated.append(paper.title)
                else:
                    if not dry_run:
                        self._gateway.update_issue(
                            issue.number,
                            IssuePayload(payload.title, payload.body, payload.labels, state="open"),
                        )
                        self._portfolio.record_issue_sync(paper, issue.number, "reopened")
                    reopened.append(paper.title)
            elif issue is not None and issue.is_open:
                if not dry_run:
                    self._gateway.update_issue(
                        issue.number,
                        IssuePayload(payload.title, payload.body, payload.labels, state="closed"),
                    )
                    self._portfolio.record_issue_sync(paper, issue.number, "closed")
                closed.append(paper.title)
            else:
                skipped.append(paper.title)

        orphans = self._close_orphans(issues, matched, len(targets), dry_run=dry_run)

        outcome = SyncOutcome(
            created=tuple(created),
            updated=tuple(updated),
            closed=tuple(closed),
            reopened=tuple(reopened),
            skipped=tuple(skipped),
            orphans_closed=orphans,
            dry_run=dry_run,
        )
        logger.info("%s", outcome.summary())
        return outcome

    def _close_orphans(
        self,
        issues: Iterable[IssueRef],
        matched: set[int],
        paper_count: int,
        *,
        dry_run: bool,
    ) -> tuple[int, ...]:
        """Close managed issues no paper claims any more.

        Skipped entirely when the portfolio is empty: a truncated data file must
        never be able to close the whole board.
        """
        if paper_count == 0:
            logger.warning("skipping orphan cleanup: the portfolio is empty")
            return ()

        github = self._settings.github
        closed: list[int] = []
        for issue in issues:
            if issue.number in matched or not issue.is_open:
                continue
            if not issue.title.startswith(github.issue_prefix):
                continue
            if github.managed_label not in issue.labels:
                continue
            if not dry_run:
                self._gateway.comment(
                    issue.number,
                    "Closing automatically: no paper in the portfolio claims this reminder any "
                    "more. Restore the entry to reopen it.",
                )
                self._gateway.update_issue(
                    issue.number,
                    IssuePayload(title=issue.title, body=issue.body, state="closed"),
                )
            closed.append(issue.number)
        return tuple(closed)


def find_issue_for_paper(paper: Paper, issues: Iterable[IssueRef]) -> IssueRef | None:
    """Match a paper to an existing issue without ever creating a second one."""
    candidates = list(issues)

    for issue in candidates:
        marker = MARKER_RE.search(issue.body)
        if marker and marker.group(1) == str(paper.id):
            return issue

    if paper.github_issue_number is not None:
        for issue in candidates:
            if issue.number == paper.github_issue_number:
                return issue

    wanted_keys = {
        legacy_reminder_key(paper.title, paper.repository_slug or ""),
        legacy_reminder_key(paper.title, ""),
    }
    for issue in candidates:
        found = LEGACY_KEY_RE.search(issue.body)
        if found and found.group(1) in wanted_keys:
            return issue

    normalised = _normalise_title(paper.title)
    for issue in candidates:
        if _normalise_title(_strip_prefix(issue.title)) == normalised:
            return issue
    return None


def _strip_prefix(title: str) -> str:
    return re.sub(r"^\[[^\]]+\]\s*", "", title.strip())


def _normalise_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip()).lower()


def summarise_reminders(reminders: Mapping[str, Sequence[Reminder]]) -> str:
    """One line per paper with reminders, for a run log."""
    return "\n".join(
        f"{paper_id}: {len(items)} reminder(s)" for paper_id, items in sorted(reminders.items())
    )
