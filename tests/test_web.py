"""The web application: every page, the JSON API, and the forms that change state."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from article_reminders.application.services import PortfolioService
from article_reminders.bootstrap import Application
from article_reminders.domain.enums import LifecycleStatus
from article_reminders.web.app import create_app
from tests.conftest import days_ago, days_ahead

S = LifecycleStatus


@pytest.fixture
def client(app: Application) -> Iterator[TestClient]:
    with TestClient(create_app(app)) as test_client:
        yield test_client


@pytest.fixture
def seeded(portfolio: PortfolioService) -> PortfolioService:
    portfolio.create(
        "Minimum Wage and Productivity in Portugal",
        status=S.ANALYSIS,
        repository="owner/minimum-wage",
        paper_path="paper/",
        next_action="Retrieve the revised OECD productivity vintage",
        next_action_due_at=days_ahead(6).isoformat(),
        tags=["labour"],
    )
    portfolio.create("A Stalled Draft", status=S.DRAFT, repository="owner/stalled")
    portfolio.create("An Accepted Paper", status=S.ACCEPTED, priority="low")
    return portfolio


class TestPages:
    @pytest.mark.parametrize(
        "path", ["/", "/papers", "/papers/new", "/board", "/calendar", "/analytics", "/settings"]
    )
    def test_every_page_renders(
        self, client: TestClient, seeded: PortfolioService, path: str
    ) -> None:
        response = client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_the_dashboard_answers_what_to_work_on_next(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        body = client.get("/").text
        assert "What to work on next" in body
        assert "Retrieve the revised OECD productivity vintage" in body
        assert "No next action defined." in body

    def test_the_dashboard_counts_the_buckets(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        body = client.get("/").text
        assert "Needs attention" in body
        assert "Under review" in body

    def test_the_paper_list_can_be_filtered(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        body = client.get("/papers", params={"q": "minimum wage"}).text
        assert "Minimum Wage" in body
        assert "A Stalled Draft" not in body

    def test_filtering_by_stage(self, client: TestClient, seeded: PortfolioService) -> None:
        body = client.get("/papers", params={"status": "draft"}).text
        assert "A Stalled Draft" in body
        assert "Minimum Wage" not in body

    def test_the_detail_page_shows_the_whole_record(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        paper = seeded.get("minimum-wage")
        body = client.get(f"/papers/{paper.id}").text

        assert paper.title in body
        assert "Retrieve the revised OECD productivity vintage" in body
        assert "owner/minimum-wage" in body
        assert "Observed activity" in body
        assert "Submission history" in body
        assert "Measured intervals" in body

    def test_the_detail_page_shows_the_warnings(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        paper = seeded.get("a-stalled-draft")
        assert "Active project has no next action." in client.get(f"/papers/{paper.id}").text

    def test_an_unknown_paper_is_a_404(self, client: TestClient) -> None:
        assert client.get("/papers/nope").status_code == 404

    def test_the_board_has_one_lane_per_stage_group(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        body = client.get("/board").text
        for label in ("Ideas", "Analysis", "Writing", "Under Review", "Published"):
            assert label in body

    def test_the_calendar_lists_deadlines(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        body = client.get("/calendar").text
        assert days_ahead(6).date().isoformat() in body

    def test_the_calendar_window_can_be_narrowed(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        assert "No deadlines recorded" in client.get("/calendar", params={"days": 2}).text

    def test_analytics_says_when_it_cannot_measure_something(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        assert "not enough data" in client.get("/analytics").text

    def test_settings_explains_that_github_is_optional(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        body = client.get("/settings").text
        assert "Staleness thresholds" in body
        assert "GitHub is optional" in body

    def test_the_legacy_banner_appears_only_before_migration(
        self, client: TestClient, app: Application
    ) -> None:
        from tests.conftest import write_legacy

        write_legacy(app.settings.paths.legacy)
        assert "Reading the legacy tracker" in client.get("/").text


class TestForms:
    def test_a_paper_can_be_created(self, client: TestClient, portfolio: PortfolioService) -> None:
        response = client.post(
            "/papers",
            data={
                "title": "A New Idea",
                "status": "idea",
                "priority": "high",
                "repository": "owner/new-idea",
                "paper_path": "paper/",
                "next_action": "Write the research question down",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        created = portfolio.get("a-new-idea")
        assert created.priority.value == "high"
        assert created.next_action is not None

    def test_creating_with_a_bad_repository_comes_back_with_an_error(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/papers", data={"title": "Broken", "repository": "not-a-slug"}, follow_redirects=True
        )
        assert "owner/name" in response.text

    def test_a_paper_can_be_edited(self, client: TestClient, seeded: PortfolioService) -> None:
        paper = seeded.get("an-accepted-paper")
        client.post(
            f"/papers/{paper.id}/edit",
            data={
                "title": "An Accepted Paper",
                "priority": "critical",
                "doi": "10.1000/example",
                "target_journal": "Econometrica",
            },
            follow_redirects=False,
        )
        updated = seeded.get(paper.id)
        assert updated.doi == "10.1000/example"
        assert updated.priority.value == "critical"

    def test_the_next_action_can_be_set_completed_and_cleared(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        paper = seeded.get("a-stalled-draft")

        client.post(
            f"/papers/{paper.id}/next-action",
            data={
                "description": "Finish the methodology section",
                "due_at": "2026-09-04",
                "action": "set",
            },
            follow_redirects=False,
        )
        assert seeded.get(paper.id).next_action is not None

        client.post(
            f"/papers/{paper.id}/next-action", data={"action": "clear"}, follow_redirects=False
        )
        assert seeded.get(paper.id).next_action is None

    def test_an_empty_next_action_is_refused_with_a_message(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        paper = seeded.get("a-stalled-draft")
        response = client.post(
            f"/papers/{paper.id}/next-action",
            data={"description": "  ", "action": "set"},
            follow_redirects=True,
        )
        assert "cannot be empty" in response.text

    def test_the_stage_can_be_changed(self, client: TestClient, seeded: PortfolioService) -> None:
        paper = seeded.get("minimum-wage")
        client.post(
            f"/papers/{paper.id}/status", data={"status": "draft"}, follow_redirects=False
        )
        assert seeded.get(paper.id).status is S.DRAFT

    def test_a_non_canonical_stage_change_is_refused_and_explained(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        paper = seeded.get("minimum-wage")
        response = client.post(
            f"/papers/{paper.id}/status", data={"status": "published"}, follow_redirects=True
        )
        assert "not a canonical transition" in response.text
        assert seeded.get(paper.id).status is S.ANALYSIS

    def test_it_can_be_forced(self, client: TestClient, seeded: PortfolioService) -> None:
        paper = seeded.get("minimum-wage")
        client.post(
            f"/papers/{paper.id}/status",
            data={"status": "published", "force": "true"},
            follow_redirects=False,
        )
        assert seeded.get(paper.id).status is S.PUBLISHED

    def test_a_board_move_is_allowed_and_recorded(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        paper = seeded.get("minimum-wage")
        response = client.post(
            "/board/move",
            data={"paper_id": str(paper.id), "column": "submission_ready"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert seeded.get(paper.id).status is S.READY_TO_SUBMIT

    def test_a_submission_and_a_decision_can_be_recorded(
        self, client: TestClient, seeded: PortfolioService
    ) -> None:
        paper = seeded.get("a-stalled-draft")
        client.post(
            f"/papers/{paper.id}/submission",
            data={"venue": "Labour Economics", "submitted_at": days_ago(30).date().isoformat()},
            follow_redirects=False,
        )
        assert seeded.get(paper.id).status is S.SUBMITTED

        client.post(
            f"/papers/{paper.id}/decision",
            data={
                "decision": "major_revision",
                "revision_due_at": days_ahead(20).date().isoformat(),
            },
            follow_redirects=False,
        )
        updated = seeded.get(paper.id)
        assert updated.status is S.REVISION
        assert updated.revision_due_at is not None


class TestApi:
    def test_papers(self, client: TestClient, seeded: PortfolioService) -> None:
        payload = client.get("/api/papers").json()
        assert {paper["title"] for paper in payload["papers"]} >= {"A Stalled Draft"}

    def test_one_paper(self, client: TestClient, seeded: PortfolioService) -> None:
        paper = seeded.get("minimum-wage")
        assert client.get(f"/api/papers/{paper.id}").json()["slug"] == str(paper.slug)

    def test_reminders(self, client: TestClient, seeded: PortfolioService) -> None:
        payload = client.get("/api/reminders").json()
        kinds = {item["kind"] for item in payload["reminders"]}
        assert "missing_next_action" in kinds

    def test_dashboard(self, client: TestClient, seeded: PortfolioService) -> None:
        payload = client.get("/api/dashboard").json()
        assert payload["counts"]["active"] == 3
        assert payload["focus"]

    def test_analytics(self, client: TestClient, seeded: PortfolioService) -> None:
        payload = client.get("/api/analytics").json()
        assert payload["total"] == 3
        # One accepted paper and no rejections, so the rate is measurable; the
        # stage medians are not, and say so rather than reporting zero.
        assert payload["acceptance_rate"] == 1.0
        assert payload["stage_durations"][1]["median_days"] is None

    def test_health(self, client: TestClient, seeded: PortfolioService) -> None:
        payload = client.get("/api/health").json()
        assert payload == {
            "healthy": True,
            "papers": 3,
            "legacy_fallback": False,
            "github": payload["github"],
        }

    def test_sync_without_github_is_a_503_not_a_crash(
        self, client: TestClient, seeded: PortfolioService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        assert client.post("/api/sync-github").status_code == 503
