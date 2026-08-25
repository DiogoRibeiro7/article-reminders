"""The ``article-reminders`` command line.

Every command goes through the same service layer the web application uses; the
CLI parses arguments and formats output, and holds no rules of its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from article_reminders.application.analytics import build_analytics
from article_reminders.application.services import PaperFilter
from article_reminders.application.workflow import (
    build_board,
    build_calendar,
    build_dashboard,
    group_calendar_by_month,
)
from article_reminders.bootstrap import (
    Application,
    GitHubUnavailableError,
    build_application,
    configure_logging,
)
from article_reminders.cli.formatting import (
    SEVERITY_COLOUR,
    bold,
    colour,
    dim,
    format_date,
    relative_days,
    table,
)
from article_reminders.domain.enums import (
    DecisionOutcome,
    LifecycleStatus,
    Priority,
    ReminderSeverity,
)
from article_reminders.domain.errors import DomainError
from article_reminders.domain.models import Paper
from article_reminders.infrastructure.storage.migration import (
    export_legacy_tracker,
    migrate_legacy_portfolio,
)

EXIT_OK = 0
EXIT_ERROR = 1
#: Reserved for "the command ran, and found problems worth acting on".
EXIT_FINDINGS = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="article-reminders",
        description="Track the operational lifecycle of research papers.",
    )
    parser.add_argument("--root", type=Path, help="Directory holding data/ (default: cwd).")
    parser.add_argument("--config", type=Path, help="Path to an article-reminders.yml.")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING (default), ERROR.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("list", help="List papers.")
    listing.add_argument("--status", action="append", help="Filter by lifecycle status.")
    listing.add_argument("--priority", action="append", help="Filter by priority.")
    listing.add_argument("--tag", action="append", help="Filter by tag.")
    listing.add_argument("--programme", help="Filter by research programme.")
    listing.add_argument("--query", help="Free-text search over titles, notes, and repositories.")
    listing.add_argument("--active", action="store_true", help="Only papers still in flight.")
    listing.add_argument(
        "--no-next-action", action="store_true", help="Only papers with no next action."
    )
    listing.add_argument("--limit", type=int, help="Show at most this many papers.")
    listing.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    listing.set_defaults(handler=cmd_list)

    show = subparsers.add_parser("show", help="Show one paper in full.")
    show.add_argument("paper", help="Id, slug, title, or a unique fragment of the title.")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_show)

    add = subparsers.add_parser("add", help="Add a paper.")
    add.add_argument("title")
    add.add_argument("--status", default=LifecycleStatus.IDEA.value)
    add.add_argument("--priority", default=Priority.MEDIUM.value)
    add.add_argument("--repo", help="owner/name of the research repository.")
    add.add_argument("--paper-path", help="Path to the manuscript inside the repository.")
    add.add_argument("--branch", help="Branch to watch (default: the repository default).")
    add.add_argument("--research-question")
    add.add_argument("--description")
    add.add_argument("--abstract")
    add.add_argument("--notes")
    add.add_argument("--journal", dest="target_journal")
    add.add_argument("--conference", dest="target_conference")
    add.add_argument("--programme", dest="research_programme")
    add.add_argument("--tags", help="Comma-separated.")
    add.add_argument("--authors", help="Comma-separated.")
    add.add_argument("--next-action")
    add.add_argument("--due", help="Next-action deadline, YYYY-MM-DD.")
    add.set_defaults(handler=cmd_add)

    update = subparsers.add_parser("update", help="Edit a paper's fields.")
    update.add_argument("paper")
    update.add_argument("--title")
    update.add_argument("--priority")
    update.add_argument("--repo", dest="repository")
    update.add_argument("--paper-path")
    update.add_argument("--branch", dest="repository_branch")
    update.add_argument("--research-question")
    update.add_argument("--description")
    update.add_argument("--abstract")
    update.add_argument("--notes")
    update.add_argument("--journal", dest="target_journal")
    update.add_argument("--conference", dest="target_conference")
    update.add_argument("--programme", dest="research_programme")
    update.add_argument("--tags")
    update.add_argument("--authors")
    update.add_argument("--doi")
    update.add_argument("--preprint-url")
    update.add_argument("--publication-url")
    update.add_argument("--waiting-for")
    update.add_argument("--revision-due", dest="revision_due_at")
    update.add_argument("--conference-deadline")
    update.add_argument("--internal-review-deadline")
    update.set_defaults(handler=cmd_update)

    status = subparsers.add_parser("status", help="Move a paper through the lifecycle.")
    status.add_argument("paper")
    status.add_argument("new_status", help="Target lifecycle status.")
    status.add_argument(
        "--force", action="store_true", help="Allow a transition outside the canonical workflow."
    )
    status.add_argument("--note", default="", help="Why the move was made.")
    status.set_defaults(handler=cmd_status)

    next_action = subparsers.add_parser(
        "next-action", help="Read or set the one next action for a paper."
    )
    next_action.add_argument("paper", nargs="?", help="Omit to list every next action.")
    next_action.add_argument("description", nargs="?", help="The next concrete piece of work.")
    next_action.add_argument("--due", help="Deadline, YYYY-MM-DD.")
    next_action.add_argument("--clear", action="store_true", help="Remove the next action.")
    next_action.add_argument("--done", action="store_true", help="Mark it complete.")
    next_action.add_argument("--then", help="Set this as the follow-up next action.")
    next_action.set_defaults(handler=cmd_next_action)

    reminders = subparsers.add_parser("reminders", help="Show what needs attention.")
    reminders.add_argument("--paper", help="Restrict to one paper.")
    reminders.add_argument("--severity", help="Minimum severity: info, warning, critical.")
    reminders.add_argument("--kind", action="append", help="Restrict to these reminder kinds.")
    reminders.add_argument("--json", action="store_true")
    reminders.add_argument(
        "--exit-code",
        action="store_true",
        help=f"Exit {EXIT_FINDINGS} when any reminder is produced, for CI.",
    )
    reminders.set_defaults(handler=cmd_reminders)

    dashboard = subparsers.add_parser("dashboard", help="The portfolio dashboard, in the terminal.")
    dashboard.add_argument("--json", action="store_true")
    dashboard.set_defaults(handler=cmd_dashboard)

    board = subparsers.add_parser("board", help="The lifecycle board, in the terminal.")
    board.add_argument("--json", action="store_true")
    board.set_defaults(handler=cmd_board)

    calendar = subparsers.add_parser("calendar", help="Upcoming deadlines.")
    calendar.add_argument("--days", type=int, help="Only deadlines within this many days.")
    calendar.add_argument("--all", action="store_true", help="Include inactive papers.")
    calendar.add_argument("--json", action="store_true")
    calendar.set_defaults(handler=cmd_calendar)

    analytics = subparsers.add_parser("analytics", help="Research pipeline analytics.")
    analytics.add_argument("--json", action="store_true")
    analytics.set_defaults(handler=cmd_analytics)

    submit = subparsers.add_parser("submit", help="Record a submission.")
    submit.add_argument("paper")
    submit.add_argument("venue")
    submit.add_argument("--date", help="Submission date, YYYY-MM-DD (default: today).")
    submit.set_defaults(handler=cmd_submit)

    decision = subparsers.add_parser("decision", help="Record a decision from a venue.")
    decision.add_argument("paper")
    decision.add_argument(
        "outcome", choices=[item.value for item in DecisionOutcome if item.value != "pending"]
    )
    decision.add_argument("--date", help="Decision date, YYYY-MM-DD (default: today).")
    decision.add_argument("--revision-due", help="Revision deadline, YYYY-MM-DD.")
    decision.add_argument("--notes", default="")
    decision.set_defaults(handler=cmd_decision)

    events = subparsers.add_parser("events", help="The recorded history of a paper.")
    events.add_argument("paper")
    events.add_argument("--json", action="store_true")
    events.set_defaults(handler=cmd_events)

    sync = subparsers.add_parser("sync-github", help="Synchronise activity and reminder issues.")
    sync.add_argument(
        "--only",
        choices=["activity", "issues"],
        help="Run only one half of the synchronisation.",
    )
    sync.add_argument("--dry-run", action="store_true", help="Report without writing anything.")
    sync.set_defaults(handler=cmd_sync_github)

    migrate = subparsers.add_parser(
        "migrate", help="Migrate data/articles.json into the portfolio."
    )
    migrate.add_argument("--dry-run", action="store_true")
    migrate.set_defaults(handler=cmd_migrate)

    export = subparsers.add_parser(
        "legacy-export", help="Write the portfolio back to data/articles.json."
    )
    export.add_argument("--dry-run", action="store_true", help="Print the file instead.")
    export.set_defaults(handler=cmd_legacy_export)

    validate = subparsers.add_parser("validate", help="Check that the portfolio loads and is sane.")
    validate.set_defaults(handler=cmd_validate)

    settings_cmd = subparsers.add_parser("settings", help="Show the resolved configuration.")
    settings_cmd.add_argument("--json", action="store_true")
    settings_cmd.set_defaults(handler=cmd_settings)

    serve = subparsers.add_parser("serve", help="Run the web application.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(handler=cmd_serve)

    return parser


# -- commands -------------------------------------------------------------


def cmd_list(app: Application, args: argparse.Namespace) -> int:
    criteria = PaperFilter(
        statuses=frozenset(_statuses(args.status)),
        priorities=frozenset(Priority(item.lower()) for item in (args.priority or [])),
        tags=frozenset(tag.lower() for tag in (args.tag or [])),
        programme=args.programme,
        query=args.query,
        active_only=bool(args.active),
        needs_next_action=bool(args.no_next_action),
    )
    papers = app.portfolio.list_papers(criteria)
    if args.limit:
        papers = papers[: args.limit]

    if args.json:
        _print_json([paper.to_dict() for paper in papers])
        return EXIT_OK

    if not papers:
        print("No papers match.")
        return EXIT_OK

    reference = app.clock.now()
    rows = [
        [
            str(paper.slug),
            paper.status.value,
            paper.priority.value,
            paper.repository_slug or "-",
            (paper.next_action.description if paper.next_action else "- none -"),
            relative_days(paper.next_action_due_at, reference),
        ]
        for paper in papers
    ]
    print(
        table(
            ["SLUG", "STAGE", "PRIORITY", "REPOSITORY", "NEXT ACTION", "DUE"],
            rows,
            max_widths=[34, 16, 8, 34, 48, 14],
        )
    )
    print(f"\n{len(papers)} paper(s).")
    return EXIT_OK


def cmd_show(app: Application, args: argparse.Namespace) -> int:
    paper = app.portfolio.get(args.paper)
    if args.json:
        _print_json(paper.to_dict())
        return EXIT_OK

    reference = app.clock.now()
    reminders = app.reminders.for_paper(paper, reference)

    print(bold(paper.title))
    print(dim(f"{paper.slug} | {paper.id}"))
    print()
    if paper.research_question:
        print(f"Research question: {paper.research_question}")
    print(f"Stage:            {paper.status.value} ({paper.board_column.label})")
    print(f"Priority:         {paper.priority.value}")
    if paper.repository is not None:
        print(f"Repository:       {paper.repository.url}")
        print(f"Manuscript path:  {paper.paper_path or '-'}")
    if paper.venue:
        print(f"Target venue:     {paper.venue}")
    if paper.authors:
        print(f"Authors:          {', '.join(paper.authors)}")
    if paper.tags:
        print(f"Tags:             {', '.join(paper.tags)}")
    if paper.research_programme:
        print(f"Programme:        {paper.research_programme}")
    if paper.waiting_for:
        print(f"Waiting for:      {paper.waiting_for}")
    if paper.doi:
        print(f"DOI:              {paper.doi}")

    print()
    if paper.next_action is None:
        print(bold("Next action:      none set"))
    else:
        due = paper.next_action.due_at
        print(bold(f"Next action:      {paper.next_action.description}"))
        if due is not None:
            print(f"Due:              {format_date(due)} ({relative_days(due, reference)})")

    timeline = [
        ("Created", paper.created_at),
        ("Started", paper.started_at),
        ("Draft started", paper.draft_started_at),
        ("Submitted", paper.submitted_at),
        ("Decision", paper.decision_received_at),
        ("Revision due", paper.revision_due_at),
        ("Accepted", paper.accepted_at),
        ("Published", paper.published_at),
        ("Last updated", paper.updated_at),
    ]
    print()
    print(bold("Timeline"))
    for label, value in timeline:
        if value is not None:
            print(f"  {label:<16}{format_date(value)}")

    activity = [
        ("Repository", paper.last_repository_activity_at),
        ("Manuscript", paper.last_manuscript_activity_at),
        ("Analysis", paper.last_analysis_activity_at),
    ]
    if any(value for _, value in activity):
        print()
        print(bold("Observed activity"))
        for label, value in activity:
            print(f"  {label:<16}{format_date(value)} ({relative_days(value, reference)})")

    if paper.submissions:
        print()
        print(bold("Submission history"))
        for record in paper.submissions:
            decided = (
                f"{record.decision.label} on {format_date(record.decision_at)}"
                if record.is_resolved
                else "pending"
            )
            print(f"  {format_date(record.submitted_at)}  {record.venue} - {decided}")

    if reminders:
        print()
        print(bold("Warnings"))
        for reminder in reminders:
            print(
                f"  {colour(reminder.severity.label.upper(), SEVERITY_COLOUR[reminder.severity])}"
                f"  {reminder.message}"
            )

    if paper.notes:
        print()
        print(bold("Notes"))
        for line in paper.notes.splitlines():
            print(f"  {line}")

    history = app.portfolio.timeline(paper)
    if history:
        print()
        print(bold("History"))
        for event in history[-10:]:
            print(f"  {format_date(event.occurred_at)}  {event.summary}")
    return EXIT_OK


def cmd_add(app: Application, args: argparse.Namespace) -> int:
    paper = app.portfolio.create(
        args.title,
        status=args.status,
        priority=args.priority,
        repository=args.repo,
        paper_path=args.paper_path,
        repository_branch=args.branch,
        next_action=args.next_action,
        next_action_due_at=args.due,
        tags=_split(args.tags),
        authors=_split(args.authors),
        **_present(
            research_question=args.research_question,
            description=args.description,
            abstract=args.abstract,
            notes=args.notes,
            target_journal=args.target_journal,
            target_conference=args.target_conference,
            research_programme=args.research_programme,
        ),
    )
    print(f"Added {paper.title} [{paper.slug}] at stage {paper.status.value}.")
    if paper.next_action is None:
        print("No next action set. Set one with: article-reminders next-action "
              f"{paper.slug} \"...\"")
    return EXIT_OK


def cmd_update(app: Application, args: argparse.Namespace) -> int:
    changes = _present(
        title=args.title,
        priority=args.priority,
        repository=args.repository,
        paper_path=args.paper_path,
        repository_branch=args.repository_branch,
        research_question=args.research_question,
        description=args.description,
        abstract=args.abstract,
        notes=args.notes,
        target_journal=args.target_journal,
        target_conference=args.target_conference,
        research_programme=args.research_programme,
        doi=args.doi,
        preprint_url=args.preprint_url,
        publication_url=args.publication_url,
        waiting_for=args.waiting_for,
        revision_due_at=args.revision_due_at,
        conference_deadline=args.conference_deadline,
        internal_review_deadline=args.internal_review_deadline,
    )
    if args.tags is not None:
        changes["tags"] = _split(args.tags)
    if args.authors is not None:
        changes["authors"] = _split(args.authors)
    if not changes:
        print("Nothing to update.")
        return EXIT_OK

    paper = app.portfolio.update(args.paper, **changes)
    print(f"Updated {paper.title}: {', '.join(sorted(changes))}.")
    return EXIT_OK


def cmd_status(app: Application, args: argparse.Namespace) -> int:
    paper = app.portfolio.get(args.paper)
    before = paper.status
    updated = app.portfolio.set_status(
        paper, args.new_status, force=args.force, note=args.note
    )
    print(f"{updated.title}: {before.value} -> {updated.status.value}.")
    return EXIT_OK


def cmd_next_action(app: Application, args: argparse.Namespace) -> int:
    if args.paper is None:
        reference = app.clock.now()
        rows = [
            [
                str(paper.slug),
                paper.status.value,
                paper.next_action.description if paper.next_action else "- none -",
                relative_days(paper.next_action_due_at, reference),
            ]
            for paper in app.portfolio.list_papers(PaperFilter(active_only=True))
        ]
        print(table(["SLUG", "STAGE", "NEXT ACTION", "DUE"], rows, max_widths=[34, 16, 60, 14]))
        return EXIT_OK

    paper = app.portfolio.get(args.paper)

    if args.clear:
        app.portfolio.clear_next_action(paper)
        print(f"Cleared the next action for {paper.title}.")
        return EXIT_OK

    if args.done:
        updated = app.portfolio.complete_next_action(
            paper, follow_up=args.then, follow_up_due_at=args.due
        )
        print(f"Completed the next action for {updated.title}.")
        if updated.next_action is not None:
            print(f"Next: {updated.next_action.description}")
        return EXIT_OK

    if args.description is None:
        if paper.next_action is None:
            print(f"{paper.title}: no next action set.")
            return EXIT_OK
        due = paper.next_action.due_at
        print(f"{paper.title}: {paper.next_action.description}")
        if due is not None:
            print(f"Due {format_date(due)} ({relative_days(due, app.clock.now())}).")
        return EXIT_OK

    updated = app.portfolio.set_next_action(paper, args.description, due_at=args.due)
    action = updated.next_action
    assert action is not None
    suffix = f" (due {format_date(action.due_at)})" if action.due_at else ""
    print(f"{updated.title}: {action.description}{suffix}")
    return EXIT_OK


def cmd_reminders(app: Application, args: argparse.Namespace) -> int:
    papers = [app.portfolio.get(args.paper)] if args.paper else list(app.portfolio.list_papers())
    reference = app.clock.now()
    reminders = app.reminders.generate(papers, reference)

    if args.severity:
        floor = ReminderSeverity(args.severity.lower()).rank
        reminders = [item for item in reminders if item.severity.rank >= floor]
    if args.kind:
        wanted = {kind.lower() for kind in args.kind}
        reminders = [item for item in reminders if item.kind.value in wanted]

    if args.json:
        _print_json([item.to_dict() for item in reminders])
    elif not reminders:
        print("Nothing needs attention.")
    else:
        rows = [
            [
                item.severity.label.upper(),
                item.kind.value,
                item.paper_title,
                item.message,
                format_date(item.due_at),
            ]
            for item in reminders
        ]
        print(
            table(
                ["SEVERITY", "KIND", "PAPER", "MESSAGE", "DUE"],
                rows,
                max_widths=[8, 22, 34, 60, 12],
            )
        )
        print(f"\n{len(reminders)} reminder(s).")

    if args.exit_code and reminders:
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_dashboard(app: Application, args: argparse.Namespace) -> int:
    reference = app.clock.now()
    dashboard = build_dashboard(
        app.portfolio.list_papers(), app.settings, reference, engine=app.reminders
    )
    if args.json:
        _print_json(
            {
                "generated_at": reference.isoformat(),
                "counts": dashboard.counts(),
                "focus": [card.paper.to_dict() for card in dashboard.focus()],
            }
        )
        return EXIT_OK

    print(bold("Portfolio"))
    rows = [[bucket.label, str(bucket.count)] for bucket in dashboard.buckets]
    print(table(["BUCKET", "COUNT"], rows))

    focus = dashboard.focus()
    if focus:
        print()
        print(bold("What to work on next"))
        for card in focus:
            head = f"  {card.paper.title}"
            print(head)
            print(f"    stage: {card.paper.status.value}")
            print(f"    next:  {card.next_action_text or '- none set -'}")
            for reason in card.attention_reasons[:3]:
                print(f"    why:   {reason}")
    return EXIT_OK


def cmd_board(app: Application, args: argparse.Namespace) -> int:
    reference = app.clock.now()
    dashboard = build_dashboard(
        app.portfolio.list_papers(), app.settings, reference, engine=app.reminders
    )
    lanes = build_board(dashboard.cards)
    if args.json:
        _print_json(
            {
                lane.column.value: [card.paper.to_dict() for card in lane.cards]
                for lane in lanes
            }
        )
        return EXIT_OK

    for lane in lanes:
        if not lane.cards:
            continue
        print(bold(f"{lane.label} ({lane.count})"))
        for card in lane.cards:
            marker = "!" if card.needs_attention else " "
            print(f" {marker} {card.paper.title}")
            if card.next_action_text:
                print(f"     next: {card.next_action_text}")
        print()
    return EXIT_OK


def cmd_calendar(app: Application, args: argparse.Namespace) -> int:
    reference = app.clock.now()
    entries = build_calendar(app.portfolio.list_papers(), include_inactive=bool(args.all))
    if args.days is not None:
        horizon = reference.date()
        entries = tuple(
            entry for entry in entries if 0 <= (entry.on - horizon).days <= args.days
        )

    if args.json:
        _print_json(
            [
                {
                    "date": entry.on.isoformat(),
                    "kind": entry.kind,
                    "label": entry.label,
                    "paper": entry.paper.title,
                    "paper_id": entry.paper_id,
                }
                for entry in entries
            ]
        )
        return EXIT_OK

    if not entries:
        print("No deadlines recorded.")
        return EXIT_OK

    for month in group_calendar_by_month(entries):
        print(bold(month.label))
        for entry in month.entries:
            print(
                f"  {entry.on.isoformat()}  {entry.kind:<16}{entry.paper.title}"
                f" - {entry.label}"
            )
        print()
    return EXIT_OK


def cmd_analytics(app: Application, args: argparse.Namespace) -> int:
    reference = app.clock.now()
    papers = app.portfolio.list_papers()
    dashboard = build_dashboard(papers, app.settings, reference, engine=app.reminders)
    analytics = build_analytics(
        papers,
        reference,
        events=app.portfolio.events.all(),
        stalled_ids=[str(card.paper.id) for card in dashboard.bucket("stalled").cards],
    )
    if args.json:
        _print_json(analytics.to_dict())
        return EXIT_OK

    print(bold("Portfolio"))
    print(
        table(
            ["METRIC", "VALUE"],
            [
                ["Papers", str(analytics.total)],
                ["Active", str(analytics.active)],
                ["Stalled", str(analytics.stalled)],
                ["Paused", str(analytics.paused)],
                ["Abandoned", str(analytics.abandoned)],
                ["Submissions", str(analytics.submissions)],
                ["Decisions", str(analytics.decisions)],
                ["Acceptances", str(analytics.acceptances)],
                ["Publications", str(analytics.publications)],
                [
                    "Acceptance rate",
                    "not enough data"
                    if analytics.acceptance_rate is None
                    else f"{analytics.acceptance_rate:.0%}",
                ],
            ],
        )
    )
    print()
    print(bold("Median time between stages"))
    print(
        table(
            ["INTERVAL", "MEDIAN DAYS", "SAMPLE"],
            [
                [
                    item.label,
                    "not enough data" if item.median_days is None else f"{item.median_days:.0f}",
                    str(item.sample_size),
                ]
                for item in analytics.stage_durations
            ],
        )
    )
    return EXIT_OK


def cmd_submit(app: Application, args: argparse.Namespace) -> int:
    paper = app.portfolio.record_submission(args.paper, args.venue, submitted_at=args.date)
    print(f"{paper.title}: submitted to {args.venue} ({paper.status.value}).")
    return EXIT_OK


def cmd_decision(app: Application, args: argparse.Namespace) -> int:
    paper = app.portfolio.record_decision(
        args.paper,
        args.outcome,
        decided_at=args.date,
        revision_due_at=args.revision_due,
        notes=args.notes,
    )
    print(f"{paper.title}: decision {args.outcome} recorded; now at {paper.status.value}.")
    return EXIT_OK


def cmd_events(app: Application, args: argparse.Namespace) -> int:
    paper = app.portfolio.get(args.paper)
    events = app.portfolio.timeline(paper)
    if args.json:
        _print_json([event.to_dict() for event in events])
        return EXIT_OK
    if not events:
        print(f"No recorded events for {paper.title}.")
        return EXIT_OK
    for event in events:
        print(f"{format_date(event.occurred_at)}  {event.event_type.value:<32}{event.summary}")
    return EXIT_OK


def cmd_sync_github(app: Application, args: argparse.Namespace) -> int:
    ran = False
    if args.only in (None, "activity"):
        report = app.activity_service().sync()
        print(report.summary())
        for title in report.unreachable:
            print(f"  unreachable: {title}")
        ran = True
    if args.only in (None, "issues"):
        outcome = app.issue_sync_service().sync(dry_run=args.dry_run)
        print(outcome.summary())
        ran = True
    return EXIT_OK if ran else EXIT_ERROR


def cmd_migrate(app: Application, args: argparse.Namespace) -> int:
    report = migrate_legacy_portfolio(
        app.settings, dry_run=args.dry_run, clock=app.clock.now()
    )
    print(report.summary())
    for warning in report.warnings:
        print(f"  warning: {warning}")
    return EXIT_OK


def cmd_legacy_export(app: Application, args: argparse.Namespace) -> int:
    papers = app.portfolio.list_papers()
    document, backup = export_legacy_tracker(
        app.settings, papers, dry_run=args.dry_run, clock=app.clock.now()
    )
    if args.dry_run:
        print(document, end="")
        return EXIT_OK
    print(f"Wrote {len(papers)} article(s) to {app.settings.paths.legacy}.")
    if backup is not None:
        print(f"Previous file backed up to {backup}.")
    return EXIT_OK


def cmd_validate(app: Application, args: argparse.Namespace) -> int:  # noqa: ARG001
    papers = app.portfolio.list_papers()
    problems: list[str] = []
    slugs: dict[str, str] = {}
    for paper in papers:
        if str(paper.slug) in slugs:
            problems.append(
                f"duplicate slug {paper.slug!r}: {paper.title!r} and {slugs[str(paper.slug)]!r}"
            )
        slugs[str(paper.slug)] = paper.title
    ids: set[str] = set()
    for paper in papers:
        if str(paper.id) in ids:
            problems.append(f"duplicate id {paper.id!r} on {paper.title!r}")
        ids.add(str(paper.id))

    source = (
        app.settings.paths.legacy if app.uses_legacy_fallback else app.settings.paths.portfolio
    )
    if problems:
        print(f"{source} failed validation with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return EXIT_ERROR
    print(f"{source} is valid: {len(papers)} papers.")
    return EXIT_OK


def cmd_settings(app: Application, args: argparse.Namespace) -> int:
    data = app.settings.to_dict()
    if args.json:
        _print_json(data)
        return EXIT_OK
    print(f"Configuration file: {app.settings.source_path or 'none (defaults)'}")
    print(f"Portfolio:          {app.settings.paths.portfolio}")
    print(f"Events:             {app.settings.paths.events}")
    print(f"Legacy tracker:     {app.settings.paths.legacy}")
    print(f"Reading legacy:     {'yes' if app.uses_legacy_fallback else 'no'}")
    print()
    print(bold("Staleness thresholds (days)"))
    for status, days in sorted(app.settings.staleness.items(), key=lambda item: item[0].value):
        print(f"  {status.value:<18}{days}")
    print()
    print(bold("Activity paths"))
    for label, values in app.settings.activity_paths.to_dict().items():
        print(f"  {label:<12}{', '.join(values)}")
    return EXIT_OK


def cmd_serve(app: Application, args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:  # pragma: no cover - only when web extras are absent
        print("uvicorn is not installed. Install the package to run the web application.")
        return EXIT_ERROR

    from article_reminders.web.app import create_app

    print(f"Serving the research portfolio on http://{args.host}:{args.port}")
    uvicorn.run(
        create_app(app),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return EXIT_OK


# -- plumbing -------------------------------------------------------------


def _statuses(values: Sequence[str] | None) -> list[LifecycleStatus]:
    return [LifecycleStatus(value.lower().replace("-", "_")) for value in (values or [])]


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _present(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Paper):
        return value.title
    return str(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        app = build_application(root=args.root, config=args.config)
        handler = args.handler
        return int(handler(app, args))
    except GitHubUnavailableError as exc:
        print(f"GitHub is not available: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except DomainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:  # pragma: no cover - piping into head
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
