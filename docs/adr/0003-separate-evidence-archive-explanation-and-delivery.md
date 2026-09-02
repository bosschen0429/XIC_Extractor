---
status: accepted
---

Doc placement: repo_support_doc
Repo owner: docs/architecture-contract.md

# Keep Evidence, Explanation, And Delivery Consumer-Driven

Owner-local machine evidence, human explanation, and product delivery are
separate authority concepts when a current consumer needs them. That separation
does not require an implementation chain. The current repository has no
production archive/query consumer, approved human-explanation delivery surface,
or product-delivery plan type. Existing Backfill Parquet support remains an
optional diagnostic/shadow capability rather than the first stage of a required
archive programme.

## Decision

- Keep `xic_extractor.alignment.backfill_evidence_parquet` and
  `xic_extractor.alignment.backfill_evidence_parquet_bundle` as owner-local
  helpers for the existing opt-in diagnostic Parquet shadow and its strict
  readback, verification, and bundle identity.
- Keep the existing Backfill/skipped TSV projections and their writers
  unchanged. The diagnostic Parquet projection does not replace or activate a
  production artifact.
- Do not introduce a generic evidence archive, a human-explanation subsystem,
  a product-delivery framework, or parallel writer authority without a newly
  proved current consumer and separately approved contract.
- Do not treat a diagnostic bundle, report, or manifest as evidence of product
  delivery authority.

The helpers do not become scientific or product owners. Record content,
validity, disposition, and query-claim semantics remain with the existing
scientific owners; persistence cannot select values, include rows, change
disposition, or grant writer authority.

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
- Remove the Backfill Parquet helpers with the unused programme. Rejected
  because the opt-in diagnostic/shadow CLI remains a current consumer; focused
  tests verify that retained surface but are not consumer evidence.

## Consequences

- Current scientific receipts, machine-evidence artifacts, Backfill diagnostic
  helpers, output transactions, and writers remain unchanged.
- Parquet remains neither a worker message bus nor a scientific/product owner.
  Existing strict readers continue to distinguish valid content from missing,
  incomplete, unsupported, corrupt, or foreign-bound content.
- Human prose cannot mint scientific or writer authority. Product writers must
  continue to consume owner-issued decisions through their current contracts.
- Earlier sequencing across these concepts is historical, not an active
  handoff or blocker. Active construction belongs in the current GitHub
  Issue/MAP surface only after a real consumer is proved.
