# Implementation report

`article-reminders` 0.1.0 → 0.2.0. Written for whoever reviews this change.

## What changed

The repository was a data file, four scripts, and four scheduled workflows. It is
now a layered Python application with two interfaces over one service layer,
while every one of those scripts and workflows still works.

| Before | After |
|---|---|
| 4 standalone scripts, no package | `src/article_reminders/` with domain / application / infrastructure / cli / web |
| 65 flat records, 11 keys | typed `Paper`, ~35 fields, validated at every boundary |
| 9 statuses | 16 lifecycle states with a validated, overridable transition graph |
| `next_action` set on 1 of 65 entries | a first-class value object, and a reminder when it is missing |
| one global staleness rule | per-stage thresholds, 7 to 180 days, configurable |
| "when was this repository last pushed" | manuscript, analysis, and data activity read separately by path |
| no history | append-only `data/events.jsonl` |
| no interface | a CLI with 20 commands and a 7-page web application |
| no tests beyond the scripts | 346 tests, `ruff` and `mypy --strict` clean |

Run against this repository's real data, the reminder engine's first output was
that 64 of the 65 tracked papers have no next action, and it named the stalled
ones by stage. That was the point of the exercise.

## Architecture decisions

**Layered, with the domain at the centre.** `domain/` imports nothing but the
standard library. `application/` declares protocols (`PaperRepository`,
`EventLog`, `ActivityGateway`, `IssueGateway`, `Clock`) and `infrastructure/`
implements them. `bootstrap.py` is the only module that wires the whole thing
together, and both the CLI and the web application build their world through it,
which is what stops them from becoming two applications.

**Frozen dataclasses, not Pydantic.** Validation belongs at the boundary
(`from_dict`, `__post_init__`), and the layer that holds the rules should not
depend on a third-party release cycle.

**Every datetime is timezone-aware, and naive ones are rejected rather than
coerced.** Deadline arithmetic is the whole product; silent local-time drift
would poison it.

**Transitions are validated but overridable.** A non-canonical move raises unless
the caller forces it, and a forced move is recorded as forced. Software that
refuses what researchers actually do gets worked around, not obeyed.

**Staleness is stage-dependent.** `d_draft = 14`, `d_under_review = 180`. One
global number cannot tell a stalled draft from a manuscript sitting with a
journal, and pretending otherwise makes the whole signal worthless.

**Activity classification is path-based, never message-based.** Each question is
asked of the GitHub API with a `path=` filter, so "when did `paper/` last change"
is one deterministic answer. A paper's own `paper_path` outranks the configured
defaults, and the longest matching prefix wins.

**Server-rendered web, no build step.** FastAPI plus Jinja2 was already
installed; a front-end toolchain would have added operational complexity without
answering any question the dashboard exists to answer. Board moves are a select
and a button, so they work without JavaScript.

**argparse for the CLI.** Zero dependencies, testable through `main(argv)`,
mypy-clean.

The full reasoning, including what was rejected, is in
`docs/research_workflow_app_design.md`.

## Compatibility decisions

1. **The four scripts are untouched, byte for byte**, along with their tests.
   Linting and typing are scoped around them rather than let loose on them.
2. **A second data file rather than an extended one.** The legacy validator
   rejects unknown keys and its test asserts the real file passes; extending
   `articles.json` would have meant weakening or breaking that gate.
   `portfolio.json` is authoritative, `articles.json` is a derived export.
3. **The application works before you migrate.** With no portfolio file, it reads
   `data/articles.json` directly and every read-only feature works. Ids are
   derived from the entry's identity, so they survive the migration and URLs do
   not change.
4. **Issue conventions are identical.** Same title prefix, same label, same
   orphan-closing rules including the empty-portfolio guard. Matching is tried by
   hidden id marker, then by the legacy reminder key, then by title, so a
   migrated repository reuses its sixty-five issues instead of duplicating them.
5. **Only one issue synchroniser is scheduled.** The existing workflow was
   switched over rather than a second one added; two would rewrite each other's
   issue bodies weekly.
6. **`legacy-export` keeps the old automation fed**, so `project-sync.yml` and
   `check_article_drift.py` continue working unchanged.

## Data migrations

`article-reminders migrate` reads `data/articles.json` and writes
`data/portfolio.json`. It never modifies the legacy file; it backs up an existing
portfolio into `data/backups/` first; it matches existing papers on title +
repository + manuscript path and leaves them untouched; it preserves and reports
unrecognised keys; and running it twice changes nothing.

All 65 real entries round-trip byte for byte, asserted directly against
`data/articles.json`, and the exported document is checked against
`scripts/validate_articles.py` in the test suite.

## New commands

```text
list  show  add  update  status  next-action  reminders  dashboard  board
calendar  analytics  submit  decision  events  sync-github  migrate
legacy-export  validate  settings  serve
```

Web routes: `/`, `/papers`, `/papers/{id}`, `/board`, `/calendar`, `/analytics`,
`/settings`, plus `/api/papers`, `/api/reminders`, `/api/dashboard`,
`/api/analytics`, `/api/health`.

## New configuration

Optional `article-reminders.yml` (see `article-reminders.example.yml`): storage
paths, per-stage staleness, reminder thresholds, stagnation thresholds, activity
paths, and GitHub settings. Every value has a working default, so the application
runs in a fresh checkout with no configuration at all.

Environment: `GITHUB_TOKEN` (writes issues here), `ARTICLE_SCAN_TOKEN` (reads the
tracked repositories), `ARTICLE_REMINDERS_ROOT`, `ARTICLE_REMINDERS_CONFIG`,
`ARTICLE_REMINDERS_LOG_LEVEL`.

## Tests added

346 tests, all offline.

| Area | Covers |
|---|---|
| `test_domain_models.py` | validation, invariants, serialisation round-trips, unknown-key preservation, timezone handling |
| `test_domain_rules.py` | the transition graph, next-action obligations, stage staleness, needs-attention |
| `test_services.py` | create, update, status changes, next actions, submissions, decisions, filters, event recording |
| `test_reminders.py` | every reminder kind, severities, thresholds, and the deliberate suppressions |
| `test_activity.py` | path classification, request building, stagnation, the sync loop, per-repository caching, failure isolation |
| `test_github.py` | client, activity gateway, issue gateway, matching, bodies, labels, sync outcomes, duplicate prevention |
| `test_analytics.py` | durations, missing endpoints, medians, sample sizes, unavailable metrics |
| `test_migration.py` | status mapping, legacy reads, the real data file, migration idempotence, backups, export |
| `test_cli.py` | every command through `main()`, plus formatting |
| `test_web.py` | every page, every form, the JSON API |
| `test_settings.py` | defaults, overrides, validation, the shipped example |

External calls go through a fake transport (`FakeTransport`) or a fake gateway;
nothing touches the network. Time is injected through a `FixedClock`, so no test
depends on today's date.

## Two CI failures on main, and what they were

`Validate` failed on 2026-08-24 22:20 (`test_close_orphaned_issues_closes_unclaimed_issue`
asserted `/repos/None/issues/4`, because `REPOSITORY` is read from
`GITHUB_REPOSITORY` at import time and Actions sets it). Already fixed on main by
`6425bff` three minutes later; this branch inherits the fix.

`Dependabot Updates (pip)` failed on 2026-08-24 03:09 with
`dependency_file_not_found: No files found in /`, and had been failing every
Monday since the config was written: the `pip` ecosystem was enabled at `/` while
the repository had no Python manifest. Adding `pyproject.toml` is the fix, and
`.github/dependabot.yml` now says so instead of carrying the stale
"uncomment this if you add a pyproject.toml" comment.

The dev dependencies are declared with lower bounds, so CI resolves to whatever
is current. That is deliberate but it does drift: `poetry install` resolves
`mypy>=1.11` to 2.3.1, which infers `dict[Literal[...], Any]` where 1.x inferred
`dict[str, Any]` and rejects it against an invariant `Mapping[str, Any]`. The one
site that hit is annotated explicitly, and the gate is verified clean under
mypy 1.20.2 and 2.3.1, and ruff 0.16.1 and 0.16.4. If that drift becomes tiresome,
commit a `poetry.lock` and let Dependabot bump it.

## Known limitations

* Activity costs one request per watched path prefix. Fine weekly at this size;
  a portfolio in the thousands would want GraphQL or conditional requests.
* The portfolio is read whole on every operation. `JsonPaperRepository` is a
  protocol implementation, so a SQLite one can replace it without touching a
  service.
* No concurrency control. Writes are atomic, so the file is never truncated, but
  two simultaneous writers means one wins.
* The web application has no authentication and binds to localhost. Do not
  expose it.
* `time_in_current_stage` only covers papers that have moved inside the
  application; migrated papers have no transition history until they move.
* The GitHub Projects sync still reads `data/articles.json` — deliberately left
  on the legacy path, and fed by `legacy-export`.
* The weekly workflow commits refreshed activity data back to `main`. That is how
  observed activity persists between runs; if you would rather it did not, drop
  the final step and accept that activity is recomputed each time.

## Recommended next version

1. Port the GitHub Projects field mapping onto the new model so
   `sync_project_items.py` can be retired.
2. Cache activity requests with `If-None-Match` to cut the request count.
3. An `article-reminders review` command for the weekly triage pass: every paper
   needing attention, one at a time, with the next action editable in place.
4. Recompute lifecycle timestamps for migrated papers from repository history, so
   the analytics have something to measure on day one.
