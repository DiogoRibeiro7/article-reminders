"""The reminder engine: deadlines, workflow problems, and stage-aware inactivity."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from article_reminders.application.reminders import ReminderEngine, group_by_paper
from article_reminders.domain.enums import LifecycleStatus, ReminderKind, ReminderSeverity
from article_reminders.domain.models import NextAction, Paper, Reminder
from article_reminders.infrastructure.configuration.settings import Settings
from tests.conftest import NOW, days_ago, days_ahead, make_paper

S = LifecycleStatus
K = ReminderKind


@pytest.fixture
def engine(settings: Settings) -> ReminderEngine:
    return ReminderEngine(settings)


def kinds(reminders: Sequence[Reminder]) -> set[ReminderKind]:
    return {item.kind for item in reminders}


def only(engine: ReminderEngine, paper: Paper, kind: ReminderKind) -> Reminder:
    matches = [item for item in engine.for_paper(paper, NOW) if item.kind is kind]
    assert matches, f"expected a {kind.value} reminder"
    return matches[0]


class TestDeadlines:
    def test_an_upcoming_next_action_warns(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.ANALYSIS,
            updated_at=NOW,
            next_action=NextAction(description="Rebuild the tables", due_at=days_ahead(6)),
        )
        reminder = only(engine, paper, K.DEADLINE_UPCOMING)
        assert reminder.severity is ReminderSeverity.WARNING
        assert "in 6 days" in reminder.message

    def test_an_overdue_deadline_is_critical(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="Rebuild the tables", due_at=days_ago(3)),
        )
        reminder = only(engine, paper, K.DEADLINE_OVERDUE)
        assert reminder.severity is ReminderSeverity.CRITICAL
        assert "3 days ago" in reminder.message

    def test_a_deadline_beyond_the_window_is_quiet(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="x", due_at=days_ahead(60)),
        )
        assert K.DEADLINE_UPCOMING not in kinds(engine.for_paper(paper, NOW))

    def test_a_revision_deadline_within_a_week_is_critical(
        self, engine: ReminderEngine
    ) -> None:
        paper = make_paper(
            status=S.REVISION,
            updated_at=NOW,
            submitted_at=days_ago(100),
            revision_due_at=days_ahead(6),
            next_action=NextAction(description="Answer reviewer 2"),
        )
        reminder = only(engine, paper, K.DEADLINE_UPCOMING)
        assert reminder.severity is ReminderSeverity.CRITICAL
        assert reminder.message.startswith("Revision in 6 days")

    def test_deadlines_on_finished_papers_are_ignored(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.PUBLISHED,
            next_action=NextAction(description="x", due_at=days_ago(3)),
        )
        assert engine.for_paper(paper, NOW) == []

    def test_a_conference_deadline_is_reported(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            target_conference="ICCSA",
            conference_deadline=days_ahead(9),
            next_action=NextAction(description="x"),
        )
        assert "ICCSA" in only(engine, paper, K.DEADLINE_UPCOMING).message


class TestWorkflowReminders:
    def test_an_active_paper_without_a_next_action(self, engine: ReminderEngine) -> None:
        paper = make_paper(status=S.ANALYSIS, updated_at=NOW)
        assert only(engine, paper, K.MISSING_NEXT_ACTION).message == (
            "Active project has no next action."
        )

    def test_a_paper_waiting_on_a_journal_does_not_owe_one(
        self, engine: ReminderEngine
    ) -> None:
        paper = make_paper(status=S.UNDER_REVIEW, updated_at=NOW, submitted_at=days_ago(10))
        assert K.MISSING_NEXT_ACTION not in kinds(engine.for_paper(paper, NOW))

    def test_ready_to_submit_without_a_venue(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.READY_TO_SUBMIT,
            updated_at=NOW,
            next_action=NextAction(description="Format the submission package"),
        )
        assert K.MISSING_TARGET_VENUE in kinds(engine.for_paper(paper, NOW))

    def test_a_draft_with_no_manuscript_activity_ever_detected(
        self, engine: ReminderEngine
    ) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="x"),
            last_repository_activity_at=days_ago(2),
            last_manuscript_activity_at=None,
        )
        assert "no manuscript activity has ever been detected" in only(
            engine, paper, K.ANALYSIS_WITHOUT_DRAFT
        ).message


class TestStagnation:
    def test_active_analysis_with_a_frozen_manuscript_is_reported(
        self, engine: ReminderEngine
    ) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="x"),
            last_repository_activity_at=days_ago(2),
            last_analysis_activity_at=days_ago(2),
            last_manuscript_activity_at=days_ago(36),
        )
        reminder = only(engine, paper, K.MANUSCRIPT_STAGNATION)
        assert reminder.message == (
            "Analysis remains active, but the manuscript has not changed for 36 days."
        )

    def test_a_quiet_repository_is_not_stagnation(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="x"),
            last_repository_activity_at=days_ago(40),
            last_manuscript_activity_at=days_ago(40),
        )
        assert K.MANUSCRIPT_STAGNATION not in kinds(engine.for_paper(paper, NOW))

    def test_a_manuscript_touched_last_week_is_not_stagnation(
        self, engine: ReminderEngine
    ) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="x"),
            last_repository_activity_at=days_ago(1),
            last_manuscript_activity_at=days_ago(6),
        )
        assert K.MANUSCRIPT_STAGNATION not in kinds(engine.for_paper(paper, NOW))

    def test_the_thresholds_are_configurable(self, settings: Settings) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="x"),
            last_repository_activity_at=days_ago(2),
            last_manuscript_activity_at=days_ago(10),
        )
        assert K.MANUSCRIPT_STAGNATION not in kinds(ReminderEngine(settings).for_paper(paper, NOW))

        tightened = replace(
            settings, stagnation=replace(settings.stagnation, manuscript_idle_days=7)
        )
        assert K.MANUSCRIPT_STAGNATION in kinds(ReminderEngine(tightened).for_paper(paper, NOW))


class TestInactivity:
    def test_staleness_is_measured_against_the_stage(self, engine: ReminderEngine) -> None:
        draft = make_paper(
            status=S.DRAFT, updated_at=days_ago(45), next_action=NextAction(description="x")
        )
        reviewed = make_paper(
            status=S.UNDER_REVIEW, updated_at=days_ago(45), submitted_at=days_ago(45)
        )

        assert K.STAGE_STALE in kinds(engine.for_paper(draft, NOW))
        assert K.STAGE_STALE not in kinds(engine.for_paper(reviewed, NOW))

    def test_a_frozen_manuscript_in_a_writing_stage_is_reported(
        self, engine: ReminderEngine
    ) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="x"),
            last_manuscript_activity_at=days_ago(47),
        )
        assert only(engine, paper, K.MANUSCRIPT_INACTIVITY).message == (
            "Manuscript has not changed for 47 days."
        )

    def test_repository_silence_is_reported(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.ANALYSIS,
            updated_at=NOW,
            next_action=NextAction(description="x"),
            last_repository_activity_at=days_ago(50),
        )
        assert K.REPOSITORY_INACTIVITY in kinds(engine.for_paper(paper, NOW))

    def test_project_inactivity_defers_to_the_stage_reminder(
        self, engine: ReminderEngine
    ) -> None:
        """The two are the same signal; only the more informative one is emitted."""
        paper = make_paper(
            status=S.DRAFT, updated_at=days_ago(90), next_action=NextAction(description="x")
        )
        found = kinds(engine.for_paper(paper, NOW))
        assert K.STAGE_STALE in found
        assert K.PROJECT_INACTIVITY not in found

    def test_project_inactivity_covers_the_deliberately_patient_stages(
        self, engine: ReminderEngine
    ) -> None:
        """An idea tolerates 90 quiet days; the portfolio as a whole tolerates 60."""
        paper = make_paper(
            status=S.IDEA, updated_at=days_ago(70), next_action=NextAction(description="x")
        )
        found = kinds(engine.for_paper(paper, NOW))
        assert K.STAGE_STALE not in found
        assert K.PROJECT_INACTIVITY in found

    def test_a_long_wait_on_a_venue_suggests_chasing_it(self, engine: ReminderEngine) -> None:
        paper = make_paper(
            status=S.UNDER_REVIEW,
            updated_at=days_ago(70),
            submitted_at=days_ago(70),
            waiting_for="Labour Economics decision",
        )
        reminder = only(engine, paper, K.AWAITING_EXTERNAL)
        assert "Labour Economics decision" in reminder.message
        assert reminder.severity is ReminderSeverity.INFO

    def test_finished_papers_generate_nothing(self, engine: ReminderEngine) -> None:
        for status in (S.PUBLISHED, S.PAUSED, S.ABANDONED):
            paper = make_paper(status=status, updated_at=days_ago(500))
            assert engine.for_paper(paper, NOW) == []


def test_generate_sorts_the_whole_portfolio_by_severity(engine: ReminderEngine) -> None:
    overdue = make_paper(
        "Overdue",
        paper_id="p1",
        status=S.DRAFT,
        updated_at=NOW,
        next_action=NextAction(description="x", due_at=days_ago(1)),
    )
    quiet = make_paper("Quiet", paper_id="p2", status=S.ANALYSIS, updated_at=NOW)

    reminders = engine.generate([quiet, overdue], NOW)
    assert reminders[0].severity is ReminderSeverity.CRITICAL
    grouped = group_by_paper(reminders)
    assert set(grouped) == {"p1", "p2"}


def test_reminders_serialise_for_the_api(engine: ReminderEngine) -> None:
    paper = make_paper(status=S.ANALYSIS, updated_at=NOW)
    payload = engine.for_paper(paper, NOW)[0].to_dict()
    assert payload["kind"] == K.MISSING_NEXT_ACTION.value
    assert payload["severity"] == "warning"
    assert payload["created_at"].startswith("2026-08-25")
