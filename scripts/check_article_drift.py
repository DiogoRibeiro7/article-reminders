"""Report where data/articles.json has drifted away from the actual repositories.

The tracker records what each paper's state is meant to be; the repositories
record what it actually is. Nothing kept the two in step, so entries accumulated
for deleted repositories, paper_path values that no longer resolve, and statuses
left behind by months of writing.

This script only reports. Deciding whether 53 KB of sections counts as a draft
is a judgement call, so the findings land in one issue for a human to act on
rather than being written back into the data file.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPOSITORY = os.environ.get("GITHUB_REPOSITORY")
# Two tokens with different jobs. SCAN_TOKEN only reads the tracked repositories, most
# of which are private and outside this one, so it needs no write access anywhere.
# ISSUE_TOKEN writes the report here and is the workflow's own GITHUB_TOKEN.
SCAN_TOKEN = os.environ.get("ARTICLE_SCAN_TOKEN") or os.environ.get("GITHUB_TOKEN")
ISSUE_TOKEN = os.environ.get("GITHUB_TOKEN") or SCAN_TOKEN
DATA_PATH = Path("data/articles.json")

ISSUE_TITLE = "[article-drift] Tracker entries that disagree with their repositories"
DRIFT_LABEL = "article-drift"

MANUSCRIPT_SUFFIXES = (".tex", ".md", ".rmd", ".qmd", ".typ")
NON_MANUSCRIPT_NAMES = {"readme.md", "license.md", "changelog.md", "contributing.md"}

# Directories that sit beside a manuscript but are not one. Without this a paper_path
# pointing at a whole project subtree counts agent prompts and research notes as prose:
# one entry here measured 122 KB, of which every byte was prompts/*.md next to a
# 591-byte main.tex.
NON_MANUSCRIPT_DIRS = {
    "archive",
    "data",
    "docs",
    "figures",
    "notebooks",
    "notes",
    "outputs",
    "prompts",
    "research",
    "results",
    "revision",
    "scripts",
    "src",
    "tables",
    "tests",
}

# An entry claiming little progress while this much manuscript source sits in the
# tracked path is worth a second look. Set well above a scaffold: the deliberate
# placeholders in this tracker run to roughly 1 KB.
SUBSTANTIAL_PROSE_BYTES = 20 * 1024
# The converse: an entry claiming a written manuscript with nothing behind it.
NEGLIGIBLE_PROSE_BYTES = 2 * 1024

# A push is not evidence of article progress on its own -- it is just as likely to be
# a CI tweak -- so only report a gap wide enough that the entry is plainly unreviewed.
STALE_TIMESTAMP_DAYS = 60

EARLY_STATUSES = {"planned", "in_progress"}
WRITTEN_STATUSES = {"draft", "submitted", "revising"}

# Below this share of reachable repositories, assume the token cannot see private
# repositories rather than that the repositories are gone. Most tracked repos are
# private, and the default GITHUB_TOKEN is scoped to this repository alone, so
# without a broader token every single lookup 404s.
MIN_REACHABLE_SHARE = 0.5


def days_between(earlier: str, later: str) -> int:
    """Whole days from one YYYY-MM-DD string to another; 0 if either is unusable."""
    try:
        start = date.fromisoformat(earlier)
        end = date.fromisoformat(later)
    except (TypeError, ValueError):
        return 0
    return max(0, (end - start).days)


@dataclass
class Finding:
    kind: str
    title: str
    detail: str


@dataclass
class RepoState:
    exists: bool
    pushed_at: str = ""
    default_branch: str = ""
    paths: Dict[str, int] = field(default_factory=dict)


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def api_request(
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Any:
    token = token or SCAN_TOKEN
    if not token:
        fail("No token available. Set ARTICLE_SCAN_TOKEN or GITHUB_TOKEN.")

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "article-drift-bot",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"

    request = Request(url=f"{API_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else None
    except HTTPError as exc:
        if exc.code in {403, 404}:
            return None
        fail(f"GitHub API request failed: {method} {path} -> {exc.code}")


def fetch_repo_state(repo: str) -> RepoState:
    meta = api_request("GET", f"/repos/{repo}")
    if not isinstance(meta, dict):
        return RepoState(exists=False)

    branch = str(meta.get("default_branch", "main"))
    tree = api_request("GET", f"/repos/{repo}/git/trees/{branch}?recursive=1")
    paths: Dict[str, int] = {}
    if isinstance(tree, dict):
        for node in tree.get("tree", []):
            if node.get("type") == "blob":
                paths[str(node["path"])] = int(node.get("size", 0))

    return RepoState(
        exists=True,
        pushed_at=str(meta.get("pushed_at", ""))[:10],
        default_branch=branch,
        paths=paths,
    )


def scope_for(paper_path: str, paths: Dict[str, int]) -> Dict[str, int]:
    """Return the files the entry's paper_path points at.

    A paper_path is normally a directory, but a few entries name a single file
    (``paper/paper.md``), in which case the whole containing directory is the
    interesting scope.
    """
    cleaned = paper_path.strip().strip("/")
    if not cleaned:
        return dict(paths)

    if cleaned in paths:
        parent = cleaned.rsplit("/", 1)[0] if "/" in cleaned else ""
        prefix = f"{parent}/" if parent else ""
        return {p: s for p, s in paths.items() if p.startswith(prefix)}

    prefix = f"{cleaned}/"
    return {p: s for p, s in paths.items() if p.startswith(prefix)}


def prose_bytes(scope: Dict[str, int], paper_path: str) -> int:
    """Total size of manuscript source under the entry's paper_path.

    Only files whose path below paper_path avoids NON_MANUSCRIPT_DIRS count, so a
    paper_path covering a whole project subtree measures the manuscript rather
    than the prompts and research notes sitting next to it.
    """
    root = paper_path.strip().strip("/")
    if root in scope:
        root = root.rsplit("/", 1)[0] if "/" in root else ""

    total = 0
    for path, size in scope.items():
        name = path.rsplit("/", 1)[-1].lower()
        if name in NON_MANUSCRIPT_NAMES or not name.endswith(MANUSCRIPT_SUFFIXES):
            continue

        relative = path[len(root) + 1 :] if root and path.startswith(f"{root}/") else path
        segments = relative.split("/")[:-1]
        if any(segment.lower() in NON_MANUSCRIPT_DIRS for segment in segments):
            continue

        total += size
    return total


def inspect(article: Dict[str, Any], state: RepoState) -> List[Finding]:
    title = str(article.get("title", "untitled"))
    repo = str(article.get("repo", ""))
    paper_path = str(article.get("paper_path", ""))
    status = str(article.get("status", "")).strip().lower()
    last_updated = str(article.get("last_updated", ""))

    if not state.exists:
        return [
            Finding(
                "missing-repo",
                title,
                f"`{repo}` does not resolve. Either the repository is gone or it was renamed, "
                f"so this entry can never sync.",
            )
        ]

    findings: List[Finding] = []
    scope = scope_for(paper_path, state.paths)

    if paper_path.strip() and not scope:
        findings.append(
            Finding(
                "missing-path",
                title,
                f"`{repo}` exists but `{paper_path}` does not. The manuscript has moved or the "
                f"path was never right.",
            )
        )
        return findings

    written = prose_bytes(scope, paper_path)
    kb = written / 1024

    if status in EARLY_STATUSES and written >= SUBSTANTIAL_PROSE_BYTES:
        findings.append(
            Finding(
                "status-behind",
                title,
                f"Tracked as `{status}`, but `{paper_path or 'the repository'}` holds "
                f"{kb:.0f} KB of manuscript source. Consider `draft`.",
            )
        )
    elif status in WRITTEN_STATUSES and written < NEGLIGIBLE_PROSE_BYTES:
        findings.append(
            Finding(
                "status-ahead",
                title,
                f"Tracked as `{status}`, but `{paper_path or 'the repository'}` holds only "
                f"{kb:.1f} KB of manuscript source. The entry may be ahead of the repository.",
            )
        )

    gap = days_between(last_updated, state.pushed_at)
    if gap >= STALE_TIMESTAMP_DAYS:
        findings.append(
            Finding(
                "stale-timestamp",
                title,
                f"`last_updated` is {last_updated} but `{repo}` was last pushed "
                f"{state.pushed_at}, {gap} days later.",
            )
        )

    return findings


def render_issue_body(findings: List[Finding], checked: int) -> str:
    groups = {
        "missing-repo": "Repositories that do not resolve",
        "missing-path": "paper_path values that no longer exist",
        "status-behind": "Status looks behind the manuscript",
        "status-ahead": "Status looks ahead of the manuscript",
        "stale-timestamp": "last_updated older than the repository's last push",
    }

    lines = [
        "This issue is regenerated by the drift check workflow.",
        "",
        f"Checked {checked} entries in `data/articles.json` against their repositories "
        f"and found {len(findings)} discrepancies.",
        "",
        "Nothing here is applied automatically — whether a given manuscript counts as a "
        "draft is a judgement call. Edit `data/articles.json` and this issue closes on the "
        "next run.",
    ]

    for kind, heading in groups.items():
        rows = [f for f in findings if f.kind == kind]
        if not rows:
            continue
        lines.extend(["", f"## {heading}", ""])
        for finding in rows:
            lines.append(f"- **{finding.title}** — {finding.detail}")

    return "\n".join(lines)


def find_drift_issue() -> Optional[Dict[str, Any]]:
    issues = api_request(
        "GET",
        f"/repos/{REPOSITORY}/issues?state=all&labels={DRIFT_LABEL}&per_page=100",
        token=ISSUE_TOKEN,
    )
    if not isinstance(issues, list):
        return None
    for issue in issues:
        if str(issue.get("title", "")) == ISSUE_TITLE:
            return issue
    return None


def ensure_label() -> None:
    api_request(
        "POST",
        f"/repos/{REPOSITORY}/labels",
        {
            "name": DRIFT_LABEL,
            "color": "D93F0B",
            "description": "Tracker disagrees with a repository",
        },
        token=ISSUE_TOKEN,
    )


def publish(findings: List[Finding], checked: int) -> None:
    issue = find_drift_issue()

    if not findings:
        if issue is not None and str(issue.get("state")) == "open":
            api_request(
                "PATCH",
                f"/repos/{REPOSITORY}/issues/{issue['number']}",
                {"state": "closed"},
                token=ISSUE_TOKEN,
            )
            print(f"No drift; closed issue #{issue['number']}.")
        else:
            print("No drift.")
        return

    ensure_label()
    body = render_issue_body(findings, checked)
    if issue is None:
        api_request(
            "POST",
            f"/repos/{REPOSITORY}/issues",
            {"title": ISSUE_TITLE, "body": body, "labels": [DRIFT_LABEL]},
            token=ISSUE_TOKEN,
        )
        print(f"Opened drift issue with {len(findings)} findings.")
    else:
        api_request(
            "PATCH",
            f"/repos/{REPOSITORY}/issues/{issue['number']}",
            {"title": ISSUE_TITLE, "body": body, "state": "open"},
            token=ISSUE_TOKEN,
        )
        print(f"Updated drift issue #{issue['number']} with {len(findings)} findings.")


def main() -> int:
    if not DATA_PATH.exists():
        fail(f"Missing data file: {DATA_PATH}")

    articles = json.loads(DATA_PATH.read_text(encoding="utf-8"))["articles"]
    if not articles:
        print("No articles to check.")
        return 0

    repos: Set[str] = {str(a.get("repo", "")) for a in articles if a.get("repo")}
    states = {repo: fetch_repo_state(repo) for repo in sorted(repos)}

    reachable = sum(1 for s in states.values() if s.exists)
    if reachable < len(states) * MIN_REACHABLE_SHARE:
        fail(
            f"Only {reachable}/{len(states)} repositories were reachable. Most tracked "
            f"repositories are private and the default GITHUB_TOKEN cannot read them, so this "
            f"looks like a token scope problem rather than {len(states) - reachable} deleted "
            f"repositories. Set ARTICLE_SCAN_TOKEN to a token with read access and re-run."
        )

    findings: List[Finding] = []
    for article in articles:
        state = states.get(str(article.get("repo", "")))
        if state is None:
            continue
        findings.extend(inspect(article, state))

    for finding in findings:
        print(f"{finding.kind}: {finding.title} — {finding.detail}")

    publish(findings, len(articles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
