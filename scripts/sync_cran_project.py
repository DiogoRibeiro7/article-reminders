"""Synchronize R/CRAN tracker issues into a GitHub Project v2.

The sync is idempotent: existing project items are preserved and only missing
tracker issues are added.

Required environment variable:
    GITHUB_TOKEN: token with read access to configured repositories and write
                  access to the target GitHub Project v2.

Optional environment variable:
    CRAN_PORTFOLIO_CONFIG: path to the portfolio JSON configuration.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("data/cran_portfolio.json")
GRAPHQL_URL = "https://api.github.com/graphql"


@dataclass(frozen=True)
class PortfolioConfig:
    """Configuration for one cross-repository CRAN portfolio."""

    owner: str
    project_number: int
    issue_title_template: str
    repositories: tuple[str, ...]

    def issue_title_for(self, repository: str) -> str:
        """Build the expected tracker title for one repository."""
        repo_name = repository.split("/", 1)[1]
        return self.issue_title_template.format(repo=repo_name, repository=repository)


def graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute an authenticated GitHub GraphQL request."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL HTTP {exc.code}: {body}") from exc

    errors = result.get("errors")
    if errors:
        raise RuntimeError(f"GitHub GraphQL error: {json.dumps(errors, indent=2)}")
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub GraphQL response did not contain a data object")
    return data


def load_config(path: Path) -> PortfolioConfig:
    """Load and validate the portfolio configuration."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    project = raw.get("project", {})
    owner = str(project.get("owner", "")).strip()
    project_number = project.get("number")
    title_template = str(raw.get("issue_title_template", "[CRAN] {repo}")).strip()
    repositories_raw = raw.get("repositories", [])

    if not owner:
        raise ValueError("project.owner must be a non-empty string")
    if not isinstance(project_number, int) or project_number <= 0:
        raise ValueError("project.number must be a positive integer")
    if "{repo}" not in title_template and "{repository}" not in title_template:
        raise ValueError("issue_title_template must contain {repo} or {repository}")
    if not isinstance(repositories_raw, list) or not repositories_raw:
        raise ValueError("repositories must be a non-empty list")

    repositories: list[str] = []
    seen: set[str] = set()
    for value in repositories_raw:
        repository = str(value).strip()
        if repository.count("/") != 1:
            raise ValueError(f"Invalid repository slug: {repository!r}")
        if repository not in seen:
            repositories.append(repository)
            seen.add(repository)

    return PortfolioConfig(
        owner=owner,
        project_number=project_number,
        issue_title_template=title_template,
        repositories=tuple(repositories),
    )


def get_project_id(token: str, owner: str, number: int) -> str:
    """Resolve a user or organization Project v2 number to its node ID."""
    user_query = """
    query($owner: String!, $number: Int!) {
      user(login: $owner) { projectV2(number: $number) { id } }
    }
    """
    user_data = graphql(token, user_query, {"owner": owner, "number": number})
    user_project = (user_data.get("user") or {}).get("projectV2")
    if user_project:
        return str(user_project["id"])

    organization_query = """
    query($owner: String!, $number: Int!) {
      organization(login: $owner) { projectV2(number: $number) { id } }
    }
    """
    organization_data = graphql(
        token,
        organization_query,
        {"owner": owner, "number": number},
    )
    organization_project = (organization_data.get("organization") or {}).get("projectV2")
    if organization_project:
        return str(organization_project["id"])

    raise RuntimeError(f"Could not resolve Project #{number} for {owner}")


def get_project_issue_ids(token: str, project_id: str) -> set[str]:
    """Return issue node IDs already present in the target project."""
    query = """
    query($projectId: ID!, $cursor: String) {
      node(id: $projectId) {
        ... on ProjectV2 {
          items(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes { content { ... on Issue { id } } }
          }
        }
      }
    }
    """
    issue_ids: set[str] = set()
    cursor: str | None = None
    while True:
        data = graphql(token, query, {"projectId": project_id, "cursor": cursor})
        node = data.get("node")
        if not node:
            raise RuntimeError("Target Project v2 could not be read")
        items = node["items"]
        for item in items["nodes"]:
            content = item.get("content") or {}
            issue_id = content.get("id")
            if issue_id:
                issue_ids.add(str(issue_id))
        page_info = items["pageInfo"]
        if not page_info["hasNextPage"]:
            return issue_ids
        cursor = str(page_info["endCursor"])


def find_tracker_issue(token: str, repository: str, title: str) -> dict[str, Any] | None:
    """Find one tracker issue in a repository by its deterministic title."""
    owner, name = repository.split("/", 1)
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        issues(first: 100, states: [OPEN, CLOSED], orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes { id number title url state }
        }
      }
    }
    """
    data = graphql(token, query, {"owner": owner, "name": name})
    repo = data.get("repository")
    if repo is None:
        raise RuntimeError(f"Repository not found or inaccessible: {repository}")

    matches = [issue for issue in repo["issues"]["nodes"] if issue.get("title") == title]
    if not matches:
        return None
    if len(matches) > 1:
        numbers = ", ".join(str(issue["number"]) for issue in matches)
        raise RuntimeError(f"Multiple tracker issues in {repository}: {numbers}")
    return matches[0]


def add_issue_to_project(token: str, project_id: str, issue_id: str) -> str:
    """Add one issue to the target Project v2."""
    mutation = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item { id }
      }
    }
    """
    data = graphql(token, mutation, {"projectId": project_id, "contentId": issue_id})
    return str(data["addProjectV2ItemById"]["item"]["id"])


def main() -> int:
    """Synchronize all configured tracker issues into the target project."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("Missing required environment variable: GITHUB_TOKEN", file=sys.stderr)
        return 2

    config_path = Path(os.environ.get("CRAN_PORTFOLIO_CONFIG", str(DEFAULT_CONFIG_PATH)))
    if not config_path.exists():
        print(f"Portfolio config does not exist: {config_path}", file=sys.stderr)
        return 2

    config = load_config(config_path)
    project_id = get_project_id(token, config.owner, config.project_number)
    existing_issue_ids = get_project_issue_ids(token, project_id)

    added = 0
    unchanged = 0
    missing: list[str] = []

    for repository in config.repositories:
        expected_title = config.issue_title_for(repository)
        issue = find_tracker_issue(token, repository, expected_title)
        if issue is None:
            missing.append(repository)
            print(f"MISSING tracker issue: {repository} ({expected_title})")
            continue

        issue_id = str(issue["id"])
        if issue_id in existing_issue_ids:
            unchanged += 1
            print(f"OK already in project: {repository}#{issue['number']}")
            continue

        add_issue_to_project(token, project_id, issue_id)
        existing_issue_ids.add(issue_id)
        added += 1
        print(f"ADDED: {repository}#{issue['number']} -> Project #{config.project_number}")

    print(
        f"CRAN portfolio sync complete: added={added}, "
        f"already_present={unchanged}, missing={len(missing)}"
    )
    if missing:
        print("Repositories missing the expected tracker issue:", file=sys.stderr)
        for repository in missing:
            print(f"  - {repository}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
