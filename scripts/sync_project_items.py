from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


DATA_PATH = Path("data/articles.json")
ISSUE_PREFIX = "[article-reminder]"
MANAGED_LABEL = "article-reminder"

VALID_PROJECT_STATUSES = {
    "Backlog",
    "Planned",
    "Reading",
    "Writing",
    "Experiments",
    "Revising",
    "Submitted",
    "Camera-ready",
    "Done",
    "Blocked",
    "Archived",
}
VALID_PRIORITIES = {"Low", "Medium", "High", "Critical"}

STATUS_TO_PROJECT_STATUS = {
    "planned": "Planned",
    "in_progress": "Writing",
    "draft": "Writing",
    "submitted": "Submitted",
    "revising": "Revising",
    "finished": "Done",
    "published": "Done",
    "archived": "Archived",
    "cancelled": "Archived",
}


@dataclass(frozen=True)
class Article:
    title: str
    repo: str
    status: str
    priority: str
    venue: str
    target_date: str
    next_action: str

    @property
    def normalized_status(self) -> str:
        return self.status.strip().lower()

    @property
    def project_status(self) -> str:
        mapped = STATUS_TO_PROJECT_STATUS.get(self.normalized_status)
        if mapped:
            return mapped
        candidate = self.status.strip()
        if candidate in VALID_PROJECT_STATUSES:
            return candidate
        return "Backlog"

    @property
    def project_priority(self) -> str:
        candidate = self.priority.strip().lower()
        mapping = {
            "low": "Low",
            "medium": "Medium",
            "high": "High",
            "critical": "Critical",
        }
        return mapping.get(candidate, "Medium")

    @property
    def issue_title(self) -> str:
        return f"{ISSUE_PREFIX} {self.title}"

    @property
    def next_action_value(self) -> str:
        return self.next_action or ""


def graphql(token: str, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("errors") and "data" not in data:
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    return data["data"]


def load_articles(path: Path) -> List[Article]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("articles", [])
    articles: List[Article] = []
    for row in rows:
        title = str(row.get("title", "")).strip()
        repo = str(row.get("repo", "")).strip()
        if not title or not repo:
            continue

        notes = str(row.get("notes", "")).strip()
        next_action = str(row.get("next_action", "")).strip() or notes
        articles.append(
            Article(
                title=title,
                repo=repo,
                status=str(row.get("status", "planned")).strip(),
                priority=str(row.get("priority", "medium")).strip(),
                venue=str(row.get("venue", "")).strip(),
                target_date=str(row.get("target_date", "")).strip(),
                next_action=next_action,
            )
        )
    return articles


def get_project_meta(token: str, owner: str, number: int) -> tuple[str, Dict[str, Dict[str, Any]], Dict[str, str]]:
    query = """
    query($owner: String!, $number: Int!) {
      user(login: $owner) {
        projectV2(number: $number) {
          id
          fields(first: 50) {
            nodes {
              ... on ProjectV2FieldCommon { id name }
              ... on ProjectV2SingleSelectField {
                id
                name
                options { id name }
              }
            }
          }
        }
      }
      organization(login: $owner) {
        projectV2(number: $number) {
          id
          fields(first: 50) {
            nodes {
              ... on ProjectV2FieldCommon { id name }
              ... on ProjectV2SingleSelectField {
                id
                name
                options { id name }
              }
            }
          }
        }
      }
    }
    """
    data = graphql(token, query, {"owner": owner, "number": number})
    project = None
    if data.get("user") and data["user"].get("projectV2"):
        project = data["user"]["projectV2"]
    elif data.get("organization") and data["organization"].get("projectV2"):
        project = data["organization"]["projectV2"]
    if project is None:
        raise RuntimeError("Could not find project. Check PROJECT_OWNER and PROJECT_NUMBER.")

    fields: Dict[str, Dict[str, Any]] = {}
    options: Dict[str, str] = {}
    for node in project["fields"]["nodes"]:
        name = node["name"]
        fields[name] = node
        for option in node.get("options", []):
            options[f"{name}:{option['name']}"] = option["id"]
    return project["id"], fields, options


def get_repository_issues(token: str, owner: str, name: str) -> List[Dict[str, Any]]:
    query = """
    query($owner: String!, $name: String!, $label: String!) {
      repository(owner: $owner, name: $name) {
        issues(first: 100, states: [OPEN, CLOSED], labels: [$label], orderBy: {field: CREATED_AT, direction: DESC}) {
          nodes { id number title body }
        }
      }
    }
    """
    data = graphql(token, query, {"owner": owner, "name": name, "label": MANAGED_LABEL})
    repo = data.get("repository")
    if repo is None:
        raise RuntimeError("Repository not found or token lacks access.")
    return repo["issues"]["nodes"]


def get_project_items(token: str, project_id: str) -> Dict[str, str]:
    query = """
    query($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          items(first: 100) {
            nodes {
              id
              content {
                ... on Issue { id }
              }
            }
          }
        }
      }
    }
    """
    data = graphql(token, query, {"projectId": project_id})
    items = data["node"]["items"]["nodes"]
    mapping: Dict[str, str] = {}
    for item in items:
        content = item.get("content")
        if content and content.get("id"):
            mapping[content["id"]] = item["id"]
    return mapping


def add_issue_to_project(token: str, project_id: str, issue_id: str) -> str:
    mutation = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item { id }
      }
    }
    """
    data = graphql(token, mutation, {"projectId": project_id, "contentId": issue_id})
    return data["addProjectV2ItemById"]["item"]["id"]


def update_text_field(token: str, project_id: str, item_id: str, field_id: str, value: str) -> None:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: String!) {
      updateProjectV2ItemFieldValue(
        input: {
          projectId: $projectId,
          itemId: $itemId,
          fieldId: $fieldId,
          value: { text: $value }
        }
      ) { projectV2Item { id } }
    }
    """
    graphql(token, mutation, {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "value": value})


def update_date_field(token: str, project_id: str, item_id: str, field_id: str, value: str) -> None:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: Date!) {
      updateProjectV2ItemFieldValue(
        input: {
          projectId: $projectId,
          itemId: $itemId,
          fieldId: $fieldId,
          value: { date: $value }
        }
      ) { projectV2Item { id } }
    }
    """
    graphql(token, mutation, {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "value": value})


def clear_field(token: str, project_id: str, item_id: str, field_id: str) -> None:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!) {
      clearProjectV2ItemFieldValue(input: {projectId: $projectId, itemId: $itemId, fieldId: $fieldId}) {
        projectV2Item { id }
      }
    }
    """
    graphql(token, mutation, {"projectId": project_id, "itemId": item_id, "fieldId": field_id})


def update_single_select_field(token: str, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
      updateProjectV2ItemFieldValue(
        input: {
          projectId: $projectId,
          itemId: $itemId,
          fieldId: $fieldId,
          value: { singleSelectOptionId: $optionId }
        }
      ) { projectV2Item { id } }
    }
    """
    graphql(token, mutation, {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "optionId": option_id})


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip())


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    project_owner = os.environ.get("PROJECT_OWNER", "")
    project_number_str = os.environ.get("PROJECT_NUMBER", "")
    missing_env: List[str] = []
    if not token:
        missing_env.append("GITHUB_TOKEN")
    if not repository:
        missing_env.append("GITHUB_REPOSITORY")
    if not project_owner:
        missing_env.append("PROJECT_OWNER")
    if not project_number_str:
        missing_env.append("PROJECT_NUMBER")
    if missing_env:
        print(f"Missing required environment variables: {', '.join(missing_env)}", file=sys.stderr)
        sys.exit(1)
    if not DATA_PATH.exists():
        print(f"Missing data file: {DATA_PATH}", file=sys.stderr)
        sys.exit(1)

    project_number = int(project_number_str)
    repo_owner, repo_name = repository.split("/", 1)
    articles = load_articles(DATA_PATH)
    issues = get_repository_issues(token, repo_owner, repo_name)
    issues_by_title = {normalize_title(str(issue.get("title", ""))): issue for issue in issues}

    project_id, fields, options = get_project_meta(token, project_owner, project_number)
    project_items = get_project_items(token, project_id)

    required_fields = ["Status", "Priority", "Repo URL", "Venue", "Target date", "Next action"]
    missing = [name for name in required_fields if name not in fields]
    if missing:
        raise RuntimeError(f"Project is missing required fields: {', '.join(missing)}")

    for article in articles:
        issue = issues_by_title.get(normalize_title(article.issue_title))
        if issue is None:
            print(f"Issue not found for article: {article.title}")
            continue

        issue_id = issue["id"]
        item_id = project_items.get(issue_id)
        if item_id is None:
            item_id = add_issue_to_project(token, project_id, issue_id)
            print(f"Added issue #{issue['number']} to project: {article.title}")
        else:
            print(f"Project item already exists for issue #{issue['number']}: {article.title}")

        status_option = options.get(f"Status:{article.project_status}")
        if not status_option:
            raise RuntimeError(f"Project field option not found: Status:{article.project_status}")
        priority_option = options.get(f"Priority:{article.project_priority}")
        if not priority_option:
            raise RuntimeError(f"Project field option not found: Priority:{article.project_priority}")

        update_single_select_field(token, project_id, item_id, fields["Status"]["id"], status_option)
        update_single_select_field(token, project_id, item_id, fields["Priority"]["id"], priority_option)
        update_text_field(token, project_id, item_id, fields["Repo URL"]["id"], article.repo)
        update_text_field(token, project_id, item_id, fields["Venue"]["id"], article.venue)
        update_text_field(token, project_id, item_id, fields["Next action"]["id"], article.next_action_value)
        if article.target_date:
            update_date_field(token, project_id, item_id, fields["Target date"]["id"], article.target_date)
        else:
            clear_field(token, project_id, item_id, fields["Target date"]["id"])


if __name__ == "__main__":
    main()
