---
status: proposed
---

Doc placement: repo_support_doc
Repo owner: docs/architecture-contract.md

# Separate Evidence Archive, Explanation, and Delivery

Machine-facing scientific evidence, human explanation, and product delivery
need separate authority boundaries. Evidence persistence translates finalized
owner facts, Explanation renders typed decisions and navigates stable
references, and Delivery compiles `ArtifactPlan` and publishes atomically.
This direction is accepted, but the implementation owner for evidence
persistence is not yet frozen.

## Considered options

- Deepen the existing Backfill Parquet modules into the typed persistence owner.
  This follows the earlier decision to reuse the proven adapter and avoids a
  repository-wide storage framework.
- Introduce a new `evidence_archive/` owner and retain existing Backfill Parquet
  modules as finite adapters. This gives a workflow-neutral archive boundary but
  may replace rather than deepen an existing owner.
- Keep TSV authoritative and convert it after the run. Rejected because it pays
  duplicate serialization, I/O, conversion, and storage costs.

The first two options conflict in owner locality. GitHub #313 must resolve that
choice and refreeze this ADR before archive implementation becomes claimable.

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
