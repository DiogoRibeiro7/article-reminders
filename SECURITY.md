# Security Policy

## Supported versions

Fixes are made on `main` and released forward. Only the latest version is supported.

## Reporting a vulnerability

**Do not open a public issue.**

Report privately through GitHub's
[private vulnerability reporting](https://github.com/DiogoRibeiro7/article-reminders/security/advisories/new),
or contact the maintainer through the address on the
[GitHub profile](https://github.com/DiogoRibeiro7).

Expect an acknowledgement within seven days and an assessment within thirty.

## What the threat model actually is

This application runs locally and in scheduled GitHub Actions workflows. It has no server of its
own, no user accounts and no database. What it does have is **a token with write access to
issues and to this repository**, and a set of workflows that act on file contents. That is where
the risk lives, and the following are in scope:

- **Token exposure.** `GITHUB_TOKEN` and `ARTICLE_SCAN_TOKEN` must never be logged, written into
  a file, committed, interpolated into an issue body, or sent anywhere but the GitHub API. Any
  path that prints or persists one is a vulnerability.
- **Privilege escalation through a workflow.** `article-reminders.yml` holds `contents: write`
  and `issues: write`; `project-sync.yml` holds `repository-projects: write`. Any change that
  lets an untrusted contributor reach those permissions — most obviously swapping a
  `pull_request` trigger for `pull_request_target`, or running unreviewed code from a fork in a
  privileged job — is a vulnerability.
- **Injection through portfolio data.** Titles, notes, abstracts and next actions flow from
  `data/*.json` into issue bodies, Markdown, GraphQL mutations and the web templates. A crafted
  value that escapes its context — breaks out of a template, forges Markdown structure in an
  issue, or alters a GraphQL query — is a vulnerability.
- **Path traversal through configuration.** `article-reminders.yml` supplies storage paths and
  `$ARTICLE_REMINDERS_ROOT` supplies a root. A configuration that causes a read or write outside
  the intended root is a vulnerability.
- **Destructive storage behaviour.** Backups are written before any overwrite. A path that
  overwrites `portfolio.json` or truncates `events.jsonl` without one, or that loses events on a
  concurrent run, is a vulnerability even though nothing is "exploited".

## What does not count

- Anything requiring write access to the repository or to the local filesystem you already have.
- GitHub API rate limits, and reminders that a token with `repo` scope can do a lot — that is the
  point of the token.
- Dependency advisories with no demonstrated path to impact here; Dependabot tracks those and
  they are handled as routine maintenance.
- Unauthenticated behaviour being limited. Without a token, observed activity and issue sync are
  simply unavailable, which is by design.

## A note on the data

`data/` holds paper titles, abstracts and notes, including for unpublished work. It is not
secret material and this application does not treat it as such — but if this repository is
public, everything in `data/` is public too. That is a deliberate trade for portability, and
worth being deliberate about: if an abstract should not be readable yet, it does not belong in a
tracked file. If unpublished material has been committed that should not have been, use the
private channel above rather than opening an issue that quotes it.
