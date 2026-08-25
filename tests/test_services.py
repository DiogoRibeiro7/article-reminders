"""The service layer: creating, editing, and moving papers, and the history it records."""

from __future__ import annotations

import pytest

from article_reminders.application.services import PaperFilter, PortfolioService
from article_reminders.domain.enums import (
    BoardColumn,
    DecisionOutcome,
    LifecycleStatus,
    Priority,
    ProjectEventType,
)
from article_reminders.domain.errors import (
    AmbiguousPaperError,
    InvalidTransitionError,
    PaperNotFoundError,
    ValidationError,
)
from tests.conftest import NOW, days_ago, days_ahead

S = LifecycleStatus


def test_create_records_a_paper_and_an_event(portfolio: PortfolioService) -> None:
    paper = portfolio.create(
        "Minimum Wage and Productivity in Portugal",
        status=S.ANALYSIS,
        priority=Priority.HIGH,
        repository="owner/minimum-wage",
        paper_path="paper/",
        next_action="Retrieve the revised OECD productivity vintage",
        next_action_due_at="2026-08-31",
        tags=["labour", "portugal"],
    )

    assert str(paper.slug) == "minimum-wage-and-productivity-in-portugal"
    assert paper.status is S.ANALYSIS
    assert paper.next_action is not None
    assert paper.created_at == NOW
    assert paper.transitions[0].to_status is S.ANALYSIS

    types = [event.event_type for event in portfolio.timeline(paper)]
    assert ProjectEventType.PROJECT_CREATED in types
    assert ProjectEventType.NEXT_ACTION_CHANGED in types


def test_create_stamps_the_timestamp_its_status_implies(portfolio: PortfolioService) -> None:
    assert portfolio.create("A", status=S.DRAFT).draft_started_at == NOW
    assert portfolio.create("B", status=S.RESEARCH).started_at == NOW
    assert portfolio.create("C", status=S.IDEA).started_at is None


def test_slugs_are_made_unique(portfolio: PortfolioService) -> None:
    first = portfolio.create("Same Title")
    second = portfolio.create("Same Title")
    assert str(first.slug) == "same-title"
    assert str(second.slug) == "same-title-2"


def test_papers_are_found_by_id_slug_title_or_fragment(portfolio: PortfolioService) -> None:
    paper = portfolio.create("Housing Prices in Portuguese Municipalities")
    assert portfolio.get(paper.id).id == paper.id
    assert portfolio.get("housing-prices-in-portuguese-municipalities").id == paper.id
    assert portfolio.get("Housing Prices in Portuguese Municipalities").id == paper.id
    assert portfolio.get("municipalities").id == paper.id


def test_an_ambiguous_fragment_is_an_error_rather_than_a_guess(
    portfolio: PortfolioService,
) -> None:
    portfolio.create("Housing One")
    portfolio.create("Housing Two")
    with pytest.raises(AmbiguousPaperError, match="matches 2 papers"):
        portfolio.get("housing")


def test_a_missing_paper_is_an_error(portfolio: PortfolioService) -> None:
    with pytest.raises(PaperNotFoundError):
        portfolio.get("nothing-like-this")


def test_update_changes_fields_and_records_one_event(portfolio: PortfolioService) -> None:
    paper = portfolio.create("A Paper")
    updated = portfolio.update(paper, target_journal="Econometrica", tags="labour, wages")

    assert updated.target_journal == "Econometrica"
    assert updated.tags == ("labour", "wages")
    assert any(
        event.event_type is ProjectEventType.PROJECT_UPDATED
        for event in portfolio.timeline(updated)
    )


def test_update_refuses_the_fields_that_have_their_own_methods(
    portfolio: PortfolioService,
) -> None:
    paper = portfolio.create("A Paper")
    with pytest.raises(ValidationError, match="dedicated method"):
        portfolio.update(paper, status="draft")


def test_update_rejects_unknown_fields(portfolio: PortfolioService) -> None:
    paper = portfolio.create("A Paper")
    with pytest.raises(ValidationError, match="Unknown field"):
        portfolio.update(paper, impact_factor="high")


def test_updating_a_deadline_is_recorded_as_a_deadline_change(
    portfolio: PortfolioService,
) -> None:
    paper = portfolio.create("A Paper")
    portfolio.update(paper, conference_deadline="2026-11-01")
    assert any(
        event.event_type is ProjectEventType.DEADLINE_CHANGED
        for event in portfolio.timeline(paper)
    )


def test_a_repository_can_be_attached_after_the_fact(portfolio: PortfolioService) -> None:
    paper = portfolio.create("A Paper")
    updated = portfolio.update(paper, repository="owner/name", paper_path="manuscript/")
    assert updated.repository is not None
    assert updated.repository.slug == "owner/name"
    assert updated.paper_path == "manuscript/"


class TestStatusChanges:
    def test_a_canonical_move_is_recorded(self, portfolio: PortfolioService) -> None:
        paper = portfolio.create("A Paper", status=S.ANALYSIS)
        updated = portfolio.set_status(paper, S.DRAFT)

        assert updated.status is S.DRAFT
        assert updated.draft_started_at == NOW
        assert updated.transitions[-1].from_status is S.ANALYSIS
        assert not updated.transitions[-1].forced
        assert any(
            event.event_type is ProjectEventType.STATUS_CHANGED
            for event in portfolio.timeline(updated)
        )

    def test_a_non_canonical_move_is_refused_by_default(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.IDEA)
        with pytest.raises(InvalidTransitionError, match="idea -> published"):
            portfolio.set_status(paper, S.PUBLISHED)

    def test_a_researcher_can_override_a_transition_explicitly(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.IDEA)
        updated = portfolio.set_status(paper, S.PUBLISHED, force=True, note="already out")

        assert updated.status is S.PUBLISHED
        assert updated.transitions[-1].forced
        assert updated.transitions[-1].note == "already out"

    def test_a_forced_but_canonical_move_is_not_marked_as_forced(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.ANALYSIS)
        updated = portfolio.set_status(paper, S.DRAFT, force=True)
        assert not updated.transitions[-1].forced

    def test_moving_to_the_same_status_changes_nothing(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.DRAFT)
        assert portfolio.set_status(paper, S.DRAFT) == paper

    def test_acceptance_and_publication_are_recorded_as_their_own_events(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.UNDER_REVIEW)
        accepted = portfolio.set_status(paper, S.ACCEPTED)
        published = portfolio.set_status(accepted, S.PUBLISHED)

        types = {event.event_type for event in portfolio.timeline(published)}
        assert ProjectEventType.PAPER_ACCEPTED in types
        assert ProjectEventType.PAPER_PUBLISHED in types
        assert published.accepted_at == NOW
        assert published.published_at == NOW

    def test_a_board_move_lands_in_that_columns_status(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.IDEA)
        moved = portfolio.move_to_column(paper, BoardColumn.WRITING)
        assert moved.status is S.DRAFT
        assert moved.board_column is BoardColumn.WRITING

    def test_a_board_move_within_one_column_is_a_no_op(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.INTERNAL_REVIEW)
        assert portfolio.move_to_column(paper, BoardColumn.WRITING).status is S.INTERNAL_REVIEW


class TestNextActions:
    def test_setting_and_clearing(self, portfolio: PortfolioService) -> None:
        paper = portfolio.create("A Paper", status=S.DRAFT)
        with_action = portfolio.set_next_action(
            paper, "Finish the methodology section", due_at="2026-09-04"
        )
        assert with_action.next_action is not None
        assert with_action.next_action_due_at is not None

        cleared = portfolio.clear_next_action(with_action)
        assert cleared.next_action is None

    def test_an_empty_next_action_is_refused(self, portfolio: PortfolioService) -> None:
        paper = portfolio.create("A Paper")
        with pytest.raises(ValueError, match="cannot be empty"):
            portfolio.set_next_action(paper, "   ")

    def test_completing_can_immediately_queue_the_follow_up(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.DRAFT, next_action="Write the intro")
        done = portfolio.complete_next_action(paper, follow_up="Write the conclusion")

        assert done.next_action is not None
        assert done.next_action.description == "Write the conclusion"
        types = [event.event_type for event in portfolio.timeline(done)]
        assert ProjectEventType.NEXT_ACTION_COMPLETED in types


class TestSubmissions:
    def test_recording_a_submission_moves_the_paper_and_marks_it_waiting(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.READY_TO_SUBMIT, next_action="Send it")
        submitted = portfolio.record_submission(
            paper, "Labour Economics", submitted_at="2026-06-01"
        )

        assert submitted.status is S.SUBMITTED
        assert submitted.submissions[-1].venue == "Labour Economics"
        assert submitted.waiting_for == "Labour Economics decision"
        assert submitted.next_action is None
        assert submitted.target_journal == "Labour Economics"

    def test_a_revision_decision_lands_the_paper_in_revision_with_a_deadline(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.READY_TO_SUBMIT)
        submitted = portfolio.record_submission(paper, "Labour Economics")
        decided = portfolio.record_decision(
            submitted,
            DecisionOutcome.MAJOR_REVISION,
            revision_due_at=days_ahead(30).isoformat(),
        )

        assert decided.status is S.REVISION
        assert decided.revision_due_at == days_ahead(30)
        assert decided.submissions[-1].decision is DecisionOutcome.MAJOR_REVISION
        assert decided.waiting_for is None

    def test_a_rejection_sends_the_paper_back_to_draft(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.READY_TO_SUBMIT)
        submitted = portfolio.record_submission(paper, "Econometrica")
        rejected = portfolio.record_decision(submitted, DecisionOutcome.REJECT)
        assert rejected.status is S.DRAFT

    def test_an_acceptance_records_the_acceptance_date(
        self, portfolio: PortfolioService
    ) -> None:
        paper = portfolio.create("A Paper", status=S.READY_TO_SUBMIT)
        submitted = portfolio.record_submission(paper, "JOSS")
        accepted = portfolio.record_decision(submitted, DecisionOutcome.ACCEPT)
        assert accepted.status is S.ACCEPTED
        assert accepted.accepted_at == NOW


def test_notes_are_appended_with_a_date(portfolio: PortfolioService) -> None:
    paper = portfolio.create("A Paper")
    noted = portfolio.add_note(paper, "Referee 2 wants a robustness table.")
    assert noted.notes.startswith("2026-08-25: Referee 2")
    assert portfolio.add_note(noted, "Second thought.").notes.count("\n") == 1


def test_listing_puts_active_work_with_the_nearest_deadline_first(
    portfolio: PortfolioService,
) -> None:
    portfolio.create("Published", status=S.IDEA)
    portfolio.create("Later", status=S.DRAFT, next_action="x", next_action_due_at=days_ahead(30))
    portfolio.create("Sooner", status=S.DRAFT, next_action="y", next_action_due_at=days_ahead(2))

    titles = [paper.title for paper in portfolio.list_papers()]
    assert titles[:2] == ["Sooner", "Later"]


def test_filters_compose(portfolio: PortfolioService) -> None:
    portfolio.create("Alpha", status=S.DRAFT, priority=Priority.HIGH, tags=["labour"])
    portfolio.create("Beta", status=S.PUBLISHED, priority=Priority.HIGH, tags=["labour"])
    portfolio.create("Gamma", status=S.DRAFT, priority=Priority.LOW, tags=["housing"])

    criteria = PaperFilter(
        statuses=frozenset({S.DRAFT}),
        priorities=frozenset({Priority.HIGH}),
        tags=frozenset({"labour"}),
    )
    assert [paper.title for paper in portfolio.list_papers(criteria)] == ["Alpha"]


def test_the_free_text_filter_searches_the_repository_too(
    portfolio: PortfolioService,
) -> None:
    portfolio.create("Alpha", repository="owner/heavytails")
    portfolio.create("Beta", repository="owner/housing")
    found = portfolio.list_papers(PaperFilter(query="heavytails"))
    assert [paper.title for paper in found] == ["Alpha"]


def test_deleting_removes_the_paper_but_keeps_its_history(
    portfolio: PortfolioService,
) -> None:
    paper = portfolio.create("A Paper")
    portfolio.delete(paper)
    assert portfolio.list_papers() == ()
    assert portfolio.timeline(paper) != []


def test_changes_survive_a_reload(portfolio: PortfolioService) -> None:
    paper = portfolio.create("A Paper", status=S.DRAFT)
    portfolio.set_next_action(paper, "Finish section 3", due_at=days_ago(1).isoformat())
    reloaded = portfolio.get(paper.id)
    assert reloaded.next_action is not None
    assert reloaded.next_action.description == "Finish section 3"
