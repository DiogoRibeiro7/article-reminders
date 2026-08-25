"""The legacy ``data/articles.json`` format, in both directions.

The scheduled GitHub Actions workflows still read this file, and a researcher who
never runs the migration should still see their portfolio. So the legacy format
is a first-class citizen: it can be read into the domain model, and the domain
model can be written back out to it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from article_reminders.domain.enums import LifecycleStatus, Priority
from article_reminders.domain.errors import ValidationError
from article_reminders.domain.ids import PaperId, slugify
from article_reminders.domain.models import NextAction, Paper, RepositoryRef, coerce_priority
from article_reminders.domain.timeutils import as_date, now, parse_optional_datetime

S = LifecycleStatus

#: Keys the legacy validator accepts. Anything else is rejected by validate.yml,
#: so the export must never emit a key outside this set.
LEGACY_KEYS: tuple[str, ...] = (
    "title",
    "repo",
    "status",
    "notes",
    "abstract",
    "paper_path",
    "priority",
    "last_updated",
    "venue",
    "target_date",
    "next_action",
)

#: Legacy status -> lifecycle status. Nine legacy values, sixteen new ones, so the
#: map is into rather than onto; :data:`LIFECYCLE_TO_LEGACY` is its left inverse.
LEGACY_STATUS_TO_LIFECYCLE: Mapping[str, LifecycleStatus] = {
    "planned": S.PLANNED,
    "in_progress": S.RESEARCH,
    "draft": S.DRAFT,
    "submitted": S.SUBMITTED,
    "revising": S.REVISION,
    "finished": S.ACCEPTED,
    "published": S.PUBLISHED,
    "archived": S.PAUSED,
    "cancelled": S.ABANDONED,
}

#: Lifecycle status -> legacy status. Chosen so that every legacy value survives a
#: round trip: planned -> planned, in_progress -> research -> in_progress, and so on.
LIFECYCLE_TO_LEGACY: Mapping[LifecycleStatus, str] = {
    S.IDEA: "planned",
    S.PLANNED: "planned",
    S.RESEARCH: "in_progress",
    S.DATA_COLLECTION: "in_progress",
    S.ANALYSIS: "in_progress",
    S.DRAFT: "draft",
    S.INTERNAL_REVIEW: "draft",
    S.READY_TO_SUBMIT: "draft",
    S.SUBMITTED: "submitted",
    S.UNDER_REVIEW: "submitted",
    S.RESUBMITTED: "submitted",
    S.REVISION: "revising",
    S.ACCEPTED: "finished",
    S.PUBLISHED: "published",
    S.PAUSED: "archived",
    S.ABANDONED: "cancelled",
}


def legacy_paper_id(title: str, repo: str, paper_path: str) -> PaperId:
    """A stable id derived from a legacy entry's identity.

    Derived rather than random so that loading the legacy file twice, or migrating
    it after having browsed it, yields the same ids and the same URLs.
    """
    digest = hashlib.sha1(
        f"{title.strip()}::{repo.strip()}::{paper_path.strip()}".encode(),
        usedforsecurity=False,
    ).hexdigest()
    return PaperId(digest[:12])


def read_legacy_file(path: Path) -> list[dict[str, Any]]:
    """Read ``data/articles.json`` and return its raw records."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, Mapping) or "articles" not in raw:
        raise ValidationError(f"{path} must be an object with an 'articles' key.")
    articles = raw["articles"]
    if not isinstance(articles, Sequence) or isinstance(articles, (str, bytes)):
        raise ValidationError(f"{path}: 'articles' must be a list.")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(articles):
        if not isinstance(item, Mapping):
            raise ValidationError(f"{path}: articles[{index}] must be an object.")
        records.append(dict(item))
    return records


def paper_from_legacy(record: Mapping[str, Any], *, observed_at: datetime | None = None) -> Paper:
    """Build a :class:`Paper` from one legacy article record.

    Nothing is dropped: keys this version does not model are kept in ``extra`` and
    written back out unchanged.
    """
    title = str(record.get("title", "")).strip()
    if not title:
        raise ValidationError("A legacy article needs a title.")
    repo = str(record.get("repo", "")).strip()
    paper_path = str(record.get("paper_path", "")).strip()

    legacy_status = str(record.get("status", "")).strip().lower()
    status = LEGACY_STATUS_TO_LIFECYCLE.get(legacy_status)
    if status is None:
        raise ValidationError(
            f"{title!r} has the unknown legacy status {legacy_status!r}. Valid: "
            f"{', '.join(sorted(LEGACY_STATUS_TO_LIFECYCLE))}."
        )

    last_updated = parse_optional_datetime(record.get("last_updated"), field="last_updated")
    target_date = parse_optional_datetime(record.get("target_date"), field="target_date")
    next_action_text = str(record.get("next_action", "")).strip()
    next_action = (
        NextAction(description=next_action_text, due_at=target_date, created_at=last_updated)
        if next_action_text
        else None
    )

    venue = str(record.get("venue", "")).strip()
    priority: Priority = coerce_priority(record.get("priority"))

    extra: dict[str, Any] = {
        key: value for key, value in record.items() if key not in LEGACY_KEYS
    }
    if target_date is not None and next_action is None:
        # No next action to hang it on, and inventing a conference deadline would be
        # a guess. Keep it verbatim so the legacy export can put it back.
        extra["target_date"] = str(record.get("target_date"))

    created = last_updated or observed_at or now()
    return Paper(
        id=legacy_paper_id(title, repo, paper_path),
        title=title,
        slug=slugify(title),
        status=status,
        priority=priority,
        notes=str(record.get("notes", "")).strip(),
        abstract=str(record.get("abstract", "")).strip(),
        repository=RepositoryRef(slug=repo, paper_path=paper_path or None) if repo else None,
        target_journal=venue or None,
        created_at=created,
        updated_at=last_updated or created,
        next_action=next_action,
        extra=extra,
    )


def legacy_from_paper(paper: Paper) -> dict[str, Any]:
    """Render a paper back into a legacy article record.

    Only keys in :data:`LEGACY_KEYS` are emitted, because ``validate.yml`` rejects
    anything else and the export has to survive that gate.
    """
    due = paper.next_action_due_at
    day = as_date(due)
    target_date = day.isoformat() if day is not None else str(paper.extra.get("target_date", ""))
    updated = as_date(paper.updated_at)

    record: dict[str, Any] = {
        "title": paper.title,
        "repo": paper.repository_slug or "",
        "status": LIFECYCLE_TO_LEGACY[paper.status],
        "notes": paper.notes or paper.description,
        "paper_path": paper.paper_path or "",
        "priority": paper.priority.value,
        "last_updated": updated.isoformat() if updated else "",
    }
    if paper.abstract:
        record["abstract"] = paper.abstract
    venue = paper.venue
    if venue:
        record["venue"] = venue
    if target_date:
        record["target_date"] = target_date
    if paper.next_action is not None:
        record["next_action"] = paper.next_action.description
    return record


def render_legacy_document(papers: Iterable[Paper]) -> str:
    """The full ``data/articles.json`` text for a portfolio.

    Two-space indent and a trailing newline, matching the file the repository has
    always had, so an export produces a readable diff rather than a rewrite.
    """
    records = [legacy_from_paper(paper) for paper in papers]
    return json.dumps({"articles": records}, indent=2, ensure_ascii=False) + "\n"
