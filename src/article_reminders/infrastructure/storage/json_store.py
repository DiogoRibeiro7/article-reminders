"""JSON-file storage for the portfolio.

One file, one array of papers, sorted by title. It is the authoritative record and
it is meant to be read with an editor: if this application disappears tomorrow the
researcher still has their portfolio in a format they can grep.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from article_reminders.domain.errors import (
    AmbiguousPaperError,
    PaperNotFoundError,
    ValidationError,
)
from article_reminders.domain.ids import PaperId
from article_reminders.domain.models import Paper
from article_reminders.domain.timeutils import format_datetime, now
from article_reminders.infrastructure.storage.legacy import paper_from_legacy, read_legacy_file

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` without ever leaving a half-written file.

    The portfolio is the source of truth; a crash mid-write must not be able to
    truncate it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class JsonPaperRepository:
    """Papers stored as a single JSON document.

    When the portfolio file does not exist but a legacy ``data/articles.json``
    does, the legacy file is read instead, so the application is useful before
    anybody runs the migration. Writes always go to the portfolio file; the legacy
    file is only ever written by an explicit export.
    """

    def __init__(self, path: Path, *, legacy_path: Path | None = None) -> None:
        self.path = path
        self.legacy_path = legacy_path

    # -- reading ----------------------------------------------------------

    @property
    def uses_legacy_fallback(self) -> bool:
        """Whether reads are currently coming from the legacy tracker."""
        return (
            not self.path.exists()
            and self.legacy_path is not None
            and self.legacy_path.exists()
        )

    def load(self) -> list[Paper]:
        """Every paper, in stored order."""
        if self.uses_legacy_fallback:
            assert self.legacy_path is not None
            logger.debug("reading legacy tracker at %s", self.legacy_path)
            return [paper_from_legacy(record) for record in read_legacy_file(self.legacy_path)]

        if not self.path.exists():
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{self.path} is not valid JSON: {exc}") from exc

        records = _records_from(raw, self.path)
        papers: list[Paper] = []
        for index, record in enumerate(records):
            try:
                papers.append(Paper.from_dict(record))
            except ValidationError as exc:
                raise ValidationError(f"{self.path}: papers[{index}] is invalid: {exc}") from exc
        return papers

    def all(self) -> tuple[Paper, ...]:
        return tuple(self.load())

    def get(self, paper_id: PaperId) -> Paper:
        for paper in self.load():
            if paper.id == paper_id:
                return paper
        raise PaperNotFoundError(str(paper_id))

    def find(self, reference: str) -> Paper:
        """Look a paper up by id, slug, exact title, or unique title fragment."""
        needle = reference.strip()
        if not needle:
            raise PaperNotFoundError(reference)
        papers = self.load()
        lowered = needle.lower()

        for paper in papers:
            if paper.id == needle or str(paper.slug) == lowered:
                return paper
        exact = [paper for paper in papers if paper.title.lower() == lowered]
        if len(exact) == 1:
            return exact[0]

        partial = [
            paper
            for paper in papers
            if lowered in paper.title.lower() or lowered in str(paper.slug)
        ]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise AmbiguousPaperError(reference, [paper.title for paper in partial])
        raise PaperNotFoundError(reference)

    # -- writing ----------------------------------------------------------

    def save(self, paper: Paper) -> Paper:
        """Insert or replace one paper."""
        papers = self.load()
        for index, existing in enumerate(papers):
            if existing.id == paper.id:
                papers[index] = paper
                break
        else:
            papers.append(paper)
        self.save_all(papers)
        return paper

    def save_all(self, papers: Iterable[Paper], *, generated_at: datetime | None = None) -> None:
        """Replace the whole portfolio."""
        ordered = sorted(papers, key=lambda item: item.title.lower())
        document = {
            "version": SCHEMA_VERSION,
            "generated_at": format_datetime(generated_at or now()),
            "papers": [paper.to_dict() for paper in ordered],
        }
        atomic_write(self.path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        logger.info("wrote %d papers to %s", len(ordered), self.path)

    def delete(self, paper_id: PaperId) -> Paper:
        """Remove a paper and return it."""
        papers = self.load()
        remaining = [paper for paper in papers if paper.id != paper_id]
        if len(remaining) == len(papers):
            raise PaperNotFoundError(str(paper_id))
        removed = next(paper for paper in papers if paper.id == paper_id)
        self.save_all(remaining)
        return removed


def _records_from(raw: object, path: Path) -> list[Mapping[str, Any]]:
    """Accept the portfolio document, a bare list, or a legacy-shaped file."""
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        items: Sequence[Any] = raw
    elif isinstance(raw, Mapping):
        for key in ("papers", "articles"):
            if key in raw:
                candidate = raw[key]
                if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
                    raise ValidationError(f"{path}: '{key}' must be a list.")
                items = candidate
                break
        else:
            raise ValidationError(f"{path} must contain a 'papers' list.")
    else:
        raise ValidationError(f"{path} must contain a 'papers' list.")

    records: list[Mapping[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ValidationError(f"{path}: papers[{index}] must be an object.")
        records.append(item)
    return records
