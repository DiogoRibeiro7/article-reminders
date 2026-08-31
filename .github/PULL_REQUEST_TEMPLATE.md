## What this changes

<!-- One paragraph. The effect, not a list of edited files. -->

Closes #

## Checks

- [ ] `poetry run ruff check .` and `poetry run mypy .` pass.
- [ ] `poetry run pytest` passes.
- [ ] Both validators pass: `scripts/validate_articles.py` and `article-reminders validate`.
- [ ] `examples/build_seed.py` still produces no diff.

## If it touches the model or the rules

- [ ] The rule lives in exactly one place, and both the CLI and the web interface reach it
      through the same service.
- [ ] `domain` still imports nothing else from this package.
- [ ] New behaviour has a test in the layer that owns it.

## If it touches tests

- [ ] No literal date is handed to a CLI command. The CLI runs on the system clock, so a date
      written today is in the past in a month; use `real_days_ahead`, or `days_ago`/`days_ahead`
      against the frozen `NOW` for fixtures.

## If it touches `data/`

- [ ] The change was made through the services or as a byte-stable JSON edit.
- [ ] `data/events.jsonl` was appended to, never rewritten.

## If it touches a workflow

- [ ] No new write permission, and no `pull_request_target`.
- [ ] The two issue synchronisers are still not both enabled.
