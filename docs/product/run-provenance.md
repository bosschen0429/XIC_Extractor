# Run Provenance

Run provenance records method and artifact context so a product run can be
reviewed, replayed, or compared without relying on branch-local command
diaries.

The workflow-neutral request/attempt/artifact/delivery identity and retention
contract is owned by [`processing-lineage.md`](processing-lineage.md). This
page owns the current Targeted method-manifest and run-metadata projection of
that broader contract; `method_manifest.json` is not yet a complete lineage
seal.

## Contract

- `config_hash` and `target_config_hash` are useful configuration fragments,
  not a complete method manifest. Do not treat them as full method replay.
- A method manifest is an additive run envelope: input artifact hashes,
  settings fragments, runtime/backend, CLI invocation when available, output
  pointers, and schema versions.
- A generated timestamp, config hash, output path, or successful process exit
  is not a canonical processing-attempt or delivery identity.
- Provenance writers render run context only. They must not recompute domain
  evidence or change selected peaks, areas, confidence, counted detections,
  workbook values, or matrix values.
- Command diaries are not durable replay contracts. If a run matters, convert
  the stable command shape and artifact contract into a repo owner.

## Surfaces

| Surface | Role |
| --- | --- |
| `Run Metadata` workbook sheet | Human-readable run metadata |
| `config_hash` / `target_config_hash` | Configuration and target fragments |
| `method_manifest.json` | Machine-readable run envelope when enabled |
| CLI arguments and runner config | Replay inputs and behavior surface |
| Output schema versions | Contract for produced CSV/TSV/workbook sidecars |

## Boundaries

- Owns: method manifest structure, run metadata keys, config hashes, output
  schema version tracking, and replay artifact validation.
- Does not own: workflow-neutral processing identity, evidence retention,
  delivery-cell lineage, or delivery sealing; those belong to the processing
  lineage contract.
- Does not own: exact replay CLI semantics for every workflow, sample metadata
  role behavior, review action import, matrix activation authority, or
  productization tier decisions.
- Expanding a targeted method-manifest slice into sample metadata, review
  roundtrip, or matrix activation requires a separate contract.

## Verification

- Manifest serialization and validation tests.
- Replay smoke for documented runner paths.
- Output-schema tests when schema metadata changes.
- Expected-diff framing if product values, selected peaks, areas, counted
  detections, or workbook sheets can change.

## Pitfalls

- Treating a config hash as full method replay.
- Treating a path, timestamp, diagnostic `run_id`, or method manifest as a
  sealed delivery lineage.
- Hiding replay-critical command shape only in a branch diary.
- Letting provenance sidecars recompute evidence or decisions.

## See Also

- [Agent parameter settings](../agent/parameter-settings.md)
- [Sample metadata and QC](sample-metadata-qc.md)
- [Review roundtrip](review-roundtrip.md)
- [Processing identity, lineage, and retention](processing-lineage.md)
- [Productization](productization.md)
- [Validation harness](../validation/harness.md)
