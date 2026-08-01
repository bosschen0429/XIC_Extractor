# XIC handoffs

這裡只放開發交接文件，不放產品 spec、正式 validation note、或 release
contract。handoff 的用途是讓下一個 agent/session 快速知道最近在做什麼、
什麼真的可用、什麼還沒收掉、下一步先做哪裡。

## Default Workflow

- `current/ACTIVE.local.md`: recommended local active handoff name. It is
  ignored by git, may be overwritten during branch work, and can point to
  private Obsidian context.
- Branch-specific local names such as `current/<branch-slug>-<topic>.md` are
  also ignored by default. Use them only when multiple local branches need
  simultaneous handoff state.
- Completed branch diaries, review rationale, and long phase history should
  move to Obsidian. Local archive drafts under `archive/` are ignored and should
  not become the default public record.
- Public closeout belongs primarily in the PR body. Add a repo closeout summary
  under `docs/superpowers/closeouts/` only when the summary is intentionally
  part of the public repo record.

## Versioned Allowlist

New handoff files are ignored by default through `.gitignore`. Repo-tracked
handoff files should be rare:

- active branch stubs that must be shared publicly for a live PR or branch;
- exceptional compatibility stubs whose exact path is intentionally part of a
  public workflow.

Productization compatibility lane registries live under
`docs/superpowers/productization/`; mutable state does not.
File-management manifests, referrer audits, and approval records live under
`docs/superpowers/file-management/`. Branch closeout summaries live under
`docs/superpowers/closeouts/`. Do not put those public records back under
`handoffs/archive/`.

Do not force-add private diaries, command transcripts, raw review rationale, or
Obsidian-only depth context.

The global `$handoff` skill writes a temporary conversation handoff outside the
workspace. Use it for cross-session context transfer when the ignored local
handoff is not appropriate. Do not confuse that temporary output with this
repo's handoff workspace.

If long-form context moves to Obsidian, the active local handoff still keeps a
self-sufficient sanitized stub. The stub may point to an optional Obsidian note
title or alias, but it must contain enough current objective, decisions,
validation, blockers, and next actions for an agent to resume without private
vault access. See `docs/agent/obsidian-handoff-contract.md`.

Repo handoffs are public-summary surfaces, not private lab notebooks. Do not
copy private Obsidian reasoning, command transcripts, local absolute paths,
sample-level investigation detail, or private data placement into a force-added
repo handoff, archive, or PR body. Distill the approved public decision and
leave the long reasoning in Obsidian.

## Maintenance

- Current handoff should stay short enough to read every time, normally under
  about 200 lines.
- Rewrite and prune current handoff instead of appending chronological updates.
- If a repo closeout summary is intentionally public, keep it compact:
  completed work, important decisions, rejected paths, and final validation.
- Use `[active]`, `[blocked]`, `[done]`, and `[superseded]` labels when helpful;
  remove `[done]` and `[superseded]` from current handoff during the next prune
  unless they prevent repeated mistakes.
- Every git-tracked file under `current/` or `archive/` must have one row in
  `RETENTION.tsv`. Ignored local handoff files do not need inventory rows.
  As of 2026-06-27, there are no tracked `current/` or `archive/` handoff files,
  so `RETENTION.tsv` is header-only.

## Retention inventory

`RETENTION.tsv` is the machine-readable cleanup queue for versioned handoff
files only. It is not deletion approval. It records which tracked files are
active, which should move to Obsidian after PR review, and which may later be
removed only through an exact manifest plus explicit approval. Public
file-management evidence, productization anchors, and closeout summaries have
their own directories and do not belong in this inventory.

Allowed `retention_decision` values:

- `active_current`: active branch resume stub. Keep in `current/` only while
  the branch or PR workflow is live.
- `move_to_obsidian_after_pr`: useful branch/review history that should not
  live in repo indefinitely after PR review.
- `superseded_by_pr`: repo archive content already replaced by the PR body.
- `remove_after_merge_approval`: candidate for tracked removal after merge or
  closeout, but still requires a concrete manifest and explicit user approval.

Allowed `next_review_event` values are `active_branch_change`,
`pr_open_update`, `pr_merge_or_close`, `referrer_audit`,
`validation_cleanup`, `productization_policy_change`, and `manual_review`.

Run the read-only audit before PR closeout or whenever tracked handoff files
change:

```powershell
python tools/diagnostics/handoff_retention_audit.py
python tools/diagnostics/docs_management_audit.py
```

After a PR is merged or closed, use the event mode only when tracked handoff
files remain and need an explicit due queue:

```powershell
python tools/diagnostics/handoff_retention_audit.py --event pr_merge_or_close
```

The audit can report candidates, but it must not auto-delete. `git rm` or
repo-tracked deletion still require exact paths, referrer audit, and explicit
user approval. Ignored local handoffs can be moved to Obsidian or deleted as
local scratch without creating a cleanup PR.

## PR closeout lifecycle

Current handoff is an input to closeout, not the durable endpoint.

1. During branch work, maintain an ignored local current handoff, preferably
   `current/ACTIVE.local.md`.
2. When opening or updating a PR, condense the current handoff into the PR body:
   problem, solution, verification actually run, residual risk, and next
   action. Do not paste the whole handoff or private Obsidian-only context.
3. If the completed phase must remain in repo history after PR closeout,
   add a compact summary under `docs/superpowers/closeouts/`.
4. After the PR is closed or merged, move useful local handoff depth to
   Obsidian during routine vault maintenance. No cleanup PR is needed unless
   tracked handoff files were intentionally added.
5. Stop maintaining that local current handoff unless a follow-up branch
   explicitly reuses it.

## Authority

handoff 不是產品權威。若文件衝突:

1. `git status` / `git diff` 決定目前工作樹實況。
2. productization status index 決定 maturity tier 與 active lane；authority
   manifest 決定 writer scope；promotion contract 決定 promotion gate。
3. current handoff 決定 branch WIP 與接手順序，但不授予產品權威。
4. product docs、named plans、schemas 決定 schema、CLI/config/output 行為契約。
5. validation notes 決定 RAW/benchmark evidence。
