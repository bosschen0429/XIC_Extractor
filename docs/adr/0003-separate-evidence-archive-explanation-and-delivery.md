---
status: accepted
---

Doc placement: repo_support_doc
Repo owner: docs/architecture-contract.md

# Separate Evidence Archive, Explanation, and Delivery

Machine-facing scientific evidence, human explanation, and product delivery
need separate authority boundaries. Evidence persistence translates finalized
owner facts, Explanation renders typed decisions and navigates stable
references, and Delivery compiles `ArtifactPlan` and publishes atomically.
The existing Backfill Parquet modules are the implementation owner for the
first canonical typed archive. They are deepened in place instead of being
wrapped by a new workflow-neutral storage package.

## Decision

- Deepen `xic_extractor.alignment.backfill_evidence_parquet` into the native
  typed persistence and bounded-query owner for finalized Backfill and skipped
  evidence records.
- Deepen `xic_extractor.alignment.backfill_evidence_parquet_bundle` into the
  staging, verification, bundle/reference-identity, and atomic archive
  publication owner. Its publication is internal archive publication, not final
  product delivery.
- Replace the TSV-mediated strings-only shadow path. TSV remains only as a
  finite compatibility adapter for named consumers, with explicit removal
  conditions.
- Do not introduce `evidence_archive/`, a generic evidence store, or a shared
  database framework in this migration.

The archive implementation owner does not become a scientific owner. Record
content, validity, disposition, and query-claim semantics remain with the
existing scientific owners; persistence cannot select values, include rows,
change disposition, or grant writer authority.

## Extraction gate

A workflow-neutral archive seam may be reconsidered only after a second current
scientific owner needs the same persistence interface and shares the same
lifecycle, retention, reference, and atomic-publication invariants. Any later
extraction must move the proven implementation and retire the superseded path;
it must not layer a parallel generic owner over the Backfill modules.

## Rejected alternatives

- Introduce a new `evidence_archive/` owner now and retain the Backfill modules
  as adapters. Rejected because the current repository has no second production
  record family proving that generic seam, while the existing modules already
  own the relevant persistence mechanics.
- Keep TSV authoritative and convert it after the run. Rejected because it pays
  duplicate serialization, I/O, conversion, and storage costs and loses native
  types before canonical persistence.

GitHub #313 carries the executable migration packet. Its implementation remains
blocked by #312 even though this owner decision is now closed.

## Consequences already fixed

- Same-run typed transport and durable persistence are separate seams. Parquet
  is not a worker message bus or a scientific owner.
- Persisted records are lazy, bounded projections of a finalized owner result.
  Writers must not materialize a complete row-list projection or persist
  provisional science.
- Required artifact failure is fail-closed. Resource pressure cannot silently
  drop rows, reduce evidence, change format, or fall back to TSV.
- Archive activation requires semantic parity and a total representation-cost
  oracle covering producer time, peak memory, temporary disk, total bytes,
  consumer queries, lifecycle failure, and packaging—not file size alone.
- Query results distinguish scientific absence from `not_produced`,
  `not_retained`, incomplete execution, unsupported schema, corrupt content,
  and foreign binding. Present-but-invalid content fails closed.
- Explanation never parses prose into authority. Delivery never recomputes
  scientific decisions and exposes no partial or mixed-revision bundle.

Current contracts: `docs/architecture-contract.md`,
`docs/agent/architecture-public-contracts.md`, and
`docs/product/shared-processing-kernel.md`. Delivery tickets: GitHub #313,
#315, and #314.
