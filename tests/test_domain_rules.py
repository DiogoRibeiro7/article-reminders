"""Lifecycle rules: transitions, staleness, and what counts as needing attention."""

from __future__ import annotations

import pytest

from article_reminders.domain.enums import BoardColumn, LifecycleStatus
from article_reminders.domain.models import NextAction
from article_reminders.domain.rules import (
    CANONICAL_TRANSITIONS,
    COLUMN_DEFAULT_STATUS,
    DEFAULT_STALENESS_DAYS,
    allowed_transitions,
    board_column_for,
    evaluate_staleness,
    is_transition_allowed,
    needs_attention_reasons,
    requires_next_action,
    staleness_threshold_days,
)
from tests.conftest import NOW, days_ago, days_ahead, make_paper

S = LifecycleStatus


class TestTransitions:
    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (S.IDEA, S.PLANNED),
            (S.PLANNED, S.RESEARCH),
            (S.RESEARCH, S.ANALYSIS),
            (S.ANALYSIS, S.DRAFT),
            (S.DRAFT, S.INTERNAL_REVIEW),
            (S.INTERNAL_REVIEW, S.READY_TO_SUBMIT),
            (S.READY_TO_SUBMIT, S.SUBMITTED),
            (S.SUBMITTED, S.UNDER_REVIEW),
            (S.UNDER_REVIEW, S.REVISION),
            (S.REVISION, S.RESUBMITTED),
            (S.RESUBMITTED, S.ACCEPTED),
            (S.ACCEPTED, S.PUBLISHED),
        ],
    )
    def test_the_canonical_path_is_allowed(self, source: S, target: S) -> None:
        assert is_transition_allowed(source, target)

    def test_skipping_the_middle_of_the_lifecycle_is_not_canonical(self) -> None:
        assert not is_transition_allowed(S.IDEA, S.SUBMITTED)
        assert not is_transition_allowed(S.ANALYSIS, S.PUBLISHED)

    def test_a_paper_can_always_be_paused_or_abandoned(self) -> None:
        for status in CANONICAL_TRANSITIONS:
            if status in (S.PAUSED, S.ABANDONED):
                continue
            assert S.PAUSED in allowed_transitions(status)
            assert S.ABANDONED in allowed_transitions(status)

    def test_paused_work_can_resume_anywhere_still_active(self) -> None:
        resumable = allowed_transitions(S.PAUSED)
        assert S.DRAFT in resumable
        assert S.UNDER_REVIEW in resumable
        assert S.PUBLISHED not in resumable

    def test_a_no_op_transition_is_allowed(self) -> None:
        assert is_transition_allowed(S.DRAFT, S.DRAFT)

    def test_every_status_has_a_board_column(self) -> None:
        for status in LifecycleStatus:
            assert isinstance(board_column_for(status), BoardColumn)

    def test_every_column_maps_back_to_a_status_in_that_column(self) -> None:
        for column, status in COLUMN_DEFAULT_STATUS.items():
            assert board_column_for(status) is column


class TestNextActionRule:
    def test_active_work_owes_a_next_action(self) -> None:
        assert requires_next_action(make_paper(status=S.ANALYSIS))

    def test_work_sitting_with_a_journal_does_not(self) -> None:
        assert not requires_next_action(make_paper(status=S.UNDER_REVIEW))
        assert not requires_next_action(make_paper(status=S.SUBMITTED))

    def test_work_explicitly_waiting_on_someone_does_not(self) -> None:
        assert not requires_next_action(make_paper(status=S.DRAFT, waiting_for="co-author"))

    def test_finished_work_does_not(self) -> None:
        assert not requires_next_action(make_paper(status=S.PUBLISHED))
        assert not requires_next_action(make_paper(status=S.PAUSED))


class TestStaleness:
    def test_the_threshold_comes_from_the_stage(self) -> None:
        assert staleness_threshold_days(S.DRAFT) == DEFAULT_STALENESS_DAYS[S.DRAFT]
        assert staleness_threshold_days(S.UNDER_REVIEW) == 180
        assert staleness_threshold_days(S.PUBLISHED) is None

    def test_configuration_overrides_the_default(self) -> None:
        assert staleness_threshold_days(S.DRAFT, {S.DRAFT: 3}) == 3

    def test_forty_five_quiet_days_is_stale_for_a_draft_but_not_under_review(self) -> None:
        """The whole point of stage-dependent staleness, in one test."""
        draft = make_paper(status=S.DRAFT, updated_at=days_ago(45))
        reviewed = make_paper(status=S.UNDER_REVIEW, updated_at=days_ago(45))

        assert evaluate_staleness(draft, NOW).is_stale
        assert not evaluate_staleness(reviewed, NOW).is_stale

    def test_a_stage_with_no_threshold_is_never_stale(self) -> None:
        paper = make_paper(status=S.PUBLISHED, updated_at=days_ago(900))
        verdict = evaluate_staleness(paper, NOW)
        assert not verdict.is_stale
        assert verdict.threshold_days is None

    def test_the_verdict_reports_how_far_past_the_threshold_it_is(self) -> None:
        verdict = evaluate_staleness(make_paper(status=S.DRAFT, updated_at=days_ago(20)), NOW)
        assert verdict.days_overdue == pytest.approx(6.0)


class TestNeedsAttention:
    def test_an_active_paper_without_a_next_action_is_flagged(self) -> None:
        reasons = needs_attention_reasons(make_paper(status=S.ANALYSIS, updated_at=NOW), NOW)
        assert "No next action defined." in reasons

    def test_an_overdue_next_action_is_flagged(self) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=NOW,
            next_action=NextAction(description="x", due_at=days_ago(2)),
        )
        assert "The next action is overdue." in needs_attention_reasons(paper, NOW)

    def test_ready_to_submit_with_nowhere_to_submit_is_flagged(self) -> None:
        paper = make_paper(
            status=S.READY_TO_SUBMIT,
            updated_at=NOW,
            next_action=NextAction(description="x", due_at=days_ahead(3)),
        )
        assert "Marked ready to submit with no target venue." in needs_attention_reasons(
            paper, NOW
        )

    def test_a_healthy_paper_has_no_reasons(self) -> None:
        paper = make_paper(
            status=S.DRAFT,
            updated_at=days_ago(1),
            next_action=NextAction(description="finish the methodology", due_at=days_ahead(10)),
        )
        assert needs_attention_reasons(paper, NOW) == ()

    def test_a_published_paper_is_never_flagged(self) -> None:
        paper = make_paper(status=S.PUBLISHED, updated_at=days_ago(400))
        assert needs_attention_reasons(paper, NOW) == ()
