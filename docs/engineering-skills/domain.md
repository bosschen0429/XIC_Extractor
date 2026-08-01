# Domain Docs

How engineering skills should consume this repo's domain documentation when
exploring, writing issues, proposing architecture, or preparing implementation
plans.

This file is not the full product contract. It is a routing layer that tells
general engineering skills which XIC docs and concepts they must respect before
they produce work.

## Layout

This repo uses a single-context domain layout:

- `docs/product/domain-glossary.md`, if present
- `docs/adr/` for architecture decision records, if present
- repo-local domain contracts referenced from `AGENTS.md`

Targeted and untargeted extraction are product lanes inside the same domain,
not separate contexts. They share the evidence spine and product safety
contracts, so skills should not treat them as independent worlds.

Use multi-context only if the repo later splits into independently owned
products with separate domain language, architecture decisions, and release
contracts. The current targeted/untargeted split does not meet that bar.

## Read Order

Before substantial work, read the nearest applicable sources:

- `AGENTS.md`
- `docs/product/domain-glossary.md`, if present
- `docs/adr/`, if present
- the relevant docs referenced by `AGENTS.md`
- current handoff and structured productization owners when the task touches productization state

If `docs/product/domain-glossary.md` or `docs/adr/` do not exist yet, proceed
silently. Their absence is not itself a blocker. Use existing productization
specs, handoffs, and architecture docs as the working source of truth until a
context doc exists.

## Required Domain Split

When creating or updating domain docs, issues, PRDs, or implementation plans,
keep these lanes distinct:

- Shared evidence spine: trace evidence, peak hypotheses, evidence vectors,
  integration results, model selection, audit trail, matrix identity, and
  workbook/export contracts.
- Targeted lane: target-list behavior, targeted MS1 shape identity, limited
  NL_FAIL rescue, and `detected_flagged` boundaries.
- Untargeted lane: family discovery, Backfill, authority manifests,
  adjudication, lockbox truth labels, review packets, and parked broad Backfill.

Do not promote evidence from one lane into product authority for another lane
without an explicit contract, expected-diff evidence, and focused tests.

## Concepts To Preserve

These terms carry product-safety meaning. Prefer them over new synonyms:

- `candidate/audit universe`: rows under review or audit, not automatically
  writable cells.
- `write_ready`: a scope with explicit current write authority.
- `authority`: the permission boundary for product writes.
- `adjudication`: machine classification of a candidate, not approval to write.
- `review packet`: a structured human-review surface, not free-form value
  entry.
- `truth lockbox`: independent peak-choice or area labels, not ProductWriter
  authority by itself.
- `EvidenceVector`, `PeakHypothesis`, and `AuditTrail`: the evidence spine that
  should receive evidence-provider outputs before product behavior changes.

If a proposed term hides one of these boundaries, keep the established term or
call out the intended contract change.

## Product Safety Rules For Skills

General engineering skills must not infer product authority from diagnostics,
sidecars, or passing tests alone.

Treat these as separate claims:

- observable evidence exists
- machine adjudication exists
- human review is possible
- expected-diff evidence exists
- ProductWriter or matrix authority exists

Only the last claim permits product writes, and only for the explicitly
authorized scope. Broad Backfill remains parked unless a later explicit goal
changes that state with independent truth, expected-diff evidence, and updated
contracts.

## Output Expectations

When a skill writes an issue, PRD, architecture note, or plan for this repo, it
should name:

- which lane it affects: shared, targeted, untargeted, or workflow-only
- whether it can affect product output
- which public contract is at risk, if any
- which evidence or validation tier would close the decision
- whether handoff or structured productization-owner updates are required

For workflow-only changes, say that no productization tier or active lane state
changes.
