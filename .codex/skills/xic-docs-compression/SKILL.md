---
name: xic-docs-compression
description: Docs compression for XIC docs/superpowers cleanup, Obsidian copy/retirement, product-owner absorption, stubs, route decisions, or referrer-bound docs.
---

# XIC Docs Compression

Use when continuing non-trivial XIC documentation digestion. The goal is to
leave the repo with compressed public knowledge, not a pile of historical
artifacts that happen to be referenced.

Documentation digestion means deciding what survives after a document has done
its job: public owner, support artifact, compact stub, Obsidian original,
ignored artifact, or deletion candidate.

## First Response Protocol

Before proposing file moves, source-copy stubs, removals, or referrer rewrites,
produce this block:

```markdown
Surface:
Why this is not ordinary docs cleanup:
Existing owner/helper to reuse:
Doc kind/lifecycle:
Route vs digestion issue:
Likely durable payload:
Likely duplicate or stale parts:
Topic owner that should survive:
Support/stub role:
Obsidian/private-history role:
Bound/referrer risk:
Validation gate:
Stop rule:
Next action:
```

## Core Rules

- Three final routes exist: `obsidian_original`,
  `repo_distilled_plus_obsidian_original`, and `repo_product_doc`.
  `needs_route_decision` is never a final state.
- Route is not lifecycle. An active plan may live in repo now and still exit to
  a distilled owner plus Obsidian original later.
- Route retention is not digestion. A file may remain because it is public,
  checker-readable, active, or referrer-bound while still needing owner
  absorption, support-surface demotion, lifecycle closeout, or Obsidian
  handling.
- One topic should have one canonical repo owner. Other same-topic files are
  support artifacts, manifests, validation packets, closeouts, active stubs, or
  private history pointers.
- Automatic retirement is the normal exit for completed transient docs after
  product absorption is verified and the original is source-copied to Obsidian.
  A same-path stub is an exception for active execution context or unresolved
  exact referrers, not the default final state.
- Before closing a newly written spec, plan, note, or handoff, run a small-model
  absorption review: check whether durable conclusions are present and correct
  in the long-term owner (`docs/product/`, agent contract, schema/status owner,
  or validation README). Use `pass_can_retire`, `missing_absorption`,
  `incorrect_absorption`, or `still_active` as the review result.
- Active execution plans cannot be Obsidian-only. Repo needs a self-sufficient
  short stub with objective, scope, constraints, next 1-3 actions,
  verification, and stop rule.
- A referrer is a mechanical dependency, not proof of semantic value.
- Validation artifacts are often time-bound snapshots of an old codebase state.
  Do not preserve long pass/fail narratives just because they once explained a
  gate.
- Compress before moving: extract the current decision, still-valid assumption,
  reusable lesson, machine-checkable artifact, or tombstone first.
- Keep machine anchors only when still used: checker inputs, schemas, hashes,
  status indexes, authority manifests, review packets, and minimal fixtures.
- If a historical result is contradicted by later code or validation, mark it as
  superseded history rather than evidence.
- Obsidian can preserve originals and private reasoning, but repo docs must not
  require private vault access for product behavior, validation policy, or the
  next safe action.

## Required Classification

For each candidate path or family, classify it as one of:

- `current_contract`: still defines active repo behavior, schema, gate, or
  checker-readable evidence.
- `current_decision_summary`: contains a still-valid product or workflow
  decision that belongs in a compact owner.
- `historical_progress`: useful only as development chronology or rationale;
  source-copy to Obsidian if needed, then remove repo authority.
- `stale_or_superseded`: past result likely invalid, contradicted, or replaced;
  keep only a tombstone or lesson if it prevents repeated mistakes.
- `duplicate_boilerplate`: repeats claims already owned elsewhere; absorb and
  retarget.

## Default Workflow

1. Identify the current owner/helper surface: `docs/agent/obsidian-handoff-contract.md`,
   `docs/project-layout.md`, generated docs-management manifests/topic indexes,
   exact referrers, and any canonical product/agent owner named by the files.
   Complete this step when the surviving repo authority is named or the run is
   marked `needs_human_decision`.
2. Cluster by topic, document family, repeated claim, and current role. Complete
   this step when every candidate path or family has doc kind, lifecycle, route,
   classification, and topic-owner status.
3. Distill the durable payload: current decision, still-valid reason, active
   gate/checker, public contract affected or explicitly unaffected, and
   supersession/tombstone note if needed. Complete this step when every
   non-empty payload has a repo owner or explicit rejection.
4. Run product-absorption review for completed transient docs. Complete this
   step when each candidate is `pass_can_retire`, `missing_absorption`,
   `incorrect_absorption`, or `still_active`.
5. Choose the smallest safe action: update owner, create or refresh index,
   demote support, tombstone, source-copy to Obsidian, auto-retire, keep a rare
   active/referrer-bound stub, retarget referrers, keep machine anchor, or ask
   for a decision on unclear/high-risk cases. Complete this step when no path is
   moved, stubbed, or deleted without vault/source-copy handling and a referrer
   plan.
6. Verify with docs-management or targeted referrer audit as relevant, staged
   placement guard, hook/script smoke checks for `.codex` changes, `git diff
   --check`, and a changed-file secret/local-path scan.

## Output Contract

Produce a compact inventory table with:

`path_or_family`, `doc_kind`, `lifecycle`, `route`, `classification`,
`durable_payload`, `duplicate_or_stale_parts`, `surviving_repo_owner`,
`support_or_stub_role`, `absorption_review`, `obsidian_action`,
`referrer_action`, `retirement_action`, `risk`, `next_step`.

End with a verdict:

- `compress_first`: owner/index/tombstone needed before moving files.
- `safe_to_retire_batch`: stable claims are already owned, originals are ready
  for Obsidian/source-copy handling, and exact referrers are absent or already
  retargeted.
- `stub_only_if_bound`: stable claims are owned, but exact referrers or active
  execution context temporarily require a short repo stub.
- `keep_machine_anchor`: file is still a live checker/status/schema artifact.
- `needs_human_decision`: information value or public contract risk is unclear.

## References

Read `references/bound-doc-compression.md` when working on bound validation,
Backfill/quant, productization, lockbox, docs-workflow history, plan/spec/note
lifecycle closeout, or Obsidian route decisions.

Read `evals/trigger-cases.md` only when tuning routing or deciding whether this
skill or a neighboring XIC skill should own a request.
