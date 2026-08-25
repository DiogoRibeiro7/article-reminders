"""Research pipeline analytics, including what it refuses to report."""

from __future__ import annotations

import pytest

from article_reminders.application.analytics import build_analytics, paper_durations
from article_reminders.application.services import PortfolioService
from article_reminders.domain.enums import DecisionOutcome, LifecycleStatus
from article_reminders.domain.models import StatusTransition, SubmissionRecord
from tests.conftest import NOW, days_ago, make_paper

S = LifecycleStatus


class TestDurations:
    def test_intervals_with_both_endpoints_are_measured(self) -> None:
        paper = make_paper(
            created_at=days_ago(200),
            draft_started_at=days_ago(120),
            submitted_at=days_ago(90),
            decision_received_at=days_ago(30),
            accepted_at=days_ago(20),
            published_at=days_ago(5),
        )
        durations = paper_durations(paper)
        assert durations.get("idea_to_draft") == pytest.approx(80.0)
        assert durations.get("draft_to_submission") == pytest.approx(30.0)
        assert durations.get("submission_to_decision") == pytest.approx(60.0)
        assert durations.get("acceptance_to_publication") == pytest.approx(15.0)
        assert durations.get("idea_to_publication") == pytest.approx(195.0)

    def test_a_missing_endpoint_is_unknown_rather_than_zero(self) -> None:
        paper = make_paper(created_at=days_ago(200))
        assert paper_durations(paper).get("draft_to_submission") is None

    def test_revision_to_resubmission_needs_the_submission_history(self) -> None:
        paper = make_paper(
            submissions=(
                SubmissionRecord(
                    venue="Journal",
                    submitted_at=days_ago(200),
                    decision=DecisionOutcome.MAJOR_REVISION,
                    decision_at=days_ago(120),
                ),
                SubmissionRecord(venue="Journal", submitted_at=days_ago(80)),
            )
        )
        assert paper_durations(paper).get("revision_to_resubmission") == pytest.approx(40.0)

    def test_without_a_second_round_it_is_unknown(self) -> None:
        paper = make_paper(
            submissions=(
                SubmissionRecord(
                    venue="Journal",
                    submitted_at=days_ago(200),
                    decision=DecisionOutcome.MAJOR_REVISION,
                    decision_at=days_ago(120),
                ),
            )
        )
        assert paper_durations(paper).get("revision_to_resubmission") is None

    def test_reversed_timestamps_are_not_reported_as_negative_durations(self) -> None:
        paper = make_paper(created_at=days_ago(10), draft_started_at=days_ago(50))
        assert paper_durations(paper).get("idea_to_draft") is None


class TestPortfolioAnalytics:
    def test_an_empty_portfolio_reports_no_medians(self) -> None:
        report = build_analytics([], NOW)
        assert report.total == 0
        assert all(not item.available for item in report.stage_durations)
        assert report.acceptance_rate is None

    def test_medians_are_reported_with_their_sample_size(self) -> None:
        papers = [
            make_paper("A", paper_id="a", created_at=days_ago(100), draft_started_at=days_ago(90)),
            make_paper("B", paper_id="b", created_at=days_ago(100), draft_started_at=days_ago(70)),
            make_paper("C", paper_id="c", created_at=days_ago(100)),
        ]
        statistic = build_analytics(papers, NOW).statistic("idea_to_draft")
        assert statistic is not None
        assert statistic.median_days == pytest.approx(20.0)
        assert statistic.sample_size == 2

    def test_counts_come_from_the_submission_history(self) -> None:
        paper = make_paper(
            status=S.REVISION,
            submissions=(
                SubmissionRecord(
                    venue="A",
                    submitted_at=days_ago(300),
                    decision=DecisionOutcome.REJECT,
                    decision_at=days_ago(250),
                ),
                SubmissionRecord(
                    venue="B",
                    submitted_at=days_ago(200),
                    decision=DecisionOutcome.MAJOR_REVISION,
                    decision_at=days_ago(150),
                ),
            ),
        )
        report = build_analytics([paper], NOW)
        assert report.submissions == 2
        assert report.decisions == 2
        assert report.rejections == 1
        assert report.revisions_requested == 1
        assert report.acceptance_rate == 0.0

    def test_a_paper_with_only_flat_timestamps_still_counts(self) -> None:
        paper = make_paper(status=S.PUBLISHED, submitted_at=days_ago(200), accepted_at=days_ago(30))
        report = build_analytics([paper], NOW)
        assert report.submissions == 1
        assert report.acceptances == 1
        assert report.publications == 1

    def test_paused_and_abandoned_are_counted_separately(self) -> None:
        papers = [
            make_paper("A", paper_id="a", status=S.PAUSED),
            make_paper("B", paper_id="b", status=S.ABANDONED),
            make_paper("C", paper_id="c", status=S.DRAFT),
        ]
        report = build_analytics(papers, NOW)
        assert (report.paused, report.abandoned, report.active) == (1, 1, 1)

    def test_stalled_papers_are_reported_from_the_dashboard(self) -> None:
        paper = make_paper("A", paper_id="a", status=S.DRAFT, updated_at=days_ago(90))
        report = build_analytics([paper], NOW, stalled_ids=["a"])
        assert report.stalled == 1

    def test_time_in_the_current_stage_uses_recorded_transitions(self) -> None:
        paper = make_paper(
            status=S.DRAFT,
            transitions=(
                StatusTransition(to_status=S.ANALYSIS, occurred_at=days_ago(80)),
                StatusTransition(
                    to_status=S.DRAFT, occurred_at=days_ago(30), from_status=S.ANALYSIS
                ),
            ),
        )
        stages = build_analytics([paper], NOW).time_in_current_stage
        assert [item.key for item in stages] == ["draft"]
        assert stages[0].median_days == pytest.approx(30.0)

    def test_without_transitions_the_stage_table_is_empty_rather_than_guessed(self) -> None:
        assert build_analytics([make_paper()], NOW).time_in_current_stage == ()

    def test_the_payload_serialises_unknown_medians_as_null(self) -> None:
        payload = build_analytics([make_paper()], NOW).to_dict()
        assert payload["stage_durations"][1]["median_days"] is None
        assert payload["acceptance_rate"] is None


def test_analytics_run_over_a_live_portfolio(portfolio: PortfolioService) -> None:
    portfolio.create("A Paper", status=S.READY_TO_SUBMIT)
    paper = portfolio.get("a-paper")
    submitted = portfolio.record_submission(paper, "Labour Economics")
    portfolio.record_decision(submitted, DecisionOutcome.ACCEPT)

    report = build_analytics(
        portfolio.list_papers(), NOW, events=portfolio.events.all()
    )
    assert report.submissions == 1
    assert report.acceptances == 1
    assert report.acceptance_rate == 1.0
