"""Structured failures for local file boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dataexcept import DataLoadingError, FileReadError, FileWriteError, wrapping


def read_utf8(path: Path) -> str:
    """Read a text file and preserve the underlying I/O or decode failure."""

    with (
        wrapping(OSError, FileReadError, path=str(path)),
        wrapping(UnicodeError, DataLoadingError, source=str(path)),
    ):
        return path.read_text(encoding="utf-8")


@contextmanager
def writing(path: Path) -> Iterator[None]:
    """Classify filesystem failures against the requested destination."""

    with wrapping(OSError, FileWriteError, path=str(path)):
        yield
