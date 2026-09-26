# Sample Metadata and QC

Sample metadata and QC describe sample identity, injection order, roles,
batches, exclusions, and instrument/QC context. Sample metadata and QC are
public behavior surfaces because they affect ordering, grouping, review
interpretation, and future activation gates.

## Contract

- The accepted `sample_metadata` contract covers no-output-change sample
  identity, injection-order projection, and lookup parity. The status index
  owns current lane readiness.
- Within `sample_metadata`, `sample_name` is the owner-stable,
  case-sensitive primary sample key.
- `raw_stem` is a lookup alias only.
- Injection order, role, batch, matrix, group, and exclusion fields do not
  participate in canonical sample identity.
- Only `xic_extractor.sample_metadata` mints `CanonicalSampleIdentity`. The
  identity is `xic.sample:<sha256>` over an explicit stable owner namespace,
  the identity schema/version, and the primary sample key.
- The namespace must identify the owning study/sample registry. The namespace
  must not be a path, timestamp, or output location.
- Renaming `sample_name` creates a new identity. Changing only a projection
  field does not create a new identity.
- Resolution requires an immutable, content-digested
  `SampleMetadataSnapshot`.
- Snapshot identity includes the namespace and all normalized metadata rows.
  Snapshot identity is row-order neutral.
- Snapshot identity changes on an alias or projection mutation, even when a
  canonical sample identity remains stable.
- Resolution accepts only an exact primary key or an unambiguous current
  `raw_stem` alias.
- A missing, ambiguous, or stale alias-to-key binding fails closed.
- Resolution never normalizes or infers padded tokens, filename suffixes,
  paths, or row order.
- The returned resolution retains the exact lookup token and the owner metadata
  row. A lineage consumer can therefore bind the resolution to a request input
  without reconstructing sample identity.
- Sample metadata can act as an injection-order and sample-identity projection
  surface when sample metadata preserves existing output values.
- Role, batch, matrix type, exclusion, calibrator, blank, QC, or
  normalization behavior must not alter extraction, QC, alignment, counted,
  workbook, or matrix values without an explicit activation/export contract.
- Instrument-QC and calibration sidecars are evidence and observability
  surfaces. Only productization authority can promote these sidecars.
- Sample metadata is part of run provenance. Sample metadata does not replace
  method manifests or output schemas.

## Surfaces

| Surface | Role |
| --- | --- |
| `sample_metadata` CSV/TSV | Shared sample identity, order, role, batch, matrix, group, exclusion schema |
| `SampleMetadataSnapshot` | Owner-minted content identity for one complete normalized metadata snapshot and stable identity namespace |
| `mint_canonical_sample_identity` / `resolve_canonical_sample_identity` | Owner-only namespaced identity minting and self-validating fail-closed lookup resolution |
| `injection_order_source` | Current no-value-change ordering projection |
| Instrument-QC sidecars | QC context and trend evidence |
| Calibration/normalization previews | Shadow or gated evidence surfaces |
| Run metadata / method manifest | Provenance links to sample metadata inputs |

## Boundaries

- Owns: sample identity schema, injection-order projection, role/batch/
  exclusion field definitions, and QC sidecar structure.
- Does not own: a specific sample's role or exclusion decision,
  normalization/calibration activation policy, or matrix activation scope.
- Any value-changing role behavior requires a separate activation contract
  and expected-diff review.

## Verification

- Schema validation tests for metadata fields and role values.
- Identity tests for deterministic owner minting, alias parity, rename
  invalidation, projection-field neutrality, and missing/ambiguous/stale
  resolution.
- Parity tests proving no value change when metadata is only an
  order/lookup source.
- Expected-diff packet for any role-aware value behavior.
- Downstream matrix/workbook tests if outputs can change.

## Pitfalls

- Treating sample roles as value-changing policy before activation.
- Using exclusions to silently remove product rows or cells.
- Treating QC sidecars or calibration previews as matrix authority.
- Hiding sample identity or batch assumptions only in private notes.

## See Also

- [Run provenance](run-provenance.md)
- [Productization](productization.md)
- [Product validation contract](../agent/product-validation-contract.md)
- [Productization status index](../superpowers/validation/productization_status_index.tsv)
