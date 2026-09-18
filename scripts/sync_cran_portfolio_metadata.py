"""Synchronize audited CRAN metadata and saved views into GitHub Project #17."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

CONFIG_PATH = Path("data/cran_portfolio.json")
GRAPHQL_URL = "https://api.github.com/graphql"
STATE_RE = re.compile(r"^\s*-\s+\*\*(.+?):\*\*\s*(.*?)\s*$")
MAX_TEXT = 1024
REQUIRED_STATE_KEYS = (
    "CRAN target",
    "Maturity",
    "Current status",
    "Priority",
    "Version",
    "R CMD check",
    "Next action",
)


@dataclass(frozen=True)
class SelectOption:
    name: str
    color: str
    description: str


@dataclass(frozen=True)
class FieldSpec:
    name: str
    data_type: str
    options: tuple[SelectOption, ...] = ()


@dataclass(frozen=True)
class ViewSpec:
    name: str
    filter_query: str
    visible_fields: tuple[str, ...]


FIELDS = (
    FieldSpec(
        "CRAN",
        "SINGLE_SELECT",
        (
            SelectOption("Yes", "GREEN", "Active CRAN target"),
            SelectOption("Maybe", "YELLOW", "CRAN target still to be decided"),
            SelectOption("Long-term", "BLUE", "Longer-term CRAN target"),
            SelectOption("No", "RED", "Not a CRAN submission target"),
        ),
    ),
    FieldSpec(
        "Maturity",
        "SINGLE_SELECT",
        (
            SelectOption("Prototype", "GRAY", "Early package or package-shaped prototype"),
            SelectOption("Alpha", "ORANGE", "Implemented but not yet stable"),
            SelectOption("Beta", "YELLOW", "Broad implementation with release blockers"),
            SelectOption("Release Candidate", "GREEN", "Release-level package awaiting final gates"),
        ),
    ),
    FieldSpec(
        "Lifecycle",
        "SINGLE_SELECT",
        (
            SelectOption("Ready to Submit", "GREEN", "Ready for CRAN"),
            SelectOption("CRAN Hardening", "BLUE", "Focused release hardening"),
            SelectOption("Active Development", "YELLOW", "Active package development"),
            SelectOption("Blocked", "RED", "Correctness or release blocker"),
            SelectOption("Paused", "GRAY", "Paused pending revive/stop decision"),
            SelectOption("Do Not Submit", "PURPLE", "Keep out of the CRAN queue"),
            SelectOption("Inventory", "GRAY", "Not yet audited"),
        ),
    ),
    FieldSpec(
        "Priority",
        "SINGLE_SELECT",
        (
            SelectOption("P0", "RED", "Immediate release attention"),
            SelectOption("P1", "ORANGE", "High priority"),
            SelectOption("P2", "YELLOW", "Medium priority"),
            SelectOption("P3", "GRAY", "Low priority"),
        ),
    ),
    FieldSpec("Version", "TEXT"),
    FieldSpec("R CMD Check", "TEXT"),
    FieldSpec("Next Action", "TEXT"),
)

EXPECTED_SYNC_FIELDS = {spec.name for spec in FIELDS}

VIEWS = (
    ViewSpec(
        "CRAN Pipeline",
        "is:issue",
        ("Title", "CRAN", "Maturity", "Lifecycle", "Priority", "Version",
         "R CMD Check", "Next Action", "Repository"),
    ),
    ViewSpec(
        "Release Queue",
        'Lifecycle:"Ready to Submit","CRAN Hardening"',
        ("Title", "Lifecycle", "Priority", "Version", "R CMD Check", "Next Action", "Repository"),
    ),
    ViewSpec(
        "Blocked — Fix First",
        "Lifecycle:Blocked",
        ("Title", "Priority", "Maturity", "R CMD Check", "Next Action", "Repository"),
    ),
    ViewSpec(
        "P0 / P1",
        "Priority:P0,P1",
        ("Title", "Priority", "Lifecycle", "Maturity", "Version", "Next Action", "Repository"),
    ),
    ViewSpec(
        "Paused — Revive?",
        "Lifecycle:Paused",
        ("Title", "Priority", "Maturity", "Version", "Next Action", "Repository"),
    ),
    ViewSpec(
        "Maturity Map",
        "is:issue",
        ("Title", "Maturity", "Lifecycle", "Priority", "CRAN", "Version", "Repository"),
    ),
    ViewSpec(
        "CRAN Decisions",
        "is:issue",
        ("Title", "CRAN", "Lifecycle", "Priority", "Maturity", "Next Action", "Repository"),
    ),
)


def graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode()
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
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"GitHub GraphQL HTTP {exc.code}: {body}") from exc
    if result.get("errors"):
        raise RuntimeError(json.dumps(result["errors"], indent=2))
    return cast(dict[str, Any], result["data"])


def load_config() -> tuple[str, int, str, list[str]]:
    raw = json.loads(CONFIG_PATH.read_text())
    project = raw["project"]
    return (
        str(project["owner"]),
        int(project["number"]),
        str(raw.get("issue_title_template", "[CRAN] {repo}")),
        [str(repo) for repo in raw["repositories"]],
    )


def project_id(token: str, owner: str, number: int) -> str:
    query = """
    query($owner: String!, $number: Int!) {
      user(login: $owner) { projectV2(number: $number) { id } }
    }
    """
    data = graphql(token, query, {"owner": owner, "number": number})
    project = (data.get("user") or {}).get("projectV2")
    if not project:
        raise RuntimeError(f"Could not resolve Project #{number} for {owner}")
    return str(project["id"])


def parse_state(body: str) -> dict[str, str]:
    state: dict[str, str] = {}
    inside = False
    for line in body.splitlines():
        if line.strip() == "## Portfolio state":
            inside = True
            continue
        if inside and line.strip().startswith("## "):
            break
        if not inside:
            continue
        match = STATE_RE.match(line)
        if match:
            state[match.group(1).strip()] = match.group(2).strip()
    return state


def normalized_values(body: str) -> dict[str, str]:
    state = parse_state(body)
    values: dict[str, str] = {}

    cran = state.get("CRAN target", "").lower()
    if cran.startswith("no"):
        values["CRAN"] = "No"
    elif "long-term" in cran or "long term" in cran:
        values["CRAN"] = "Long-term"
    elif cran.startswith("maybe"):
        values["CRAN"] = "Maybe"
    elif cran.startswith("yes"):
        values["CRAN"] = "Yes"

    maturity = state.get("Maturity", "").lower()
    for label in ("Release Candidate", "Beta", "Alpha", "Prototype"):
        if maturity.startswith(label.lower()):
            values["Maturity"] = label
            break

    lifecycle = state.get("Current status", "")
    allowed_lifecycle = {
        "Ready to Submit", "CRAN Hardening", "Active Development",
        "Blocked", "Paused", "Do Not Submit", "Inventory",
    }
    if lifecycle in allowed_lifecycle:
        values["Lifecycle"] = lifecycle

    priority = re.match(r"^\s*(P[0-3])\b", state.get("Priority", ""), re.I)
    if priority:
        values["Priority"] = priority.group(1).upper()

    for source, target in (
        ("Version", "Version"),
        ("R CMD check", "R CMD Check"),
        ("Next action", "Next Action"),
    ):
        value = state.get(source, "").strip()
        if value:
            values[target] = value if len(value) <= MAX_TEXT else value[: MAX_TEXT - 1] + "…"
    return values


def audit_integrity_errors(issue: dict[str, Any]) -> list[str]:
    """Return human-readable audit-schema errors for one canonical tracker issue."""
    body = str(issue.get("body") or "")
    state = parse_state(body)
    errors: list[str] = []

    missing = [key for key in REQUIRED_STATE_KEYS if not state.get(key, "").strip()]
    if missing:
        errors.append("missing Portfolio state keys: " + ", ".join(missing))

    placeholder_values = {
        "CRAN target": {"To be reviewed"},
        "Maturity": {"To be reviewed"},
        "Current status": {"Inventory"},
        "Priority": {"To be assigned"},
        "Next action": {"Review current package and release state"},
    }
    for key, placeholders in placeholder_values.items():
        if state.get(key, "").strip() in placeholders:
            errors.append(f"{key} still has inventory placeholder value")

    values = normalized_values(body)
    missing_sync = sorted(EXPECTED_SYNC_FIELDS - values.keys())
    if missing_sync:
        errors.append("does not normalize all Project fields: " + ", ".join(missing_sync))

    return errors


def validate_audit_integrity(issues: list[dict[str, Any]]) -> None:
    """Fail before mutating Project #17 when any audited tracker is incomplete."""
    failures: list[str] = []
    for issue in issues:
        errors = audit_integrity_errors(issue)
        if errors:
            failures.append(
                f"{issue.get('title', '<untitled>')} ({issue.get('url', '<no url>')}): "
                + "; ".join(errors)
            )
    if failures:
        joined = "\n  - ".join(failures)
        raise RuntimeError(
            "CRAN audit integrity check failed; refusing partial Project sync:\n  - " + joined
        )


def tracker_issues(token: str, repos: list[str], title_template: str) -> list[dict[str, Any]]:
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        issues(first: 100, states: [OPEN, CLOSED], orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes { id number title body url }
        }
      }
    }
    """
    found: list[dict[str, Any]] = []
    for slug in repos:
        owner, name = slug.split("/", 1)
        expected = title_template.format(repo=name, repository=slug)
        data = graphql(token, query, {"owner": owner, "name": name})
        nodes = (data.get("repository") or {}).get("issues", {}).get("nodes", [])
        matches = [issue for issue in nodes if issue.get("title") == expected]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one tracker issue for {slug}, found {len(matches)}")
        found.append(cast(dict[str, Any], matches[0]))
    return found


def project_items(token: str, pid: str) -> dict[str, str]:
    query = """
    query($projectId: ID!, $cursor: String) {
      node(id: $projectId) {
        ... on ProjectV2 {
          items(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes { id content { ... on Issue { id } } }
          }
        }
      }
    }
    """
    result: dict[str, str] = {}
    cursor: str | None = None
    while True:
        data = graphql(token, query, {"projectId": pid, "cursor": cursor})
        items = data["node"]["items"]
        for item in items["nodes"]:
            issue = item.get("content") or {}
            if issue.get("id"):
                result[str(issue["id"])] = str(item["id"])
        page = items["pageInfo"]
        if not page["hasNextPage"]:
            return result
        cursor = str(page["endCursor"])


def fields(token: str, pid: str) -> dict[str, dict[str, Any]]:
    query = """
    query($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          fields(first: 100) {
            nodes {
              ... on ProjectV2FieldCommon { id name dataType }
              ... on ProjectV2SingleSelectField {
                options { id name color description }
              }
            }
          }
        }
      }
    }
    """
    data = graphql(token, query, {"projectId": pid})
    return {
        str(field["name"]): cast(dict[str, Any], field)
        for field in data["node"]["fields"]["nodes"]
        if field.get("name")
    }


def create_field(token: str, pid: str, spec: FieldSpec) -> dict[str, Any]:
    mutation = """
    mutation($projectId: ID!, $name: String!, $dataType: ProjectV2CustomFieldType!,
             $options: [ProjectV2SingleSelectFieldOptionInput!]) {
      createProjectV2Field(input: {
        projectId: $projectId, name: $name, dataType: $dataType,
        singleSelectOptions: $options
      }) {
        projectV2Field {
          ... on ProjectV2FieldCommon { id name dataType }
          ... on ProjectV2SingleSelectField { options { id name color description } }
        }
      }
    }
    """
    options = [
        {"name": option.name, "color": option.color, "description": option.description}
        for option in spec.options
    ]
    data = graphql(
        token,
        mutation,
        {
            "projectId": pid,
            "name": spec.name,
            "dataType": spec.data_type,
            "options": options if options else None,
        },
    )
    return cast(dict[str, Any], data["createProjectV2Field"]["projectV2Field"])


def ensure_fields(token: str, pid: str) -> dict[str, dict[str, Any]]:
    existing = fields(token, pid)
    for spec in FIELDS:
        if spec.name not in existing:
            existing[spec.name] = create_field(token, pid, spec)
            print(f"CREATED field: {spec.name}")
    return existing


def set_value(
    token: str,
    pid: str,
    item_id: str,
    field: dict[str, Any],
    value: str,
) -> None:
    if field["dataType"] == "SINGLE_SELECT":
        options = {str(option["name"]): str(option["id"]) for option in field["options"]}
        option_id = options.get(value)
        if not option_id:
            raise RuntimeError(f"Missing option {value!r} in {field['name']!r}")
        field_value = {"singleSelectOptionId": option_id}
    else:
        field_value = {"text": value}

    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!,
             $value: ProjectV2FieldValue!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId, itemId: $itemId, fieldId: $fieldId, value: $value
      }) { projectV2Item { id } }
    }
    """
    graphql(
        token,
        mutation,
        {
            "projectId": pid,
            "itemId": item_id,
            "fieldId": field["id"],
            "value": field_value,
        },
    )


def sync_values(
    token: str,
    pid: str,
    issues: list[dict[str, Any]],
    items: dict[str, str],
    project_fields: dict[str, dict[str, Any]],
) -> None:
    for issue in issues:
        item_id = items.get(str(issue["id"]))
        if not item_id:
            raise RuntimeError(f"Tracker issue is not present in Project #17: {issue['url']}")
        values = normalized_values(str(issue.get("body") or ""))
        for name, value in values.items():
            set_value(token, pid, item_id, project_fields[name], value)
        print(f"SYNCED {issue['title']}: {len(values)} fields")


def views(token: str, pid: str) -> dict[str, dict[str, Any]]:
    query = """
    query($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          views(first: 100) { nodes { id name layout filter } }
        }
      }
    }
    """
    data = graphql(token, query, {"projectId": pid})
    return {
        str(view["name"]): cast(dict[str, Any], view)
        for view in data["node"]["views"]["nodes"]
        if view.get("name")
    }


def create_view(token: str, pid: str, spec: ViewSpec, visible_ids: list[str]) -> str:
    mutation = """
    mutation($projectId: ID!, $name: String!, $visible: [ID!]) {
      createProjectV2View(input: {
        projectId: $projectId,
        name: $name,
        layout: TABLE_LAYOUT,
        configuration: {visibleFieldIds: $visible}
      }) { projectV2View { id } }
    }
    """
    data = graphql(
        token,
        mutation,
        {"projectId": pid, "name": spec.name, "visible": visible_ids},
    )
    return str(data["createProjectV2View"]["projectV2View"]["id"])


def update_view(token: str, view_id: str, spec: ViewSpec, visible_ids: list[str]) -> None:
    mutation = """
    mutation($viewId: ID!, $filter: String!, $visible: [ID!]) {
      updateProjectV2View(input: {
        viewId: $viewId,
        layout: TABLE_LAYOUT,
        filter: $filter,
        configuration: {visibleFieldIds: $visible}
      }) { projectV2View { id } }
    }
    """
    graphql(
        token,
        mutation,
        {"viewId": view_id, "filter": spec.filter_query, "visible": visible_ids},
    )


def ensure_views(token: str, pid: str, project_fields: dict[str, dict[str, Any]]) -> None:
    existing = views(token, pid)
    for spec in VIEWS:
        visible_ids = [
            str(project_fields[name]["id"])
            for name in spec.visible_fields
            if name in project_fields
        ]
        view = existing.get(spec.name)
        view_id = str(view["id"]) if view else create_view(token, pid, spec, visible_ids)
        if not view:
            print(f"CREATED view: {spec.name}")
        update_view(token, view_id, spec, visible_ids)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("Missing GITHUB_TOKEN", file=sys.stderr)
        return 2

    owner, number, title_template, repos = load_config()
    pid = project_id(token, owner, number)
    issues = tracker_issues(token, repos, title_template)
    validate_audit_integrity(issues)
    print(f"AUDIT integrity OK: {len(issues)} canonical trackers complete")
    items = project_items(token, pid)
    project_fields = ensure_fields(token, pid)
    sync_values(token, pid, issues, items, project_fields)
    ensure_views(token, pid, project_fields)
    print(f"Portfolio metadata sync complete: issues={len(issues)}, views={len(VIEWS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
