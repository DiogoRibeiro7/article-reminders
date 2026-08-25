"""Storage adapters: the portfolio file, the event log, and the legacy tracker."""

from article_reminders.infrastructure.storage.event_log import JsonlEventLog
from article_reminders.infrastructure.storage.json_store import JsonPaperRepository, atomic_write
from article_reminders.infrastructure.storage.legacy import (
    LEGACY_STATUS_TO_LIFECYCLE,
    LIFECYCLE_TO_LEGACY,
    legacy_from_paper,
    paper_from_legacy,
    read_legacy_file,
    render_legacy_document,
)
from article_reminders.infrastructure.storage.migration import (
    MigrationReport,
    backup_file,
    export_legacy_tracker,
    migrate_legacy_portfolio,
)

__all__ = [
    "LEGACY_STATUS_TO_LIFECYCLE",
    "LIFECYCLE_TO_LEGACY",
    "JsonPaperRepository",
    "JsonlEventLog",
    "MigrationReport",
    "atomic_write",
    "backup_file",
    "export_legacy_tracker",
    "legacy_from_paper",
    "migrate_legacy_portfolio",
    "paper_from_legacy",
    "read_legacy_file",
    "render_legacy_document",
]
