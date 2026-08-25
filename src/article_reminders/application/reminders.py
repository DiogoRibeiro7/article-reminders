"""The reminder engine.

It produces structured :class:`Reminder` objects rather than strings, so the CLI,
the web dashboard, and the GitHub issue body can each render the same finding in
their own way and the severities can be sorted rather than parsed.

Three families of reminder:

* **deadlines** — something is due, or was due;
* **inactivity** — nothing has happened for longer than this stage tolerates;
* **workflow** — the record itself is incoherent, such as an active paper with no
  next action, or a paper marked ready to submit with nowhere to submit it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime

from article_reminders.application.activity import detect_stagnation
from article_reminders.domain.enums import LifecycleStatus, ReminderKind, ReminderSeverity
from article_reminders.domain.models import Paper, Reminder
from article_reminders.domain.rules import (
    WRITING_STATUSES,
    evaluate_staleness,
    requires_next_action,
)
from article_reminders.domain.timeutils import days_between
from article_reminders.infrastructure.configuration.settings import Settings

logger = logging.getLogger(__name__)

#: A revision deadline this close is critical rather than merely upcoming: revision
#: windows are short and missing one usually means starting the submission again.
REVISION_URGENT_DAYS = 7


class ReminderEngine:
    """Evaluate the portfolio and say what needs attention."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def for_paper(self, paper: Paper, reference: datetime) -> list[Reminder]:
        """Every reminder one paper generates at ``reference``."""
        reminders: list[Reminder] = []
        reminders.extend(self._deadline_reminders(paper, reference))
        reminders.extend(self._workflow_reminders(paper, reference))
        reminders.extend(self._inactivity_reminders(paper, reference))
        return sorted(reminders, key=lambda item: item.sort_key)

    def generate(self, papers: Iterable[Paper], reference: datetime) -> list[Reminder]:
        """Every reminder the portfolio generates, most severe first."""
        reminders: list[Reminder] = []
        for paper in papers:
            reminders.extend(self.for_paper(paper, reference))
        reminders.sort(key=lambda item: item.sort_key)
        return reminders

    # -- deadlines --------------------------------------------------------

    def _deadline_reminders(self, paper: Paper, reference: datetime) -> Sequence[Reminder]:
        window = self._settings.reminders.upcoming_deadline_days
        out: list[Reminder] = []

        for kind, label, when in paper.deadlines():
            if not paper.is_active:
                continue
            remaining = days_between(reference, when)
            if remaining < 0:
                out.append(
                    self._reminder(
                        paper,
                        ReminderKind.DEADLINE_OVERDUE,
                        ReminderSeverity.CRITICAL,
                        f"{_deadline_label(kind, label)} was due {abs(remaining):.0f} days ago.",
                        reference,
                        due_at=when,
                        context={"deadline": kind, "days_overdue": round(abs(remaining), 1)},
                    )
                )
            elif remaining <= window:
                urgent = kind == "revision" and remaining <= REVISION_URGENT_DAYS
                out.append(
                    self._reminder(
                        paper,
                        ReminderKind.DEADLINE_UPCOMING,
                        ReminderSeverity.CRITICAL if urgent else ReminderSeverity.WARNING,
                        f"{_deadline_label(kind, label)} in {remaining:.0f} days.",
                        reference,
                        due_at=when,
                        context={"deadline": kind, "days_remaining": round(remaining, 1)},
                    )
                )
        return out

    # -- workflow ---------------------------------------------------------

    def _workflow_reminders(self, paper: Paper, reference: datetime) -> Sequence[Reminder]:
        out: list[Reminder] = []

        if requires_next_action(paper) and paper.next_action is None:
            out.append(
                self._reminder(
                    paper,
                    ReminderKind.MISSING_NEXT_ACTION,
                    ReminderSeverity.WARNING,
                    "Active project has no next action.",
                    reference,
                    context={"status": paper.status.value},
                )
            )

        if paper.status is LifecycleStatus.READY_TO_SUBMIT and paper.venue is None:
            out.append(
                self._reminder(
                    paper,
                    ReminderKind.MISSING_TARGET_VENUE,
                    ReminderSeverity.WARNING,
                    "Marked ready to submit, but no target journal or conference is set.",
                    reference,
                )
            )

        if (
            paper.status in WRITING_STATUSES
            and paper.repository is not None
            and paper.last_manuscript_activity_at is None
            and paper.last_repository_activity_at is not None
        ):
            out.append(
                self._reminder(
                    paper,
                    ReminderKind.ANALYSIS_WITHOUT_DRAFT,
                    ReminderSeverity.WARNING,
                    (
                        f"Tracked as {paper.status.label.lower()}, but no manuscript activity has "
                        f"ever been detected under "
                        f"{paper.paper_path or 'the configured manuscript paths'}."
                    ),
                    reference,
                    context={"paper_path": paper.paper_path},
                )
            )

        finding = detect_stagnation(paper, reference, self._settings)
        if finding is not None:
            out.append(
                self._reminder(
                    paper,
                    ReminderKind.MANUSCRIPT_STAGNATION,
                    ReminderSeverity.WARNING,
                    finding.message,
                    reference,
                    context={
                        "repository_age_days": round(finding.repository_age_days, 1),
                        "manuscript_age_days": (
                            None
                            if finding.manuscript_age_days is None
                            else round(finding.manuscript_age_days, 1)
                        ),
                    },
                )
            )
        return out

    # -- inactivity -------------------------------------------------------

    def _inactivity_reminders(self, paper: Paper, reference: datetime) -> Sequence[Reminder]:
        if not paper.is_active:
            return ()

        out: list[Reminder] = []
        thresholds = self._settings.reminders

        verdict = evaluate_staleness(paper, reference, self._settings.staleness)
        if verdict.is_stale and verdict.days_since_activity is not None:
            out.append(
                self._reminder(
                    paper,
                    ReminderKind.STAGE_STALE,
                    ReminderSeverity.WARNING,
                    (
                        f"No activity for {verdict.days_since_activity:.0f} days; the "
                        f"{paper.status.label.lower()} stage tolerates "
                        f"{verdict.threshold_days}."
                    ),
                    reference,
                    context={
                        "days": round(verdict.days_since_activity, 1),
                        "threshold": verdict.threshold_days,
                        "stage": paper.status.value,
                    },
                )
            )

        manuscript_at = paper.last_manuscript_activity_at
        if (
            paper.status in WRITING_STATUSES
            and manuscript_at is not None
            and days_between(manuscript_at, reference) > thresholds.manuscript_inactivity_days
        ):
            days = days_between(manuscript_at, reference)
            out.append(
                self._reminder(
                    paper,
                    ReminderKind.MANUSCRIPT_INACTIVITY,
                    ReminderSeverity.WARNING,
                    f"Manuscript has not changed for {days:.0f} days.",
                    reference,
                    context={"days": round(days, 1)},
                )
            )

        repository_at = paper.last_repository_activity_at
        if (
            repository_at is not None
            and days_between(repository_at, reference) > thresholds.repository_inactivity_days
        ):
            days = days_between(repository_at, reference)
            out.append(
                self._reminder(
                    paper,
                    ReminderKind.REPOSITORY_INACTIVITY,
                    ReminderSeverity.INFO if paper.is_waiting else ReminderSeverity.WARNING,
                    f"No repository activity for {days:.0f} days.",
                    reference,
                    context={"days": round(days, 1)},
                )
            )

        # Only reported when the stage threshold did not already fire, and when
        # nobody else owes the next move. Both of those are the same signal seen
        # through a sharper lens: a stage-aware staleness reminder, or a note that
        # the paper is waiting on a venue. This one catches what neither does --
        # a paper in a deliberately patient stage that has still gone quiet for
        # longer than the portfolio as a whole tolerates.
        last_activity = paper.last_activity_at()
        if (
            not verdict.is_stale
            and not paper.is_waiting
            and last_activity is not None
            and days_between(last_activity, reference) > thresholds.project_inactivity_days
        ):
            days = days_between(last_activity, reference)
            out.append(
                self._reminder(
                    paper,
                    ReminderKind.PROJECT_INACTIVITY,
                    ReminderSeverity.INFO,
                    f"No meaningful project activity for {days:.0f} days.",
                    reference,
                    context={"days": round(days, 1)},
                )
            )

        if paper.is_waiting:
            waited_since = paper.submitted_at or paper.updated_at
            waited = days_between(waited_since, reference)
            if waited > thresholds.waiting_follow_up_days:
                out.append(
                    self._reminder(
                        paper,
                        ReminderKind.AWAITING_EXTERNAL,
                        ReminderSeverity.INFO,
                        (
                            f"Waiting on {paper.waiting_for or 'an external decision'} for "
                            f"{waited:.0f} days; consider chasing it."
                        ),
                        reference,
                        context={"days": round(waited, 1)},
                    )
                )
        return out

    # -- helpers ----------------------------------------------------------

    def _reminder(
        self,
        paper: Paper,
        kind: ReminderKind,
        severity: ReminderSeverity,
        message: str,
        reference: datetime,
        *,
        due_at: datetime | None = None,
        context: dict[str, object] | None = None,
    ) -> Reminder:
        return Reminder(
            project_id=paper.id,
            kind=kind,
            severity=severity,
            message=message,
            created_at=reference,
            due_at=due_at,
            paper_title=paper.title,
            context=context or {},
        )


def _deadline_label(kind: str, label: str) -> str:
    if kind == "next_action":
        return f"Next action ({label})"
    if kind == "revision":
        return "Revision"
    if kind == "conference":
        return f"Conference deadline ({label})"
    if kind == "internal_review":
        return "Internal review"
    return label


def group_by_paper(reminders: Iterable[Reminder]) -> dict[str, list[Reminder]]:
    """Reminders keyed by paper id, each group most severe first."""
    grouped: dict[str, list[Reminder]] = {}
    for reminder in reminders:
        grouped.setdefault(str(reminder.project_id), []).append(reminder)
    for group in grouped.values():
        group.sort(key=lambda item: item.sort_key)
    return grouped
