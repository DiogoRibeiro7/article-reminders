"""Validate data/articles.json before the sync workflows ever read it.

The reminder and project-sync workflows treat this file as the source of truth
and run on a schedule, so a malformed entry is otherwise discovered halfway
through a cron run that has already created or edited issues. This script is the
gate: it runs on every push and pull request and fails loudly instead.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

DATA_PATH = Path("data/articles.json")

REQUIRED_KEYS = {"title", "repo", "status", "notes", "paper_path", "priority", "last_updated"}
OPTIONAL_KEYS = {"abstract", "venue", "target_date", "next_action"}
KNOWN_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS

VALID_STATUSES = {
    "planned",
    "in_progress",
    "draft",
    "submitted",
    "revising",
    "finished",
    "published",
    "archived",
    "cancelled",
}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_entry(index: int, item: Any) -> List[str]:
    where = f"articles[{index}]"
    if not isinstance(item, dict):
        return [f"{where}: expected an object, found {type(item).__name__}"]

    errors: List[str] = []
    title = str(item.get("title", "")).strip()
    label = f"{where} ({title or 'untitled'})"

    for key in sorted(REQUIRED_KEYS - set(item)):
        errors.append(f"{label}: missing required key '{key}'")

    for key in sorted(set(item) - KNOWN_KEYS):
        errors.append(
            f"{label}: unknown key '{key}'. The sync scripts ignore it, so it would be "
            f"silently inert. Known keys: {', '.join(sorted(KNOWN_KEYS))}"
        )

    for key in sorted(set(item) & KNOWN_KEYS):
        if not isinstance(item[key], str):
            errors.append(f"{label}: '{key}' must be a string, found {type(item[key]).__name__}")

    if not title:
        errors.append(f"{label}: 'title' must not be empty")

    repo = str(item.get("repo", "")).strip()
    if repo and not REPO_RE.match(repo):
        errors.append(f"{label}: 'repo' should be owner/name, found {repo!r}")

    status = str(item.get("status", "")).strip().lower()
    if status and status not in VALID_STATUSES:
        errors.append(
            f"{label}: status {status!r} is not recognised. The sync skips unknown statuses "
            f"entirely, so this article would never get a reminder. Valid: "
            f"{', '.join(sorted(VALID_STATUSES))}"
        )

    priority = str(item.get("priority", "")).strip().lower()
    if priority and priority not in VALID_PRIORITIES:
        errors.append(
            f"{label}: priority {priority!r} is not recognised. Valid: "
            f"{', '.join(sorted(VALID_PRIORITIES))}"
        )

    for key in ("last_updated", "target_date"):
        value = str(item.get(key, "")).strip()
        if value and not DATE_RE.match(value):
            errors.append(f"{label}: '{key}' must be YYYY-MM-DD, found {value!r}")

    return errors


def check_collection(articles: List[Any]) -> List[str]:
    errors: List[str] = []
    seen_titles: Dict[str, int] = {}
    seen_targets: Dict[tuple, int] = {}

    for index, item in enumerate(articles):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        target = (str(item.get("repo", "")).strip(), str(item.get("paper_path", "")).strip())

        if title:
            if title in seen_titles:
                errors.append(
                    f"articles[{index}]: duplicate title, already used by "
                    f"articles[{seen_titles[title]}]. Reminder issues are matched by title, so "
                    f"both entries would fight over one issue: {title!r}"
                )
            else:
                seen_titles[title] = index

        if target[0]:
            if target in seen_targets:
                errors.append(
                    f"articles[{index}]: duplicate repo + paper_path, already used by "
                    f"articles[{seen_targets[target]}]: {target[0]}::{target[1]}"
                )
            else:
                seen_targets[target] = index

    return errors


def validate(path: Path) -> List[str]:
    if not path.exists():
        return [f"Missing data file: {path}"]

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path} is not valid JSON: {exc}"]

    if not isinstance(raw, dict) or "articles" not in raw:
        return [f"{path} must be an object with an 'articles' key"]

    articles = raw["articles"]
    if not isinstance(articles, list):
        return [f"{path}: 'articles' must be a list, found {type(articles).__name__}"]

    errors: List[str] = []
    for index, item in enumerate(articles):
        errors.extend(check_entry(index, item))
    errors.extend(check_collection(articles))
    return errors


def main() -> int:
    errors = validate(DATA_PATH)
    if errors:
        print(f"{DATA_PATH} failed validation with {len(errors)} problem(s):\n")
        for error in errors:
            print(f"  - {error}")
        return 1

    count = len(json.loads(DATA_PATH.read_text(encoding="utf-8"))["articles"])
    print(f"{DATA_PATH} is valid: {count} articles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
