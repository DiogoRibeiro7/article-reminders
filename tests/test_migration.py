"""Legacy compatibility: reading, migrating, and exporting ``data/articles.json``.

The repository this application grew out of has sixty-five real entries in that
file, and the promise is that none of them is lost or altered by any of this.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from article_reminders.application.services import PortfolioService
from article_reminders.domain.enums import LifecycleStatus, Priority
from article_reminders.domain.errors import ValidationError
from article_reminders.infrastructure.configuration.settings import Settings
from article_reminders.infrastructure.storage.json_store import JsonPaperRepository
from article_reminders.infrastructure.storage.legacy import (
    LEGACY_STATUS_TO_LIFECYCLE,
    LIFECYCLE_TO_LEGACY,
    legacy_from_paper,
    legacy_paper_id,
    paper_from_legacy,
    read_legacy_file,
    render_legacy_document,
)
from article_reminders.infrastructure.storage.migration import (
    backup_file,
    export_legacy_tracker,
    migrate_legacy_portfolio,
)
from tests.conftest import LEGACY_ARTICLE, write_legacy

S = LifecycleStatus

REPOSITORY_DATA = Path(__file__).resolve().parents[1] / "data" / "articles.json"


class TestStatusMapping:
    def test_every_legacy_status_round_trips(self) -> None:
        """Nine legacy values in, nine legacy values out."""
        for legacy, lifecycle in LEGACY_STATUS_TO_LIFECYCLE.items():
            assert LIFECYCLE_TO_LEGACY[lifecycle] == legacy

    def test_every_lifecycle_status_maps_to_something_legacy(self) -> None:
        for status in LifecycleStatus:
            assert status in LIFECYCLE_TO_LEGACY

    def test_an_unknown_legacy_status_is_rejected_loudly(self) -> None:
        with pytest.raises(ValidationError, match="unknown legacy status"):
            paper_from_legacy({"title": "A Paper", "status": "experiments running"})


class TestReadingLegacyRecords:
    def test_a_full_record_is_translated(self) -> None:
        paper = paper_from_legacy(LEGACY_ARTICLE)

        assert paper.title == LEGACY_ARTICLE["title"]
        assert paper.status is S.RESEARCH
        assert paper.priority is Priority.HIGH
        assert paper.repository is not None
        assert paper.repository.slug == "owner/uncertainty-bench"
        assert paper.paper_path == "paper/"
        assert paper.abstract.startswith("We present")
        assert paper.target_journal == "JOSS"
        assert paper.next_action is not None
        assert paper.next_action.description == "Regenerate tables and figures."
        assert paper.next_action_due_at is not None
        assert paper.next_action_due_at.date().isoformat() == "2026-03-20"
        assert paper.updated_at.date().isoformat() == "2026-03-07"

    def test_ids_are_stable_across_loads(self) -> None:
        first = paper_from_legacy(LEGACY_ARTICLE)
        second = paper_from_legacy(dict(LEGACY_ARTICLE))
        assert first.id == second.id
        assert first.id == legacy_paper_id(
            str(LEGACY_ARTICLE["title"]), "owner/uncertainty-bench", "paper/"
        )

    def test_a_target_date_with_no_next_action_is_kept_verbatim(self) -> None:
        record = {**LEGACY_ARTICLE}
        del record["next_action"]
        paper = paper_from_legacy(record)

        assert paper.next_action is None
        assert paper.extra["target_date"] == "2026-03-20"
        assert legacy_from_paper(paper)["target_date"] == "2026-03-20"

    def test_an_entry_with_no_repository_is_still_readable(self) -> None:
        record = {**LEGACY_ARTICLE, "repo": "", "paper_path": ""}
        paper = paper_from_legacy(record)
        assert paper.repository is None
        assert legacy_from_paper(paper)["repo"] == ""

    def test_a_broken_file_is_reported_rather_than_half_read(self, tmp_path: Path) -> None:
        path = tmp_path / "articles.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(ValidationError, match="not valid JSON"):
            read_legacy_file(path)

    def test_a_wrong_shape_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "articles.json"
        path.write_text(json.dumps([{"title": "A"}]), encoding="utf-8")
        with pytest.raises(ValidationError, match="'articles' key"):
            read_legacy_file(path)


class TestTheRepositorysOwnData:
    """The strongest compatibility check available: the real file."""

    def test_every_entry_loads(self) -> None:
        records = read_legacy_file(REPOSITORY_DATA)
        assert len(records) >= 60
        papers = [paper_from_legacy(record) for record in records]
        assert len({paper.id for paper in papers}) == len(papers)

    def test_every_entry_round_trips_byte_for_byte(self) -> None:
        records = read_legacy_file(REPOSITORY_DATA)
        for record in records:
            assert legacy_from_paper(paper_from_legacy(record)) == record

    def test_the_export_reorders_but_never_alters(self) -> None:
        """The one thing a full export changes about the real file.

        The portfolio is stored sorted by title, so the first export reorders
        data/articles.json. Every record survives unchanged; only their order
        moves, and only once.
        """
        records = read_legacy_file(REPOSITORY_DATA)
        papers = [paper_from_legacy(record) for record in records]
        exported = json.loads(render_legacy_document(sorted(papers, key=lambda p: p.title.lower())))

        by_title = sorted(records, key=lambda record: str(record["title"]).lower())
        assert exported["articles"] == by_title

    def test_the_export_order_does_not_depend_on_the_order_it_is_given(self) -> None:
        """Callers pass ``list_papers()``, which is sorted by urgency.

        Urgency moves on its own as deadlines approach and papers go stale. An export that
        inherited it would rewrite the whole file whenever the ranking shifted rather than
        when a paper changed, which is how one scheduled run came to move 453 lines to add a
        single paper.
        """
        papers = [paper_from_legacy(record) for record in read_legacy_file(REPOSITORY_DATA)]
        by_slug = sorted(papers, key=lambda paper: str(paper.slug))

        forwards = render_legacy_document(papers)
        backwards = render_legacy_document(list(reversed(papers)))
        by_title_desc = render_legacy_document(sorted(papers, key=lambda p: p.title, reverse=True))

        assert forwards == backwards == by_title_desc

        exported = json.loads(forwards)["articles"]
        assert [record["title"] for record in exported] == [paper.title for paper in by_slug]

    def test_the_exported_document_is_still_valid_for_the_old_workflow(
        self, tmp_path: Path
    ) -> None:
        """The export has to survive ``scripts/validate_articles.py``."""
        import importlib.util
        import sys

        module_path = REPOSITORY_DATA.parents[1] / "scripts" / "validate_articles.py"
        spec = importlib.util.spec_from_file_location("validate_articles_check", module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        papers = [paper_from_legacy(record) for record in read_legacy_file(REPOSITORY_DATA)]
        rendered = render_legacy_document(papers)

        scratch = tmp_path / "articles.json"
        scratch.write_text(rendered, encoding="utf-8")
        assert module.validate(scratch) == []


class TestMigration:
    def test_it_creates_the_portfolio_without_touching_the_legacy_file(
        self, settings: Settings
    ) -> None:
        write_legacy(settings.paths.legacy)
        before = settings.paths.legacy.read_text(encoding="utf-8")

        report = migrate_legacy_portfolio(settings)

        assert report.created == 1
        assert settings.paths.portfolio.exists()
        assert settings.paths.legacy.read_text(encoding="utf-8") == before

    def test_running_it_twice_changes_nothing(self, settings: Settings) -> None:
        write_legacy(settings.paths.legacy)
        migrate_legacy_portfolio(settings)
        after_first = settings.paths.portfolio.read_text(encoding="utf-8")

        second = migrate_legacy_portfolio(settings)

        assert second.created == 0
        assert second.already_present == 1
        assert settings.paths.portfolio.read_text(encoding="utf-8") == after_first

    def test_a_dry_run_writes_nothing(self, settings: Settings) -> None:
        write_legacy(settings.paths.legacy)
        report = migrate_legacy_portfolio(settings, dry_run=True)

        assert report.created == 1
        assert report.dry_run
        assert not settings.paths.portfolio.exists()

    def test_unknown_legacy_keys_are_reported_and_preserved(self, settings: Settings) -> None:
        write_legacy(settings.paths.legacy, {**LEGACY_ARTICLE, "reviewer": "R2"})

        report = migrate_legacy_portfolio(settings)

        assert "reviewer" in report.preserved_unknown_keys
        stored = json.loads(settings.paths.portfolio.read_text(encoding="utf-8"))
        assert stored["papers"][0]["reviewer"] == "R2"

    def test_existing_papers_are_never_overwritten(self, settings: Settings) -> None:
        """Work done in the new model outranks whatever the legacy file still says."""
        write_legacy(settings.paths.legacy)
        migrate_legacy_portfolio(settings)

        repository = JsonPaperRepository(settings.paths.portfolio)
        paper = repository.load()[0]
        repository.save(paper.evolve(notes="Edited in the new model."))

        migrate_legacy_portfolio(settings)

        assert repository.load()[0].notes == "Edited in the new model."

    def test_an_existing_portfolio_is_backed_up_before_it_is_touched(
        self, settings: Settings
    ) -> None:
        write_legacy(settings.paths.legacy)
        migrate_legacy_portfolio(settings)
        write_legacy(
            settings.paths.legacy,
            LEGACY_ARTICLE,
            {**LEGACY_ARTICLE, "title": "Another Paper", "repo": "owner/other"},
        )

        report = migrate_legacy_portfolio(settings)

        assert report.created == 1
        assert report.backup_path is not None
        assert report.backup_path.exists()

    def test_a_missing_legacy_file_is_reported_not_raised(self, settings: Settings) -> None:
        report = migrate_legacy_portfolio(settings)
        assert report.created == 0
        assert "No legacy articles found" in report.warnings[0]

    def test_the_migration_is_recorded_in_the_event_log(self, settings: Settings) -> None:
        write_legacy(settings.paths.legacy)
        migrate_legacy_portfolio(settings)

        events = settings.paths.events.read_text(encoding="utf-8").strip().splitlines()
        assert len(events) == 1
        assert json.loads(events[0])["event_type"] == "migrated_from_legacy"

    def test_a_paper_with_no_repository_is_flagged(self, settings: Settings) -> None:
        write_legacy(settings.paths.legacy, {**LEGACY_ARTICLE, "repo": ""})
        report = migrate_legacy_portfolio(settings)
        assert "activity cannot be detected" in report.warnings[0]


class TestLegacyExport:
    def test_the_portfolio_can_be_written_back_out(
        self, settings: Settings, portfolio: PortfolioService
    ) -> None:
        portfolio.create(
            "A Paper",
            status=S.ANALYSIS,
            repository="owner/name",
            paper_path="paper/",
            next_action="Rebuild the tables",
            next_action_due_at="2026-09-30",
        )

        document, _ = export_legacy_tracker(settings, portfolio.list_papers())
        record = json.loads(document)["articles"][0]

        assert record["status"] == "in_progress"
        assert record["next_action"] == "Rebuild the tables"
        assert record["target_date"] == "2026-09-30"
        assert settings.paths.legacy.read_text(encoding="utf-8") == document

    def test_the_previous_tracker_is_backed_up(
        self, settings: Settings, portfolio: PortfolioService
    ) -> None:
        write_legacy(settings.paths.legacy)
        portfolio.create("A Paper")

        _, backup = export_legacy_tracker(settings, portfolio.list_papers())

        assert backup is not None
        assert json.loads(backup.read_text(encoding="utf-8"))["articles"][0]["title"] == (
            LEGACY_ARTICLE["title"]
        )

    def test_a_dry_run_only_renders(
        self, settings: Settings, portfolio: PortfolioService
    ) -> None:
        portfolio.create("A Paper")
        document, backup = export_legacy_tracker(settings, portfolio.list_papers(), dry_run=True)

        assert backup is None
        assert not settings.paths.legacy.exists()
        assert "A Paper" in document


class TestLegacyFallback:
    def test_the_application_reads_the_legacy_file_before_any_migration(
        self, settings: Settings
    ) -> None:
        write_legacy(settings.paths.legacy)
        repository = JsonPaperRepository(
            settings.paths.portfolio, legacy_path=settings.paths.legacy
        )

        assert repository.uses_legacy_fallback
        assert [paper.title for paper in repository.load()] == [LEGACY_ARTICLE["title"]]

    def test_the_first_write_lands_in_the_portfolio_file(self, settings: Settings) -> None:
        write_legacy(settings.paths.legacy)
        repository = JsonPaperRepository(
            settings.paths.portfolio, legacy_path=settings.paths.legacy
        )
        paper = repository.load()[0]
        legacy_before = settings.paths.legacy.read_text(encoding="utf-8")

        repository.save(paper.evolve(notes="edited"))

        assert not repository.uses_legacy_fallback
        assert settings.paths.legacy.read_text(encoding="utf-8") == legacy_before
        assert repository.load()[0].notes == "edited"


def test_a_backup_of_a_missing_file_is_a_no_op(tmp_path: Path) -> None:
    assert backup_file(tmp_path / "nothing.json", tmp_path / "backups") is None
