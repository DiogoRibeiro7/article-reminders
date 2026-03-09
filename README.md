# research-tracker

Public tracker for article repositories with GitHub Issues, GitHub Projects, and scheduled GitHub Actions.

## What this repo does

This repository treats **one GitHub issue as one article**. A scheduled workflow reads `data/articles.json`, creates or updates article issues, and then syncs those issues into a GitHub Project. GitHub Projects supports custom fields such as single select, text, date, and iteration, and GitHub supports both built-in Project automations and GraphQL-based automation from Actions. citeturn0search19turn0search7turn0search1turn0search0

The default setup in this repo supports:

- one issue per article
- a fixed set of workflow statuses
- scheduled issue reminders
- GitHub Project sync for article issues
- Project fields for status, priority, repo, venue, target date, and next action

## Repository layout

```text
.github/
  ISSUE_TEMPLATE/
    article.yml
  workflows/
    article-reminders.yml
    project-sync.yml
scripts/
  sync_article_issues.py
  sync_project_items.py
data/
  articles.json
```

## Required project fields

Create a GitHub Project named `Research Tracker` and add these fields:

- **Status** (single select)
  - Backlog
  - Planned
  - Reading
  - Writing
  - Experiments
  - Revising
  - Submitted
  - Camera-ready
  - Done
  - Blocked
  - Archived
- **Priority** (single select)
  - Low
  - Medium
  - High
  - Critical
- **Repo** (text)
- **Venue** (text)
- **Target date** (date)
- **Next action** (text)

GitHub Projects supports custom fields including single select, text, date, and iteration fields, and Projects can be automated with Actions and the GraphQL API. citeturn0search19turn0search20turn0search1turn0search0

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

GitHub supports built-in auto-add and status automations directly in Projects. citeturn0search2turn0search4

## Source of truth

The source of truth is `data/articles.json`.

Example:

```json
[
  {
    "title": "Uncertainty and Calibration Under Shift, Noise, and Autocorrelation: A Simulation Benchmark",
    "repo": "DiogoRibeiro7/uncertainty-bench",
    "status": "Experiments",
    "priority": "High",
    "venue": "",
    "target_date": "2026-03-20",
    "next_action": "Regenerate tables and figures from latest aggregated metrics.",
    "notes": "Current experiments are running on the medium grid."
  }
]
```

## Important rule

Use controlled values in `status`:

- Backlog
- Planned
- Reading
- Writing
- Experiments
- Revising
- Submitted
- Camera-ready
- Done
- Blocked
- Archived

Do not use free-text statuses such as `experiments running`. Put that richer description in `notes` or `next_action` instead.

## How the automation works

### `article-reminders.yml`

- runs on a schedule and on manual dispatch
- reads `data/articles.json`
- creates or updates one issue per article
- closes issues for articles marked `Done` or `Archived`

### `project-sync.yml`

- runs after issue sync, on issue events, and on manual dispatch
- finds each article issue
- adds the issue to the configured GitHub Project if missing
- updates Project fields using the GraphQL API

GitHub documents GraphQL mutations for adding items to Projects and updating field values, and also documents how to automate Projects from Actions. citeturn0search0turn0search1turn0search20
