"""The GitHub issue tracker as a notification channel.

One issue per active paper, exactly as this repository has always worked. The
portfolio file stays authoritative; the issue is a rendering of it that happens to
send email.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Mapping, Sequence

from article_reminders.application.ports import IssuePayload, IssueRef
from article_reminders.infrastructure.configuration.settings import GitHubSettings
from article_reminders.infrastructure.github.client import GitHubClient

logger = logging.getLogger(__name__)

LABEL_COLOURS: Mapping[str, str] = {
    "article-reminder": "0E8A16",
    "research-paper": "1D76DB",
    "needs-action": "D93F0B",
    "stalled": "B60205",
    "submission": "5319E7",
    "revision": "FBCA04",
}


class GitHubIssueGateway:
    """Create, update, and close the issues that mirror the portfolio."""

    def __init__(self, client: GitHubClient, settings: GitHubSettings, repository: str) -> None:
        self._client = client
        self._settings = settings
        self._repository = repository

    @property
    def repository(self) -> str:
        return self._repository

    def list_managed_issues(self) -> list[IssueRef]:
        """Every issue carrying the managed label, open or closed."""
        issues: list[IssueRef] = []
        page = 1
        while True:
            query = urllib.parse.urlencode(
                {
                    "state": "all",
                    "labels": self._settings.managed_label,
                    "per_page": "100",
                    "page": str(page),
                }
            )
            response = self._client.get(f"/repos/{self._repository}/issues?{query}")
            if not isinstance(response.data, list) or not response.data:
                break
            for item in response.data:
                if not isinstance(item, Mapping) or "pull_request" in item:
                    continue
                issues.append(_to_ref(item))
            if len(response.data) < 100:
                break
            page += 1
        return issues

    def ensure_labels(self, labels: Sequence[str]) -> None:
        """Create any label that does not exist yet.

        A 422 saying the label already exists is the expected outcome most of the
        time and is not an error; any other 422 is.
        """
        for label in labels:
            response = self._client.post(
                f"/repos/{self._repository}/labels",
                {
                    "name": label,
                    "color": LABEL_COLOURS.get(label, "EDEDED"),
                    "description": "Managed by article-reminders",
                },
                tolerate=(422,),
            )
            if response.ok:
                logger.info("created label %s", label)
                continue
            if _already_exists(response.data):
                continue
            raise RuntimeError(f"Could not create label {label!r}: {response.data}")

    def create_issue(self, payload: IssuePayload) -> IssueRef:
        response = self._client.post(
            f"/repos/{self._repository}/issues",
            {"title": payload.title, "body": payload.body, "labels": list(payload.labels)},
        )
        if not isinstance(response.data, Mapping):
            raise RuntimeError("GitHub did not return the created issue.")
        return _to_ref(response.data)

    def update_issue(self, number: int, payload: IssuePayload) -> IssueRef:
        body: dict[str, object] = {"title": payload.title, "body": payload.body}
        if payload.labels:
            body["labels"] = list(payload.labels)
        if payload.state is not None:
            body["state"] = payload.state
        response = self._client.patch(f"/repos/{self._repository}/issues/{number}", body)
        if not isinstance(response.data, Mapping):
            raise RuntimeError(f"GitHub did not return issue #{number}.")
        return _to_ref(response.data)

    def comment(self, number: int, body: str) -> None:
        self._client.post(f"/repos/{self._repository}/issues/{number}/comments", {"body": body})


def _already_exists(data: object) -> bool:
    if not isinstance(data, Mapping):
        return False
    errors = data.get("errors")
    if not isinstance(errors, list):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("resource") == "Label"
        and item.get("code") == "already_exists"
        for item in errors
    )


def _to_ref(raw: Mapping[str, object]) -> IssueRef:
    labels_raw = raw.get("labels")
    labels: tuple[str, ...] = ()
    if isinstance(labels_raw, list):
        labels = tuple(
            str(item.get("name", "")) if isinstance(item, Mapping) else str(item)
            for item in labels_raw
        )
    return IssueRef(
        number=int(str(raw.get("number", 0))),
        title=str(raw.get("title", "")),
        body=str(raw.get("body") or ""),
        state=str(raw.get("state", "open")),
        labels=labels,
    )
