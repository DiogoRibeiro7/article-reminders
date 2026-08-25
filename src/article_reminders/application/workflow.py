"""Portfolio views: the dashboard, the board, and the calendar.

These are read models. They answer the questions a researcher opens the
application with — what needs me today, what is stuck, what is due — by grouping
papers and reminders that other modules produced.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from article_reminders.application.reminders import ReminderEngine, group_by_paper
from article_reminders.domain.enums import (
    BoardColumn,
    LifecycleStatus,
    ReminderKind,
    ReminderSeverity,
)
from article_reminders.domain.models import Paper, Reminder
from article_reminders.domain.rules import evaluate_staleness, needs_attention_reasons
from article_reminders.domain.timeutils import as_date, days_between
from article_reminders.infrastructure.configuration.settings import Settings

S = LifecycleStatus


@dataclass(frozen=True, slots=True)
class PaperCard:
    """One paper as the views need it: the record plus why it is being shown."""

    paper: Paper
    reminders: tuple[Reminder, ...] = ()
    attention_reasons: tuple[str, ...] = ()
    days_since_activity: float | None = None
    is_stale: bool = False

    @property
    def severity(self) -> ReminderSeverity | None:
        """The loudest reminder on this paper, if any."""
        if not self.reminders:
            return None
        return max((item.severity for item in self.reminders), key=lambda value: value.rank)

    @property
    def needs_attention(self) -> bool:
        return bool(self.attention_reasons)

    @property
    def next_action_text(self) -> str:
        action = self.paper.next_action
        return action.description if action is not None else ""


@dataclass(frozen=True, slots=True)
class Bucket:
    """A named group of papers on the dashboard."""

    key: str
    label: str
    question: str
    cards: tuple[PaperCard, ...]

    @property
    def count(self) -> int:
        return len(self.cards)


@dataclass(frozen=True, slots=True)
class Dashboard:
    """Everything the portfolio page shows."""

    generated_at: datetime
    cards: tuple[PaperCard, ...]
    buckets: tuple[Bucket, ...]
    reminders: tuple[Reminder, ...]

    @property
    def by_key(self) -> Mapping[str, Bucket]:
        return {bucket.key: bucket for bucket in self.buckets}

    def bucket(self, key: str) -> Bucket:
        return self.by_key[key]

    @property
    def total(self) -> int:
        return len(self.cards)

    def focus(self, limit: int = 5) -> tuple[PaperCard, ...]:
        """The papers to work on next.

        Ordered by the loudest reminder, then by the nearest deadline, then by
        priority. This is the answer to "what should I work on next?".
        """
        ranked = sorted(self.cards, key=_focus_key)
        return tuple(card for card in ranked if card.paper.is_active)[:limit]

    def counts(self) -> dict[str, int]:
        return {bucket.key: bucket.count for bucket in self.buckets}


def build_cards(
    papers: Iterable[Paper],
    reminders: Iterable[Reminder],
    reference: datetime,
    settings: Settings,
) -> tuple[PaperCard, ...]:
    """Decorate papers with their reminders and staleness."""
    grouped = group_by_paper(reminders)
    cards: list[PaperCard] = []
    for paper in papers:
        verdict = evaluate_staleness(paper, reference, settings.staleness)
        cards.append(
            PaperCard(
                paper=paper,
                reminders=tuple(grouped.get(str(paper.id), ())),
                attention_reasons=needs_attention_reasons(paper, reference, settings.staleness),
                days_since_activity=verdict.days_since_activity,
                is_stale=verdict.is_stale,
            )
        )
    return tuple(cards)


def build_dashboard(
    papers: Sequence[Paper],
    settings: Settings,
    reference: datetime,
    *,
    engine: ReminderEngine | None = None,
) -> Dashboard:
    """Assemble the portfolio dashboard."""
    reminder_engine = engine or ReminderEngine(settings)
    reminders = reminder_engine.generate(papers, reference)
    cards = build_cards(papers, reminders, reference, settings)

    window = settings.reminders.upcoming_deadline_days
    horizon = reference + timedelta(days=window)

    def has_deadline_within(card: PaperCard) -> bool:
        return any(when <= horizon for _, _, when in card.paper.deadlines())

    def in_status(card: PaperCard, *statuses: LifecycleStatus) -> bool:
        return card.paper.status in statuses

    buckets = (
        Bucket(
            "active",
            "Active",
            "What is currently in flight?",
            tuple(card for card in cards if card.paper.is_active),
        ),
        Bucket(
            "needs_attention",
            "Needs attention",
            "Which paper needs me, and why?",
            tuple(card for card in cards if card.needs_attention),
        ),
        Bucket(
            "stalled",
            "Stalled",
            "Which papers have gone quiet for longer than their stage allows?",
            tuple(card for card in cards if card.is_stale and card.paper.is_active),
        ),
        Bucket(
            "waiting",
            "Waiting",
            "Which papers are waiting for somebody else?",
            tuple(card for card in cards if card.paper.is_waiting and card.paper.is_active),
        ),
        Bucket(
            "upcoming_deadlines",
            "Upcoming deadlines",
            f"What is due in the next {window} days?",
            tuple(card for card in cards if card.paper.is_active and has_deadline_within(card)),
        ),
        Bucket(
            "no_next_action",
            "No next action",
            "Which active papers have nothing concrete queued up?",
            tuple(
                card
                for card in cards
                if any(
                    reminder.kind is ReminderKind.MISSING_NEXT_ACTION
                    for reminder in card.reminders
                )
            ),
        ),
        Bucket(
            "manuscript_stalled",
            "Analysis active, manuscript stalled",
            "Which papers are moving in code but not in prose?",
            tuple(
                card
                for card in cards
                if any(
                    reminder.kind
                    in (ReminderKind.MANUSCRIPT_STAGNATION, ReminderKind.ANALYSIS_WITHOUT_DRAFT)
                    for reminder in card.reminders
                )
            ),
        ),
        Bucket(
            "ready_to_submit",
            "Ready to submit",
            "Which papers are almost out the door?",
            tuple(card for card in cards if in_status(card, S.READY_TO_SUBMIT)),
        ),
        Bucket(
            "submitted",
            "Submitted",
            "What has gone out?",
            tuple(card for card in cards if in_status(card, S.SUBMITTED, S.RESUBMITTED)),
        ),
        Bucket(
            "under_review",
            "Under review",
            "What is sitting with a referee?",
            tuple(card for card in cards if in_status(card, S.UNDER_REVIEW)),
        ),
        Bucket(
            "revision",
            "Revision required",
            "What is waiting on my response to reviewers?",
            tuple(card for card in cards if in_status(card, S.REVISION)),
        ),
        Bucket(
            "accepted",
            "Accepted",
            "What has been accepted but not yet published?",
            tuple(card for card in cards if in_status(card, S.ACCEPTED)),
        ),
        Bucket(
            "published",
            "Published",
            "What is out in the world?",
            tuple(card for card in cards if in_status(card, S.PUBLISHED)),
        ),
        Bucket(
            "paused",
            "Paused",
            "What has been shelved?",
            tuple(card for card in cards if in_status(card, S.PAUSED, S.ABANDONED)),
        ),
    )

    return Dashboard(
        generated_at=reference,
        cards=cards,
        buckets=buckets,
        reminders=tuple(reminders),
    )


@dataclass(frozen=True, slots=True)
class BoardLane:
    """One Kanban column."""

    column: BoardColumn
    cards: tuple[PaperCard, ...]

    @property
    def label(self) -> str:
        return self.column.label

    @property
    def count(self) -> int:
        return len(self.cards)


def build_board(cards: Iterable[PaperCard]) -> tuple[BoardLane, ...]:
    """Group cards into the Kanban lanes, in lifecycle order."""
    lanes: dict[BoardColumn, list[PaperCard]] = {column: [] for column in BoardColumn}
    for card in cards:
        lanes[card.paper.board_column].append(card)
    for group in lanes.values():
        group.sort(key=_focus_key)
    return tuple(BoardLane(column, tuple(group)) for column, group in lanes.items())


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """One dated commitment."""

    on: date
    kind: str
    label: str
    paper: Paper

    @property
    def paper_id(self) -> str:
        return str(self.paper.id)


@dataclass(frozen=True, slots=True)
class CalendarMonth:
    """A month of entries, for rendering a simple grid."""

    year: int
    month: int
    entries: tuple[CalendarEntry, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%B %Y")

    def by_day(self) -> dict[int, tuple[CalendarEntry, ...]]:
        buckets: dict[int, list[CalendarEntry]] = {}
        for entry in self.entries:
            buckets.setdefault(entry.on.day, []).append(entry)
        return {day: tuple(values) for day, values in sorted(buckets.items())}


def build_calendar(
    papers: Iterable[Paper],
    *,
    include_inactive: bool = False,
) -> tuple[CalendarEntry, ...]:
    """Every dated commitment in the portfolio, earliest first."""
    entries: list[CalendarEntry] = []
    for paper in papers:
        if not include_inactive and not paper.is_active:
            continue
        for kind, label, when in paper.deadlines():
            day = as_date(when)
            if day is None:
                continue
            entries.append(CalendarEntry(on=day, kind=kind, label=label, paper=paper))
    entries.sort(key=lambda entry: (entry.on, entry.paper.title.lower()))
    return tuple(entries)


def group_calendar_by_month(
    entries: Sequence[CalendarEntry],
) -> tuple[CalendarMonth, ...]:
    """Split calendar entries into months, chronologically."""
    buckets: dict[tuple[int, int], list[CalendarEntry]] = {}
    for entry in entries:
        buckets.setdefault((entry.on.year, entry.on.month), []).append(entry)
    return tuple(
        CalendarMonth(year=year, month=month, entries=tuple(items))
        for (year, month), items in sorted(buckets.items())
    )


def _focus_key(card: PaperCard) -> tuple[int, float, int, str]:
    severity = card.severity
    due = card.paper.next_action_due_at or card.paper.revision_due_at
    return (
        -(severity.rank + 1 if severity is not None else 0),
        due.timestamp() if due is not None else float("inf"),
        -card.paper.priority.rank,
        card.paper.title.lower(),
    )


def days_idle(paper: Paper, reference: datetime) -> float | None:
    """Days since anything at all happened to a paper."""
    last = paper.last_activity_at()
    return None if last is None else days_between(last, reference)
