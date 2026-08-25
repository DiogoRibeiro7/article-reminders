"""Application layer: services, reminders, activity, analytics, and views.

Depends on ``domain`` and on the protocols in :mod:`article_reminders.application.ports`.
"""

from article_reminders.application.activity import (
    ActivityService,
    StagnationFinding,
    classify_path,
    classify_paths,
    detect_stagnation,
)
from article_reminders.application.analytics import PortfolioAnalytics, build_analytics
from article_reminders.application.reminders import ReminderEngine
from article_reminders.application.services import PaperFilter, PortfolioService
from article_reminders.application.workflow import (
    Dashboard,
    PaperCard,
    build_board,
    build_calendar,
    build_dashboard,
)

__all__ = [
    "ActivityService",
    "Dashboard",
    "PaperCard",
    "PaperFilter",
    "PortfolioAnalytics",
    "PortfolioService",
    "ReminderEngine",
    "StagnationFinding",
    "build_analytics",
    "build_board",
    "build_calendar",
    "build_dashboard",
    "classify_path",
    "classify_paths",
    "detect_stagnation",
]
