# research-tracker

Public tracker for article repositories with GitHub Issues, GitHub Projects, and scheduled GitHub Actions.

## What this repo does

This repository treats **one GitHub issue as one article**. A scheduled workflow reads `data/articles.json`, creates or updates article issues, and then syncs those issues into a GitHub Project. GitHub Projects supports custom fields such as single select, text, date, and iteration, and GitHub supports both built-in Project automations and GraphQL-based automation from Actions.

The default setup in this repo supports:

- one issue per article
- a fixed set of workflow statuses
- scheduled issue reminders
- GitHub Project sync for article issues
- Project fields for status, priority, repo URL, venue, target date, and next action

## Repository layout

```text
.github/
  ISSUE_TEMPLATE/
    article.yml
  workflows/
    article-reminders.yml
    project-sync.yml
    validate.yml
    drift-check.yml
scripts/
  sync_article_issues.py
  sync_project_items.py
  validate_articles.py
  check_article_drift.py
data/
  articles.json
```

## Required project fields

Create a GitHub Project named `Research Tracker` and add these fields:

- **Status** (single select)
  - Backlog — planned; work not started
  - Experiments — code, data, and experiments in progress
  - Drafting — manuscript being written
  - Review — submitted or under revision
  - Done — finished, published, or archived
- **Priority** (single select)
  - Low
  - Medium
  - High
  - Critical
- **Repo URL** (text)
- **Venue** (text)
- **Target date** (date)
- **Next action** (text)

GitHub Projects supports custom fields including single select, text, date, and iteration fields, and Projects can be automated with Actions and the GraphQL API.

## Required labels

Create these labels in the repository:

- `type:article`
- `priority:low`
- `priority:medium`
- `priority:high`
- `priority:critical`
- `status:blocked`

## Secrets and variables

Set these repository variables:

- `PROJECT_OWNER` — the GitHub login or organization that owns the Project
- `PROJECT_NUMBER` — the Project number, not the title

Optional secret:

- `PROJECT_TOKEN` — a fine-grained or classic token with permission to read/write issues and projects

The workflows fall back to the default `GITHUB_TOKEN`, but for some Project automation setups a dedicated token is safer.

## Recommended built-in Project automation

In the Project UI, enable built-in automation to:

- auto-add issues with label `type:article`
- set status when an item is added
- mark items as done when an issue is closed

GitHub supports built-in auto-add and status automations directly in Projects.

## Source of truth

The source of truth is `data/articles.json`.

Example:

```json
{
  "articles": [
    {
      "title": "Uncertainty and Calibration Under Shift, Noise, and Autocorrelation: A Simulation Benchmark",
      "repo": "DiogoRibeiro7/uncertainty-bench",
      "status": "in_progress",
      "notes": "Current experiments are running on the medium grid.",
      "paper_path": "paper/",
      "priority": "high",
      "last_updated": "2026-03-07",
      "venue": "",
      "target_date": "2026-03-20",
      "next_action": "Regenerate tables and figures from latest aggregated metrics."
    }
  ]
}
```

`title`, `repo`, `status`, `notes`, `paper_path`, `priority`, and `last_updated` are
required. `venue`, `target_date`, and `next_action` are optional and feed the Project
columns of the same name; leave them out and those columns stay blank. Any other key
is rejected by `validate.yml`, because the sync scripts read none of them and it would
sit in the file doing nothing.

## Article status mapping

The `status` field in `articles.json` is mapped to the Project Status field as follows:

| `articles.json` status | Project Status |
|---|---|
| `planned` | Backlog |
| `in_progress` | Experiments |
| `draft` | Drafting |
| `submitted` | Review |
| `revising` | Review |
| `finished` | Done |
| `published` | Done |
| `archived` | Done |
| `cancelled` | Done |

Use the left-hand values only. A Project Status name such as `Experiments` sorts onto
the board correctly but is not a status `sync_article_issues.py` recognises, so the
article is skipped and never gets a reminder issue at all. The same goes for free text
such as `experiments running`; put that richer description in `notes` or `next_action`.
`validate.yml` rejects both.

## How the automation works

### `article-reminders.yml`

- runs on a schedule and on manual dispatch
- reads `data/articles.json`
- creates or updates one issue per article
- closes issues for articles marked `Done` or `Archived`
- closes orphaned issues: any reminder whose article has been deleted from
  `data/articles.json` outright, rather than moved to a closed status

Only issues the workflow owns are eligible for orphan cleanup — they must carry
the `article-reminder` label and the `[article-reminder]` title prefix — and the
cleanup is skipped entirely when no articles load, so a truncated or malformed
data file cannot close every reminder at once. Deleting an article is therefore
a safe way to retire it; the issue closes on the next run and reopens if the
entry comes back.

### `validate.yml`

- runs on every push to `main`, every pull request, and manual dispatch
- validates `data/articles.json`: required keys, no unknown keys, known status and
  priority vocabulary, `YYYY-MM-DD` dates, unique titles, unique repo + `paper_path`
- runs the test suite

The sync workflows run on a schedule and treat this file as the source of truth, so
without this gate a malformed entry surfaces midway through a cron run that has
already created or edited issues.

### `drift-check.yml`

- runs monthly and on manual dispatch
- compares every entry against its actual repository and reports the disagreements
  in a single `[article-drift]` issue, which closes automatically once nothing drifts

It reports rather than edits: whether 50 KB of sections counts as a draft is a
judgement call. It flags repositories that no longer resolve, `paper_path` values
that no longer exist, statuses contradicted by how much manuscript source is
actually committed, and a `last_updated` more than 60 days behind the repository's
last push.

Requires an `ARTICLE_SCAN_TOKEN` secret with read access to the tracked
repositories. Most of them are private and the default `GITHUB_TOKEN` is scoped to
this repository alone, so without it every lookup returns 404; the workflow skips
with a notice rather than reporting a tracker full of deleted repositories. The
scan token only reads — the report is written with the workflow's own
`GITHUB_TOKEN`.

### `project-sync.yml`

- runs after issue sync, on issue events, and on manual dispatch
- finds each article issue
- adds the issue to the configured GitHub Project if missing
- updates Project fields using the GraphQL API

GitHub documents GraphQL mutations for adding items to Projects and updating field values, and also documents how to automate Projects from Actions.
