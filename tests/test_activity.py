"""Activity classification, stagnation detection, and the activity sync loop."""

from __future__ import annotations

from dataclasses import replace

import pytest

from article_reminders.application.activity import (
    ActivityService,
    activity_request_for,
    classify_path,
    classify_paths,
    detect_stagnation,
    manuscript_paths_for,
)
from article_reminders.application.ports import RepositoryActivityRequest
from article_reminders.application.services import PortfolioService
from article_reminders.domain.enums import ActivityKind, LifecycleStatus
from article_reminders.domain.models import ActivitySnapshot, Paper
from article_reminders.infrastructure.configuration.settings import ActivityPaths, Settings
from tests.conftest import NOW, days_ago, make_paper

S = LifecycleStatus
PATHS = ActivityPaths()


class FakeActivityGateway:
    """Records what it was asked and answers from a fixed script."""

    def __init__(self, answers: dict[str, ActivitySnapshot | None] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[RepositoryActivityRequest] = []

    def repository_activity(self, request: RepositoryActivityRequest) -> ActivitySnapshot | None:
        self.requests.append(request)
        return self.answers.get(request.slug)


class TestPathClassification:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("paper/main.tex", ActivityKind.MANUSCRIPT),
            ("papers/01_identifiability/main.tex", ActivityKind.MANUSCRIPT),
            ("manuscript/sections/intro.md", ActivityKind.MANUSCRIPT),
            ("src/estimator.py", ActivityKind.ANALYSIS),
            ("notebooks/explore.ipynb", ActivityKind.ANALYSIS),
            ("results/table1.csv", ActivityKind.ANALYSIS),
            ("data/raw/oecd.csv", ActivityKind.DATA),
            ("datasets/panel.parquet", ActivityKind.DATA),
            ("README.md", ActivityKind.OTHER),
            (".github/workflows/ci.yml", ActivityKind.OTHER),
        ],
    )
    def test_paths_are_classified_by_prefix(self, path: str, expected: ActivityKind) -> None:
        assert classify_path(path, PATHS) is expected

    def test_the_longest_matching_prefix_wins(self) -> None:
        """A manuscript that lives under an analysis directory is still a manuscript."""
        paths = ActivityPaths(manuscript=("analysis/paper/",), analysis=("analysis/",))
        assert classify_path("analysis/paper/main.tex", paths) is ActivityKind.MANUSCRIPT
        assert classify_path("analysis/model.py", paths) is ActivityKind.ANALYSIS

    def test_a_papers_own_path_takes_priority_over_the_defaults(self) -> None:
        assert (
            classify_path("src/paper/main.tex", PATHS, manuscript_extra=("src/paper/",))
            is ActivityKind.MANUSCRIPT
        )

    def test_classify_paths_groups_a_commit(self) -> None:
        grouped = classify_paths(
            ["paper/main.tex", "src/model.py", "data/panel.csv", "README.md"], PATHS
        )
        assert grouped[ActivityKind.MANUSCRIPT] == ("paper/main.tex",)
        assert grouped[ActivityKind.ANALYSIS] == ("src/model.py",)
        assert grouped[ActivityKind.DATA] == ("data/panel.csv",)
        assert grouped[ActivityKind.OTHER] == ("README.md",)

    def test_classification_never_looks_at_a_commit_message(self) -> None:
        """Path-based and deterministic: "wip: writing" touching code is analysis."""
        assert classify_path("src/wip-writing-the-paper.py", PATHS) is ActivityKind.ANALYSIS


class TestRequestBuilding:
    def test_the_papers_own_paper_path_is_watched(self, settings: Settings) -> None:
        paper = make_paper(paper_path="papers/03_reconstruction/")
        assert manuscript_paths_for(paper, settings) == ("papers/03_reconstruction/",)

    def test_a_single_file_paper_path_is_left_alone(self, settings: Settings) -> None:
        paper = make_paper(paper_path="paper/paper.md")
        assert manuscript_paths_for(paper, settings) == ("paper/paper.md",)

    def test_without_a_paper_path_the_defaults_are_used(self, settings: Settings) -> None:
        paper = make_paper(paper_path=None)
        assert manuscript_paths_for(paper, settings) == settings.activity_paths.manuscript

    def test_a_paper_with_no_repository_produces_no_request(self, settings: Settings) -> None:
        assert activity_request_for(make_paper(repository=None), settings) is None


class TestStagnation:
    def test_the_documented_rule(self, settings: Settings) -> None:
        """A_r < 7 days and A_m > 30 days."""
        paper = make_paper(
            status=S.DRAFT,
            last_repository_activity_at=days_ago(3),
            last_analysis_activity_at=days_ago(3),
            last_manuscript_activity_at=days_ago(36),
        )
        finding = detect_stagnation(paper, NOW, settings)
        assert finding is not None
        assert finding.repository_age_days == pytest.approx(3.0)
        assert finding.manuscript_age_days == pytest.approx(36.0)

    def test_no_finding_when_the_repository_is_also_quiet(self, settings: Settings) -> None:
        paper = make_paper(
            status=S.DRAFT,
            last_repository_activity_at=days_ago(20),
            last_manuscript_activity_at=days_ago(60),
        )
        assert detect_stagnation(paper, NOW, settings) is None

    def test_no_finding_without_any_observed_activity(self, settings: Settings) -> None:
        assert detect_stagnation(make_paper(status=S.DRAFT), NOW, settings) is None

    def test_a_manuscript_never_touched_is_reported_only_once_it_should_exist(
        self, settings: Settings
    ) -> None:
        active = make_paper(status=S.DRAFT, last_repository_activity_at=days_ago(1))
        finding = detect_stagnation(active, NOW, settings)
        assert finding is not None
        assert finding.manuscript_never_touched

        early = make_paper(status=S.IDEA, last_repository_activity_at=days_ago(1))
        assert detect_stagnation(early, NOW, settings) is None

    def test_finished_papers_are_never_stagnant(self, settings: Settings) -> None:
        paper = make_paper(
            status=S.PUBLISHED,
            last_repository_activity_at=days_ago(1),
            last_manuscript_activity_at=days_ago(400),
        )
        assert detect_stagnation(paper, NOW, settings) is None

    def test_the_window_is_configurable(self, settings: Settings) -> None:
        paper = make_paper(
            status=S.DRAFT,
            last_repository_activity_at=days_ago(10),
            last_manuscript_activity_at=days_ago(60),
        )
        assert detect_stagnation(paper, NOW, settings) is None

        widened = replace(
            settings, stagnation=replace(settings.stagnation, repository_active_within_days=14)
        )
        assert detect_stagnation(paper, NOW, widened) is not None


class TestActivityService:
    def _snapshot(self, slug: str = "owner/name") -> ActivitySnapshot:
        return ActivitySnapshot(
            repository_slug=slug,
            observed_at=NOW,
            last_repository_activity_at=days_ago(2),
            last_manuscript_activity_at=days_ago(30),
            last_analysis_activity_at=days_ago(2),
        )

    def test_a_snapshot_is_folded_into_the_paper(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        paper = portfolio.create(
            "A Paper", status=S.DRAFT, repository="owner/name", paper_path="paper/"
        )
        gateway = FakeActivityGateway({"owner/name": self._snapshot()})

        report = ActivityService(portfolio, gateway, settings).sync()

        refreshed = portfolio.get(paper.id)
        assert refreshed.last_manuscript_activity_at == days_ago(30)
        assert refreshed.last_analysis_activity_at == days_ago(2)
        assert report.updated == ("A Paper",)
        assert gateway.requests[0].manuscript_paths == ("paper/",)

    def test_activity_events_are_recorded(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        from article_reminders.domain.enums import ProjectEventType

        paper = portfolio.create("A Paper", repository="owner/name")
        gateway = FakeActivityGateway({"owner/name": self._snapshot()})
        ActivityService(portfolio, gateway, settings).sync()

        types = {event.event_type for event in portfolio.timeline(paper)}
        assert ProjectEventType.MANUSCRIPT_ACTIVITY_DETECTED in types
        assert ProjectEventType.ANALYSIS_ACTIVITY_DETECTED in types

    def test_observed_timestamps_only_move_forward(self, portfolio: PortfolioService) -> None:
        """A narrower scan must not erase evidence a wider one already found."""
        paper = portfolio.create("A Paper", repository="owner/name")
        portfolio.apply_activity(paper, self._snapshot())
        current = portfolio.get(paper.id)

        stale = ActivitySnapshot(
            repository_slug="owner/name",
            observed_at=NOW,
            last_repository_activity_at=days_ago(400),
            last_manuscript_activity_at=None,
        )
        assert portfolio.apply_activity(current, stale) == current

    def test_papers_without_a_repository_are_skipped(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("No repository")
        report = ActivityService(portfolio, FakeActivityGateway(), settings).sync()
        assert report.skipped == ("No repository",)
        assert report.checked == 0

    def test_an_unreachable_repository_is_reported_not_raised(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("A Paper", repository="owner/private")
        report = ActivityService(portfolio, FakeActivityGateway({}), settings).sync()
        assert report.unreachable == ("A Paper",)

    def test_a_failing_repository_does_not_stop_the_run(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        class Exploding(FakeActivityGateway):
            def repository_activity(
                self, request: RepositoryActivityRequest
            ) -> ActivitySnapshot | None:
                if request.slug == "owner/broken":
                    raise RuntimeError("boom")
                return super().repository_activity(request)

        portfolio.create("Broken", repository="owner/broken")
        portfolio.create("Fine", repository="owner/name", paper_path="paper/")
        gateway = Exploding({"owner/name": self._snapshot()})

        report = ActivityService(portfolio, gateway, settings).sync()
        assert "Broken" in report.unreachable
        assert "Fine" in report.updated

    def test_one_repository_is_fetched_once_for_several_papers(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("Paper one", repository="owner/mono", paper_path="papers/01/")
        portfolio.create("Paper two", repository="owner/mono", paper_path="papers/01/")
        gateway = FakeActivityGateway({"owner/mono": self._snapshot("owner/mono")})

        report = ActivityService(portfolio, gateway, settings).sync()
        assert report.checked == 1
        assert len(gateway.requests) == 1

    def test_papers_in_one_repository_but_different_folders_are_asked_separately(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("Paper one", repository="owner/mono", paper_path="papers/01/")
        portfolio.create("Paper two", repository="owner/mono", paper_path="papers/02/")
        gateway = FakeActivityGateway({"owner/mono": self._snapshot("owner/mono")})

        ActivityService(portfolio, gateway, settings).sync()
        assert {request.manuscript_paths for request in gateway.requests} == {
            ("papers/01/",),
            ("papers/02/",),
        }


def test_a_paper_is_unchanged_when_the_snapshot_says_nothing_new(
    portfolio: PortfolioService,
) -> None:
    paper: Paper = portfolio.create("A Paper", repository="owner/name")
    empty = ActivitySnapshot(repository_slug="owner/name", observed_at=NOW)
    assert portfolio.apply_activity(paper, empty) == paper
