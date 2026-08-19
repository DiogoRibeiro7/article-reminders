from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "sync_article_issues.py"


spec = importlib.util.spec_from_file_location("sync_article_issues", MODULE_PATH)
assert spec and spec.loader
sync_article_issues = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sync_article_issues
spec.loader.exec_module(sync_article_issues)


Article = sync_article_issues.Article


def test_ensure_label_exists_ignores_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_request(method: str, path: str, payload=None, expected_error_codes=None):
        return {
            "__error__": {
                "status": 422,
                "body": {
                    "errors": [
                        {
                            "resource": "Label",
                            "code": "already_exists",
                            "field": "name",
                        }
                    ]
                },
            }
        }

    monkeypatch.setattr(sync_article_issues, "api_request", fake_api_request)
    sync_article_issues.ensure_label_exists()


def test_ensure_label_exists_fails_on_unexpected_422(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_request(method: str, path: str, payload=None, expected_error_codes=None):
        return {
            "__error__": {
                "status": 422,
                "body": {
                    "errors": [
                        {
                            "resource": "Label",
                            "code": "invalid",
                            "field": "name",
                        }
                    ]
                },
            }
        }

    monkeypatch.setattr(sync_article_issues, "api_request", fake_api_request)

    with pytest.raises(SystemExit) as exc:
        sync_article_issues.ensure_label_exists()

    assert exc.value.code == 1


def test_sync_handles_create_update_reopen_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    articles = [
        Article(title="Create", repo="a/b", status="planned"),
        Article(title="Update", repo="a/b", status="in_progress"),
        Article(title="Reopen", repo="a/b", status="draft"),
        Article(title="Close", repo="a/b", status="finished"),
    ]

    issues = [
        {
            "number": 2,
            "title": f"{sync_article_issues.ISSUE_PREFIX} Update",
            "state": "open",
            "body": "",
        },
        {
            "number": 3,
            "title": f"{sync_article_issues.ISSUE_PREFIX} Reopen",
            "state": "closed",
            "body": "",
        },
        {
            "number": 4,
            "title": f"{sync_article_issues.ISSUE_PREFIX} Close",
            "state": "open",
            "body": "",
        },
    ]

    actions: list[tuple] = []

    monkeypatch.setattr(sync_article_issues, "DATA_PATH", Path("data/articles.json"))
    monkeypatch.setattr(sync_article_issues, "load_articles", lambda path: articles)
    monkeypatch.setattr(sync_article_issues, "ensure_label_exists", lambda: actions.append(("ensure_label",)))
    monkeypatch.setattr(sync_article_issues, "list_open_issues", lambda: issues)
    monkeypatch.setattr(sync_article_issues, "create_issue", lambda article: actions.append(("create", article.title)))
    monkeypatch.setattr(
        sync_article_issues,
        "update_issue",
        lambda number, article, state=None: actions.append(("update", number, article.title, state)),
    )
    monkeypatch.setattr(sync_article_issues, "close_issue", lambda number, article: actions.append(("close", number, article.title)))

    sync_article_issues.sync()

    assert ("ensure_label",) in actions
    assert ("create", "Create") in actions
    assert ("update", 2, "Update", None) in actions
    assert ("update", 3, "Reopen", "open") in actions
    assert ("close", 4, "Close") in actions


def _managed_issue(number: int, title: str, state: str = "open", labels=None):
    return {
        "number": number,
        "title": f"{sync_article_issues.ISSUE_PREFIX} {title}",
        "state": state,
        "body": "",
        "labels": [{"name": sync_article_issues.MANAGED_LABEL}] if labels is None else labels,
    }


def _record_orphan_calls(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        sync_article_issues,
        "comment_on_issue",
        lambda number, body: calls.append(("comment", number)),
    )
    monkeypatch.setattr(
        sync_article_issues,
        "api_request",
        lambda method, path, payload=None, expected_error_codes=None: calls.append(
            ("api", method, path, payload)
        ),
    )
    return calls


def test_close_orphaned_issues_closes_unclaimed_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_orphan_calls(monkeypatch)
    issues = [_managed_issue(4, "Deleted article"), _managed_issue(9, "Still tracked")]

    sync_article_issues.close_orphaned_issues(issues, {9}, article_count=3)

    assert ("comment", 4) in calls
    assert ("api", "PATCH", "/repos/None/issues/4", {"state": "closed"}) in calls
    assert not any(call[1] == 9 for call in calls)


def test_close_orphaned_issues_skips_unmanaged_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_orphan_calls(monkeypatch)
    issues = [
        _managed_issue(1, "Already closed", state="closed"),
        _managed_issue(2, "No managed label", labels=[{"name": "bug"}]),
        {"number": 3, "title": "Unrelated hand-written issue", "state": "open", "body": "", "labels": [{"name": sync_article_issues.MANAGED_LABEL}]},
    ]

    sync_article_issues.close_orphaned_issues(issues, set(), article_count=3)

    assert calls == []


def test_close_orphaned_issues_refuses_to_run_on_empty_data(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_orphan_calls(monkeypatch)

    sync_article_issues.close_orphaned_issues([_managed_issue(4, "Anything")], set(), article_count=0)

    assert calls == []


def test_sync_closes_issue_left_behind_by_a_removed_article(monkeypatch: pytest.MonkeyPatch) -> None:
    articles = [Article(title="Kept", repo="a/b", status="draft")]
    issues = [_managed_issue(2, "Kept"), _managed_issue(7, "Removed from the tracker")]
    orphaned: list = []

    monkeypatch.setattr(sync_article_issues, "DATA_PATH", Path("data/articles.json"))
    monkeypatch.setattr(sync_article_issues, "load_articles", lambda path: articles)
    monkeypatch.setattr(sync_article_issues, "ensure_label_exists", lambda: None)
    monkeypatch.setattr(sync_article_issues, "list_open_issues", lambda: issues)
    monkeypatch.setattr(sync_article_issues, "update_issue", lambda number, article, state=None: None)
    monkeypatch.setattr(
        sync_article_issues,
        "close_orphaned_issues",
        lambda issues, matched, count: orphaned.append((sorted(matched), count)),
    )

    sync_article_issues.sync()

    assert orphaned == [([2], 1)]
