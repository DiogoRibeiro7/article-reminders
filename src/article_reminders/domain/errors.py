"""Domain exceptions.

Every failure mode the domain can produce is one of these, so callers never have
to distinguish a rule violation from an accidental ``KeyError``.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every error raised by the domain layer."""


class ValidationError(DomainError):
    """A value was rejected at a boundary or by a model invariant."""


class InvalidTransitionError(DomainError):
    """A lifecycle transition is not part of the canonical workflow.

    Raised only when the caller did not ask for an explicit override: researchers
    are allowed to move a paper anywhere, but they have to say so.
    """

    def __init__(self, source: str, target: str) -> None:
        super().__init__(
            f"{source} -> {target} is not a canonical transition. "
            f"Pass force=True to override it explicitly."
        )
        self.source = source
        self.target = target


class PaperNotFoundError(DomainError):
    """No paper matches the given id, slug, or title fragment."""

    def __init__(self, reference: str) -> None:
        super().__init__(f"No paper matches {reference!r}.")
        self.reference = reference


class AmbiguousPaperError(DomainError):
    """A lookup string matched more than one paper."""

    def __init__(self, reference: str, matches: list[str]) -> None:
        super().__init__(
            f"{reference!r} matches {len(matches)} papers: {', '.join(matches[:5])}"
            + (" ..." if len(matches) > 5 else "")
        )
        self.reference = reference
        self.matches = matches
