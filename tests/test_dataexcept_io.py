"""File failures retain their destination/source and the original cause."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from dataexcept import DataLoadingError, FileReadError, FileWriteError

from article_reminders.cli.main import EXIT_ERROR, main
from article_reminders.domain.enums import ProjectEventType
from article_reminders.domain.events import ProjectEvent
from article_reminders.domain.ids import PaperId
from article_reminders.infrastructure.configuration.settings import load_settings
from article_reminders.infrastructure.storage.event_log import JsonlEventLog
from article_reminders.infrastructure.storage.json_store import JsonPaperRepository, atomic_write
from article_reminders.infrastructure.storage.legacy import read_legacy_file
from article_reminders.infrastructure.storage.migration import backup_file


@pytest.mark.parametrize("source", ["portfolio", "legacy", "events", "settings"])
def test_read_failure_identifies_source_and_keeps_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    path = tmp_path / "existing.json"
    path.write_text("{}", encoding="utf-8")
    original = PermissionError("read denied")

    def fail_read(self: Path, *, encoding: str) -> str:
        raise original

    monkeypatch.setattr(Path, "read_text", fail_read)
    with pytest.raises(FileReadError) as caught:
        if source == "portfolio":
            JsonPaperRepository(path).load()
        elif source == "legacy":
            read_legacy_file(path)
        elif source == "events":
            JsonlEventLog(path).all()
        else:
            load_settings(path=path, root=tmp_path)

    assert caught.value.path == str(path)
    assert caught.value.original is original
    assert caught.value.__cause__ is original


def test_invalid_utf8_is_a_data_loading_failure(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.json"
    path.write_bytes(b"\xff")

    with pytest.raises(DataLoadingError) as caught:
        JsonPaperRepository(path).load()

    assert caught.value.source == str(path)
    assert isinstance(caught.value.original, UnicodeDecodeError)
    assert caught.value.__cause__ is caught.value.original


def test_failed_atomic_replace_preserves_portfolio_and_removes_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "portfolio.json"
    path.write_text("original\n", encoding="utf-8")
    original = PermissionError("replace denied")

    def fail_replace(self: Path, target: Path) -> Path:
        raise original

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(FileWriteError) as caught:
        atomic_write(path, "replacement\n")

    assert path.read_text(encoding="utf-8") == "original\n"
    assert not list(tmp_path.glob(".portfolio.json.*.tmp"))
    assert caught.value.path == str(path)
    assert caught.value.original is original
    assert caught.value.__cause__ is original


def test_event_append_and_backup_report_write_destination(tmp_path: Path) -> None:
    parent = tmp_path / "blocked"
    parent.write_text("not a directory", encoding="utf-8")
    event_path = parent / "events.jsonl"
    event = ProjectEvent(
        project_id=PaperId("paper0000001"),
        event_type=ProjectEventType.MIGRATED_FROM_LEGACY,
        occurred_at=datetime(2026, 9, 26, tzinfo=UTC),
    )

    with pytest.raises(FileWriteError) as append_error:
        JsonlEventLog(event_path).append(event)
    assert append_error.value.path == str(event_path)
    assert isinstance(append_error.value.__cause__, OSError)

    source = tmp_path / "portfolio.json"
    source.write_text("{}", encoding="utf-8")
    with pytest.raises(FileWriteError) as backup_error:
        backup_file(source, parent, stamp=datetime(2026, 9, 26, tzinfo=UTC))
    assert backup_error.value.path == str(parent / "portfolio.20260926T000000Z.json")
    assert isinstance(backup_error.value.__cause__, OSError)


def test_cli_reports_dataexcept_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "portfolio.json").write_bytes(b"\xff")

    assert main(["--root", str(tmp_path), "list"]) == EXIT_ERROR
    assert "portfolio.json" in capsys.readouterr().err
