# Issue Tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.
This file owns GitHub mechanics only. What an Issue and PR contain is in
[`delivery.md`](delivery.md).

## Conventions

Use `gh --help` for command syntax and `--body-file` for multiline text. Read
only the fields or comments needed for the decision; paginate when an exhaustive
inventory is required. A limited issue list is not a complete census.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

GitHub shares one number space across issues and PRs, so a bare `#42` may be either — resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Read the selected issue as above. Include comments when they carry the current
scope, acceptance, or evidence needed for the task.

## Wayfinding operations

Used by `/wayfinder`. Wayfinder children resolve decisions; they do not become
implementation work by receiving a task label. When the route is clear, keep
the same parent issue and open implementation children under it, each with its
own outcome and acceptance. A child carrying an
implementation contract is excluded from the Wayfinder frontier even when a
legacy `wayfinder:*` label remains.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Decision ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** are the canonical, UI-visible schedule gate. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed. Native blocking does not define an implementation handoff.
- **Frontier query**: list only open decision children with a `wayfinder:<type>` label and `## Question` body (`gh issue list --state open`, scoped to the map's sub-issues / task list); exclude children carrying an implementation contract, then drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee. First in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve decision ticket**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far. Implementation children close when their acceptance is met ([`delivery.md`](delivery.md#closing)).
