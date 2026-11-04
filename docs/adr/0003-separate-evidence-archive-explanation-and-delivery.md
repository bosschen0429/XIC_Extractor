---
status: accepted
---

# Keep Evidence, Explanation, And Delivery Consumer-Driven

Owner-local machine evidence, human explanation, and product delivery are
separate authority concepts when a current consumer needs them. That separation
does not require an implementation chain. The current repository has no
production archive/query consumer, approved human-explanation delivery surface,
or product-delivery plan type.

## Decision

- Keep the existing Backfill evidence TSV projection and its writer unchanged;
  its strict reader is the only reader that mints `EvidenceQueryResult`.
- Do not introduce a generic evidence archive, a human-explanation subsystem,
  a product-delivery framework, or parallel writer authority without a newly
  proved current consumer and separately approved contract.
- Do not treat a diagnostic bundle, report, or manifest as evidence of product
  delivery authority.

Record content, validity, disposition, and query-claim semantics remain with the
existing scientific owners; persistence cannot select values, include rows,
change disposition, or grant writer authority.

## Extraction gate

A workflow-neutral archive seam may be reconsidered only after a current
production consumer needs it and a second scientific owner proves the same
persistence, lifecycle, retention, reference, and publication invariants. A
human-explanation or product-delivery surface likewise requires its own current
consumer, authority boundary, and observable acceptance oracle. If those edges
are later introduced, keep evidence, explanation, and delivery authority
separate; do not infer that implementation work from this ADR alone.

## Rejected alternatives

- Introduce a new generic archive owner now. Rejected because no current
  production archive/query consumer exists.
- Introduce human-explanation or product-delivery scaffolding to preserve the
  separation mechanically. Rejected because boundary separation is not a
  consumer or an implementation mandate.

## Consequences

- Current scientific receipts, machine-evidence artifacts, output
  transactions, and writers remain unchanged.
- The strict TSV reader continues to distinguish valid content from missing,
  incomplete, unsupported, corrupt, or foreign-bound content.
- Human prose cannot mint scientific or writer authority. Product writers must
  continue to consume owner-issued decisions through their current contracts.
