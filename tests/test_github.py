"""GitHub: the client, path-scoped activity reading, and issue synchronisation.

Every external call goes through a fake transport or a fake gateway; nothing here
touches the network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from article_reminders.application.github_sync import (
    IssueSyncService,
    find_issue_for_paper,
    legacy_reminder_key,
    render_issue_body,
    workflow_labels,
)
from article_reminders.application.ports import IssuePayload, IssueRef, RepositoryActivityRequest
from article_reminders.application.reminders import ReminderEngine
from article_reminders.application.services import PortfolioService
from article_reminders.domain.enums import LifecycleStatus, ReminderKind, ReminderSeverity
from article_reminders.domain.models import Reminder
from article_reminders.infrastructure.configuration.settings import GitHubSettings, Settings
from article_reminders.infrastructure.github.activity import GitHubActivityGateway
from article_reminders.infrastructure.github.client import GitHubClient, GitHubError, Response
from article_reminders.infrastructure.github.issues import GitHubIssueGateway
from tests.conftest import NOW, days_ago, make_paper

S = LifecycleStatus


class FakeTransport:
    """Answers from a routing table keyed by ``METHOD path``."""

    def __init__(self, routes: Mapping[str, Response] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str, object]] = []

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: bytes | None,
    ) -> Response:
        path = url.replace("https://api.github.com", "")
        body = json.loads(payload) if payload else None
        self.calls.append((method, path, body))
        for pattern, response in self.routes.items():
            route_method, route_path = pattern.split(" ", 1)
            if route_method == method and path.startswith(route_path):
                return response
        return Response(404, {"message": "Not Found"})


def commit(date: str) -> list[dict[str, object]]:
    return [{"sha": "abc", "commit": {"committer": {"date": date}}}]


class TestClient:
    def test_a_token_is_sent_as_a_bearer_header(self) -> None:
        captured: dict[str, Mapping[str, str]] = {}

        class Recorder(FakeTransport):
            def send(self, method, url, *, headers, payload):
                captured["headers"] = headers
                return Response(200, {})

        GitHubClient("secret", transport=Recorder()).get("/repos/owner/name")
        assert captured["headers"]["Authorization"] == "Bearer secret"

    def test_an_unexpected_error_raises(self) -> None:
        client = GitHubClient("t", transport=FakeTransport())
        with pytest.raises(GitHubError, match="404"):
            client.get("/repos/owner/name")

    def test_a_tolerated_status_is_returned_for_inspection(self) -> None:
        client = GitHubClient("t", transport=FakeTransport())
        assert client.get("/repos/owner/name", tolerate=(404,)).status == 404


class TestActivityGateway:
    def _gateway(self) -> tuple[GitHubActivityGateway, FakeTransport]:
        transport = FakeTransport(
            {
                "GET /repos/owner/name?": Response(200, {}),
                "GET /repos/owner/name/commits?per_page=1&path=paper": Response(
                    200, commit("2026-07-20T10:00:00Z")
                ),
                "GET /repos/owner/name/commits?per_page=1&path=src": Response(
                    200, commit("2026-08-23T10:00:00Z")
                ),
                "GET /repos/owner/name/commits?per_page=1&sha=main": Response(
                    200, commit("2026-08-24T10:00:00Z")
                ),
                "GET /repos/owner/name": Response(
                    200, {"default_branch": "main", "pushed_at": "2026-08-24T11:00:00Z"}
                ),
            }
        )
        return GitHubActivityGateway(GitHubClient("t", transport=transport)), transport

    def test_manuscript_and_analysis_activity_are_read_separately(self) -> None:
        gateway, transport = self._gateway()
        snapshot = gateway.repository_activity(
            RepositoryActivityRequest(
                slug="owner/name",
                manuscript_paths=("paper/",),
                analysis_paths=("src/",),
            )
        )

        assert snapshot is not None
        assert snapshot.last_manuscript_activity_at is not None
        assert snapshot.last_manuscript_activity_at.date().isoformat() == "2026-07-20"
        assert snapshot.last_analysis_activity_at is not None
        assert snapshot.last_analysis_activity_at.date().isoformat() == "2026-08-23"
        assert snapshot.default_branch == "main"

        asked = [path for method, path, _ in transport.calls if "commits" in path]
        assert any("path=paper" in path for path in asked)
        assert any("path=src" in path for path in asked)

    def test_repository_activity_is_the_newest_evidence_available(self) -> None:
        gateway, _ = self._gateway()
        snapshot = gateway.repository_activity(RepositoryActivityRequest(slug="owner/name"))
        assert snapshot is not None
        assert snapshot.last_repository_activity_at is not None
        assert snapshot.last_repository_activity_at.date().isoformat() == "2026-08-24"

    def test_an_invisible_repository_returns_none_rather_than_raising(self) -> None:
        """Most tracked repositories are private; a 404 is a scope problem, not a crash."""
        gateway = GitHubActivityGateway(GitHubClient("t", transport=FakeTransport()))
        assert gateway.repository_activity(RepositoryActivityRequest(slug="owner/secret")) is None


class TestIssueGateway:
    def test_an_existing_label_is_not_an_error(self) -> None:
        transport = FakeTransport(
            {
                "POST /repos/o/t/labels": Response(
                    422, {"errors": [{"resource": "Label", "code": "already_exists"}]}
                )
            }
        )
        gateway = GitHubIssueGateway(
            GitHubClient("t", transport=transport), GitHubSettings(), "o/t"
        )
        gateway.ensure_labels(["article-reminder"])

    def test_any_other_422_is_an_error(self) -> None:
        transport = FakeTransport(
            {
                "POST /repos/o/t/labels": Response(
                    422, {"errors": [{"resource": "Label", "code": "invalid"}]}
                )
            }
        )
        gateway = GitHubIssueGateway(
            GitHubClient("t", transport=transport), GitHubSettings(), "o/t"
        )
        with pytest.raises(RuntimeError, match="Could not create label"):
            gateway.ensure_labels(["article-reminder"])

    def test_pull_requests_are_not_mistaken_for_issues(self) -> None:
        transport = FakeTransport(
            {
                "GET /repos/o/t/issues": Response(
                    200,
                    [
                        {"number": 1, "title": "[article-reminder] A", "body": "", "labels": []},
                        {"number": 2, "title": "A PR", "body": "", "pull_request": {}},
                    ],
                )
            }
        )
        gateway = GitHubIssueGateway(
            GitHubClient("t", transport=transport), GitHubSettings(), "o/t"
        )
        assert [issue.number for issue in gateway.list_managed_issues()] == [1]


class FakeIssueGateway:
    """An in-memory issue tracker."""

    def __init__(self, issues: list[IssueRef] | None = None) -> None:
        self.issues = list(issues or [])
        self.created: list[IssuePayload] = []
        self.updated: list[tuple[int, IssuePayload]] = []
        self.comments: list[tuple[int, str]] = []
        self.labels: list[str] = []
        self._next_number = max((issue.number for issue in self.issues), default=0) + 1

    def list_managed_issues(self) -> list[IssueRef]:
        return list(self.issues)

    def ensure_labels(self, labels) -> None:
        self.labels.extend(labels)

    def create_issue(self, payload: IssuePayload) -> IssueRef:
        self.created.append(payload)
        issue = IssueRef(
            number=self._next_number,
            title=payload.title,
            body=payload.body,
            labels=payload.labels,
        )
        self._next_number += 1
        self.issues.append(issue)
        return issue

    def update_issue(self, number: int, payload: IssuePayload) -> IssueRef:
        self.updated.append((number, payload))
        issue = IssueRef(
            number=number,
            title=payload.title,
            body=payload.body,
            state=payload.state or "open",
            labels=payload.labels,
        )
        self.issues = [issue if item.number == number else item for item in self.issues]
        return issue

    def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))


def legacy_issue(
    number: int, title: str, repo: str = "owner/name", state: str = "open"
) -> IssueRef:
    """An issue exactly as ``scripts/sync_article_issues.py`` would have written it."""
    body = (
        "This issue is managed automatically by the article reminder workflow.\n\n"
        f"- **Title:** {title}\n"
        f"- **Repository:** `{repo}`\n"
        f"- **Reminder key:** `{legacy_reminder_key(title, repo)}`\n"
    )
    return IssueRef(
        number=number,
        title=f"[article-reminder] {title}",
        body=body,
        state=state,
        labels=("article-reminder",),
    )


class TestIssueMatching:
    def test_the_hidden_marker_wins(self) -> None:
        paper = make_paper("A Paper")
        issue = IssueRef(number=7, title="Renamed since", body=render_issue_body(paper))
        assert find_issue_for_paper(paper, [issue]) is issue

    def test_a_legacy_issue_is_recognised_by_its_reminder_key(self) -> None:
        """The whole point: no duplicate issues for a tracker migrated from the script."""
        paper = make_paper("Uncertainty and Calibration", repository="owner/uncertainty-bench")
        issue = legacy_issue(12, "Uncertainty and Calibration", "owner/uncertainty-bench")
        assert find_issue_for_paper(paper, [issue]) is issue

    def test_a_legacy_issue_is_recognised_by_its_title(self) -> None:
        paper = make_paper("A Paper", repository="owner/moved-since")
        issue = IssueRef(
            number=3,
            title="[article-reminder] A Paper",
            body="no key here",
            labels=("article-reminder",),
        )
        assert find_issue_for_paper(paper, [issue]) is issue

    def test_a_remembered_issue_number_is_honoured(self) -> None:
        paper = make_paper("A Paper", github_issue_number=42)
        issue = IssueRef(number=42, title="Something else entirely", body="")
        assert find_issue_for_paper(paper, [issue]) is issue

    def test_an_unrelated_issue_is_not_matched(self) -> None:
        paper = make_paper("A Paper")
        other = IssueRef(number=5, title="[article-reminder] Another Paper", body="")
        assert find_issue_for_paper(paper, [other]) is None


class TestIssueBody:
    def test_it_carries_the_marker_the_stage_and_the_next_action(self) -> None:
        from article_reminders.domain.models import NextAction

        paper = make_paper(
            "A Paper",
            status=S.ANALYSIS,
            next_action=NextAction(description="Rebuild the tables"),
        )
        body = render_issue_body(paper)
        assert f"article-reminders:id={paper.id}" in body
        assert "- **Stage:** `analysis`" in body
        assert "Rebuild the tables" in body

    def test_a_missing_next_action_is_stated_plainly(self) -> None:
        assert "_None set._" in render_issue_body(make_paper())

    def test_reminders_are_listed(self) -> None:
        paper = make_paper()
        reminder = Reminder(
            project_id=paper.id,
            kind=ReminderKind.STAGE_STALE,
            severity=ReminderSeverity.WARNING,
            message="No activity for 47 days.",
            created_at=NOW,
        )
        assert "No activity for 47 days." in render_issue_body(paper, [reminder])


class TestWorkflowLabels:
    def test_a_missing_next_action_earns_needs_action(self) -> None:
        paper = make_paper(status=S.DRAFT)
        reminder = Reminder(
            project_id=paper.id,
            kind=ReminderKind.MISSING_NEXT_ACTION,
            severity=ReminderSeverity.WARNING,
            message="x",
            created_at=NOW,
        )
        assert "needs-action" in workflow_labels(paper, [reminder])

    def test_staleness_earns_stalled(self) -> None:
        paper = make_paper(status=S.DRAFT)
        reminder = Reminder(
            project_id=paper.id,
            kind=ReminderKind.MANUSCRIPT_STAGNATION,
            severity=ReminderSeverity.WARNING,
            message="x",
            created_at=NOW,
        )
        assert "stalled" in workflow_labels(paper, [reminder])

    def test_the_stage_contributes_its_own_label(self) -> None:
        assert "revision" in workflow_labels(make_paper(status=S.REVISION), [])
        assert "submission" in workflow_labels(make_paper(status=S.SUBMITTED), [])


class TestIssueSync:
    def _service(
        self, portfolio: PortfolioService, settings: Settings, gateway: FakeIssueGateway
    ) -> IssueSyncService:
        return IssueSyncService(portfolio, gateway, settings, engine=ReminderEngine(settings))

    def test_an_active_paper_without_an_issue_gets_one(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        paper = portfolio.create("A Paper", status=S.DRAFT, repository="owner/name")
        gateway = FakeIssueGateway()

        outcome = self._service(portfolio, settings, gateway).sync()

        assert outcome.created == ("A Paper",)
        assert gateway.created[0].title == "[article-reminder] A Paper"
        assert portfolio.get(paper.id).github_issue_number == 1

    def test_a_migrated_tracker_reuses_its_existing_issues(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        """The duplicate-prevention guarantee, end to end."""
        portfolio.create("A Paper", status=S.DRAFT, repository="owner/name")
        gateway = FakeIssueGateway([legacy_issue(11, "A Paper")])

        outcome = self._service(portfolio, settings, gateway).sync()

        assert outcome.created == ()
        assert outcome.updated == ("A Paper",)
        assert gateway.updated[0][0] == 11

    def test_a_second_run_creates_nothing_new(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("A Paper", status=S.DRAFT, repository="owner/name")
        gateway = FakeIssueGateway()
        service = self._service(portfolio, settings, gateway)

        service.sync()
        second = service.sync()

        assert second.created == ()
        assert second.updated == ("A Paper",)
        assert len(gateway.issues) == 1

    def test_a_finished_paper_closes_its_issue(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("A Paper", status=S.PUBLISHED, repository="owner/name")
        gateway = FakeIssueGateway([legacy_issue(4, "A Paper")])

        outcome = self._service(portfolio, settings, gateway).sync()

        assert outcome.closed == ("A Paper",)
        assert gateway.updated[0][1].state == "closed"

    def test_a_revived_paper_reopens_its_issue(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("A Paper", status=S.DRAFT, repository="owner/name")
        gateway = FakeIssueGateway([legacy_issue(4, "A Paper", state="closed")])

        outcome = self._service(portfolio, settings, gateway).sync()

        assert outcome.reopened == ("A Paper",)
        assert gateway.updated[0][1].state == "open"

    def test_an_issue_no_paper_claims_is_closed_with_an_explanation(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("Kept", status=S.DRAFT, repository="owner/name")
        gateway = FakeIssueGateway([legacy_issue(9, "Deleted from the tracker")])

        outcome = self._service(portfolio, settings, gateway).sync()

        assert outcome.orphans_closed == (9,)
        assert gateway.comments[0][0] == 9
        assert "no paper in the portfolio claims" in gateway.comments[0][1]

    def test_an_empty_portfolio_never_closes_the_board(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        """A truncated data file must not be able to close sixty-five issues."""
        gateway = FakeIssueGateway([legacy_issue(9, "Something"), legacy_issue(10, "Else")])

        outcome = self._service(portfolio, settings, gateway).sync()

        assert outcome.orphans_closed == ()
        assert gateway.comments == []

    def test_hand_written_issues_are_left_alone(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("Kept", status=S.DRAFT, repository="owner/name")
        unmanaged = IssueRef(number=3, title="Please fix the CI", body="", labels=("bug",))
        gateway = FakeIssueGateway([unmanaged])

        outcome = self._service(portfolio, settings, gateway).sync()
        assert outcome.orphans_closed == ()

    def test_a_dry_run_writes_nothing(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        portfolio.create("A Paper", status=S.DRAFT, repository="owner/name")
        gateway = FakeIssueGateway()

        outcome = self._service(portfolio, settings, gateway).sync(dry_run=True)

        assert outcome.created == ("A Paper",)
        assert gateway.created == []
        assert gateway.labels == []

    def test_workflow_labels_can_be_switched_off(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        from dataclasses import replace

        quiet = replace(settings, github=replace(settings.github, workflow_labels=False))
        portfolio.create("A Paper", status=S.DRAFT, repository="owner/name")
        gateway = FakeIssueGateway()

        IssueSyncService(portfolio, gateway, quiet).sync()

        assert set(gateway.created[0].labels) == {"article-reminder", "research-paper"}

    def test_the_sync_is_recorded_in_the_papers_history(
        self, portfolio: PortfolioService, settings: Settings
    ) -> None:
        from article_reminders.domain.enums import ProjectEventType

        paper = portfolio.create("A Paper", status=S.DRAFT, repository="owner/name")
        self._service(portfolio, settings, FakeIssueGateway()).sync()

        assert any(
            event.event_type is ProjectEventType.ISSUE_SYNCHRONISED
            for event in portfolio.timeline(paper)
        )


def test_the_legacy_reminder_key_matches_the_old_script() -> None:
    """Reproduced from ``scripts/sync_article_issues.py``; the match depends on it."""
    assert legacy_reminder_key("A Paper: Part 1", "owner/name") == "a-paper-part-1-owner-name"


def test_activity_snapshots_are_serialisable() -> None:
    from article_reminders.domain.models import ActivitySnapshot

    snapshot = ActivitySnapshot(
        repository_slug="owner/name",
        observed_at=NOW,
        last_repository_activity_at=days_ago(3),
    )
    payload = snapshot.to_dict()
    assert payload["repository_slug"] == "owner/name"
    assert payload["last_repository_activity_at"].startswith("2026-08-22")
