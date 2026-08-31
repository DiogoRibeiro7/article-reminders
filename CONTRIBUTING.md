# Contributing

This is a small application with strong opinions about where things live. Most of what
follows is about keeping those opinions intact; the rest is the usual.

## Setup

Requires Python 3.11 or newer and [Poetry](https://python-poetry.org/) 2.0 or newer.

```bash
poetry install --with dev
poetry run pre-commit install
```

## The checks

Everything CI runs, in the order it runs it:

```bash
poetry run ruff check .
poetry run mypy .
poetry run python scripts/validate_articles.py     # the legacy tracker
poetry run article-reminders validate              # the portfolio
poetry run python examples/build_seed.py && git diff --exit-code examples/
poetry run pytest
```

The example portfolio is deterministic — same input, same bytes — and the diff check is what
keeps it that way. If `build_seed.py` produces a diff you did not intend, something in the model
gained an unstable ordering or a timestamp.

## Where code goes

```text
domain/          the model and the rules; imports nothing else
application/     services, reminders, activity, analytics, views
infrastructure/  JSON storage, GitHub, configuration
cli/             argparse interface
web/             FastAPI + Jinja2, server-rendered
```

Dependencies point inwards: `cli` and `web` → `application` → `domain`, with `infrastructure`
implementing the protocols the application declares in `ports.py`.

Two consequences worth stating plainly:

- **A rule lives in exactly one place.** If the CLI and the web interface disagree about when a
  paper is stalled, the rule is in the wrong layer. Put it in `domain` or in a service and let
  both interfaces call it.
- **`domain` imports nothing from this package.** Not the storage, not the settings, not the
  GitHub client. If a domain rule seems to need one of them, it needs a value passed in instead.

## Time

The test fixtures run on a frozen clock — `conftest.NOW`, fixed at 2026-08-25 — but **the CLI
builds its own application on the system clock**. A literal future date handed to a CLI command
in a test is a fuse: correct when written, in the past some weeks later, and then the suite
fails on a calendar day rather than on a change. `tests/test_cli.py` has a `real_days_ahead`
helper for exactly this; use it rather than writing a date.

The same applies to fixtures that must stay a fixed distance from `NOW`: use `days_ago` and
`days_ahead` from `conftest`, not literals.

## Data

`data/portfolio.json` is the source of truth, `data/events.jsonl` is append-only history, and
`data/articles.json` is the legacy tracker that is still read and still written. All three are
plain text on purpose: if this application disappears, the portfolio is still readable.

- Adding or updating a paper is usually a direct edit to `data/articles.json`. A JSON round-trip
  with `indent=2` and `ensure_ascii=False` reproduces the file byte-for-byte, so a scripted edit
  leaves everything else alone.
- Never hand-edit `data/events.jsonl`. It is history; append through the services or not at all.
- The `validate-portfolio` pre-commit hook runs on any change under `data/`. If it fails, the
  file is wrong, not the hook.

## The legacy scripts

`scripts/sync_article_issues.py`, `sync_project_items.py`, `validate_articles.py` and
`check_article_drift.py` predate the package. They are kept verbatim, they still work, and both
`ruff` and `mypy` are scoped around them rather than let loose on them. Do not rewrite them to
satisfy a tool that arrived after they did; if one needs to change, change the behaviour that is
wrong and leave the style alone.

**Do not enable both issue synchronisers.** The workflow and the script use the same
`[article-reminder]` title prefix and the same label; running both means they rewrite each
other's issues every week.

## Pull requests

- One concern per pull request.
- Conventional prefixes on the subject line — `fix:`, `chore:`, `feat:`, `docs:` — in the
  imperative mood, describing the effect rather than the edit.
- New behaviour comes with a test. New rules come with a test in the layer that owns the rule.
- A user-visible change gets a `CHANGELOG.md` entry under `Unreleased`.

## Reporting problems

Open an issue. For anything involving a token, a workflow permission, or data that should not
have been published, follow [`SECURITY.md`](SECURITY.md) instead.

## Licence

Contributions are accepted under the [MIT Licence](LICENSE).
