from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_article_drift.py"

spec = importlib.util.spec_from_file_location("check_article_drift", MODULE_PATH)
assert spec and spec.loader
drift = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = drift
spec.loader.exec_module(drift)


RepoState = drift.RepoState


def article(**overrides):
    entry = {
        "title": "A Paper",
        "repo": "owner/name",
        "status": "draft",
        "paper_path": "paper/",
        "last_updated": "2026-08-19",
    }
    entry.update(overrides)
    return entry


def kinds(findings):
    return [f.kind for f in findings]


def test_missing_repo_is_reported() -> None:
    findings = drift.inspect(article(), RepoState(exists=False))
    assert kinds(findings) == ["missing-repo"]


def test_missing_paper_path_is_reported() -> None:
    state = RepoState(exists=True, pushed_at="2026-08-19", paths={"src/main.py": 100})
    findings = drift.inspect(article(), state)
    assert kinds(findings) == ["missing-path"]


def test_early_status_with_a_written_manuscript_is_reported() -> None:
    state = RepoState(exists=True, pushed_at="2026-08-19", paths={"paper/main.tex": 90_000})
    findings = drift.inspect(article(status="planned"), state)
    assert "status-behind" in kinds(findings)


def test_written_status_with_no_manuscript_is_reported() -> None:
    state = RepoState(exists=True, pushed_at="2026-08-19", paths={"paper/main.tex": 300})
    findings = drift.inspect(article(status="draft"), state)
    assert "status-ahead" in kinds(findings)


def test_a_real_draft_raises_nothing() -> None:
    state = RepoState(exists=True, pushed_at="2026-08-19", paths={"paper/main.tex": 90_000})
    assert drift.inspect(article(status="draft"), state) == []


def test_prompts_and_research_notes_do_not_count_as_manuscript() -> None:
    """A paper_path covering a whole project subtree must measure the manuscript only.

    This is the false positive that made a 591-byte scaffold read as 122 KB.
    """
    paths = {
        "papers/x/manuscript/main.tex": 591,
        "papers/x/prompts/01_audit.md": 60_000,
        "papers/x/research/notes.md": 60_000,
    }
    scope = drift.scope_for("papers/x/", paths)
    assert drift.prose_bytes(scope, "papers/x/") == 591

    state = RepoState(exists=True, pushed_at="2026-08-19", paths=paths)
    assert drift.inspect(article(status="planned", paper_path="papers/x/"), state) == []


def test_readme_does_not_count_as_manuscript() -> None:
    scope = drift.scope_for("paper/", {"paper/README.md": 80_000})
    assert drift.prose_bytes(scope, "paper/") == 0


def test_paper_path_naming_a_single_file_scopes_to_its_directory() -> None:
    paths = {"paper/paper.md": 9_000, "paper/supplement.md": 21_000, "src/a.py": 10}
    scope = drift.scope_for("paper/paper.md", paths)
    assert drift.prose_bytes(scope, "paper/paper.md") == 30_000


def test_small_timestamp_gaps_are_not_reported() -> None:
    state = RepoState(exists=True, pushed_at="2026-08-25", paths={"paper/main.tex": 90_000})
    assert "stale-timestamp" not in kinds(drift.inspect(article(last_updated="2026-08-19"), state))


def test_wide_timestamp_gaps_are_reported() -> None:
    state = RepoState(exists=True, pushed_at="2026-08-19", paths={"paper/main.tex": 90_000})
    findings = drift.inspect(article(last_updated="2026-03-08"), state)
    assert "stale-timestamp" in kinds(findings)


def test_days_between_survives_unusable_dates() -> None:
    assert drift.days_between("", "2026-08-19") == 0
    assert drift.days_between("not-a-date", "2026-08-19") == 0
    assert drift.days_between("2026-08-19", "2026-03-08") == 0


def test_main_refuses_to_report_when_the_token_cannot_see_the_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Most tracked repositories are private.

    A token without access 404s on every one of them, which must not be reported as
    a tracker full of deleted repositories.
    """
    data = tmp_path / "articles.json"
    data.write_text(
        '{"articles": [%s]}'
        % ",".join(
            '{"title": "P%d", "repo": "owner/r%d", "status": "draft", '
            '"paper_path": "paper/", "last_updated": "2026-08-19"}' % (i, i)
            for i in range(4)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(drift, "DATA_PATH", data)
    monkeypatch.setattr(drift, "fetch_repo_state", lambda repo: RepoState(exists=False))
    monkeypatch.setattr(drift, "publish", lambda findings, checked: pytest.fail("must not publish"))

    with pytest.raises(SystemExit) as exc:
        drift.main()
    assert exc.value.code == 1


def test_issue_body_groups_findings_by_kind() -> None:
    findings = [
        drift.Finding("missing-repo", "Gone", "repo does not resolve"),
        drift.Finding("status-behind", "Behind", "90 KB of manuscript"),
    ]
    body = drift.render_issue_body(findings, checked=48)
    assert "Checked 48 entries" in body
    assert "## Repositories that do not resolve" in body
    assert "## Status looks behind the manuscript" in body
    assert "## paper_path values that no longer exist" not in body
