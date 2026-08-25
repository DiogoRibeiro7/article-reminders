"""The web application: server-rendered pages over the same services as the CLI.

FastAPI plus Jinja2, no build step and no client-side framework. The pages are
forms and links; the only state lives in the portfolio file. That is deliberate —
a researcher should be able to run this locally with one command, and a single
page application would add a toolchain without adding an answer to any of the
questions the dashboard exists to answer.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from article_reminders.application.analytics import build_analytics
from article_reminders.application.services import PaperFilter
from article_reminders.application.workflow import (
    Dashboard,
    build_board,
    build_calendar,
    build_dashboard,
    group_calendar_by_month,
)
from article_reminders.bootstrap import Application, GitHubUnavailableError, build_application
from article_reminders.domain.enums import (
    BoardColumn,
    DecisionOutcome,
    LifecycleStatus,
    Priority,
)
from article_reminders.domain.errors import DomainError, PaperNotFoundError
from article_reminders.domain.models import Paper
from article_reminders.domain.rules import allowed_transitions
from article_reminders.domain.timeutils import days_between

logger = logging.getLogger(__name__)

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"


def create_app(application: Application | None = None) -> FastAPI:
    """Build the ASGI application.

    ``application`` is injected by the CLI and by tests; when it is absent the
    world is composed from the working directory, which is what
    ``uvicorn --factory article_reminders.web.app:build`` needs.
    """
    app = FastAPI(title="Research portfolio", docs_url="/api/docs", redoc_url=None)
    app.state.application = application or build_application()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters["date"] = _format_date
    templates.env.filters["relative"] = _relative
    templates.env.globals["statuses"] = list(LifecycleStatus)
    templates.env.globals["priorities"] = list(Priority)
    templates.env.globals["columns"] = list(BoardColumn)
    templates.env.globals["decisions"] = [
        item for item in DecisionOutcome if item is not DecisionOutcome.PENDING
    ]
    app.state.templates = templates

    _register_routes(app)
    return app


def get_application(request: Request) -> Application:
    application: Application = request.app.state.application
    return application


AppDep = Annotated[Application, Depends(get_application)]


def _register_routes(app: FastAPI) -> None:
    templates: Jinja2Templates = app.state.templates

    def render(request: Request, name: str, context: dict[str, Any]) -> HTMLResponse:
        application = get_application(request)
        base = {
            "request": request,
            "now": application.clock.now(),
            "legacy_fallback": application.uses_legacy_fallback,
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        }
        return templates.TemplateResponse(request, name, {**base, **context})

    def dashboard_for(application: Application) -> Dashboard:
        return build_dashboard(
            application.portfolio.list_papers(),
            application.settings,
            application.clock.now(),
            engine=application.reminders,
        )

    def lookup(application: Application, reference: str) -> Paper:
        try:
            return application.portfolio.get(reference)
        except PaperNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def back_to(
        path: str, *, notice: str | None = None, error: str | None = None
    ) -> RedirectResponse:
        query = ""
        if notice:
            query = f"?notice={_quote(notice)}"
        elif error:
            query = f"?error={_quote(error)}"
        return RedirectResponse(url=f"{path}{query}", status_code=303)

    # -- pages ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def portfolio_dashboard(request: Request, application: AppDep) -> HTMLResponse:
        dashboard = dashboard_for(application)
        return render(
            request,
            "dashboard.html",
            {
                "dashboard": dashboard,
                "focus": dashboard.focus(limit=6),
                "buckets": dashboard.buckets,
            },
        )

    @app.get("/papers", response_class=HTMLResponse)
    def papers_index(
        request: Request,
        application: AppDep,
        status: Annotated[list[str] | None, Query()] = None,
        priority: Annotated[list[str] | None, Query()] = None,
        tag: Annotated[str | None, Query()] = None,
        q: Annotated[str | None, Query()] = None,
        active: Annotated[bool, Query()] = False,
    ) -> HTMLResponse:
        criteria = PaperFilter(
            statuses=frozenset(_as_statuses(status)),
            priorities=frozenset(Priority(item) for item in (priority or [])),
            tags=frozenset({tag.lower()} if tag else ()),
            query=q,
            active_only=active,
        )
        papers = application.portfolio.list_papers(criteria)
        dashboard = dashboard_for(application)
        selected = {str(paper.id) for paper in papers}
        return render(
            request,
            "papers.html",
            {
                "cards": [card for card in dashboard.cards if str(card.paper.id) in selected],
                "filters": {
                    "status": status or [],
                    "priority": priority or [],
                    "tag": tag or "",
                    "q": q or "",
                    "active": active,
                },
                "all_tags": sorted(
                    {tag for paper in application.portfolio.list_papers() for tag in paper.tags}
                ),
            },
        )

    @app.get("/papers/new", response_class=HTMLResponse)
    def new_paper_form(request: Request) -> HTMLResponse:
        return render(request, "paper_form.html", {"paper": None})

    @app.post("/papers")
    def create_paper(
        application: AppDep,
        title: Annotated[str, Form()],
        status: Annotated[str, Form()] = LifecycleStatus.IDEA.value,
        priority: Annotated[str, Form()] = Priority.MEDIUM.value,
        repository: Annotated[str, Form()] = "",
        paper_path: Annotated[str, Form()] = "",
        research_question: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
        target_journal: Annotated[str, Form()] = "",
        target_conference: Annotated[str, Form()] = "",
        research_programme: Annotated[str, Form()] = "",
        tags: Annotated[str, Form()] = "",
        authors: Annotated[str, Form()] = "",
        next_action: Annotated[str, Form()] = "",
        next_action_due_at: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        try:
            paper = application.portfolio.create(
                title,
                status=status,
                priority=priority,
                repository=repository or None,
                paper_path=paper_path or None,
                research_question=research_question,
                description=description,
                target_journal=target_journal or None,
                target_conference=target_conference or None,
                research_programme=research_programme or None,
                tags=_split(tags),
                authors=_split(authors),
                next_action=next_action or None,
                next_action_due_at=next_action_due_at or None,
            )
        except DomainError as exc:
            return back_to("/papers/new", error=str(exc))
        return back_to(f"/papers/{paper.id}", notice="Paper created.")

    @app.get("/papers/{paper_id}", response_class=HTMLResponse)
    def paper_detail(request: Request, application: AppDep, paper_id: str) -> HTMLResponse:
        paper = lookup(application, paper_id)
        reference = application.clock.now()
        reminders = application.reminders.for_paper(paper, reference)
        return render(
            request,
            "paper_detail.html",
            {
                "paper": paper,
                "reminders": reminders,
                "events": list(reversed(application.portfolio.timeline(paper))),
                "transitions": sorted(
                    allowed_transitions(paper.status), key=lambda item: item.value
                ),
                "durations": _durations(paper),
            },
        )

    @app.get("/papers/{paper_id}/edit", response_class=HTMLResponse)
    def edit_paper_form(request: Request, application: AppDep, paper_id: str) -> HTMLResponse:
        return render(request, "paper_form.html", {"paper": lookup(application, paper_id)})

    @app.post("/papers/{paper_id}/edit")
    def edit_paper(
        application: AppDep,
        paper_id: str,
        title: Annotated[str, Form()],
        priority: Annotated[str, Form()] = Priority.MEDIUM.value,
        repository: Annotated[str, Form()] = "",
        paper_path: Annotated[str, Form()] = "",
        research_question: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
        abstract: Annotated[str, Form()] = "",
        notes: Annotated[str, Form()] = "",
        target_journal: Annotated[str, Form()] = "",
        target_conference: Annotated[str, Form()] = "",
        research_programme: Annotated[str, Form()] = "",
        tags: Annotated[str, Form()] = "",
        authors: Annotated[str, Form()] = "",
        doi: Annotated[str, Form()] = "",
        preprint_url: Annotated[str, Form()] = "",
        publication_url: Annotated[str, Form()] = "",
        waiting_for: Annotated[str, Form()] = "",
        revision_due_at: Annotated[str, Form()] = "",
        conference_deadline: Annotated[str, Form()] = "",
        internal_review_deadline: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        paper = lookup(application, paper_id)
        try:
            application.portfolio.update(
                paper,
                title=title,
                priority=priority,
                repository=repository or None,
                paper_path=paper_path or None,
                research_question=research_question,
                description=description,
                abstract=abstract,
                notes=notes,
                target_journal=target_journal or None,
                target_conference=target_conference or None,
                research_programme=research_programme or None,
                tags=_split(tags),
                authors=_split(authors),
                doi=doi or None,
                preprint_url=preprint_url or None,
                publication_url=publication_url or None,
                waiting_for=waiting_for or None,
                revision_due_at=revision_due_at or None,
                conference_deadline=conference_deadline or None,
                internal_review_deadline=internal_review_deadline or None,
            )
        except DomainError as exc:
            return back_to(f"/papers/{paper_id}/edit", error=str(exc))
        return back_to(f"/papers/{paper_id}", notice="Saved.")

    @app.post("/papers/{paper_id}/next-action")
    def set_next_action(
        application: AppDep,
        paper_id: str,
        description: Annotated[str, Form()] = "",
        due_at: Annotated[str, Form()] = "",
        action: Annotated[str, Form()] = "set",
    ) -> RedirectResponse:
        paper = lookup(application, paper_id)
        try:
            if action == "clear":
                application.portfolio.clear_next_action(paper)
                notice = "Next action cleared."
            elif action == "done":
                application.portfolio.complete_next_action(
                    paper, follow_up=description or None, follow_up_due_at=due_at or None
                )
                notice = "Next action completed."
            else:
                application.portfolio.set_next_action(paper, description, due_at=due_at or None)
                notice = "Next action set."
        except (DomainError, ValueError) as exc:
            return back_to(f"/papers/{paper_id}", error=str(exc))
        return back_to(f"/papers/{paper_id}", notice=notice)

    @app.post("/papers/{paper_id}/status")
    def change_status(
        application: AppDep,
        paper_id: str,
        status: Annotated[str, Form()],
        note: Annotated[str, Form()] = "",
        force: Annotated[bool, Form()] = False,
        redirect_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        paper = lookup(application, paper_id)
        destination = redirect_to or f"/papers/{paper_id}"
        try:
            updated = application.portfolio.set_status(paper, status, force=force, note=note)
        except DomainError as exc:
            return back_to(destination, error=str(exc))
        return back_to(destination, notice=f"Moved to {updated.status.label}.")

    @app.post("/papers/{paper_id}/submission")
    def record_submission(
        application: AppDep,
        paper_id: str,
        venue: Annotated[str, Form()],
        submitted_at: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        paper = lookup(application, paper_id)
        try:
            application.portfolio.record_submission(
                paper, venue, submitted_at=submitted_at or None
            )
        except DomainError as exc:
            return back_to(f"/papers/{paper_id}", error=str(exc))
        return back_to(f"/papers/{paper_id}", notice=f"Submission to {venue} recorded.")

    @app.post("/papers/{paper_id}/decision")
    def record_decision(
        application: AppDep,
        paper_id: str,
        decision: Annotated[str, Form()],
        decided_at: Annotated[str, Form()] = "",
        revision_due_at: Annotated[str, Form()] = "",
        notes: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        paper = lookup(application, paper_id)
        try:
            application.portfolio.record_decision(
                paper,
                decision,
                decided_at=decided_at or None,
                revision_due_at=revision_due_at or None,
                notes=notes,
            )
        except (DomainError, ValueError) as exc:
            return back_to(f"/papers/{paper_id}", error=str(exc))
        return back_to(f"/papers/{paper_id}", notice="Decision recorded.")

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request, application: AppDep) -> HTMLResponse:
        dashboard = dashboard_for(application)
        return render(request, "board.html", {"lanes": build_board(dashboard.cards)})

    @app.post("/board/move")
    def board_move(
        application: AppDep,
        paper_id: Annotated[str, Form()],
        column: Annotated[str, Form()],
    ) -> RedirectResponse:
        paper = lookup(application, paper_id)
        try:
            updated = application.portfolio.move_to_column(paper, column)
        except (DomainError, ValueError) as exc:
            return back_to("/board", error=str(exc))
        return back_to("/board", notice=f"{updated.title} moved to {updated.status.label}.")

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(
        request: Request,
        application: AppDep,
        days: Annotated[int | None, Query()] = None,
        show_all: Annotated[bool, Query(alias="all")] = False,
    ) -> HTMLResponse:
        reference = application.clock.now()
        entries = build_calendar(application.portfolio.list_papers(), include_inactive=show_all)
        if days is not None:
            horizon = reference + timedelta(days=days)
            entries = tuple(
                entry
                for entry in entries
                if reference.date() <= entry.on <= horizon.date()
            )
        return render(
            request,
            "calendar.html",
            {
                "months": group_calendar_by_month(entries),
                "entries": entries,
                "days": days,
                "show_all": show_all,
            },
        )

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics(request: Request, application: AppDep) -> HTMLResponse:
        reference = application.clock.now()
        papers = application.portfolio.list_papers()
        dashboard = dashboard_for(application)
        report = build_analytics(
            papers,
            reference,
            events=application.portfolio.events.all(),
            stalled_ids=[str(card.paper.id) for card in dashboard.bucket("stalled").cards],
        )
        return render(request, "analytics.html", {"analytics": report, "dashboard": dashboard})

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, application: AppDep) -> HTMLResponse:
        return render(
            request,
            "settings.html",
            {
                "settings": application.settings,
                "config": application.settings.to_dict(),
                "github_repository": application.settings.github.resolved_repository(),
                "github_token": bool(application.settings.github.token()),
            },
        )

    # -- JSON API ---------------------------------------------------------

    @app.get("/api/papers")
    def api_papers(application: AppDep) -> dict[str, Any]:
        return {"papers": [paper.to_dict() for paper in application.portfolio.list_papers()]}

    @app.get("/api/papers/{paper_id}")
    def api_paper(application: AppDep, paper_id: str) -> dict[str, Any]:
        return lookup(application, paper_id).to_dict()

    @app.get("/api/reminders")
    def api_reminders(application: AppDep) -> dict[str, Any]:
        reference = application.clock.now()
        reminders = application.reminders.generate(
            application.portfolio.list_papers(), reference
        )
        return {
            "generated_at": reference.isoformat(),
            "reminders": [item.to_dict() for item in reminders],
        }

    @app.get("/api/dashboard")
    def api_dashboard(application: AppDep) -> dict[str, Any]:
        dashboard = dashboard_for(application)
        return {
            "generated_at": dashboard.generated_at.isoformat(),
            "counts": dashboard.counts(),
            "focus": [
                {
                    "id": str(card.paper.id),
                    "title": card.paper.title,
                    "status": card.paper.status.value,
                    "next_action": card.next_action_text or None,
                    "reasons": list(card.attention_reasons),
                }
                for card in dashboard.focus()
            ],
        }

    @app.get("/api/analytics")
    def api_analytics(application: AppDep) -> dict[str, Any]:
        reference = application.clock.now()
        papers = application.portfolio.list_papers()
        return build_analytics(
            papers, reference, events=application.portfolio.events.all()
        ).to_dict()

    @app.get("/api/health")
    def api_health(application: AppDep) -> dict[str, Any]:
        try:
            count = len(application.portfolio.list_papers())
            healthy = True
        except DomainError:  # pragma: no cover - surfaced on the page instead
            count, healthy = 0, False
        return {
            "healthy": healthy,
            "papers": count,
            "legacy_fallback": application.uses_legacy_fallback,
            "github": application.settings.github.resolved_repository(),
        }

    @app.post("/api/sync-github")
    def api_sync(application: AppDep) -> dict[str, Any]:
        try:
            outcome = application.issue_sync_service().sync()
        except GitHubUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"summary": outcome.summary()}


# -- helpers --------------------------------------------------------------


def _as_statuses(values: Sequence[str] | None) -> list[LifecycleStatus]:
    out: list[LifecycleStatus] = []
    for value in values or []:
        try:
            out.append(LifecycleStatus(value))
        except ValueError:
            continue
    return out


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


def _format_date(value: datetime | None) -> str:
    return "" if value is None else value.date().isoformat()


def _relative(value: datetime | None, reference: datetime | None = None) -> str:
    if value is None:
        return ""
    from article_reminders.domain.timeutils import now as utc_now

    delta = round(days_between(reference or utc_now(), value))
    if delta == 0:
        return "today"
    if delta > 0:
        return f"in {delta} day{'s' if delta != 1 else ''}"
    return f"{abs(delta)} day{'s' if abs(delta) != 1 else ''} ago"


def _durations(paper: Paper) -> list[tuple[str, float | None]]:
    from article_reminders.application.analytics import paper_durations

    return [(item.label, item.days) for item in paper_durations(paper).durations]


def build() -> FastAPI:  # pragma: no cover - used by ``uvicorn ...:build --factory``
    """Factory entry point for ``uvicorn --factory``."""
    return create_app()
