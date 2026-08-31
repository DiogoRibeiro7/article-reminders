"""The command line, exercised through ``main()``."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from article_reminders.cli.formatting import relative_days, table, truncate
from article_reminders.cli.main import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK, main
from tests.conftest import NOW, days_ahead, write_legacy


def real_days_ahead(days: int) -> str:
    """Return a date that many days after *today*, as ``YYYY-MM-DD``.

    The frozen ``NOW`` in ``conftest`` governs the fixtures, but the CLI builds its own
    application on the system clock. A literal date handed to a CLI command is therefore
    a fuse: it is in the future when it is written and in the past some weeks later, and
    the suite then fails on a calendar day rather than on a change.
    """

    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


@pytest.fixture
def run(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., str]:
    """Run the CLI inside the temporary workspace and return its stdout."""
    monkeypatch.setenv("ARTICLE_REMINDERS_ROOT", str(workspace))
    monkeypatch.chdir(workspace)

    def invoke(*args: str, expect: int = EXIT_OK) -> str:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--root", str(workspace), *args])
        assert code == expect, f"{args} exited {code}\n{buffer.getvalue()}"
        return buffer.getvalue()

    return invoke


def test_add_then_list_then_show(run: Callable[..., str]) -> None:
    run(
        "add",
        "Minimum Wage and Productivity in Portugal",
        "--status",
        "analysis",
        "--priority",
        "high",
        "--repo",
        "owner/minimum-wage",
        "--paper-path",
        "paper/",
        "--next-action",
        "Retrieve the revised OECD productivity vintage",
        "--due",
        real_days_ahead(6),
        "--tags",
        "labour,portugal",
    )

    listing = run("list")
    assert "minimum-wage-and-productivity" in listing
    assert "analysis" in listing
    assert "owner/minimum-wage" in listing

    shown = run("show", "minimum-wage")
    assert "Retrieve the revised OECD productivity vintage" in shown
    assert "Stage:            analysis" in shown
    assert "labour, portugal" in shown


def test_add_warns_when_no_next_action_is_given(run: Callable[..., str]) -> None:
    assert "No next action set" in run("add", "A Paper")


def test_list_as_json(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "draft")
    payload = json.loads(run("list", "--json"))
    assert payload[0]["title"] == "A Paper"
    assert payload[0]["status"] == "draft"


def test_list_filters(run: Callable[..., str]) -> None:
    run("add", "Alpha", "--status", "draft", "--priority", "high")
    run("add", "Beta", "--status", "published", "--priority", "low")

    assert "Beta" not in run("list", "--status", "draft")
    assert "Alpha" not in run("list", "--priority", "low")
    assert "Beta" not in run("list", "--active")


def test_status_moves_a_paper_and_refuses_a_leap(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "analysis")
    assert "analysis -> draft" in run("status", "a-paper", "draft")

    run("add", "Another", "--status", "idea")
    assert main(["--root", ".", "status", "another", "published"]) == EXIT_ERROR


def test_a_forced_transition_is_allowed(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "idea")
    assert "idea -> published" in run("status", "a-paper", "published", "--force")


def test_next_action_lifecycle(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "draft")

    run(
        "next-action",
        "a-paper",
        "Finish the methodology section",
        "--due",
        real_days_ahead(10),
    )
    assert "Finish the methodology section" in run("next-action", "a-paper")

    run("next-action", "a-paper", "--done", "--then", "Rebuild the tables")
    assert "Rebuild the tables" in run("next-action", "a-paper")

    run("next-action", "a-paper", "--clear")
    assert "no next action set" in run("next-action", "a-paper")


def test_next_action_without_a_paper_lists_them_all(run: Callable[..., str]) -> None:
    run("add", "Alpha", "--status", "draft", "--next-action", "Write the intro")
    run("add", "Beta", "--status", "draft")

    output = run("next-action")
    assert "Write the intro" in output
    assert "- none -" in output


def test_reminders_report_and_exit_code(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "analysis")

    output = run("reminders", expect=EXIT_OK)
    assert "Active project has no next action." in output

    run("reminders", "--exit-code", expect=EXIT_FINDINGS)


def test_reminders_as_json_can_be_filtered_by_kind(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "analysis")
    payload = json.loads(run("reminders", "--kind", "missing_next_action", "--json"))
    assert [item["kind"] for item in payload] == ["missing_next_action"]


def test_a_healthy_portfolio_says_so(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "draft", "--next-action", "x", "--due", real_days_ahead(120))
    assert "Nothing needs attention." in run("reminders")


def test_dashboard_shows_counts_and_focus(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "analysis")
    output = run("dashboard")
    assert "Needs attention" in output
    assert "What to work on next" in output
    assert "- none set -" in output


def test_board_groups_by_column(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "analysis")
    run("add", "Written", "--status", "draft")

    output = run("board")
    assert "Analysis (1)" in output
    assert "Writing (1)" in output


def test_calendar_lists_deadlines(run: Callable[..., str]) -> None:
    due = real_days_ahead(40)
    run("add", "A Paper", "--status", "draft", "--next-action", "x", "--due", due)
    output = run("calendar")
    assert datetime.fromisoformat(due).strftime("%B %Y") in output
    assert due in output


def test_calendar_is_empty_when_nothing_is_dated(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "draft")
    assert "No deadlines recorded." in run("calendar")


def test_analytics_reports_unavailable_rather_than_zero(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "idea")
    output = run("analytics")
    assert "not enough data" in output

    payload = json.loads(run("analytics", "--json"))
    assert payload["acceptance_rate"] is None


def test_submission_and_decision(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "ready_to_submit")
    assert "submitted to Labour Economics" in run("submit", "a-paper", "Labour Economics")

    output = run("decision", "a-paper", "major_revision", "--revision-due", real_days_ahead(5))
    assert "now at revision" in output

    # The CLI runs on the real clock, so assert on the shape rather than the count.
    reminders = run("reminders")
    assert "Revision in " in reminders
    assert "CRITICAL" in reminders


def test_events_show_the_history(run: Callable[..., str]) -> None:
    run("add", "A Paper", "--status", "draft")
    run("next-action", "a-paper", "Write the intro")

    output = run("events", "a-paper")
    assert "project_created" in output
    assert "Next action set: Write the intro" in output


def test_update_edits_fields(run: Callable[..., str]) -> None:
    run("add", "A Paper")
    assert "target_journal" in run("update", "a-paper", "--journal", "Econometrica")
    assert "Econometrica" in run("show", "a-paper")


def test_update_with_nothing_to_do_says_so(run: Callable[..., str]) -> None:
    run("add", "A Paper")
    assert "Nothing to update." in run("update", "a-paper")


def test_migrate_then_validate(run: Callable[..., str], workspace: Path) -> None:
    write_legacy(workspace / "data" / "articles.json")

    assert "Migrated 1 legacy article" in run("migrate")
    assert "portfolio.json is valid: 1 papers." in run("validate")


def test_migrate_dry_run_reports_without_writing(
    run: Callable[..., str], workspace: Path
) -> None:
    write_legacy(workspace / "data" / "articles.json")
    assert "Would migrate" in run("migrate", "--dry-run")
    assert not (workspace / "data" / "portfolio.json").exists()


def test_legacy_export_round_trips(run: Callable[..., str], workspace: Path) -> None:
    write_legacy(workspace / "data" / "articles.json")
    run("migrate")
    due = real_days_ahead(90)
    run("next-action", "uncertainty", "Rebuild the figures", "--due", due)

    run("legacy-export")
    exported = json.loads((workspace / "data" / "articles.json").read_text(encoding="utf-8"))
    assert exported["articles"][0]["next_action"] == "Rebuild the figures"
    assert exported["articles"][0]["target_date"] == due


def test_the_legacy_tracker_is_readable_before_any_migration(
    run: Callable[..., str], workspace: Path
) -> None:
    write_legacy(workspace / "data" / "articles.json")
    assert "owner/uncertainty-bench" in run("list")
    assert "articles.json is valid" in run("validate")


def test_settings_shows_the_resolved_configuration(run: Callable[..., str]) -> None:
    output = run("settings")
    assert "Staleness thresholds" in output
    assert "portfolio.json" in output

    payload = json.loads(run("settings", "--json"))
    assert payload["staleness"]["draft"] == 14


def test_sync_github_without_a_token_fails_politely(
    run: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("ARTICLE_SCAN_TOKEN", raising=False)
    assert main(["--root", ".", "sync-github"]) == EXIT_ERROR


def test_an_unknown_paper_is_an_error_not_a_traceback(run: Callable[..., str]) -> None:
    assert main(["--root", ".", "show", "nothing-like-this"]) == EXIT_ERROR


class TestFormatting:
    def test_truncation_stays_ascii(self) -> None:
        assert truncate("a very long sentence indeed", 12) == "a very lo..."
        assert truncate("short", 12) == "short"

    def test_a_table_aligns_and_underlines(self) -> None:
        rendered = table(["A", "B"], [["1", "2"]])
        assert rendered.splitlines()[1].startswith("-")

    def test_an_empty_table_renders_nothing(self) -> None:
        assert table(["A"], []) == ""

    def test_relative_days_reads_naturally(self) -> None:
        assert relative_days(days_ahead(1), NOW) == "in 1 day"
        assert relative_days(days_ahead(-3), NOW) == "3 days ago"
        assert relative_days(NOW, NOW) == "today"
        assert relative_days(None, NOW) == "-"
