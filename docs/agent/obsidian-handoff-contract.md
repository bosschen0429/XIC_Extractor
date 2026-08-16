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
| Ignored `output/` | Generated audits, routing tables, retirement evidence, full validation output | maintained source of truth |

The normal durable closeout surface is the PR body. Do not create a tracked
closeout summary, PR-body seed, active plan stub, or same-path history stub.
Git history and closed Issues/PRs preserve disposable construction history.

## Source-of-truth promotion rule

Before retiring a tracked historical document:

1. Identify the current canonical owner.
2. Move only still-valid decisions, invariants, negative claims, and operator
   instructions into that owner.
3. Retarget every exact repo referrer to the owner or to a current
   machine-readable artifact that genuinely consumes the old path.
4. Put unresolved construction work in a GitHub Issue.
5. Verify the repo remains understandable without the retired document or a
   private vault.

Do not preserve chronology merely because it once explained a decision. The
current owner states the decision; Git and GitHub preserve how it was reached.

## Retirement evidence gate

Every tracked deletion requires an exact repo-referrer scan. A remaining exact
referrer blocks deletion until it is retargeted. Retirement evidence is
generated under ignored `output/`; it is not another permanent repo artifact.

Two history dispositions are supported:

| Disposition | Use when | Required evidence |
| --- | --- | --- |
| `discard_git_history` | A plan, spec, closeout, cleanup inventory, or other construction record has no maintained value after owner absorption | `review_result: pass_can_retire`, source hash, empty exact referrers, durable owner locators, explicit discard reason, and operator approval |
| `obsidian_source_copy` | Private research or reasoning still has independent long-term value | the same owner/referrer evidence plus verified source-copy readback |

`tools/diagnostics/retire_docs.py --evidence <ignored-json> --execute` performs
the local destructive step. For retired construction lanes, the staged
placement guard accepts deletion only when the final staged tree has no exact
repo referrers. It does not require committing a retirement packet. The one
existing checker-bound productization retirement packet remains an exact-path
machine exception; it is not a template for new cleanup history.

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
7. Before deletion: absorb durable claims, scan exact referrers, choose a
   history disposition, then use the retirement tool.

Never add tracked documents under `docs/superpowers/plans/`, `specs/`,
`closeouts/`, or `file-management/docs-cleanup/`. The two exact machine
exceptions named above are read-only compatibility surfaces.

## Stop rules

Stop a retirement when a durable claim has no current owner, an exact referrer
remains, a checker consumes the exact path, or the proposed replacement would
invent a new product/semantic owner. Private-vault unavailability blocks only
`obsidian_source_copy`; it does not justify keeping disposable construction
history in the repo.
