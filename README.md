# article-reminders

[![Validate](https://github.com/DiogoRibeiro7/article-reminders/actions/workflows/validate.yml/badge.svg)](https://github.com/DiogoRibeiro7/article-reminders/actions/workflows/validate.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Lint](https://img.shields.io/badge/lint-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Typing](https://img.shields.io/badge/mypy-strict-1f5082.svg)](https://mypy-lang.org/)

A research workflow application for the operational side of writing papers:
which paper needs attention, why, and what the next concrete piece of work is.

```text
idea → research → analysis → draft → submission → revision → publication
```

It runs locally, keeps its data in plain files you can read without it, and uses
GitHub as evidence rather than as a database.

---

## What problem it solves

A researcher with a dozen papers in flight loses track of the operational layer,
not the intellectual one. Which manuscript has not moved in six weeks. Which
revision is due next Friday. Which paper is generating commits every day while
its actual text has not changed since July. Which one is waiting on a co-author
and which one is waiting on nobody at all.

This application answers exactly those questions, and one more that a task list
cannot:

> The analysis is moving. Is the manuscript?

Because it knows which repository each paper lives in and which path inside it is
the manuscript, it can tell the difference between a project that is progressing
and a project that is merely busy.

## What it is not

Not a reference manager (Zotero), not a general project manager (Notion), not a
manuscript editor (Overleaf). It does not store PDFs, chase citations, or write
prose. It tracks the lifecycle of papers, their repositories, their deadlines,
their next actions, and their publication progress. That narrowness is the point:
every field exists because a research workflow needs it.

Deliberately absent: PDF annotation, reference management, citation graphs,
automatic literature review, co-editing, LLM-generated text, institutional login,
multi-tenancy, payments.

---

## Architecture

```text
src/article_reminders/
├── domain/          the model and the rules; imports nothing else
├── application/     services, reminders, activity, analytics, views
├── infrastructure/  JSON storage, GitHub, configuration
├── cli/             argparse interface
└── web/             FastAPI + Jinja2, server-rendered
```

Dependencies point inwards: `cli` and `web` → `application` → `domain`, with
`infrastructure` implementing the protocols the application declares. Both
interfaces call the same services, so a rule exists in exactly one place.

Storage is three plain files:

| File | What it is |
|---|---|
| `data/portfolio.json` | the source of truth |
| `data/events.jsonl` | append-only history, one event per line |
| `data/articles.json` | the legacy tracker, still read and still written |

`docs/research_workflow_app_design.md` records why each of these decisions went
the way it did.

---

## Installation

Requires Python 3.11 or newer.

```bash
poetry install            # or: pip install .
poetry run article-reminders --help
```

## First run

In a fresh repository there is nothing to migrate and nothing to configure:

```bash
article-reminders add "My First Paper" --status idea
article-reminders serve
```

In this repository, where `data/articles.json` already exists, everything works
before you migrate — the application reads the legacy tracker directly and says
so on every page. When you are ready:

```bash
article-reminders migrate           # writes data/portfolio.json
article-reminders migrate --dry-run # to see what it would do first
```

The migration never modifies `data/articles.json`, backs up any existing
portfolio into `data/backups/` first, preserves keys it does not recognise, and
does nothing at all the second time you run it.

Try the shipped example portfolio without touching your own data:

```bash
article-reminders --root examples dashboard
article-reminders --root examples serve
```

## Adding a paper

```bash
article-reminders add "Minimum Wage and Productivity in Portuguese Firms" \
  --status analysis \
  --priority high \
  --repo example-lab/minimum-wage-productivity \
  --paper-path paper/ \
  --journal "Labour Economics" \
  --tags labour,portugal \
  --next-action "Run the robustness specification on the revised OECD vintage" \
  --due 2026-08-31
```

`--repo` and `--paper-path` are what make activity detection possible: the first
says where the work lives, the second says which part of it is the manuscript.

## Setting next actions

Every active paper should have exactly one, and the application treats a missing
one as a workflow problem rather than an empty field.

```bash
article-reminders next-action                      # every paper's next action
article-reminders next-action minimum-wage         # just this one
article-reminders next-action minimum-wage "Rebuild the tables" --due 2026-09-04
article-reminders next-action minimum-wage --done --then "Write the discussion"
article-reminders next-action minimum-wage --clear
```

Papers in `submitted`, `under_review`, or `resubmitted`, and any paper with
`waiting_for` set, are exempt: the ball is in someone else's court and demanding
a next action from them would only train you to write "wait" over and over.

## Moving through the lifecycle

Sixteen states, from `idea` to `published`, plus `paused` and `abandoned`:

```bash
article-reminders status minimum-wage draft
article-reminders submit minimum-wage "Labour Economics"
article-reminders decision minimum-wage major_revision --revision-due 2026-09-12
article-reminders status old-paper published --force --note "published elsewhere"
```

Transitions outside the canonical path are refused unless you pass `--force`, and
a forced move is recorded as forced. Research does not walk the graph in order,
so the rule bends — but it says so afterwards.

## Configuring GitHub

GitHub is optional. Without it the dashboard, board, calendar, analytics, and
every deadline and workflow reminder still work; what you lose is observed
repository activity and issue synchronisation.

```bash
export GITHUB_TOKEN=...          # writes reminder issues in this repository
export ARTICLE_SCAN_TOKEN=...    # reads the tracked research repositories
article-reminders sync-github
```

Two tokens because they have different jobs: most tracked repositories are
private and live outside this one, so the scan token needs read access there and
write access nowhere.

Tell it which paths mean what in `article-reminders.yml` (copy
`article-reminders.example.yml`):

```yaml
activity_paths:
  manuscript: [paper/, papers/, manuscript/]
  analysis:   [src/, analysis/, notebooks/, results/]
  data:       [data/, datasets/]
```

Classification is path-based and deterministic. A commit that touches `paper/` is
manuscript work; a commit whose message says "writing" but touches `src/` is not.

### Issues

`article-reminders sync-github --only issues` maintains one issue per active
paper, using the same `[article-reminder]` prefix and `article-reminder` label
this repository has always used, so issues created by the older script are
reused rather than duplicated. Issues carry `research-paper` plus, optionally,
`needs-action`, `stalled`, `submission`, and `revision`.

The portfolio file stays authoritative. Issues are a notification channel.

## Running reminders

```bash
article-reminders reminders                       # everything
article-reminders reminders --severity critical   # only what is on fire
article-reminders reminders --json                # for scripting
article-reminders reminders --exit-code           # exit 2 if anything is found
```

Reminders come in three families:

* **Deadlines** — next actions, revision deadlines, conference deadlines,
  internal review deadlines. Overdue is critical; a revision due within a week is
  critical; anything else inside the window is a warning.
* **Inactivity** — stage-aware staleness, a frozen manuscript, a quiet
  repository, a project nothing has touched.
* **Workflow** — an active paper with no next action, a paper marked ready to
  submit with nowhere to submit it, a draft with no manuscript activity ever
  detected, and analysis moving while the manuscript does not.

## Interpreting staleness

Staleness is measured against the stage, not against a single global number:

```text
stale  ⟺  (now − last meaningful activity) > d_stage
```

| Stage | Days | Stage | Days |
|---|---|---|---|
| idea | 90 | ready_to_submit | 14 |
| planned | 60 | submitted | 120 |
| research | 30 | under_review | 180 |
| data_collection | 21 | revision | 7 |
| analysis | 21 | resubmitted | 120 |
| draft | 14 | accepted | 60 |
| internal_review | 21 | | |

A draft untouched for 45 days is a problem. A manuscript sitting with a journal
for 45 days is a Tuesday. All of these are configurable.

### Manuscript stagnation

The finding this application exists for:

```text
A_r < 7 days   and   A_m > 30 days   ⟹   "Analysis remains active, but the
                                           manuscript has not changed for 36 days."
```

where `A_r` is the age of the newest repository or analysis activity and `A_m`
the age of the newest manuscript activity. Both thresholds are configurable, and
the rule stays quiet when it has no evidence rather than guessing.

## The web application

```bash
article-reminders serve                  # http://127.0.0.1:8000
article-reminders serve --port 9000
```

| Route | Page |
|---|---|
| `/` | portfolio dashboard: buckets, counts, and what to work on next |
| `/papers` | every paper, filterable by stage, priority, tag, and text |
| `/papers/{id}` | one paper: warnings, next action, timeline, history, submissions |
| `/board` | lifecycle Kanban, ten columns |
| `/calendar` | every dated commitment, by month |
| `/analytics` | pipeline durations and portfolio counts |
| `/settings` | the resolved configuration |

There is also a small JSON API — `/api/papers`, `/api/papers/{id}`,
`/api/reminders`, `/api/dashboard`, `/api/analytics`, `/api/health` — and
interactive docs at `/api/docs`.

Server-rendered, no build step, no JavaScript. It binds to localhost and has no
authentication; it is not built to be exposed.

## Analytics

```bash
article-reminders analytics
article-reminders analytics --json
```

Median time from idea to draft, draft to submission, submission to decision,
revision to resubmission, acceptance to publication, and idea to publication,
each with the sample size it rests on. Plus counts of active, stalled, submitted,
accepted, published, paused, and abandoned papers, and an acceptance rate.

Intervals without enough history report *not enough data* rather than a number.
A median over one paper is not a median.

## Migrations

```bash
article-reminders migrate --dry-run   # report only
article-reminders migrate             # data/articles.json → data/portfolio.json
article-reminders legacy-export       # data/portfolio.json → data/articles.json
article-reminders validate            # the portfolio loads and is internally consistent
```

`legacy-export` is what keeps the older automation working: `project-sync.yml`
and `check_article_drift.py` still read `data/articles.json`, and the scheduled
workflow regenerates it after every run. Both commands back up what they are
about to overwrite into `data/backups/`.

Every one of this repository's 65 legacy entries round-trips through the new
model byte for byte, and the test suite asserts it against the real file. The
first `legacy-export` does reorder `data/articles.json`: the portfolio is stored
sorted by title, so the export follows that order. No record changes, and the
order is stable from then on.

### The legacy tracker format

`data/articles.json` keeps the shape it has always had, and `legacy-export`
emits exactly the keys `scripts/validate_articles.py` accepts:

```json
{
  "articles": [
    {
      "title": "Minimum Wage and Productivity in Portuguese Firms",
      "repo": "example-lab/minimum-wage-productivity",
      "status": "in_progress",
      "notes": "Robustness specification running on the revised vintage.",
      "abstract": "Matched employer-employee panel around the statutory minimum wage increases.",
      "paper_path": "paper/",
      "priority": "high",
      "last_updated": "2026-08-25",
      "venue": "Labour Economics",
      "target_date": "2026-08-31",
      "next_action": "Run the robustness specification on the revised OECD vintage"
    }
  ]
}
```

`title`, `repo`, `status`, `notes`, `paper_path`, `priority`, and `last_updated`
are required; `abstract`, `venue`, `target_date`, and `next_action` are optional;
anything else is rejected. The nine legacy statuses map onto the sixteen
lifecycle states and back again without loss — the table is in
`docs/research_workflow_app_design.md`.

## The scheduled workflows

| Workflow | When | What |
|---|---|---|
| `validate.yml` | every push and PR | ruff, mypy, both validators, the example build, pytest |
| `article-reminders.yml` | weekly | migrate, refresh activity, export, sync issues, commit |
| `project-sync.yml` | after reminders | issues into the GitHub Project, via GraphQL |
| `drift-check.yml` | monthly | compares entries against the real repositories |

`scripts/sync_article_issues.py` still exists and still works; it is simply no
longer the thing that runs on a schedule. **Do not enable both issue
synchronisers** — they would rewrite each other's issue bodies every week.

## Development

```bash
poetry install --with dev
poetry run ruff check .
poetry run mypy .
poetry run pytest
poetry run pre-commit install
```

`mypy` runs strict over `src`; the four legacy scripts and their tests are
checked under relaxed rules rather than rewritten, because they work and they
predate the tooling.

To regenerate the example portfolio after changing the model:

```bash
poetry run python examples/build_seed.py
```

It is deterministic — same input, same bytes — and CI checks that it still is.

## The five ideas this is built on

1. **Research first.** The vocabulary is stages, venues, submissions, and
   revisions, not tickets and sprints.
2. **One next action.** An active paper makes the next concrete piece of work
   obvious, or it is a workflow problem.
3. **Evidence over guesses.** Commit paths and recorded timestamps are evidence
   of activity. Nothing here claims they are evidence of thinking.
4. **Data portability.** Plain JSON in git. If this application disappears, the
   portfolio is still readable and still recoverable.
5. **GitHub-enhanced, not GitHub-dependent.** Everything except observed activity
   and issue sync works with no token at all.

---

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) covers where code goes, why `domain` imports nothing, how
the deterministic example seed is checked, and the one thing that has actually broken this
repository twice: the tests run on a frozen clock while the CLI runs on the system clock, so a
literal date in a CLI test is a fuse with a few weeks on it.

## Security

Tokens and workflow permissions are the whole threat model here — the application has no server
and no accounts, but it does hold a token that can write issues and commit to this repository.
[`SECURITY.md`](SECURITY.md) says what counts and how to report it privately. Not in a public
issue.

## Citation

[`CITATION.cff`](CITATION.cff); GitHub renders it as APA and BibTeX from the sidebar.

## Licence

[MIT](LICENSE).
