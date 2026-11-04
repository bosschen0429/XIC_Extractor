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

Keep current behavior, reserved but unimplemented contracts, and unresolved
questions visibly distinct. A frozen design is not an implementation claim.
When implementation lands, reconcile its status and remove superseded
construction instructions in the same change.

A rule has one detailed owner. Consuming guides keep only the local boundary
needed to avoid misuse and link to that owner; they do not require synchronized
copies of field lists, thresholds, or implementation status. Update an overview
when its navigation or ownership changes, not for every change in a linked topic.

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

## Public/private publication boundary

Repo documents may contain maintained contracts, current operator guidance,
compact evidence summaries, and minimal checker-consumed artifacts. They must
not contain raw chat chronology, speculative branch sequencing, command
transcripts, private RAW layout, machine-local paths, credentials, or notes
that require a private vault to understand.

Obsidian may contain long research and private context, but any decision that
changes software behavior, authority, validation policy, or agent workflow must
first be represented in the canonical repo owner or a live GitHub Issue.

## Placement

| Destination | Holds |
| --- | --- |
| Canonical repo owner | Maintained public contract or source of truth |
| Validation or fixture lane | Current support artifact consumed by a checker or test |
| Private vault | Optional private research or reasoning |
| Ignored `output/` or scratch | Generated audits, evidence packets, bulky or disposable output |

Tracked documents need no placement or lifecycle header; their path and their
owner's links say what they are. Construction plans, specs, handoffs, and
closeouts are not tracked. `scripts/check_repository_docs.py` rejects relative
Markdown links that no longer resolve.

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

## Unresolved Dependencies

Before removing the affected file, resolve any ownerless durable claim,
remaining referrer, checker dependency, or replacement that would invent a new
product/semantic owner. Private-vault unavailability affects only an explicitly
requested private copy.
