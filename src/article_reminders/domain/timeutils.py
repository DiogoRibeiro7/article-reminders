"""Timezone-aware time handling.

Every datetime that crosses a boundary is UTC-aware. Naive datetimes are a
recurring source of off-by-a-day deadline bugs, so they are rejected on the way
in rather than coerced silently.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from article_reminders.domain.errors import ValidationError

__all__ = [
    "SECONDS_PER_DAY",
    "as_date",
    "days_between",
    "ensure_aware",
    "format_datetime",
    "now",
    "parse_datetime",
    "parse_optional_datetime",
]

SECONDS_PER_DAY = 86_400


def now() -> datetime:
    """The current instant, UTC-aware."""
    return datetime.now(UTC)


def ensure_aware(value: datetime, *, field: str = "datetime") -> datetime:
    """Return ``value`` in UTC, rejecting naive datetimes."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValidationError(
            f"{field} must be timezone-aware; got the naive value {value.isoformat()}."
        )
    return value.astimezone(UTC)


def parse_datetime(value: str | datetime | date, *, field: str = "datetime") -> datetime:
    """Parse a date or datetime into a UTC-aware datetime.

    ``YYYY-MM-DD`` is accepted and anchored at midnight UTC, because that is the
    shape every legacy record and every hand-edited deadline uses.
    """
    if isinstance(value, datetime):
        return ensure_aware(value, field=field)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)

    text = value.strip()
    if not text:
        raise ValidationError(f"{field} must not be empty.")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        try:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError:
            raise ValidationError(
                f"{field} must be an ISO-8601 date or datetime; got {value!r}."
            ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_optional_datetime(
    value: str | datetime | date | None, *, field: str = "datetime"
) -> datetime | None:
    """Like :func:`parse_datetime`, but ``None`` and the empty string pass through."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return parse_datetime(value, field=field)


def format_datetime(value: datetime | None) -> str | None:
    """Serialise a datetime for storage: ISO-8601 in UTC, or ``None``."""
    if value is None:
        return None
    return ensure_aware(value).isoformat()


def as_date(value: datetime | None) -> date | None:
    """The UTC calendar date of a datetime."""
    return None if value is None else ensure_aware(value).date()


def days_between(earlier: datetime, later: datetime) -> float:
    """Fractional days from ``earlier`` to ``later``; negative if reversed."""
    delta: timedelta = ensure_aware(later) - ensure_aware(earlier)
    return delta.total_seconds() / SECONDS_PER_DAY
