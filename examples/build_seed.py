"""Rebuild the example portfolio under ``examples/data``.

Run it with ``python examples/build_seed.py``. The dates are absolute and the
clock is fixed, so regenerating produces the same files and a clean diff.

The papers are invented. They are shaped to exercise the parts of the
application that are hard to see on an empty portfolio: a manuscript that has
stalled while its analysis keeps moving, a revision with a deadline a week out,
a paper sitting with a journal, an idea with no next action, and one that made
it all the way to a DOI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from article_reminders.application.services import PortfolioService
from article_reminders.domain.enums import DecisionOutcome, LifecycleStatus, Priority
from article_reminders.domain.models import ActivitySnapshot
from article_reminders.infrastructure.clock import FixedClock
from article_reminders.infrastructure.configuration.settings import Settings
from article_reminders.infrastructure.storage.event_log import JsonlEventLog
from article_reminders.infrastructure.storage.json_store import JsonPaperRepository
from article_reminders.infrastructure.storage.legacy import legacy_paper_id
from article_reminders.infrastructure.storage.migration import export_legacy_tracker

ROOT = Path(__file__).resolve().parent
TODAY = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)

S = LifecycleStatus


def at(text: str) -> datetime:
    return datetime.fromisoformat(f"{text}T09:00:00+00:00")


def seed_id(title: str) -> str:
    """A stable id, so regenerating the seed produces a clean diff."""
    return str(legacy_paper_id(title, "example-lab", "seed"))


def build(root: Path = ROOT) -> PortfolioService:
    """Create the example portfolio from scratch."""
    settings = Settings.default(root)
    for path in (settings.paths.portfolio, settings.paths.events, settings.paths.legacy):
        path.unlink(missing_ok=True)

    portfolio = PortfolioService(
        JsonPaperRepository(settings.paths.portfolio),
        JsonlEventLog(settings.paths.events),
        clock=FixedClock(TODAY),
    )

    # A. Analysis is alive; the manuscript is not. This is the finding the
    #    application exists to surface.
    minimum_wage = portfolio.create(
        "Minimum Wage and Productivity in Portuguese Firms",
        paper_id=seed_id("Minimum Wage and Productivity in Portuguese Firms"),
        status=S.ANALYSIS,
        priority=Priority.HIGH,
        repository="example-lab/minimum-wage-productivity",
        paper_path="paper/",
        research_question=(
            "Did the 2019-2024 minimum wage increases move firm-level productivity, "
            "or only the wage bill?"
        ),
        research_programme="Labour market dynamics",
        tags=["labour", "portugal", "firm-level"],
        authors=["Ribeiro, D.", "Silva, M."],
        next_action="Run the robustness specification on the revised OECD vintage",
        next_action_due_at="2026-08-31",
        target_journal="Labour Economics",
        description=(
            "Matched employer-employee panel, difference-in-differences around the "
            "statutory increases, with a firm-level productivity decomposition."
        ),
    )
    portfolio.apply_activity(
        minimum_wage,
        ActivitySnapshot(
            repository_slug="example-lab/minimum-wage-productivity",
            observed_at=TODAY,
            last_repository_activity_at=at("2026-08-23"),
            last_analysis_activity_at=at("2026-08-23"),
            last_manuscript_activity_at=at("2026-07-18"),
        ),
    )

    # B. A healthy draft: recent work, a concrete next action, a near deadline.
    housing = portfolio.create(
        "Parish-Level Rent Indices from Administrative Records",
        paper_id=seed_id("Parish-Level Rent Indices from Administrative Records"),
        status=S.DRAFT,
        priority=Priority.HIGH,
        repository="example-lab/parish-rents",
        paper_path="manuscript/",
        research_question=(
            "Can a defensible rent index be built at parish level from administrative "
            "records alone?"
        ),
        research_programme="Housing and cities",
        tags=["housing", "portugal", "measurement"],
        next_action="Finish the methodology section",
        next_action_due_at="2026-09-04",
        target_journal="Journal of Housing Economics",
    )
    portfolio.apply_activity(
        housing,
        ActivitySnapshot(
            repository_slug="example-lab/parish-rents",
            observed_at=TODAY,
            last_repository_activity_at=at("2026-08-24"),
            last_analysis_activity_at=at("2026-08-20"),
            last_manuscript_activity_at=at("2026-08-24"),
        ),
    )

    # C. Sitting with a journal. No next action is owed, and it is not stalled.
    uncertainty = portfolio.create(
        "Calibration of Predictive Uncertainty Under Dataset Shift",
        paper_id=seed_id("Calibration of Predictive Uncertainty Under Dataset Shift"),
        status=S.READY_TO_SUBMIT,
        priority=Priority.MEDIUM,
        repository="example-lab/uncertainty-bench",
        paper_path="paper/",
        research_question=(
            "Which uncertainty estimators stay calibrated when the test distribution "
            "moves away from the training one?"
        ),
        research_programme="Statistical methodology",
        tags=["uncertainty", "benchmark"],
        target_journal="Journal of Machine Learning Research",
    )
    uncertainty = portfolio.record_submission(
        uncertainty, "Journal of Machine Learning Research", submitted_at="2026-05-12"
    )
    uncertainty = portfolio.set_status(uncertainty, S.UNDER_REVIEW)

    # D. A revision with a deadline inside the reporting window.
    tails = portfolio.create(
        "Adaptive Threshold Selection for Heavy-Tailed Insurance Losses",
        paper_id=seed_id("Adaptive Threshold Selection for Heavy-Tailed Insurance Losses"),
        status=S.READY_TO_SUBMIT,
        priority=Priority.CRITICAL,
        repository="example-lab/heavy-tails",
        paper_path="papers/threshold/",
        research_question=(
            "Does adaptive threshold selection beat fixed quantile rules for tail-index "
            "estimation on real loss data?"
        ),
        research_programme="Statistical methodology",
        tags=["extreme-values", "insurance"],
        target_journal="Insurance: Mathematics and Economics",
    )
    tails = portfolio.record_submission(
        tails, "Insurance: Mathematics and Economics", submitted_at="2026-02-10"
    )
    tails = portfolio.set_status(tails, S.UNDER_REVIEW)
    tails = portfolio.record_decision(
        tails,
        DecisionOutcome.MAJOR_REVISION,
        decided_at="2026-07-30",
        revision_due_at="2026-09-12",
        notes="Reviewer 2 wants the bootstrap variance derived rather than asserted.",
    )
    portfolio.set_next_action(
        tails, "Answer reviewer 2 on the bootstrap variance", due_at="2026-09-05"
    )

    # E. Finished, with an identifier.
    crises = portfolio.create(
        "A Dynamical Systems View of Fiscal Crises",
        paper_id=seed_id("A Dynamical Systems View of Fiscal Crises"),
        status=S.READY_TO_SUBMIT,
        priority=Priority.MEDIUM,
        repository="example-lab/fiscal-dynamics",
        paper_path="paper/",
        research_programme="Macro-financial dynamics",
        tags=["macro", "dynamical-systems"],
        target_journal="Journal of Economic Dynamics and Control",
    )
    crises = portfolio.record_submission(
        crises, "Journal of Economic Dynamics and Control", submitted_at="2025-09-01"
    )
    crises = portfolio.set_status(crises, S.UNDER_REVIEW)
    crises = portfolio.record_decision(
        crises, DecisionOutcome.ACCEPT, decided_at="2026-01-20"
    )
    crises = portfolio.set_status(crises, S.PUBLISHED)
    portfolio.update(
        crises,
        doi="10.1000/example.2026.0142",
        publication_url="https://doi.org/10.1000/example.2026.0142",
        accepted_at="2026-01-20",
        published_at="2026-03-04",
    )

    # F. An idea with nothing queued up: the workflow problem the dashboard is
    #    meant to make impossible to miss.
    portfolio.create(
        "Do Rewritten Documents Keep a Recoverable Order?",
        paper_id=seed_id("Do Rewritten Documents Keep a Recoverable Order?"),
        status=S.IDEA,
        priority=Priority.LOW,
        research_question=(
            "Is the order of successive machine rewrites recoverable from the final text?"
        ),
        research_programme="Statistical methodology",
        tags=["text", "identifiability"],
    )

    # G. Shelved on purpose, so the board has a Paused lane with something in it.
    paused = portfolio.create(
        "Multi-Stage Watermark Interference",
        paper_id=seed_id("Multi-Stage Watermark Interference"),
        status=S.RESEARCH,
        priority=Priority.LOW,
        repository="example-lab/watermark-dynamics",
        paper_path="papers/watermarks/",
        research_programme="Statistical methodology",
        tags=["text", "watermarking"],
        notes="Parked until there is a sharper claim than 'watermarks can coexist'.",
    )
    portfolio.set_status(paused, S.PAUSED, note="Waiting for a sharper contribution.")

    # Re-stamp the document header with the fixed clock so the file is byte-stable.
    repository = JsonPaperRepository(settings.paths.portfolio)
    repository.save_all(repository.load(), generated_at=TODAY)
    export_legacy_tracker(settings, portfolio.list_papers(), clock=TODAY)
    return portfolio


if __name__ == "__main__":
    service = build()
    print(f"Wrote {len(service.list_papers())} example papers to {ROOT / 'data'}.")
