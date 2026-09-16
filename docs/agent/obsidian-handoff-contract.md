# Documentation authority and private-work boundary

This contract keeps repository documentation durable. The repo is not a diary,
construction queue, PR archive, or cache of dated plans. Obsidian is optional
private context; GitHub Issues and PRs own construction history.

## Authority surfaces

| Surface | Owns | Must not own |
| --- | --- | --- |
| Canonical repo docs | Maintained product, user, architecture, agent, evidence, schema, and validation contracts | branch chronology, temporary plans, raw research, review transcripts |
| GitHub Issue | Active PRD, bounded implementation plan, acceptance, decomposition, unresolved product decisions | shipped behavior authority after implementation |
| GitHub PR body | Delivered diff, verification, review disposition, residual risk | a second product or architecture owner |
| Ignored local handoff | Next 1–3 actions, current blocker, disposable execution context | tracked history or public contract |
| Obsidian | Optional private research, reasoning, and local context | the only copy of a repo behavior or required next action |
| Ignored `output/` | Generated audits, routing tables, full validation output | maintained source of truth |

The normal durable closeout surface is the PR body. Do not create a tracked
closeout summary, PR-body seed, active plan stub, or same-path history stub.
Git history and closed Issues/PRs preserve disposable construction history.

## Maintain The Current Document

Update the canonical document when its method or behavior changes. Git preserves
previous versions; routine maintenance does not require document archival,
historical copies, a retirement packet, or a separate approval.

For a merge or removal, use the normal task diff:

1. Identify the current canonical owner.
2. Move only still-valid decisions, invariants, negative claims, and operator
   instructions into that owner.
3. Scan exact repo referrers and retarget them to the owner or a replacement
   artifact. Preserve a consumed path until its consumer has been updated.
4. Put unresolved construction work in a GitHub Issue.
5. Verify the repo remains understandable without the retired document or a
   private vault.

Do not preserve chronology merely because it once explained a decision. The
current owner states the decision; Git and GitHub preserve how it was reached.

This also applies to obsolete plans and closeouts. Their age or former lifecycle
does not create an archival obligation. If the user requests a separate private
copy, verify that copy at the requested destination; it is not a prerequisite
for maintaining repo docs.

## Current Machine Dependencies

Retain schemas, fixtures, and evidence artifacts while a current checker or
scientific claim consumes them. Their retention follows that consumer's
contract, not a general document-archiving rule.

The compatibility index at
`docs/superpowers/plans/2026-06-15-productization-control-plane.md` is also an
exact-path machine exception. It is a current lookup contract despite its
legacy path and must not be used to justify new tracked plans.

## Public/private publication boundary

Repo documents may contain maintained contracts, current operator guidance,
compact evidence summaries, and minimal checker-consumed artifacts. They must
not contain raw chat chronology, speculative branch sequencing, command
transcripts, private RAW layout, machine-local paths, credentials, or notes
that require a private vault to understand.

Obsidian may contain long research and private context, but any decision that
changes software behavior, authority, validation policy, or agent workflow must
first be represented in the canonical repo owner or a live GitHub Issue.

## Placement marker and taxonomy

Use these placements for new durable or local material:

| Placement | Destination | Meaning |
| --- | --- | --- |
| `formal_repo_doc` | canonical repo owner | Maintained public contract or source of truth |
| `repo_subcontract_doc` | bounded repo owner child | Maintained contract under one named owner |
| `repo_support_doc` | schema, validation, fixture, or evidence lane | Current support artifact with no parallel topic authority |
| `private_obsidian_note` | private vault | Optional private research or reasoning |
| `ignored_artifact` | ignored local storage | Generated audit, evidence packet, or bulky output |
| `throwaway_scratch` | ignored local scratch | Material with no retention value |

Tracked `repo_active_stub`, `branch_closeout_summary`,
`repo_stub_plus_obsidian`, and `repo_stub_plus_formal_doc` are retired migration vocabulary.
Do not create or refresh them.

Machine-readable markers for non-canonical repo documents:

```markdown
Doc placement: <formal_repo_doc | repo_subcontract_doc | repo_support_doc>
Repo owner: <canonical repo path>
```

Private and ignored material may use its corresponding placement marker, but
those values are invalid under tracked `docs/`. Canonical owners do not need
generic lifecycle metadata. New tracked `Doc kind: plan`, `spec`, `handoff`, or
`closeout` documents are forbidden regardless of lifecycle value.

## Daily document routing

1. Public behavior, schema, authority, evidence policy, or workflow rule:
   update its canonical repo owner.
2. Work not yet implemented: create or update a GitHub Issue.
3. Delivered branch narrative: update the GitHub PR body.
4. Current next actions: use an ignored local handoff.
5. Long private reasoning worth retaining: use Obsidian.
6. Generated or disposable material: use ignored `output/` or scratch.
7. Before deletion: absorb durable claims, repair exact referrers, and verify
   the affected docs and consumers through the normal task diff.

Never add tracked documents under `docs/superpowers/plans/`, `specs/`,
`closeouts/`, or `file-management/docs-cleanup/`. The exact machine exception
named above remains a read-only compatibility surface.

## Unresolved Dependencies

Before removing the affected file, resolve any ownerless durable claim,
remaining referrer, checker dependency, or replacement that would invent a new
product/semantic owner. Private-vault unavailability affects only an explicitly
requested private copy.
