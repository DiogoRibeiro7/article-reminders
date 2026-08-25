"""GitHub adapters: the REST client, activity reading, and issue synchronisation."""

from article_reminders.infrastructure.github.activity import GitHubActivityGateway
from article_reminders.infrastructure.github.client import (
    GitHubClient,
    GitHubError,
    Response,
    Transport,
    UrllibTransport,
)
from article_reminders.infrastructure.github.issues import GitHubIssueGateway

__all__ = [
    "GitHubActivityGateway",
    "GitHubClient",
    "GitHubError",
    "GitHubIssueGateway",
    "Response",
    "Transport",
    "UrllibTransport",
]
