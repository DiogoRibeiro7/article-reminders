"""article-reminders: an operational lifecycle tracker for research papers.

The package is layered. ``domain`` holds the model and the rules and depends on
nothing else; ``application`` orchestrates it behind ports; ``infrastructure``
implements those ports (JSON storage, GitHub, configuration); ``cli`` and ``web``
are two interfaces over the same services.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.0"
