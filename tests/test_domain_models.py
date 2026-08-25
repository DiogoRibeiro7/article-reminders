"""Domain model: validation, invariants, and serialisation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from article_reminders.domain.enums import DecisionOutcome, LifecycleStatus, Priority
from article_reminders.domain.errors import ValidationError
from article_reminders.domain.ids import new_paper_id, slugify
from article_reminders.domain.models import (
    NextAction,
    Paper,
    Reminder,
    RepositoryRef,
    SubmissionRecord,
    coerce_priority,
    coerce_status,
)
from article_reminders.domain.timeutils import ensure_aware, parse_datetime
from tests.conftest import NOW, days_ago, days_ahead, make_paper


class TestNextAction:
    def test_description_cannot_be_empty(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            NextAction(description="   ")

    def test_description_is_stripped(self) -> None:
        assert NextAction(description="  run the robustness check  ").description == (
            "run the robustness check"
        )

    def test_naive_deadlines_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NextAction(description="x", due_at=datetime(2026, 9, 1))

    def test_round_trips_through_a_dict(self) -> None:
        action = NextAction(description="x", due_at=days_ahead(3))
        assert NextAction.from_dict(action.to_dict()) == action


class TestRepositoryRef:
    def test_requires_owner_slash_name_for_github(self) -> None:
        with pytest.raises(ValidationError, match="owner/name"):
            RepositoryRef(slug="not-a-slug")

    def test_exposes_a_url(self) -> None:
        ref = RepositoryRef(slug="owner/name", paper_path="paper/")
        assert ref.url == "https://github.com/owner/name"
        assert (ref.owner, ref.name) == ("owner", "name")

    def test_a_non_github_provider_is_not_forced_into_that_shape(self) -> None:
        ref = RepositoryRef(slug="https://gitlab.com/g/p", provider="gitlab")
        assert ref.url == "https://gitlab.com/g/p"


class TestPaperValidation:
    def test_a_paper_needs_a_title(self) -> None:
        with pytest.raises(ValidationError, match="needs a title"):
            make_paper("   ")

    def test_a_missing_slug_is_derived_from_the_title(self) -> None:
        paper = Paper(id=new_paper_id(), title="Minimum Wage & Productivity", slug=slugify(""))
        assert str(paper.slug) == "minimum-wage-productivity"

    def test_every_timestamp_must_be_timezone_aware(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            make_paper(submitted_at=datetime(2026, 1, 1))

    def test_a_revision_deadline_before_its_submission_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="revision_due_at"):
            make_paper(submitted_at=days_ago(10), revision_due_at=days_ago(40))

    def test_publication_before_acceptance_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="published_at"):
            make_paper(accepted_at=days_ago(5), published_at=days_ago(30))

    def test_unknown_status_names_the_vocabulary(self) -> None:
        with pytest.raises(ValidationError, match="Valid: idea, planned"):
            coerce_status("experiments running")

    def test_priority_defaults_to_medium_only_when_absent(self) -> None:
        assert coerce_priority("") is Priority.MEDIUM
        with pytest.raises(ValidationError, match="Unknown priority"):
            coerce_priority("urgent")


class TestPaperDerivedState:
    def test_active_excludes_published_paused_and_abandoned(self) -> None:
        assert make_paper(status=LifecycleStatus.DRAFT).is_active
        assert not make_paper(status=LifecycleStatus.PUBLISHED).is_active
        assert not make_paper(status=LifecycleStatus.PAUSED).is_active
        assert not make_paper(status=LifecycleStatus.ABANDONED).is_active

    def test_waiting_covers_both_the_stage_and_an_explicit_note(self) -> None:
        assert make_paper(status=LifecycleStatus.UNDER_REVIEW).is_waiting
        assert make_paper(waiting_for="co-author").is_waiting
        assert not make_paper(status=LifecycleStatus.DRAFT).is_waiting

    def test_venue_prefers_the_journal(self) -> None:
        paper = make_paper(target_journal="Econometrica", target_conference="NeurIPS")
        assert paper.venue == "Econometrica"
        assert make_paper(target_conference="NeurIPS").venue == "NeurIPS"

    def test_deadlines_are_sorted_and_labelled(self) -> None:
        paper = make_paper(
            status=LifecycleStatus.REVISION,
            submitted_at=days_ago(60),
            revision_due_at=days_ahead(6),
            conference_deadline=days_ahead(2),
            next_action=NextAction(description="answer reviewer 2", due_at=days_ahead(30)),
        )
        kinds = [kind for kind, _, _ in paper.deadlines()]
        assert kinds == ["conference", "revision", "next_action"]

    def test_last_activity_uses_the_freshest_evidence(self) -> None:
        paper = make_paper(
            updated_at=days_ago(40),
            last_repository_activity_at=days_ago(30),
            last_manuscript_activity_at=days_ago(3),
        )
        assert paper.last_activity_at() == days_ago(3)


class TestPaperSerialisation:
    def test_round_trips_without_loss(self) -> None:
        paper = make_paper(
            "Minimum Wage and Productivity",
            status=LifecycleStatus.ANALYSIS,
            priority=Priority.HIGH,
            research_question="Does the minimum wage move productivity?",
            authors=("Ribeiro, D.",),
            tags=("labour", "portugal"),
            next_action=NextAction(description="Retrieve the OECD vintage", due_at=days_ahead(6)),
            submissions=(
                SubmissionRecord(
                    venue="Labour Economics",
                    submitted_at=days_ago(90),
                    decision=DecisionOutcome.MAJOR_REVISION,
                    decision_at=days_ago(20),
                ),
            ),
        )
        assert Paper.from_dict(paper.to_dict()) == paper

    def test_empty_values_are_left_out_of_the_file(self) -> None:
        data = make_paper().to_dict()
        assert "doi" not in data
        assert "submissions" not in data
        assert data["repository"] == "owner/name"

    def test_unknown_keys_survive_a_round_trip(self) -> None:
        """A migration must never silently discard a field it does not model."""
        raw = {
            "title": "A Paper",
            "status": "draft",
            "curiosity": "kept for later",
            "target_date": "2026-09-01",
        }
        paper = Paper.from_dict(raw)
        assert paper.extra["curiosity"] == "kept for later"
        assert paper.to_dict()["curiosity"] == "kept for later"

    def test_a_string_next_action_is_accepted_from_older_records(self) -> None:
        paper = Paper.from_dict(
            {
                "title": "A Paper",
                "status": "draft",
                "next_action": "finish the methodology section",
                "next_action_due_at": "2026-09-04",
            }
        )
        assert paper.next_action is not None
        assert paper.next_action.description == "finish the methodology section"
        assert paper.next_action_due_at == datetime(2026, 9, 4, tzinfo=UTC)

    def test_a_bad_submission_record_is_rejected_at_the_boundary(self) -> None:
        with pytest.raises(ValidationError, match="submissions must be a list"):
            Paper.from_dict({"title": "A Paper", "submissions": "nope"})

    def test_a_bad_github_issue_number_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="github_issue_number"):
            Paper.from_dict({"title": "A Paper", "github_issue_number": "not-a-number"})


class TestReminder:
    def test_sorts_by_severity_then_deadline(self) -> None:
        from article_reminders.domain.enums import ReminderKind, ReminderSeverity

        critical = Reminder(
            project_id=make_paper().id,
            kind=ReminderKind.DEADLINE_OVERDUE,
            severity=ReminderSeverity.CRITICAL,
            message="overdue",
            created_at=NOW,
            due_at=days_ahead(10),
        )
        warning = Reminder(
            project_id=make_paper().id,
            kind=ReminderKind.STAGE_STALE,
            severity=ReminderSeverity.WARNING,
            message="stale",
            created_at=NOW,
            due_at=days_ahead(1),
        )
        assert sorted([warning, critical], key=lambda item: item.sort_key)[0] is critical


class TestTimeUtils:
    def test_a_plain_date_is_anchored_at_midnight_utc(self) -> None:
        assert parse_datetime("2026-09-04") == datetime(2026, 9, 4, tzinfo=UTC)

    def test_a_trailing_z_is_understood(self) -> None:
        assert parse_datetime("2026-09-04T12:00:00Z") == datetime(2026, 9, 4, 12, tzinfo=UTC)

    def test_nonsense_is_rejected_with_the_field_name(self) -> None:
        with pytest.raises(ValidationError, match="revision_due_at"):
            parse_datetime("last tuesday", field="revision_due_at")

    def test_ensure_aware_converts_rather_than_assumes(self) -> None:
        from datetime import timedelta, timezone

        lisbon = timezone(timedelta(hours=1))
        value = datetime(2026, 9, 4, 1, 0, tzinfo=lisbon)
        assert ensure_aware(value) == datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
