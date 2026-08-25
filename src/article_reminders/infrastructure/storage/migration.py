"""Migrating the legacy tracker into the portfolio.

Rules this module holds itself to:

* the legacy ``data/articles.json`` is never modified;
* an existing portfolio is backed up before it is touched;
* unknown legacy keys survive into ``Paper.extra`` rather than being dropped;
* running it twice changes nothing the second time.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from article_reminders.domain.enums import ProjectEventType
from article_reminders.domain.events import ProjectEvent
from article_reminders.domain.models import Paper
from article_reminders.domain.timeutils import format_datetime, now
from article_reminders.infrastructure.configuration.settings import Settings
from article_reminders.infrastructure.storage.event_log import JsonlEventLog
from article_reminders.infrastructure.storage.json_store import (
    JsonPaperRepository,
    atomic_write,
)
from article_reminders.infrastructure.storage.legacy import (
    LEGACY_KEYS,
    LIFECYCLE_TO_LEGACY,
    paper_from_legacy,
    read_legacy_file,
    render_legacy_document,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """What a migration did, or would do."""

    source: Path
    destination: Path
    read: int = 0
    created: int = 0
    already_present: int = 0
    preserved_unknown_keys: tuple[str, ...] = ()
    backup_path: Path | None = None
    dry_run: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return self.created > 0

    def summary(self) -> str:
        head = "Would migrate" if self.dry_run else "Migrated"
        parts = [
            f"{head} {self.read} legacy article(s) from {self.source}",
            f"{self.created} new, {self.already_present} already present",
        ]
        if self.preserved_unknown_keys:
            parts.append(f"preserved unknown keys: {', '.join(self.preserved_unknown_keys)}")
        if self.backup_path is not None:
            parts.append(f"backup: {self.backup_path}")
        return "; ".join(parts) + "."


def backup_file(path: Path, backup_dir: Path, *, stamp: datetime | None = None) -> Path | None:
    """Copy ``path`` into ``backup_dir`` under a timestamped name.

    Returns ``None`` when there was nothing to back up.
    """
    if not path.exists():
        return None
    marker = (stamp or now()).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{path.stem}.{marker}{path.suffix}"
    shutil.copy2(path, destination)
    logger.info("backed up %s to %s", path, destination)
    return destination


def _identity(paper: Paper) -> tuple[str, str, str]:
    return (
        paper.title.strip().lower(),
        (paper.repository_slug or "").lower(),
        (paper.paper_path or "").lower(),
    )


def migrate_legacy_portfolio(
    settings: Settings,
    *,
    dry_run: bool = False,
    clock: datetime | None = None,
) -> MigrationReport:
    """Load the legacy tracker and merge it into the portfolio file.

    Existing papers are matched on title plus repository plus manuscript path and
    are left exactly as they are: a migration must never overwrite work done in
    the new model.
    """
    stamp = clock or now()
    source = settings.paths.legacy
    destination = settings.paths.portfolio
    repository = JsonPaperRepository(destination, legacy_path=None)
    events = JsonlEventLog(settings.paths.events)

    records = read_legacy_file(source)
    if not records:
        return MigrationReport(
            source=source,
            destination=destination,
            dry_run=dry_run,
            warnings=(f"No legacy articles found at {source}.",),
        )

    existing = repository.load()
    by_identity: Mapping[tuple[str, str, str], Paper] = {
        _identity(paper): paper for paper in existing
    }
    known_ids = {paper.id for paper in existing}

    created: list[Paper] = []
    already = 0
    unknown_keys: set[str] = set()
    warnings: list[str] = []

    for record in records:
        unknown_keys.update(key for key in record if key not in LEGACY_KEYS)
        paper = paper_from_legacy(record, observed_at=stamp)
        if paper.id in known_ids or _identity(paper) in by_identity:
            already += 1
            continue
        created.append(paper)
        known_ids.add(paper.id)

        if not paper.repository_slug:
            warnings.append(f"{paper.title!r} has no repository; activity cannot be detected.")

    backup: Path | None = None
    if not dry_run and created:
        backup = backup_file(destination, settings.paths.backups, stamp=stamp)
        repository.save_all([*existing, *created], generated_at=stamp)
        events.extend(
            ProjectEvent(
                project_id=paper.id,
                event_type=ProjectEventType.MIGRATED_FROM_LEGACY,
                occurred_at=stamp,
                metadata={
                    "source": str(source),
                    "legacy_status": record_status(paper),
                    "migrated_at": format_datetime(stamp),
                },
            )
            for paper in created
        )

    return MigrationReport(
        source=source,
        destination=destination,
        read=len(records),
        created=len(created),
        already_present=already,
        preserved_unknown_keys=tuple(sorted(unknown_keys)),
        backup_path=backup,
        dry_run=dry_run,
        warnings=tuple(warnings),
    )


def record_status(paper: Paper) -> str:
    """The legacy status a migrated paper came from."""
    return LIFECYCLE_TO_LEGACY[paper.status]


def export_legacy_tracker(
    settings: Settings,
    papers: Sequence[Paper],
    *,
    dry_run: bool = False,
    clock: datetime | None = None,
) -> tuple[str, Path | None]:
    """Render the portfolio back into ``data/articles.json``.

    The scheduled workflows read that file, so this is how the new model keeps the
    old automation fed. The previous file is backed up first.
    """
    document = render_legacy_document(papers)
    if dry_run:
        return document, None

    backup = backup_file(settings.paths.legacy, settings.paths.backups, stamp=clock or now())
    atomic_write(settings.paths.legacy, document)
    logger.info("exported %d papers to %s", len(papers), settings.paths.legacy)
    return document, backup
