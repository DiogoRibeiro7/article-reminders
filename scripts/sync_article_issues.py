from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.error import HTTPError
from urllib.request import Request, urlopen


API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPOSITORY = os.environ.get("GITHUB_REPOSITORY")
TOKEN = os.environ.get("GITHUB_TOKEN")
DATA_PATH = Path("data/articles.json")

UNFINISHED_STATUSES = {"planned", "in_progress", "draft", "submitted", "revising"}
CLOSED_STATUSES = {"finished", "published", "archived", "cancelled"}
ISSUE_PREFIX = "[article-reminder]"
MANAGED_LABEL = "article-reminder"


@dataclass(frozen=True)
class Article:
    title: str
    repo: str
    status: str
    notes: str = ""
    paper_path: str = ""
    priority: str = ""
    last_updated: str = ""

    @property
    def issue_title(self) -> str:
        return f"{ISSUE_PREFIX} {self.title}"

    @property
    def normalized_status(self) -> str:
        return self.status.strip().lower()

    @property
    def slug(self) -> str:
        base = f"{self.title}::{self.repo}".strip().lower()
        return re.sub(r"[^a-z0-9]+", "-", base).strip("-")

    def to_issue_body(self) -> str:
        lines: List[str] = []
        lines.append("This issue is managed automatically by the article reminder workflow.")
        lines.append("")
        lines.append(f"- **Title:** {self.title}")
        lines.append(f"- **Repository:** `{self.repo}`")
        lines.append(f"- **Status:** `{self.status}`")
        lines.append(f"- **Priority:** `{self.priority or 'unspecified'}`")
        lines.append(f"- **Paper path:** `{self.paper_path or 'not set'}`")
        lines.append(f"- **Last updated:** `{self.last_updated or 'not set'}`")
        lines.append(f"- **Reminder key:** `{self.slug}`")
        lines.append("")
        if self.notes:
            lines.append("## Notes")
            lines.append(self.notes)
            lines.append("")
        lines.append("## What to do")
        lines.append("- Continue the article work in the linked repository.")
        lines.append("- Update `data/articles.json` when the status changes.")
        lines.append("- Mark the article as `finished` or `published` to close this reminder automatically.")
        return "\n".join(lines).strip() + "\n"


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def api_request(
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    expected_error_codes: Optional[Set[int]] = None,
) -> Any:
    if not TOKEN:
        fail("GITHUB_TOKEN is not available.")
    if not REPOSITORY:
        fail("GITHUB_REPOSITORY is not available.")

    url = f"{API_URL}{path}"
    data: Optional[bytes] = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "article-reminder-bot",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url=url, data=data, headers=headers, method=method)
    try:
        with urlopen(request) as response:
            content = response.read().decode("utf-8")
            return json.loads(content) if content else None
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if expected_error_codes and exc.code in expected_error_codes:
            try:
                parsed_body: Any = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed_body = {"message": body}
            return {"__error__": {"status": exc.code, "body": parsed_body}}
        fail(f"GitHub API request failed: {method} {path} -> {exc.code}\n{body}")


def load_articles(path: Path) -> List[Article]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("articles", [])
    articles: List[Article] = []
    for item in items:
        articles.append(
            Article(
                title=str(item["title"]),
                repo=str(item["repo"]),
                status=str(item["status"]),
                notes=str(item.get("notes", "")),
                paper_path=str(item.get("paper_path", "")),
                priority=str(item.get("priority", "")),
                last_updated=str(item.get("last_updated", "")),
            )
        )
    return articles


def list_open_issues() -> List[Dict[str, Any]]:
    return api_request(
        "GET",
        f"/repos/{REPOSITORY}/issues?state=all&labels={MANAGED_LABEL}&per_page=100",
    )


def ensure_label_exists() -> None:
    response = api_request(
        "POST",
        f"/repos/{REPOSITORY}/labels",
        {
            "name": MANAGED_LABEL,
            "color": "0E8A16",
            "description": "Managed reminder issues for article work",
        },
        expected_error_codes={422},
    )
    if isinstance(response, dict) and "__error__" in response:
        error = response["__error__"]
        body = error.get("body", {})
        details = body.get("errors", []) if isinstance(body, dict) else []
        label_exists = any(
            isinstance(item, dict)
            and item.get("resource") == "Label"
            and item.get("code") == "already_exists"
            for item in details
        )
        if label_exists:
            return
        fail(f"Unexpected error while ensuring label exists: {json.dumps(body)}")


def create_issue(article: Article) -> None:
    payload = {
        "title": article.issue_title,
        "body": article.to_issue_body(),
        "labels": [MANAGED_LABEL],
    }
    api_request("POST", f"/repos/{REPOSITORY}/issues", payload)
    print(f"Created issue for: {article.title}")


def update_issue(issue_number: int, article: Article, state: Optional[str] = None) -> None:
    payload: Dict[str, Any] = {
        "title": article.issue_title,
        "body": article.to_issue_body(),
    }
    if state is not None:
        payload["state"] = state
    api_request("PATCH", f"/repos/{REPOSITORY}/issues/{issue_number}", payload)
    print(f"Updated issue #{issue_number} for: {article.title}")


def close_issue(issue_number: int, article: Article) -> None:
    update_issue(issue_number, article, state="closed")
    print(f"Closed issue #{issue_number} for: {article.title}")


def find_issue_for_article(issues: Iterable[Dict[str, Any]], article: Article) -> Optional[Dict[str, Any]]:
    expected_title = article.issue_title
    expected_key = f"`{article.slug}`"
    for issue in issues:
        title = str(issue.get("title", ""))
        body = str(issue.get("body", ""))
        if title == expected_title or expected_key in body:
            return issue
    return None


def sync() -> None:
    if not DATA_PATH.exists():
        fail(f"Missing data file: {DATA_PATH}")

    articles = load_articles(DATA_PATH)
    ensure_label_exists()
    issues = list_open_issues()

    for article in articles:
        issue = find_issue_for_article(issues, article)
        status = article.normalized_status

        if status in UNFINISHED_STATUSES:
            if issue is None:
                create_issue(article)
            else:
                number = int(issue["number"])
                state = str(issue.get("state", "open"))
                if state == "closed":
                    update_issue(number, article, state="open")
                else:
                    update_issue(number, article)
        elif status in CLOSED_STATUSES:
            if issue is not None and str(issue.get("state", "open")) != "closed":
                close_issue(int(issue["number"]), article)
        else:
            print(f"Skipping article with unknown status '{article.status}': {article.title}")


if __name__ == "__main__":
    sync()
