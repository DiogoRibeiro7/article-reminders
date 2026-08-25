"""Reading activity out of a GitHub repository.

Every question is asked with a path filter, so the answers are deterministic:
"when was the last commit that touched ``paper/``" is a fact, whereas "does this
commit message sound like writing" is a guess.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from article_reminders.application.ports import RepositoryActivityRequest
from article_reminders.domain.models import ActivitySnapshot
from article_reminders.domain.timeutils import now, parse_optional_datetime
from article_reminders.infrastructure.github.client import GitHubClient

logger = logging.getLogger(__name__)

#: Statuses that mean "this repository is not visible to this token". Reported as
#: unreachable rather than raised: most tracked repositories are private, and one
#: missing scope must not stop a portfolio-wide scan.
UNREACHABLE = (403, 404, 409, 451)


class GitHubActivityGateway:
    """Answers :class:`RepositoryActivityRequest` questions against the REST API."""

    def __init__(self, client: GitHubClient, *, clock: Any | None = None) -> None:
        self._client = client
        self._clock = clock

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else now()

    def repository_activity(self, request: RepositoryActivityRequest) -> ActivitySnapshot | None:
        """Latest overall, manuscript, analysis, and data activity for a repository."""
        meta = self._client.get(f"/repos/{request.slug}", tolerate=UNREACHABLE)
        if not meta.ok or not isinstance(meta.data, Mapping):
            logger.info("repository %s is unreachable (%s)", request.slug, meta.status)
            return None

        branch = request.branch or str(meta.data.get("default_branch") or "") or None
        pushed_at = parse_optional_datetime(meta.data.get("pushed_at"), field="pushed_at")

        overall = self._last_commit(request.slug, branch=branch)
        snapshot = ActivitySnapshot(
            repository_slug=request.slug,
            observed_at=self._now(),
            last_repository_activity_at=_latest(overall, pushed_at),
            last_manuscript_activity_at=self._last_commit_under(
                request.slug, request.manuscript_paths, branch=branch
            ),
            last_analysis_activity_at=self._last_commit_under(
                request.slug, request.analysis_paths, branch=branch
            ),
            last_data_activity_at=self._last_commit_under(
                request.slug, request.data_paths, branch=branch
            ),
            default_branch=branch,
        )
        if not request.include_issue_counts:
            return snapshot

        open_issues = meta.data.get("open_issues_count")
        pulls = self._client.get(
            f"/repos/{request.slug}/pulls?state=open&per_page=100", tolerate=UNREACHABLE
        )
        open_pulls = len(pulls.data) if pulls.ok and isinstance(pulls.data, list) else None
        return ActivitySnapshot(
            repository_slug=snapshot.repository_slug,
            observed_at=snapshot.observed_at,
            last_repository_activity_at=snapshot.last_repository_activity_at,
            last_manuscript_activity_at=snapshot.last_manuscript_activity_at,
            last_analysis_activity_at=snapshot.last_analysis_activity_at,
            last_data_activity_at=snapshot.last_data_activity_at,
            default_branch=snapshot.default_branch,
            open_issue_count=(
                None
                if open_issues is None
                else max(0, int(open_issues) - (open_pulls or 0))
            ),
            open_pull_request_count=open_pulls,
        )

    # -- internals --------------------------------------------------------

    def _last_commit(
        self, slug: str, *, path: str | None = None, branch: str | None
    ) -> datetime | None:
        query: dict[str, str] = {"per_page": "1"}
        if path:
            query["path"] = path
        if branch:
            query["sha"] = branch
        url = f"/repos/{slug}/commits?{urllib.parse.urlencode(query)}"
        response = self._client.get(url, tolerate=(*UNREACHABLE, 422))
        if not response.ok or not isinstance(response.data, list) or not response.data:
            return None
        head = response.data[0]
        if not isinstance(head, Mapping):
            return None
        commit = head.get("commit")
        if not isinstance(commit, Mapping):
            return None
        for section in ("committer", "author"):
            block = commit.get(section)
            if isinstance(block, Mapping) and block.get("date"):
                return parse_optional_datetime(block["date"], field="commit.date")
        return None

    def _last_commit_under(
        self, slug: str, paths: Sequence[str], *, branch: str | None
    ) -> datetime | None:
        """The newest commit touching any of ``paths``."""
        newest: datetime | None = None
        for path in paths:
            candidate = self._last_commit(slug, path=path.rstrip("/") or None, branch=branch)
            newest = _latest(newest, candidate)
        return newest


def _latest(*values: datetime | None) -> datetime | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None
