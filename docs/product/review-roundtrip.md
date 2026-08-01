# Review Roundtrip

Review roundtrip is the path from machine-produced review worklists to typed
human actions, dry-run application plans, expected-diff review, and only then
approved product changes.

## Contract

- Review queues, candidate tables, lockbox packets, and labels are review
  evidence. They do not change product outputs by themselves.
- An immutable retained review conclusion is referenced through
  `xic_extractor.review_records`, an implementation surface of this owner.
  This first slice accepts only an owner-validated typed `ReviewAction` under
  `review_action_v1`; arbitrary schema labels or JSON mappings cannot mint a
  record. The owner computes canonical JSON SHA-256 content identity and
  derives a typed `xic.review-record.v1:<sha256>` ID; callers cannot supply
  either ID or digest.
- A review subject is owner-derived from one resolved canonical sample identity
  plus one owner-stable target key. Bare `sample_name` and `target_label` remain
  fields in `review_action_v1`, but they must match those owner references and
  cannot mint the subject by themselves.
- A review-record identity separately binds its record schema version, full
  content digest, exact target-definition identity, exact processing-request
  identity, canonical set of owner-minted typed evidence-artifact references,
  and any predecessor plus non-empty supersession reason. The record derives
  artifact IDs from those immutable references instead of accepting caller
  strings. Artifact enumeration order is neutral; substitution or content
  mutation invalidates the identity. A successor must retain the same review
  subject.
- A user may legally change a target definition before creating a new
  processing request. The stable target key keeps that history under the same
  review subject, but the previous review is stale for the new definition or
  request until an explicit same-subject successor binds the new identities.
- Corrections are append-only successors. Resolution rejects missing,
  ambiguous, stale, rebound, orphaned, forked, or superseded records rather
  than treating a mutable "latest" row as identity. It returns a
  `ResolvedReviewRecordRef` that retains and revalidates the immutable registry
  snapshot; that snapshot has its own content-derived ID and canonical record
  ordering, so omission or substitution produces a different bound snapshot.
  Lineage accepts this resolved-current type plus its exact snapshot, not a
  freshly minted unresolved record or a tuple of caller-selected records.
- Within one registry snapshot, every review subject has exactly one decision
  root and at most one current head. A second same-subject root is an ambiguous
  competing chain, not another current conclusion.
- Typed review actions must be validated and planned before any mutation.
  Apply steps require stable identifiers, recompute rules, sidecar contracts,
  and approved expected-diff evidence.
- The implemented review-action contract is limited to identity verification
  and candidate-sidecar planning. Selected-candidate switching and
  manual-boundary area recomputation have no product apply authority until
  stable IDs, recompute rules, and expected-diff apply contracts exist. Consult
  the status index for current lane readiness.
- Peak-choice truth labels are separate from product authority. They can
  inform future automation only through a later goal with authority and
  expected-diff gates.
- Lockbox labels and shadow scoring are evidence acquisition only. They do
  not change selected peak, selected area, counted detection, workbook
  output, matrix values, or GUI behavior.
- Reviewer rationale and dispute discussion are private notes unless a
  compact public verdict or checker-readable artifact is required.

## Surfaces

| Surface | Role |
| --- | --- |
| Review Queue | Worklist and triage surface |
| `review_action_v1` | Typed action import schema |
| `ImmutableReviewRecordRef` | Owner-minted immutable sample/target/request/content/artifact/supersession reference for retained conclusions |
| `ReviewRecordRegistrySnapshot` | Content-digested immutable owner snapshot used for current/superseded resolution |
| `ResolvedReviewRecordRef` | Owner-validated current reference within an immutable registry snapshot |
| Dry-run application plan | Non-mutating audit of possible changes |
| Expected-diff approval rows | Gate before product-impacting changes |
| Lockbox sampling and label logs | Structured evidence-acquisition artifacts |
| Inter-reviewer agreement summary | Evidence quality and disagreement surface |

## Boundaries

- Owns: review queue generation, typed action import/validation, dry-run
  planning, lockbox sampling, and label log structure.
- Does not own: the current label truth for a specific lockbox case, whether
  a reviewer action is approved for product mutation, backfill writer
  authority, matrix activation scope, or GUI review UX details.
- Checker-backed lockbox queues, templates, and logs stay in-repo (not
  Obsidian). Private reviewer rationale belongs in Obsidian unless a compact
  public verdict is required.

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
- Moving checker-backed lockbox queues/templates/logs to Obsidian.
- Keeping private reviewer rationale in public repo because it came from a
  validation folder.

## See Also

- [Validation retention](../superpowers/validation/RETENTION.md)
- [Backfill](backfill.md)
- [Quant matrix](quant-matrix.md)
- [Productization](productization.md)
