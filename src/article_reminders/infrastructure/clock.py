"""Clocks.

Injected everywhere rather than called directly, so that "is this paper stale?"
is a pure question with a reproducible answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from article_reminders.domain.timeutils import ensure_aware
from article_reminders.domain.timeutils import now as utc_now


class SystemClock:
    """The real clock, in UTC."""

    def now(self) -> datetime:
        return utc_now()


@dataclass(frozen=True, slots=True)
class FixedClock:
    """A clock stopped at one instant, for tests and reproducible reports."""

    instant: datetime

    def now(self) -> datetime:
        return ensure_aware(self.instant)
