# Planning And Workflows

This file owns planning discipline, reusable workflow routing, and owner
migration policy. Stable runners and validation command shapes remain in
`docs/agent/parameter-settings.md`.

## Planning And Review

- Before phase plans or expensive validation, name the decision the run can
  close, strongest existing oracle, missing independent evidence, expected
  runtime/artifacts, and fail-fast or inconclusive path.
- Use tools proactively. Pick the available search, CodeGraph, subagent,
  plugin, external-docs, or real-data validation path that best closes the
  decision; do not choose a weaker path only to save tokens or cost.
- For long tool chains, write the decision map first: question, evidence source,
  action if pass, action if fail. This prevents blind loops without discouraging
  tool use.
- Any `audit_only`, `shadow_only`, or `diagnostic_only` path needs an exit rule:
  promote, kill, externalize, or name the single missing evidence.
- Do not expand a specific validation run when its result cannot change the next
  action; use a different tool or stop with the blocker instead.
- Use a goal-shaped contract for long-running, multi-step, cross-turn, or
  repeatedly drifting work. Goal contracts must point to canonical local
  surfaces, active product docs/plans/schemas, and existing diagnostics or validation outputs.
- Non-trivial specs, plans, docs, workflow rules, and implementations need a
  critical-thinking review angle: strongest assumption, stale-artifact risk,
  cheaper existing oracle, or condition that would invalidate the path.

## Productization Work Cycle

Productization planning limits active work instead of turning every diagnostic
idea into a lane. Per maintenance cycle, keep at most one primary productization
lane, one supporting-infrastructure lane, and one diagnostic-only lane. Starting
another requires closing one as promoted, parked with an explicit reopening
condition, externalized/killed, or abandoned with a reason.

Before work starts, record the lane key, current structured state, intended
state for this change, capability owner, affected public surface, expected
behavior diff and required non-changes, evidence tier, stop/rollback rule, and
downstream consumer. If product values or identity may change and no
expected-diff plan exists, stop the intake.

At maintenance and closeout:

- verify current state only against the structured status index and authority
  manifest, not a branch diary or compatibility index;
- confirm new diagnostics, reports, and sidecars have a promote, kill,
  externalize, or missing-evidence exit rule;
- check schema/version, CLI/config/GUI documentation, downstream handoff,
  expected diff, replay/provenance, and reviewer/audit records where relevant;
- name the validation evidence actually used and do not equate tests passing
  with production readiness;
- remove stale ownerless plans and conflicting narrative state instead of
  appending another board;
- resolve only the active branch's handoff when one exists, and fold normal
  durable closeout into the PR body.

Periodically count active lanes. If the WIP limit is exceeded, freeze new work,
select one lane to advance, and select one to park, kill, archive, or
externalize. Review aging diagnostic artifacts and ownerless public docs before
adding new ones.

## Stacked PR And Ledger Ownership

Stacked PRs are allowed only when each PR has one owner surface and can pass CI
from a clean checkout of its base. Before opening, retargeting, or repairing a
stacked PR series, write or inspect a stack map that lists:

- base and head branch for every PR;
- repeated files across PRs, especially global ledgers such as productization
  status indexes, authority manifests, schemas, active handoffs, and artifact
  inventories;
- tracked fixtures versus externalized local artifacts;
- which PR owns schema/product behavior, which PR owns artifact hygiene, and
  which PR owns final rollup ledger updates;
- whether default CI requires ignored `output/`, `.worktrees/`, or
  `local_validation_artifacts/` bytes.

If two or more PRs mutate the same global ledger or handoff, do not keep
repairing each PR independently. Split out a prerequisite boundary PR or a final
rollup PR so intermediate PRs carry scope-local manifests rather than a
different snapshot of the whole productization world.

Never use a later cleanup PR as an implicit prerequisite for an earlier PR's CI.
If a PR externalizes bulky outputs, the same PR must also remove default CI
requirements for those ignored local bytes. Local rendered review assets may be
validated by explicit opt-in flags such as `--require-rendered-local`, but the
default check must validate only tracked contract artifacts: schemas, summaries,
minimal fixtures, paths, row counts, and hashes.

### Issue Closure Ownership

Issue linkage is part of delivery ownership, not a second progress ledger. A PR
that completes its issue and targets the repository's default branch uses a
GitHub closing keyword. When several PRs deliver one issue, only the completing
PR uses that keyword; in a stack this is normally the final rollup PR already
responsible for shared-ledger reconciliation. If the completing PR targets a
non-default branch, use `Refs #N` there. The later PR that integrates the work
into the default branch owns the closing keyword; if no such PR remains, verify
default-branch integration and use the evidence-backed manual closure path below.

Intermediate and non-default-branch PRs use `Refs #N` only. They must not place
any supported closing-keyword form (`close`, `closes`, `closed`, `fix`, `fixes`,
`fixed`, `resolve`, `resolves`, or `resolved`) immediately before the issue
number, including uppercase or colon variants and negated sentences, because
GitHub may still classify that text as a closing reference.

If historical work has no remaining completing PR, reconcile the issue against
the merged-PR census and canonical evidence, add one evidence comment, and close
it manually. Stop instead when completion cannot be verified. Open/closed plus
that evidence chain is the default lifecycle; do not mirror programme progress
vocabularies as labels. Use native `blocked_by` only for a real open prerequisite,
not parent/child membership, optional sequencing, or intentional deferral.

## Goal Contract Shape

Use goals for phase-sized, cross-turn, RAW/data-backed, PR-ready, CI/release-like,
or repeatedly drifting work. Do not use a runtime goal for tiny bug fixes, simple
status checks, or one-command validations with an obvious done state.

A useful goal is one measurable objective plus enough context to prevent drift.
It should include:

```markdown
Objective:
Context:
Constraints:
In scope:
Out of scope:
Current surfaces/artifacts:
Plan:
Verification:
Boundaries:
Iteration policy:
Done when:
Pause if:
Stop rules:
Handoff:
```

Keep `Objective` singular. If two outcomes need different verification gates,
split them into separate goals or state which one is primary. `Done when` must
be auditable from files, commands, PR state, or validation artifacts. `Stop
rules` must name conditions that require user decision instead of continued
blind continuation.

When drafting a goal for a vague request, give the best executable version
first rather than a blank form. Choose conservative defaults for low-risk
unknowns and ask only when the answer changes cost, risk, ownership, public
contracts, or product direction. If the domain is unfamiliar, make discovery of
repo docs, existing scripts, sample data, or official references part of the
goal before implementation.

Keep `Done when` for completion evidence and `Pause if` for human/external
blockers such as credentials, production data, destructive operations,
permissions, budget, unclear ownership, or product-direction decisions.
Iteration should be bounded: after the same failure shape repeats twice, require
a new source of evidence instead of another blind retry.

## Skills And Subagents

- When the user asks for subagent review, follow
  `docs/agent/subagent-routing.md`. Do not replace a requested multi-angle
  review with one generic reviewer unless a runtime limit blocks it and the
  bypass is reported.
- Repo-local execution subagents are opt-in. The main agent owns synthesis,
  edits, final judgment, and verification.
- Reusable workflows belong in global skills first. Repo-local `.codex/skills/`
  entries should exist only for XIC-specific checklists that cannot live cleanly
  in routing docs.
- Use `xic-architecture-preflight` before implementing or planning non-trivial
  diagnostics, RAW-backed evidence, preset-performance changes,
  matrix/activation/value-delta paths, model-selection work, HCD-PI, Delta Mass,
  CID-NL expansion, or other evidence-provider additions.
- Use the global code-review workflow for explicit fixed-point review. Repeated
  hosted findings follow the root review-incident rule: stop instance patches,
  classify plan/design/implementation/oracle failure, and redesign or delete
  when the failure universe cannot be bounded.

## Legacy Owner Transformation Policy

- The project direction is explicit transformation: successor modules absorb the
  useful capability and product invariants of legacy owners, fix the legacy pain
  points, then retire or reduce the legacy owner to a thin compatibility adapter.
- When the user explicitly asks to absorb a legacy owner into an existing
  successor model, do not stop at another shadow/report/spec layer if a bounded
  ownership-transfer slice can be named.
- A valid ownership-transfer slice names: current owner, successor owner,
  public surface, preserved invariant, expected product diff or parity oracle,
  and the focused tests that prove it.
- After those are named, prefer implementing one narrow transfer over creating a
  new broad plan. Review gates should close the decision and then unblock
  execution; they should not become an indefinite replacement for migration.
- Tests protect product invariants, not old module shapes. When a successor owns
  an invariant, migrate the test to the successor surface or delete the
  legacy-specific test. Keep legacy tests only for public compatibility,
  explicit rejection/migration behavior, or parity during an active transition.
- Do not preserve a legacy owner merely because tests exist. First classify what
  the tests protect: product invariant, compatibility contract, diagnostic
  evidence, or obsolete implementation detail.
- Keep public safety rules: if selected peak, selected area, confidence, reason,
  matrix identity, workbook schema, TSV schema, or config behavior would change,
  require an expected-diff contract and focused output tests before promotion.
- `audit_only`, `shadow_only`, and `diagnostic_only` artifacts must still name an
  exit rule, but for owner-migration work the exit rule should include the
  smallest next production or adapter transfer, not only the missing evidence.
