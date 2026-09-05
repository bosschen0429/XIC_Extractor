---
name: xic-docs-compression
description: Digest XIC documentation by combining audit facts with owner absorption, lifecycle routing, referrer-safe retirement, and optional Obsidian source-copy handling.
---

# XIC Docs Compression

Produce one compression decision per document family. Read
`docs/project-layout.md` and `docs/agent/obsidian-handoff-contract.md`; they own
route names, lifecycle metadata, public/private boundaries, and retirement
policy. This skill orders the work without redefining those contracts.

## 1. Audit The Surface

Run the existing read-only audit before inferring semantic value:

```powershell
.venv\Scripts\python.exe tools/diagnostics/docs_management_audit.py --repo-only --json
```

When a reusable review packet helps, add task-specific ignored routing and
topic-cluster TSV outputs supported by that tool. Record lifecycle fields,
exact referrers, owner claims, checker/schema use, and topic clusters as
mechanical facts rather than move/delete permission.

Completion criterion: every candidate path belongs to a bounded family and has
its current role, exact-referrer state, machine-anchor state, and claimed owner
recorded.

## 2. Absorb Durable Meaning

For each family, name one canonical repo owner and extract only the still-valid
decision, reason, public contract, active gate/checker, next safe action, or
supersession lesson. Compare completed transient docs with that owner and record
the absorption result defined by the Obsidian handoff contract.

When prior project knowledge could change the classification, use
`$agent-memory` and the configured Research Vault `_meta/routing.md` contract;
if unavailable, skip this optional branch. Treat private memory as a search
clue: revalidate any public-action claim with repo-visible evidence and place a
stable claim in its canonical repo owner before acting on it. Record the
consulted source/status and repo-visible revalidation result.

Completion criterion: every non-empty durable payload has one repo owner or an
explicit rejection; private context is not required to understand repo behavior
or the next safe action.

## 3. Route The Remainder

Apply the exact route and lifecycle vocabulary from the canonical docs. Choose
the smallest safe action: update the owner, retain a live machine anchor,
demote support, tombstone a superseded claim, source-copy private history,
retarget referrers, keep a temporary active/referrer-bound stub, or request a
human decision.

Read `references/bound-doc-compression.md` for XIC-specific heuristics when the
family covers bound validation, Backfill/quant, productization, lockbox,
docs-workflow history, or lifecycle closeout. For completed transient docs, use
the canonical retirement-evidence and `retire_docs.py` path; unclear public
contracts, unresolved exact referrers, or out-of-policy deletions remain human
decisions.

Completion criterion: route, lifecycle, owner action, Obsidian action, referrer
action, and retirement action agree; no public claim is left ownerless and no
machine dependency is silently removed.

## 4. Verify The Decision

Rerun the docs-management or targeted referrer audit. Apply the staged placement
guard and retirement-evidence gate when relevant. For `.codex` changes, run the
hook/script smoke checks. When the task mutated files, inspect `git diff
--check` and scan changed files for secrets and unintended local paths. A
read-only decision closes with the relevant audit/referrer checks.

Return a compact packet:

```markdown
Path or family / mechanical evidence:
Current role / lifecycle / route:
Durable payload / canonical repo owner:
Absorption result:
Memory consultation / repo-visible revalidation:
Machine-anchor or support/stub role:
Obsidian / referrer / retirement actions:
Remaining risk or human decision:
Verification performed:
Next action:
```

Completion criterion: the proposed action is supported by both mechanical facts
and semantic absorption review, required gates pass, and remaining uncertainty
is named instead of converted into a mechanical migration.

Read `evals/trigger-cases.md` only when tuning routing or deciding whether this
skill or a neighboring XIC skill should own a request.
