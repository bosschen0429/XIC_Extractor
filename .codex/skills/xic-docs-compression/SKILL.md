---
name: xic-docs-compression
description: Consolidate overlapping XIC documentation by checking current consumers, preserving useful claims in their owners, and repairing references before removal.
---

# XIC Docs Compression

Produce one compression decision per document family. Read
`docs/project-layout.md` and `docs/agent/obsidian-handoff-contract.md`; they own
placement, public/private boundaries, and maintenance policy. Use this workflow
for document-family consolidation; ordinary edits update the existing owner
directly. This skill orders the work without redefining those contracts.

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
which claims were absorbed, already owned, rejected, or remain unresolved under
the owner's source-of-truth promotion rule.

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

Apply the current placement and routing policy from the canonical docs. Update
the owner, retarget ordinary referrers, and retain live checker/schema/status
anchors or support only while their current consumer requires them. Route
unfinished construction to the owning Issue, delivered narrative to the PR
body, and current next actions to an ignored handoff. Git history preserves
previous versions; create a separate private copy only when requested.

Read `references/bound-doc-compression.md` for XIC-specific heuristics when the
family covers bound validation, Backfill/quant, productization, lockbox,
docs-workflow history, or lifecycle closeout. Merges and removals use the
owner's normal task diff and referrer checks; no separate retirement packet or
archive is required. Unclear public contracts or unresolved exact referrers
require the cheapest decisive repo
evidence. Preserve the affected file while resolving ownership, absorption, or
replacement; proceed within existing authorization when those facts are closed.
Ask only when unavailable intent or operator-only authority changes the result.

Completion criterion: owner updates, referrer repairs, and removals agree;
no public claim is left ownerless and no
machine dependency is silently removed.

## 4. Verify The Decision

After document or referrer changes, run the docs-management or targeted referrer
audit and apply the owner's placement and consumer checks when relevant. Reuse an
unchanged accepted audit for a read-only decision and check only its required
bindings. For skill or agent-guidance changes, follow
`docs/agent/codex-operating-system.md#change-protocol`; it selects format,
reference, scenario, and runtime checks for the affected behavior.

Return a compact packet:

```markdown
Path or family / mechanical evidence:
Current role / lifecycle / route:
Durable payload / canonical repo owner:
Absorption result:
Memory consultation / repo-visible revalidation:
Machine-anchor or support role:
Owner updates / referrer repairs / removals:
Remaining risk or human decision:
Verification performed:
Next action:
```

Completion criterion: the proposed action is supported by both mechanical facts
and semantic absorption review, required gates pass, and remaining uncertainty
is named instead of converted into a mechanical migration.

Read `evals/trigger-cases.md` only when tuning routing or deciding whether this
skill or a neighboring XIC skill should own a request.
