"""Identifiers.

A paper's id is stable and opaque; its slug is derived from the title and is what
a human types on the command line.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import NewType

PaperId = NewType("PaperId", str)
Slug = NewType("Slug", str)

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

MAX_SLUG_LENGTH = 80


def new_paper_id() -> PaperId:
    """Return a fresh opaque identifier.

    Random rather than derived from the title: a paper is routinely renamed
    between the idea and the accepted version, and its id must survive that.
    """
    return PaperId(uuid.uuid4().hex[:12])


def slugify(value: str, *, max_length: int = MAX_SLUG_LENGTH) -> Slug:
    """Turn a title into a lowercase hyphenated slug.

    Accents are folded rather than dropped, so a Portuguese title still produces
    a slug someone can guess.
    """
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", ascii_only.lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return Slug(slug)


def is_valid_slug(value: str) -> bool:
    """Whether ``value`` is already a well-formed slug."""
    return bool(_SLUG_RE.match(value))
