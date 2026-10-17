# Review Roundtrip

Review roundtrip is the path from machine-produced review worklists to typed
human actions, dry-run application plans, expected-diff review, and only then
approved product changes.

## Contract

- Review queues, candidate tables, and labels are review evidence. Review
  evidence alone does not change product outputs.

### Review record identity

- `xic_extractor.review_records` is the implementation surface of this owner.
  Callers reference an immutable retained review conclusion through
  `xic_extractor.review_records`.
- This first slice accepts only an owner-validated typed `ReviewAction` under
  `review_action`. Arbitrary schema labels or JSON mappings cannot mint a
  record.
- The owner computes the canonical JSON SHA-256 content identity. The owner
  derives a typed `xic.review-record:<sha256>` ID from that content identity.
  Callers cannot supply either the ID or the digest.
- The owner derives a review subject from one resolved canonical sample
  identity plus one owner-stable target key.
- Bare `sample_name` and `target_label` remain fields in `review_action`. These
  two fields must match the owner references. These two fields cannot mint the
  subject by themselves.
- A review-record identity separately binds each of these inputs:
  - the record schema version;
  - the full content digest;
  - the exact target-definition identity;
  - the exact processing-request identity;
  - the canonical set of owner-minted typed evidence-artifact references;
  - any predecessor, plus a non-empty supersession reason.
- The record derives artifact IDs from the immutable artifact references. The
  record does not accept caller strings as artifact IDs.
- Artifact enumeration order is neutral. Artifact substitution or content
  mutation invalidates the identity.
- A successor must retain the same review subject.

### Target redefinition and succession

- A user can legally change a target definition before creating a new
  processing request.
- The stable target key keeps that history under the same review subject.
- The previous review is stale for the new definition or request. The previous
  review stays stale until an explicit same-subject successor binds the new
  identities.
- Corrections are append-only successors.

### Resolution

- Resolution rejects missing, ambiguous, stale, rebound, orphaned, forked, or
  superseded records. Resolution does not treat a mutable "latest" row as
  identity.
- Resolution returns a `ResolvedReviewRecordRef`. The
  `ResolvedReviewRecordRef` retains and revalidates the immutable registry
  snapshot.
- The registry snapshot has its own content-derived ID and canonical record
  ordering. Therefore omission or substitution produces a different bound
  snapshot.
- Lineage accepts a `ResolvedReviewRecordRef` plus its exact registry
  snapshot. Lineage
  does not accept a freshly minted unresolved record or a tuple of
  caller-selected records.
- Within one registry snapshot, every review subject has exactly one decision
  root and at most one current head.
- A second same-subject root is an ambiguous competing chain. A second root is
  not another current conclusion.

### Apply authority

- Typed review actions must be validated and planned before any mutation.
- Apply steps require stable identifiers, recompute rules, sidecar contracts,
  and approved expected-diff evidence.
- The implemented review-action contract is limited to identity verification
  and dry-run application planning. Candidate-sidecar verification is unavailable;
  selected-candidate switching remains parked pending a new evidence/apply contract.
- Selected-candidate switching and manual-boundary area recomputation have no
  product apply authority. That authority requires stable IDs, recompute rules,
  and expected-diff apply contracts first.
- Current lane readiness lives in the status index.
- Peak-choice truth labels are separate from product authority. Peak-choice
  truth labels can inform future automation only through an active authority
  and expected-diff gate.
- Reviewer rationale and dispute discussion are private notes unless a
  compact public verdict or checker-readable artifact is required.

## Surfaces

| Surface | Role |
| --- | --- |
| Review Queue | Worklist and triage surface |
| `review_action` | Typed action import schema |
| `ImmutableReviewRecordRef` | Owner-minted immutable sample/target/request/content/artifact/supersession reference for retained conclusions |
| `ReviewRecordRegistrySnapshot` | Content-digested immutable owner snapshot used for current/superseded resolution |
| `ResolvedReviewRecordRef` | Owner-validated current reference within an immutable registry snapshot |
| Dry-run application plan | Non-mutating audit of possible changes |
| Expected-diff approval rows | Gate before product-impacting changes |
| Inter-reviewer agreement summary | Evidence quality and disagreement surface |

## Operator commands

Use `python -m scripts.<name>`. The implementation owner is
`xic_extractor.review_actions.workflows`.

| Need | Command | Result and limit |
| --- | --- | --- |
| Validate actions or approved expected-diff rows | `validate_review_actions`; add `--expected-diff` for approvals | Validation without mutation |
| Compare actions with current targeted rows | `plan_review_action_applications` | Application plan and optional approval template |
| Prepare ready/blocked copy edits | `plan_review_action_apply_changesets` | Changeset; optional `--output-apply-readiness-tsv` emits the intermediate audit |
| Produce an audited targeted-long copy | `apply_review_action_changesets` | Review/product-state fields and audit in a separate copy; no peak/area recomputation, workbook update, or Matrix publication |

The changeset planner validates approvals and calculates readiness itself;
separate preliminary commands are unnecessary. A ready copy edit does not
grant the parked peak-switch or area-writer lanes product authority.

## Boundaries

- Owns: review queue generation, typed action import/validation, and dry-run
  planning.
- Does not own: whether a reviewer action is approved for product mutation,
  matrix activation scope, or HTML review UX details.
- Dataset-run queues, templates, labels, and rendered packets default to ignored
  output. Git tracks only an active compact verdict or contract with an enduring
  product or scientific consumer.

## Verification

- Schema validation tests for actions or labels.
- Stale-source/hash checks for imported review artifacts.
- Owner-reference tests for deterministic minting, sample/target/request and
  content/artifact invalidation, missing/ambiguous/stale resolution, and
  append-only supersession.
- Expected-diff packet for any selected peak, area, counted detection,
  workbook, or matrix change.
- Productization authority review before writer behavior changes.

## Pitfalls

- Treating review labels as direct writer predicates.
- Applying manual boundaries or candidate switches without stable IDs and
  approved expected diffs.
- Promoting a dataset-run review packet into a permanent repo contract without
  an enduring consumer.
- Keeping private reviewer rationale in public repo because it came from a
  validation folder.

## See Also

- [Backfill](backfill.md)
- [Quant matrix](quant-matrix.md)
- [Productization](productization.md)
