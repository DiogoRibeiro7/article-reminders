from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_articles.py"

spec = importlib.util.spec_from_file_location("validate_articles", MODULE_PATH)
assert spec and spec.loader
validate_articles = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validate_articles
spec.loader.exec_module(validate_articles)


def write(tmp_path: Path, articles) -> Path:
    path = tmp_path / "articles.json"
    path.write_text(json.dumps({"articles": articles}), encoding="utf-8")
    return path


def good(**overrides):
    entry = {
        "title": "A Paper",
        "repo": "owner/name",
        "status": "draft",
        "notes": "",
        "paper_path": "paper/",
        "priority": "high",
        "last_updated": "2026-08-19",
    }
    entry.update(overrides)
    return entry


def test_the_real_data_file_passes() -> None:
    assert validate_articles.validate(ROOT / "data" / "articles.json") == []


def test_accepts_a_well_formed_entry(tmp_path: Path) -> None:
    assert validate_articles.validate(write(tmp_path, [good()])) == []


def test_accepts_the_optional_planning_fields(tmp_path: Path) -> None:
    entry = good(venue="JOSS", target_date="2026-12-01", next_action="Rebuild tables")
    assert validate_articles.validate(write(tmp_path, [entry])) == []


def test_rejects_unknown_keys(tmp_path: Path) -> None:
    errors = validate_articles.validate(write(tmp_path, [good(slug="a-slug", year="2026")]))
    assert any("unknown key 'slug'" in e for e in errors)
    assert any("unknown key 'year'" in e for e in errors)


def test_rejects_unknown_status(tmp_path: Path) -> None:
    errors = validate_articles.validate(write(tmp_path, [good(status="experiments running")]))
    assert any("is not recognised" in e and "never get a reminder" in e for e in errors)


def test_rejects_unknown_priority(tmp_path: Path) -> None:
    errors = validate_articles.validate(write(tmp_path, [good(priority="urgent")]))
    assert any("priority 'urgent' is not recognised" in e for e in errors)


def test_rejects_missing_required_key(tmp_path: Path) -> None:
    entry = good()
    del entry["repo"]
    errors = validate_articles.validate(write(tmp_path, [entry]))
    assert any("missing required key 'repo'" in e for e in errors)


def test_rejects_malformed_repo_and_dates(tmp_path: Path) -> None:
    entry = good(repo="not-a-slug", last_updated="19-08-2026")
    errors = validate_articles.validate(write(tmp_path, [entry]))
    assert any("should be owner/name" in e for e in errors)
    assert any("must be YYYY-MM-DD" in e for e in errors)


def test_rejects_non_string_values(tmp_path: Path) -> None:
    errors = validate_articles.validate(write(tmp_path, [good(priority=3)]))
    assert any("must be a string" in e for e in errors)


def test_rejects_duplicate_titles(tmp_path: Path) -> None:
    errors = validate_articles.validate(write(tmp_path, [good(), good(repo="owner/other")]))
    assert any("duplicate title" in e for e in errors)


def test_rejects_duplicate_repo_and_path(tmp_path: Path) -> None:
    errors = validate_articles.validate(write(tmp_path, [good(), good(title="Another Paper")]))
    assert any("duplicate repo + paper_path" in e for e in errors)


def test_reports_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "articles.json"
    path.write_text("{ not json", encoding="utf-8")
    assert any("not valid JSON" in e for e in validate_articles.validate(path))


def test_reports_missing_file(tmp_path: Path) -> None:
    assert any("Missing data file" in e for e in validate_articles.validate(tmp_path / "nope.json"))


def test_reports_wrong_top_level_shape(tmp_path: Path) -> None:
    path = tmp_path / "articles.json"
    path.write_text(json.dumps([good()]), encoding="utf-8")
    assert any("must be an object with an 'articles' key" in e for e in validate_articles.validate(path))


def test_main_exits_nonzero_on_invalid_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validate_articles, "DATA_PATH", write(tmp_path, [good(status="bogus")]))
    assert validate_articles.main() == 1


def test_main_exits_zero_on_valid_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validate_articles, "DATA_PATH", write(tmp_path, [good()]))
    assert validate_articles.main() == 0
