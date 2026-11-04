---
name: xic-docs-compression
description: Consolidate overlapping XIC documentation by checking current consumers, preserving useful claims in their owners, and repairing references before removal.
---

# XIC Docs Compression

Consolidate a bounded document family around its current owners. Ordinary edits
update the existing owner directly. Read `docs/project-layout.md` and
`docs/agent/obsidian-handoff-contract.md` for placement and public/private boundaries.

## Establish What Must Survive

Use current consumers, exact referrers, and checker/schema use to identify each
candidate's role with `git grep` for its path and anchors. Reuse still-current
evidence.

Compare useful claims with their canonical owners: current decisions, reasons,
public contracts, active checks, and still-needed lessons. Absorb unique,
supported claims before removing redundant sources. Do not preserve cleanup
history merely to describe the cleanup.

Consult private memory through `$agent-memory` only if prior knowledge can
change the decision. Revalidate any public-action claim against repo evidence;
the public owner must remain understandable without private context.

## Consolidate And Verify

Update owners and ordinary referrers together. Keep machine anchors only while
a current consumer needs them. Route unfinished construction to its Issue,
delivered narrative to the PR, and disposable next actions to an ignored handoff.
Git preserves previous versions; make a private copy only when requested.

Read `references/bound-doc-compression.md` when handling XIC validation,
Backfill/quant, lockbox, or docs-workflow history. Resolve unclear
ownership with the cheapest decisive evidence and preserve the affected source
until its useful claims and consumers have a replacement. Proceed under
existing authorization; no separate retirement packet is required.

After changes, check affected references and consumers and run
`scripts/check_repository_docs.py` for broken links. Guidance changes follow
`docs/agent/codex-operating-system.md#change-protocol`.

Report what was consolidated, the surviving owners, actual verification, and
material unresolved facts. No fixed per-document report form is required.
Manual routing and ownership scenarios live in `evals/trigger-cases.md`.
