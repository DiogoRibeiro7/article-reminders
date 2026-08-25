"""Composition root.

The only module that knows how every layer is wired together. The CLI and the web
application both build their world here, which is what stops them from drifting
into two different applications.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from article_reminders.application.activity import ActivityService
from article_reminders.application.github_sync import IssueSyncService
from article_reminders.application.ports import Clock
from article_reminders.application.reminders import ReminderEngine
from article_reminders.application.services import PortfolioService
from article_reminders.infrastructure.clock import SystemClock
from article_reminders.infrastructure.configuration.settings import Settings, load_settings
from article_reminders.infrastructure.github.activity import GitHubActivityGateway
from article_reminders.infrastructure.github.client import GitHubClient
from article_reminders.infrastructure.github.issues import GitHubIssueGateway
from article_reminders.infrastructure.storage.event_log import JsonlEventLog
from article_reminders.infrastructure.storage.json_store import JsonPaperRepository

logger = logging.getLogger(__name__)

LOG_LEVEL_ENV = "ARTICLE_REMINDERS_LOG_LEVEL"


class GitHubUnavailableError(RuntimeError):
    """GitHub was asked for, but is not configured.

    Everything that does not need GitHub keeps working: the application is
    GitHub-enhanced, not GitHub-dependent.
    """


@dataclass(frozen=True, slots=True)
class Application:
    """Everything wired together."""

    settings: Settings
    portfolio: PortfolioService
    reminders: ReminderEngine
    clock: Clock

    @property
    def papers(self) -> JsonPaperRepository:
        repository = self.portfolio.repository
        assert isinstance(repository, JsonPaperRepository)
        return repository

    @property
    def uses_legacy_fallback(self) -> bool:
        """Whether the portfolio is still being read out of ``data/articles.json``."""
        return self.papers.uses_legacy_fallback

    def github_client(self, *, scan: bool = False) -> GitHubClient:
        token = self.settings.github.scan_token() if scan else self.settings.github.token()
        if not token:
            variable = (
                self.settings.github.scan_token_env if scan else self.settings.github.token_env
            )
            raise GitHubUnavailableError(
                f"No GitHub token found. Set {variable} to use this command; every other "
                f"command works without it."
            )
        return GitHubClient(token, api_url=self.settings.github.api_url)

    def activity_service(self) -> ActivityService:
        gateway = GitHubActivityGateway(self.github_client(scan=True), clock=self.clock)
        return ActivityService(self.portfolio, gateway, self.settings)

    def issue_sync_service(self) -> IssueSyncService:
        repository = self.settings.github.resolved_repository()
        if not repository:
            raise GitHubUnavailableError(
                "No GitHub repository configured. Set github.repository in "
                "article-reminders.yml or the GITHUB_REPOSITORY environment variable."
            )
        gateway = GitHubIssueGateway(self.github_client(), self.settings.github, repository)
        return IssueSyncService(self.portfolio, gateway, self.settings, engine=self.reminders)


def configure_logging(level: str | None = None) -> None:
    """Structured, single-line logging to stderr."""
    resolved = (level or os.environ.get(LOG_LEVEL_ENV) or "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, resolved, logging.WARNING),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def build_application(
    *,
    root: Path | None = None,
    config: Path | None = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
) -> Application:
    """Wire the application for one process."""
    resolved = settings or load_settings(config, root=root)
    repository = JsonPaperRepository(
        resolved.paths.portfolio, legacy_path=resolved.paths.legacy
    )
    events = JsonlEventLog(resolved.paths.events)
    the_clock = clock or SystemClock()
    portfolio = PortfolioService(repository, events, clock=the_clock)
    return Application(
        settings=resolved,
        portfolio=portfolio,
        reminders=ReminderEngine(resolved),
        clock=the_clock,
    )
