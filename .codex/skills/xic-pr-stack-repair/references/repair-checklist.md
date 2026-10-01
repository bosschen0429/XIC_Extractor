# Repair Checklist

## Evidence Relevant To The Failure

- base branch, head branch, and whether the base is already merged or stale;
- repeated global ledgers, active handoffs, artifact inventories, retained
  fixture manifests, and CI checkers;
- tracked fixtures versus ignored local artifacts;
- product behavior owner, artifact hygiene owner, fixture retention owner, and
  final rollup owner;
- whether default CI requires ignored `output/`, `.worktrees/`, or
  `local_validation_artifacts/` bytes.

## Rebuild Guidance

- Rebuild stale branches from the current intended base.
- Cherry-pick only commits that match the PR owner surface.
- Put artifact cleanup in the same PR that needs it, or in an already-merged
  prerequisite.
- Prefer a prerequisite boundary PR or final rollup PR when multiple PRs would
  otherwise edit the same global ledger.
- Use tracked summaries, manifests, row counts, path shape, and hash format for
  default CI; use explicit opt-in flags for ignored local bytes.

## Actions Trigger Triage

When GitHub Actions checks do not appear after a ref update, do not assume the
GitHub API update mechanism itself is the root cause. Check:

- token and event source: GitHub documents that most `GITHUB_TOKEN`-triggered
  events do not create new workflow runs, except `workflow_dispatch` and
  `repository_dispatch`;
- workflow event and activity filters, including branch/path filters;
- PR approval requirements for automation-created or updated PRs;
- whether the workflow supports manual `workflow_dispatch`;
- whether the branch really needs a rewritten-history publish.

Official references:

- `https://docs.github.com/en/actions/concepts/security/github_token`
- `https://docs.github.com/actions/managing-workflow-runs/manually-running-a-workflow`
- `https://docs.github.com/rest/actions/workflows#create-a-workflow-dispatch-event`

Use normal push when history is fast-forward. Use `git push --force-with-lease`
only for an agent-owned PR branch after the user has authorized push, the branch
was intentionally rebuilt, and the expected remote head has been checked.

## Focused Checks

Select the affected checker when artifact-boundary or productization surfaces
are involved; these are not a required suite for every stack repair:

```powershell
.venv\Scripts\python.exe -m scripts.check_productization_state
.venv\Scripts\python.exe -m scripts.check_productization_authority
.venv\Scripts\python.exe -m scripts.check_diagnostics_index
```
