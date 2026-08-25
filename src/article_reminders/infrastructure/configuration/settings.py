"""Configuration: typed settings loaded from a small YAML file.

Everything has a working default, so the application runs in a fresh checkout
with no configuration at all. A ``article-reminders.yml`` at the repository root
overrides whatever it mentions and nothing else.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from article_reminders.domain.enums import LifecycleStatus
from article_reminders.domain.errors import ValidationError
from article_reminders.domain.rules import DEFAULT_STALENESS_DAYS

CONFIG_ENV_VAR = "ARTICLE_REMINDERS_CONFIG"
ROOT_ENV_VAR = "ARTICLE_REMINDERS_ROOT"

#: Files searched, in order, when no explicit config path is given.
CONFIG_FILENAMES: tuple[str, ...] = (
    "article-reminders.yml",
    "article-reminders.yaml",
    ".article-reminders.yml",
)

DEFAULT_MANUSCRIPT_PATHS: tuple[str, ...] = ("paper/", "papers/", "manuscript/", "manuscripts/")
DEFAULT_ANALYSIS_PATHS: tuple[str, ...] = (
    "src/",
    "analysis/",
    "notebooks/",
    "scripts/",
    "results/",
    "code/",
    "R/",
)
DEFAULT_DATA_PATHS: tuple[str, ...] = ("data/", "datasets/", "raw/")


def _as_paths(value: object, fallback: Sequence[str], *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return tuple(fallback)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable):
        raise ValidationError(f"{field_name} must be a list of path prefixes.")
    out: list[str] = []
    for item in value:
        text = str(item).strip().lstrip("./")
        if not text:
            continue
        out.append(text if text.endswith("/") or "." in text.rsplit("/", 1)[-1] else f"{text}/")
    return tuple(out)


def _as_int(value: object, fallback: int, *, field_name: str) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, (int, str, float)):
        raise ValidationError(f"{field_name} must be a whole number of days.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a whole number of days.") from exc
    if parsed < 0:
        raise ValidationError(f"{field_name} must not be negative.")
    return parsed


@dataclass(frozen=True, slots=True)
class ActivityPaths:
    """Path prefixes that decide what a commit touched.

    Path-based and deterministic on purpose: commit messages say whatever the
    author felt like saying, and "wip" is not evidence about a manuscript.
    """

    manuscript: tuple[str, ...] = DEFAULT_MANUSCRIPT_PATHS
    analysis: tuple[str, ...] = DEFAULT_ANALYSIS_PATHS
    data: tuple[str, ...] = DEFAULT_DATA_PATHS

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "manuscript": list(self.manuscript),
            "analysis": list(self.analysis),
            "data": list(self.data),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ActivityPaths:
        return cls(
            manuscript=_as_paths(
                raw.get("manuscript"),
                DEFAULT_MANUSCRIPT_PATHS,
                field_name="activity_paths.manuscript",
            ),
            analysis=_as_paths(
                raw.get("analysis"), DEFAULT_ANALYSIS_PATHS, field_name="activity_paths.analysis"
            ),
            data=_as_paths(raw.get("data"), DEFAULT_DATA_PATHS, field_name="activity_paths.data"),
        )


@dataclass(frozen=True, slots=True)
class ReminderSettings:
    """Thresholds for the reminder engine that are not stage-dependent."""

    upcoming_deadline_days: int = 14
    manuscript_inactivity_days: int = 30
    repository_inactivity_days: int = 45
    project_inactivity_days: int = 60
    waiting_follow_up_days: int = 60

    def to_dict(self) -> dict[str, int]:
        return {
            "upcoming_deadline_days": self.upcoming_deadline_days,
            "manuscript_inactivity_days": self.manuscript_inactivity_days,
            "repository_inactivity_days": self.repository_inactivity_days,
            "project_inactivity_days": self.project_inactivity_days,
            "waiting_follow_up_days": self.waiting_follow_up_days,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ReminderSettings:
        base = cls()
        inactivity = raw.get("inactivity") or {}
        if not isinstance(inactivity, Mapping):
            raise ValidationError("reminders.inactivity must be a mapping.")
        return cls(
            upcoming_deadline_days=_as_int(
                raw.get("upcoming_deadline_days"),
                base.upcoming_deadline_days,
                field_name="reminders.upcoming_deadline_days",
            ),
            manuscript_inactivity_days=_as_int(
                inactivity.get("manuscript"),
                base.manuscript_inactivity_days,
                field_name="reminders.inactivity.manuscript",
            ),
            repository_inactivity_days=_as_int(
                inactivity.get("repository"),
                base.repository_inactivity_days,
                field_name="reminders.inactivity.repository",
            ),
            project_inactivity_days=_as_int(
                inactivity.get("project"),
                base.project_inactivity_days,
                field_name="reminders.inactivity.project",
            ),
            waiting_follow_up_days=_as_int(
                raw.get("waiting_follow_up_days"),
                base.waiting_follow_up_days,
                field_name="reminders.waiting_follow_up_days",
            ),
        )


@dataclass(frozen=True, slots=True)
class StagnationSettings:
    """When an active repository with an untouched manuscript becomes a finding."""

    repository_active_within_days: int = 7
    manuscript_idle_days: int = 30

    def to_dict(self) -> dict[str, int]:
        return {
            "repository_active_within_days": self.repository_active_within_days,
            "manuscript_idle_days": self.manuscript_idle_days,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> StagnationSettings:
        base = cls()
        return cls(
            repository_active_within_days=_as_int(
                raw.get("repository_active_within_days"),
                base.repository_active_within_days,
                field_name="stagnation.repository_active_within_days",
            ),
            manuscript_idle_days=_as_int(
                raw.get("manuscript_idle_days"),
                base.manuscript_idle_days,
                field_name="stagnation.manuscript_idle_days",
            ),
        )


@dataclass(frozen=True, slots=True)
class GitHubSettings:
    """How the application talks to GitHub, and what it labels."""

    repository: str | None = None
    api_url: str = "https://api.github.com"
    issue_prefix: str = "[article-reminder]"
    managed_label: str = "article-reminder"
    paper_label: str = "research-paper"
    workflow_labels: bool = True
    token_env: str = "GITHUB_TOKEN"
    scan_token_env: str = "ARTICLE_SCAN_TOKEN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "api_url": self.api_url,
            "issue_prefix": self.issue_prefix,
            "managed_label": self.managed_label,
            "paper_label": self.paper_label,
            "workflow_labels": self.workflow_labels,
            "token_env": self.token_env,
            "scan_token_env": self.scan_token_env,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GitHubSettings:
        base = cls()
        repository = raw.get("repository")
        return cls(
            repository=str(repository).strip() or None if repository else None,
            api_url=str(raw.get("api_url") or base.api_url).rstrip("/"),
            issue_prefix=str(raw.get("issue_prefix") or base.issue_prefix),
            managed_label=str(raw.get("managed_label") or base.managed_label),
            paper_label=str(raw.get("paper_label") or base.paper_label),
            workflow_labels=bool(raw.get("workflow_labels", base.workflow_labels)),
            token_env=str(raw.get("token_env") or base.token_env),
            scan_token_env=str(raw.get("scan_token_env") or base.scan_token_env),
        )

    def resolved_repository(self) -> str | None:
        """The configured repository, or the one the Actions runner is in."""
        return self.repository or os.environ.get("GITHUB_REPOSITORY") or None

    def token(self) -> str | None:
        return os.environ.get(self.token_env) or None

    def scan_token(self) -> str | None:
        return os.environ.get(self.scan_token_env) or self.token()


@dataclass(frozen=True, slots=True)
class StoragePaths:
    """Where the portfolio lives on disk.

    Three files, all plain text: the portfolio, the event log, and the legacy
    tracker the GitHub Actions workflows still read.
    """

    root: Path
    portfolio: Path
    events: Path
    legacy: Path
    backups: Path

    @classmethod
    def under(cls, root: Path) -> StoragePaths:
        data = root / "data"
        return cls(
            root=root,
            portfolio=data / "portfolio.json",
            events=data / "events.jsonl",
            legacy=data / "articles.json",
            backups=data / "backups",
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "portfolio": str(self.portfolio),
            "events": str(self.events),
            "legacy": str(self.legacy),
            "backups": str(self.backups),
        }


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the application needs to know that is not a paper."""

    paths: StoragePaths
    staleness: Mapping[LifecycleStatus, int] = field(
        default_factory=lambda: dict(DEFAULT_STALENESS_DAYS)
    )
    reminders: ReminderSettings = field(default_factory=ReminderSettings)
    stagnation: StagnationSettings = field(default_factory=StagnationSettings)
    activity_paths: ActivityPaths = field(default_factory=ActivityPaths)
    github: GitHubSettings = field(default_factory=GitHubSettings)
    source_path: Path | None = None

    @classmethod
    def default(cls, root: Path | None = None) -> Settings:
        return cls(paths=StoragePaths.under(root or repository_root()))

    def with_paths(self, **changes: Path) -> Settings:
        return replace(self, paths=replace(self.paths, **changes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "paths": self.paths.to_dict(),
            "staleness": {status.value: days for status, days in sorted(self.staleness.items())},
            "reminders": self.reminders.to_dict(),
            "stagnation": self.stagnation.to_dict(),
            "activity_paths": self.activity_paths.to_dict(),
            "github": self.github.to_dict(),
            "source_path": None if self.source_path is None else str(self.source_path),
        }


def repository_root() -> Path:
    """The directory the portfolio lives under.

    ``ARTICLE_REMINDERS_ROOT`` wins; otherwise the current working directory, which
    is what both the CLI and the GitHub Actions runner want.
    """
    override = os.environ.get(ROOT_ENV_VAR)
    return Path(override).expanduser().resolve() if override else Path.cwd()


def find_config_file(root: Path) -> Path | None:
    """The configuration file for ``root``, if there is one."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        candidate = Path(override).expanduser()
        if not candidate.exists():
            raise ValidationError(f"{CONFIG_ENV_VAR} points at {candidate}, which does not exist.")
        return candidate
    for name in CONFIG_FILENAMES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _staleness_from(raw: object) -> dict[LifecycleStatus, int]:
    values = dict(DEFAULT_STALENESS_DAYS)
    if raw is None:
        return values
    if not isinstance(raw, Mapping):
        raise ValidationError("staleness must be a mapping of stage to days.")
    for key, value in raw.items():
        try:
            status = LifecycleStatus(str(key))
        except ValueError as exc:
            raise ValidationError(
                f"staleness names an unknown stage {key!r}. Valid: "
                f"{', '.join(item.value for item in LifecycleStatus)}."
            ) from exc
        values[status] = _as_int(value, 0, field_name=f"staleness.{key}")
    return values


def load_settings(path: Path | None = None, *, root: Path | None = None) -> Settings:
    """Load settings, falling back to defaults for everything unmentioned."""
    base_root = (root or repository_root()).resolve()
    config_path = path or find_config_file(base_root)
    settings = Settings.default(base_root)
    if config_path is None:
        return settings

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"{config_path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValidationError(f"{config_path} must contain a mapping at the top level.")

    paths = settings.paths
    storage_raw = raw.get("storage") or {}
    if not isinstance(storage_raw, Mapping):
        raise ValidationError("storage must be a mapping.")
    for key in ("portfolio", "events", "legacy", "backups"):
        value = storage_raw.get(key)
        if value:
            candidate = Path(str(value)).expanduser()
            resolved = candidate if candidate.is_absolute() else base_root / candidate
            paths = replace(paths, **{key: resolved})

    github_raw = raw.get("github") or {}
    if not isinstance(github_raw, Mapping):
        raise ValidationError("github must be a mapping.")
    reminders_raw = raw.get("reminders") or {}
    if not isinstance(reminders_raw, Mapping):
        raise ValidationError("reminders must be a mapping.")
    stagnation_raw = raw.get("stagnation") or {}
    if not isinstance(stagnation_raw, Mapping):
        raise ValidationError("stagnation must be a mapping.")
    activity_raw = raw.get("activity_paths") or {}
    if not isinstance(activity_raw, Mapping):
        raise ValidationError("activity_paths must be a mapping.")

    return Settings(
        paths=paths,
        staleness=_staleness_from(raw.get("staleness")),
        reminders=ReminderSettings.from_dict(reminders_raw),
        stagnation=StagnationSettings.from_dict(stagnation_raw),
        activity_paths=ActivityPaths.from_dict(activity_raw),
        github=GitHubSettings.from_dict(github_raw),
        source_path=config_path,
    )
