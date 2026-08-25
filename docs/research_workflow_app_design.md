# Research workflow application: design note

This note records what the repository was, what it is now, and why each decision
went the way it did. It is written for whoever has to change this next.

---

## 1. What was here before

`article-reminders` was a GitHub-native tracker: one JSON file, four standalone
scripts, and four scheduled workflows.

```text
data/articles.json         65 entries, flat records, 11 possible keys
scripts/validate_articles.py    schema gate, run on every push and PR
scripts/sync_article_issues.py  one issue per article, opens/updates/closes
scripts/sync_project_items.py   GraphQL sync into a GitHub Project (v2)
scripts/check_article_drift.py  monthly: compares entries against real repos
.github/workflows/*.yml         reminders (weekly), project sync, validate, drift (monthly)
tests/                          three test modules, one per script
```

### The record it kept

```json
{
  "title": "...", "repo": "owner/name", "status": "draft",
  "notes": "...", "abstract": "...", "paper_path": "paper/",
  "priority": "high", "last_updated": "2026-08-19",
  "venue": "", "target_date": "", "next_action": ""
}
```

Nine statuses: `planned`, `in_progress`, `draft`, `submitted`, `revising`,
`finished`, `published`, `archived`, `cancelled`. Four priorities. Unknown keys
were rejected outright, on the reasoning that a key no script reads is a key that
sits in the file doing nothing.

### What was good, and was kept

* **The file is the source of truth**, it is plain JSON, and it is in git.
* **Validation on the way in.** The sync workflows run on a schedule; a malformed
  entry discovered halfway through a cron run has already created issues.
* **Orphan-closing has a safety rule**: cleanup is skipped entirely when no
  articles load, so a truncated file cannot close every reminder at once. That
  rule is reproduced verbatim in the new sync service, and tested.
* **Drift detection reports rather than edits.** Whether 50 KB of sections counts
  as a draft is a judgement call.
* **Private repositories are expected.** `check_article_drift.py` refuses to
  report when fewer than half the repositories resolve, because that is a token
  scope problem, not fifty deleted repositories. The new activity gateway makes
  the same assumption: an unreachable repository is reported, never raised.

### What was missing

1. **No lifecycle.** Nine flat statuses covering idea to publication, with
   `submitted` doing the work of submitted, under review, and resubmitted.
2. **One global notion of staleness.** `last_updated` more than 60 days behind
   the repository's last push was the only signal, applied identically to a draft
   and to a manuscript sitting with a journal.
3. **`next_action` existed but was empty.** 1 of 65 entries had one. Nothing
   detected that, and nothing asked for one.
4. **No evidence of manuscript progress.** Repository activity was measured as
   "when was this repository last pushed", which a CI tweak satisfies.
5. **No history.** Every write overwrote the previous state; the only record of
   what happened was the git log of the data file.
6. **No interface.** Reading the portfolio meant reading JSON or the issue list.
7. **No package.** Four scripts, no `pyproject.toml`, no linting, no typing, and
   tests that loaded modules through `importlib.util.spec_from_file_location`.

---

## 2. What it is now

```text
src/article_reminders/
├── domain/            model, vocabulary, rules -- imports nothing else
│   ├── enums.py       LifecycleStatus, Priority, BoardColumn, ActivityKind,
│   │                  ReminderKind/Severity, DecisionOutcome, ProjectEventType
│   ├── ids.py         PaperId, Slug, slugify
│   ├── timeutils.py   timezone-aware parsing; naive datetimes are rejected
│   ├── models.py      Paper, NextAction, RepositoryRef, SubmissionRecord,
│   │                  StatusTransition, ActivitySnapshot, Reminder
│   ├── events.py      ProjectEvent
│   ├── rules.py       transitions, staleness, needs-attention, board mapping
│   └── errors.py
│
├── application/       orchestration, behind protocols
│   ├── ports.py       PaperRepository, EventLog, ActivityGateway, IssueGateway, Clock
│   ├── services.py    PortfolioService -- every state change goes through here
│   ├── reminders.py   ReminderEngine
│   ├── activity.py    path classification, stagnation detection, ActivityService
│   ├── github_sync.py IssueSyncService
│   ├── analytics.py   pipeline durations and portfolio counts
│   └── workflow.py    dashboard, board, and calendar read models
│
├── infrastructure/
│   ├── storage/       json_store, event_log, legacy, migration
│   ├── github/        client (stdlib urllib), activity, issues
│   ├── configuration/ typed settings from YAML
│   └── clock.py
│
├── bootstrap.py       the composition root, shared by both interfaces
├── cli/               argparse; formatting only
└── web/               FastAPI + Jinja2, server-rendered
```

Dependency direction is `cli`/`web` → `application` → `domain`, with
`infrastructure` implementing the protocols the application declares. The one
deliberate exception is that `application` imports the settings dataclasses from
`infrastructure.configuration`: they are typed value objects with no I/O, and
threading a second configuration protocol through every service bought nothing.

### The record it keeps now

`data/portfolio.json`, flat keys, empty values omitted:

```json
{
  "version": 2,
  "generated_at": "2026-08-25T09:00:00+00:00",
  "papers": [
    {
      "id": "3f0a1c2b4d5e",
      "title": "Minimum Wage and Productivity in Portuguese Firms",
      "slug": "minimum-wage-and-productivity-in-portuguese-firms",
      "status": "analysis",
      "priority": "high",
      "research_question": "...",
      "repository": "example-lab/minimum-wage-productivity",
      "paper_path": "paper/",
      "next_action": {"description": "...", "due_at": "2026-08-31T00:00:00+00:00"},
      "last_manuscript_activity_at": "2026-07-18T09:00:00+00:00",
      "last_analysis_activity_at": "2026-08-23T09:00:00+00:00",
      "transitions": [...],
      "submissions": [...]
    }
  ]
}
```

`data/events.jsonl` holds the append-only history, one event per line.

---

## 3. Data model changes

| Legacy | Now | Note |
|---|---|---|
| `title` | `title` | unchanged |
| — | `id`, `slug` | id is stable across renames; slug is what you type |
| `repo`, `paper_path` | `repository`, `paper_path`, `repository_provider`, `repository_branch` | a `RepositoryRef` value object |
| `status` (9) | `status` (16) | see the mapping below |
| `priority` | `priority` | unchanged vocabulary |
| `notes`, `abstract` | `notes`, `abstract` | unchanged |
| `venue` | `target_journal`, `target_conference` | `venue` maps to `target_journal` |
| `target_date` | `next_action.due_at` | kept verbatim in `extra` when there is no next action |
| `next_action` | `next_action.description` | now a value object with a deadline |
| `last_updated` | `updated_at` | now a full timestamp |
| — | `research_question`, `description`, `authors`, `corresponding_author`, `tags`, `research_programme`, `waiting_for` | |
| — | `started_at`, `draft_started_at`, `submitted_at`, `decision_received_at`, `revision_due_at`, `accepted_at`, `published_at`, `conference_deadline`, `internal_review_deadline` | |
| — | `doi`, `preprint_url`, `publication_url` | |
| — | `last_repository_activity_at`, `last_manuscript_activity_at`, `last_analysis_activity_at` | observed, not asserted |
| — | `submissions[]`, `transitions[]`, `github_issue_number` | |
| — | `extra` | anything the model does not know about, round-tripped |

### Status mapping

Chosen so that every legacy value survives a round trip, which is what makes
`legacy-export` safe to run on a schedule.

| Legacy | → | Lifecycle | → | Legacy |
|---|---|---|---|---|
| `planned` | | `planned` | | `planned` |
| `in_progress` | | `research` | | `in_progress` |
| `draft` | | `draft` | | `draft` |
| `submitted` | | `submitted` | | `submitted` |
| `revising` | | `revision` | | `revising` |
| `finished` | | `accepted` | | `finished` |
| `published` | | `published` | | `published` |
| `archived` | | `paused` | | `archived` |
| `cancelled` | | `abandoned` | | `cancelled` |

The new statuses with no legacy equivalent fold into the nearest one on export:
`idea`→`planned`, `data_collection`/`analysis`→`in_progress`,
`internal_review`/`ready_to_submit`→`draft`, `under_review`/`resubmitted`→`submitted`.
That direction is lossy, which is fine: the legacy file is a derived artefact,
and `test_every_entry_round_trips_byte_for_byte` pins the direction that matters.

---

## 4. Migration strategy

Three states a repository can be in, and all three work:

1. **Before migrating.** `data/portfolio.json` does not exist, so the repository
   reads `data/articles.json` directly and every read-only feature works: list,
   show, dashboard, board, calendar, analytics, reminders. Ids are derived from
   `sha1(title::repo::paper_path)`, so they are stable across loads and URLs do
   not change. A banner on every web page says this is happening.
2. **Migrating.** `article-reminders migrate` reads the legacy file and writes
   the portfolio. It never modifies `data/articles.json`; it backs up any
   existing portfolio into `data/backups/` before touching it; it matches
   existing papers on title + repository + manuscript path and leaves them alone;
   unknown legacy keys are preserved into `extra` and reported. Running it twice
   is a no-op.
3. **After migrating.** The portfolio is authoritative. `article-reminders
   legacy-export` regenerates `data/articles.json` from it, so
   `project-sync.yml` and `check_article_drift.py` keep working unchanged.

The first write in state 1 (setting a next action, say) creates the portfolio
file from the legacy contents. That is an implicit migration without a backup or
an event, so the scheduled workflow runs `migrate` explicitly first rather than
letting it happen by accident.

---

## 5. Compatibility decisions

**The four scripts stay, byte for byte.** They work, they are tested, and they
depend on nothing but the standard library. `scripts/sync_article_issues.py` is
no longer what runs on a schedule — `article-reminders sync-github` is — but it
remains a working fallback. Linting and typing are scoped around them rather than
let loose on them; rewriting known-good automation to satisfy a tool introduced
afterwards is not an improvement.

**The issue conventions are unchanged.** Same `[article-reminder]` title prefix,
same `article-reminder` label, same orphan-closing rules including the
empty-portfolio guard. The new sync adds a hidden `<!-- article-reminders:id=… -->`
marker, but matches in three ways so that issues created by the old script are
recognised: by marker, by the legacy `Reminder key` in the body, then by title.
`legacy_reminder_key()` reproduces the old slug function exactly. Without this,
a migrated repository would wake up to sixty-five duplicate issues.

**Only one issue synchroniser may be scheduled.** The old workflow was switched
over rather than a second one added: two of them would rewrite the same issue
bodies against each other every week.

**`data/articles.json` keeps its exact shape.** The export emits only the eleven
keys `validate_articles.py` accepts, with the same two-space indent, so the diff
after an export is the content that changed and nothing else.

---

## 6. Design decisions, and what was rejected

### Storage: a second JSON file, not an extended `articles.json`

The new model has roughly thirty-five fields; the legacy validator rejects
unknown keys, and its test asserts the real data file passes. Extending
`articles.json` in place would have meant either weakening that gate or breaking
it. A separate `portfolio.json` with `articles.json` as a derived export keeps
both files valid under their own rules and keeps the old automation fed.

*Rejected:* SQLite. It would buy transactions and queries for a portfolio of
sixty-five records, and cost the property this project actually depends on — that
the data is diffable, reviewable in a pull request, and readable with `cat` if
the application disappears.

*Rejected:* one file per paper. Better diffs on a single paper, worse for every
operation that reads the whole portfolio, which is all of them.

### Storage: JSONL for events

Appending is the only write the event log ever needs, and a corrupted tail costs
one event instead of the whole history.

### Web: FastAPI with server-rendered Jinja2

FastAPI was already installed, gives typed handlers and a free JSON API for the
same data, and needs no build step. Every page is HTML and forms; the only
JavaScript on the site is none.

*Rejected:* a React or HTMX front end. The pages are a dashboard, a list, a
detail view, a board, a calendar, and a settings table. A build toolchain would
add operational complexity without answering any question the dashboard exists to
answer. Kanban drag-and-drop was implemented as a per-card select and a Move
button for the same reason: it works without JavaScript, and it survives a
keyboard.

### CLI: argparse

Zero dependencies, trivially testable through `main(argv)`, and mypy-clean.
Typer would have been prettier at the cost of a dependency the application does
not otherwise need.

### Domain: frozen dataclasses, not Pydantic

The domain is pure standard library, so nothing at the centre depends on a
third-party release cycle. Validation happens in `__post_init__` and in
`from_dict`, which is where the boundary actually is. Pydantic would have
duplicated that work and pulled a dependency into the layer that most wants to
stay dependency-free.

### Rules: transitions are validated but overridable

`CANONICAL_TRANSITIONS` encodes the ordinary path. Anything else raises
`InvalidTransitionError` unless the caller passes `force=True`, and a forced move
is *recorded as forced* so the history says a rule was overridden. Real research
does not walk the graph in order; software that pretends otherwise gets worked
around rather than corrected. Board moves force by default: dragging a card is an
explicit human decision, and silently refusing it would make the board lie.

### Rules: staleness depends on the stage

```text
stale  ⟺  (now − last meaningful activity) > d_stage
```

with `d_draft = 14` and `d_under_review = 180`. A paper waiting on a journal for
45 days is not the same situation as a draft untouched for 45 days, and one
global threshold cannot tell them apart. Every threshold is configurable.

### Rules: who owes a next action

An active paper owes one — unless it is in `submitted`, `under_review`, or
`resubmitted`, or has `waiting_for` set. Demanding a next action from a paper
sitting with an editor trains the researcher to write "wait" sixty-five times,
which destroys the signal the field exists to carry.

### Activity: path-based classification, never commit messages

```yaml
manuscript: [paper/, papers/, manuscript/]
analysis:   [src/, analysis/, notebooks/, scripts/, results/]
data:       [data/, datasets/]
```

A paper's own `paper_path` takes priority over the defaults, and the longest
matching prefix wins, so a manuscript living under `analysis/paper/` is still a
manuscript. Each question is asked of the API with a `path=` filter, so "when was
the last commit touching `paper/`" is one deterministic answer rather than an
inference. Commit-message classification was rejected: `wip` is not evidence.

### The application-specific feature: manuscript stagnation

```text
A_r < 7 days   and   A_m > 30 days   ⟹   the manuscript is stalled
```

where `A_r` is the age of the newest repository *or analysis* activity and `A_m`
the age of the newest manuscript activity. This is the finding that a general
task manager cannot produce, and the one that justifies the application existing:
it distinguishes a project that has stopped from a project whose *writing* has
stopped while the code keeps moving. Both thresholds are configurable, and the
rule refuses to fire when it has no evidence at all rather than guessing.

### Analytics: unknown is a value

Every interval reports `None` when it lacks an endpoint, and every median
reports its sample size. `time_in_current_stage` is empty until papers have
actually moved inside the application, rather than being back-filled from
`last_updated`. Fabricated metrics on a young portfolio are worse than blank
ones, because they get quoted.

### Reminders: structured objects, not strings

`Reminder(project_id, kind, severity, message, created_at, due_at, context)`.
Three interfaces render the same finding — the terminal, the dashboard, and the
GitHub issue body — and severity has to sort rather than be parsed out of prose.

One deliberate suppression: `project_inactivity` is not emitted when
`stage_stale` already fired or when the paper is waiting on someone. They are the
same signal seen through a sharper lens, and emitting all three would teach the
researcher to skim.

---

## 7. Implementation phases

1. **Audit and domain model.** This note, the package skeleton, the typed model,
   the legacy adapter, the migration, and their tests.
2. **Workflow engine.** Lifecycle rules, next actions, stage staleness, the
   reminder engine, the event log.
3. **GitHub activity.** REST client, path-scoped activity reading, stagnation
   detection, issue synchronisation with duplicate prevention.
4. **Web application.** Dashboard, papers, detail, board, calendar, analytics,
   settings, plus the JSON API.
5. **Analytics.** Stage durations, portfolio counts, pipeline statistics.
6. **Hardening.** README, this note, the changelog, CI, the example portfolio,
   and the quality gate (`ruff`, `mypy --strict`, `pytest`).

---

## 8. Known limitations

* **Activity costs one request per watched path prefix.** Fine for sixty-five
  papers on a weekly schedule; a portfolio in the thousands would want the GraphQL
  API or a conditional-request cache.
* **The portfolio is read whole on every operation.** Simplicity over speed at
  this size; `JsonPaperRepository` is a protocol implementation, so a SQLite one
  can replace it without touching a service.
* **No concurrency control.** Two processes writing at once will have one win.
  Writes are atomic (temp file plus `os.replace`), so the file is never truncated,
  but there is no lock.
* **The web application has no authentication.** It is local-first by design and
  binds to `127.0.0.1`. Do not expose it.
* **`time_in_current_stage` only covers papers that moved inside the
  application.** Migrated papers have no transition history until they move.
* **GitHub Projects sync still reads `data/articles.json`.** It was left on the
  legacy path deliberately; porting the GraphQL field mapping into the new model
  is worthwhile but was not required to make the lifecycle work.

## 9. Recommended next version

* An import path for the Projects board, so `sync_project_items.py` can be
  retired in favour of the new model.
* Per-paper activity caching with `If-None-Match`, to cut the request count.
* An `article-reminders review` command for the weekly triage pass: every paper
  needing attention, one at a time, with the next action editable in place.
