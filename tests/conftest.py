"""Shared fixtures.

Everything is built on a temporary directory and a stopped clock, so no test
depends on today's date or touches the repository's own data files.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from article_reminders.application.services import PortfolioService
from article_reminders.bootstrap import Application, build_application
from article_reminders.domain.enums import LifecycleStatus, Priority
from article_reminders.domain.ids import PaperId, Slug
from article_reminders.domain.models import Paper, RepositoryRef
from article_reminders.infrastructure.clock import FixedClock
from article_reminders.infrastructure.configuration.settings import Settings

#: The instant every test pretends it is.
NOW = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def days_ago(days: float, *, reference: datetime = NOW) -> datetime:
    return reference - timedelta(days=days)


def days_ahead(days: float, *, reference: datetime = NOW) -> datetime:
    return reference + timedelta(days=days)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def settings(workspace: Path) -> Settings:
    return Settings.default(workspace)


@pytest.fixture
def app(workspace: Path, clock: FixedClock) -> Application:
    return build_application(root=workspace, clock=clock)


@pytest.fixture
def portfolio(app: Application) -> PortfolioService:
    return app.portfolio


def make_paper(
    title: str = "A Paper",
    *,
    paper_id: str = "paper0000001",
    status: LifecycleStatus = LifecycleStatus.DRAFT,
    priority: Priority = Priority.MEDIUM,
    repository: str | None = "owner/name",
    paper_path: str | None = "paper/",
    **fields: object,
) -> Paper:
    """A paper with sensible defaults, for domain-level tests."""
    return Paper(
        id=PaperId(paper_id),
        title=title,
        slug=Slug(title.lower().replace(" ", "-")),
        status=status,
        priority=priority,
        repository=(
            RepositoryRef(slug=repository, paper_path=paper_path) if repository else None
        ),
        created_at=fields.pop("created_at", days_ago(120)),  # type: ignore[arg-type]
        updated_at=fields.pop("updated_at", days_ago(1)),  # type: ignore[arg-type]
        **fields,  # type: ignore[arg-type]
    )


LEGACY_ARTICLE = {
    "title": "Uncertainty and Calibration Under Shift",
    "repo": "owner/uncertainty-bench",
    "status": "in_progress",
    "notes": "Experiments running on the medium grid.",
    "abstract": "We present a reproducible simulation benchmark.",
    "paper_path": "paper/",
    "priority": "high",
    "last_updated": "2026-03-07",
    "venue": "JOSS",
    "target_date": "2026-03-20",
    "next_action": "Regenerate tables and figures.",
}


def write_legacy(path: Path, *articles: Mapping[str, Any]) -> Path:
    """Write a legacy ``data/articles.json`` and return its path."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"articles": [dict(item) for item in articles] or [dict(LEGACY_ARTICLE)]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
