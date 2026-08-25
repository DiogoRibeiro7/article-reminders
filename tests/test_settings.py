"""Configuration loading, and what happens without any configuration at all."""

from __future__ import annotations

from pathlib import Path

import pytest

from article_reminders.domain.enums import LifecycleStatus
from article_reminders.domain.errors import ValidationError
from article_reminders.infrastructure.configuration.settings import (
    CONFIG_ENV_VAR,
    ActivityPaths,
    Settings,
    find_config_file,
    load_settings,
    repository_root,
)

S = LifecycleStatus


def write_config(root: Path, body: str) -> Path:
    path = root / "article-reminders.yml"
    path.write_text(body, encoding="utf-8")
    return path


def test_defaults_work_with_no_configuration_file(workspace: Path) -> None:
    settings = load_settings(root=workspace)

    assert settings.source_path is None
    assert settings.paths.portfolio == workspace / "data" / "portfolio.json"
    assert settings.paths.legacy == workspace / "data" / "articles.json"
    assert settings.staleness[S.DRAFT] == 14
    assert settings.reminders.upcoming_deadline_days == 14
    assert settings.github.repository is None


def test_a_config_file_overrides_only_what_it_mentions(workspace: Path) -> None:
    write_config(
        workspace,
        """
        staleness:
          draft: 5
          under_review: 200
        reminders:
          upcoming_deadline_days: 21
          inactivity:
            manuscript: 10
        stagnation:
          manuscript_idle_days: 15
        github:
          repository: owner/tracker
          workflow_labels: false
        """,
    )
    settings = load_settings(root=workspace)

    assert settings.staleness[S.DRAFT] == 5
    assert settings.staleness[S.UNDER_REVIEW] == 200
    assert settings.staleness[S.ANALYSIS] == 21, "unmentioned stages keep their default"
    assert settings.reminders.upcoming_deadline_days == 21
    assert settings.reminders.manuscript_inactivity_days == 10
    assert settings.reminders.repository_inactivity_days == 45
    assert settings.stagnation.manuscript_idle_days == 15
    assert settings.stagnation.repository_active_within_days == 7
    assert settings.github.repository == "owner/tracker"
    assert settings.github.workflow_labels is False
    assert settings.source_path is not None


def test_activity_paths_are_normalised_to_prefixes(workspace: Path) -> None:
    write_config(
        workspace,
        """
        activity_paths:
          manuscript:
            - ./ms
            - writeup/
          analysis:
            - code
        """,
    )
    paths = load_settings(root=workspace).activity_paths

    assert paths.manuscript == ("ms/", "writeup/")
    assert paths.analysis == ("code/",)
    assert paths.data == ActivityPaths().data


def test_relative_storage_paths_are_resolved_against_the_root(workspace: Path) -> None:
    write_config(
        workspace,
        """
        storage:
          portfolio: research/papers.json
          events: research/events.jsonl
        """,
    )
    settings = load_settings(root=workspace)

    assert settings.paths.portfolio == workspace / "research" / "papers.json"
    assert settings.paths.events == workspace / "research" / "events.jsonl"
    assert settings.paths.legacy == workspace / "data" / "articles.json"


def test_an_unknown_stage_is_rejected_with_the_vocabulary(workspace: Path) -> None:
    write_config(workspace, "staleness:\n  experiments: 10\n")
    with pytest.raises(ValidationError, match="unknown stage"):
        load_settings(root=workspace)


def test_a_negative_threshold_is_rejected(workspace: Path) -> None:
    write_config(workspace, "reminders:\n  upcoming_deadline_days: -1\n")
    with pytest.raises(ValidationError, match="must not be negative"):
        load_settings(root=workspace)


def test_a_non_numeric_threshold_is_rejected(workspace: Path) -> None:
    write_config(workspace, "stagnation:\n  manuscript_idle_days: soon\n")
    with pytest.raises(ValidationError, match="whole number of days"):
        load_settings(root=workspace)


def test_broken_yaml_is_reported_with_the_file_name(workspace: Path) -> None:
    write_config(workspace, "staleness: [unclosed\n")
    with pytest.raises(ValidationError, match="not valid YAML"):
        load_settings(root=workspace)


def test_an_environment_override_that_does_not_exist_is_an_error(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONFIG_ENV_VAR, str(workspace / "missing.yml"))
    with pytest.raises(ValidationError, match="which does not exist"):
        find_config_file(workspace)


def test_the_root_can_be_set_from_the_environment(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTICLE_REMINDERS_ROOT", str(workspace))
    assert repository_root() == workspace.resolve()


def test_the_github_repository_falls_back_to_the_actions_environment(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/tracker")
    assert Settings.default(workspace).github.resolved_repository() == "owner/tracker"


def test_the_settings_serialise_for_the_settings_page(workspace: Path) -> None:
    payload = Settings.default(workspace).to_dict()
    assert payload["staleness"]["revision"] == 7
    assert "manuscript" in payload["activity_paths"]
    assert payload["github"]["managed_label"] == "article-reminder"


def test_the_example_configuration_in_the_repository_loads(workspace: Path) -> None:
    """The shipped example is the file people copy; it has to be valid."""
    example = Path(__file__).resolve().parents[1] / "article-reminders.example.yml"
    settings = load_settings(example, root=workspace)
    assert settings.source_path == example
    assert settings.staleness[S.REVISION] >= 1
