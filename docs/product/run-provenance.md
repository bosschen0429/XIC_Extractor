# Run Provenance

Run provenance records method and artifact context so a product run can be
reviewed, replayed, or compared without relying on branch-local command
diaries.

[`processing-lineage.md`](processing-lineage.md) owns the workflow-neutral
request/attempt/artifact/delivery identity and retention contract. This page
owns the current Targeted method-manifest and run-metadata projection of the
processing-lineage contract. `method_manifest.json` is not yet a complete
lineage seal.

## Contract

- `config_hash` and `target_config_hash` are configuration fragments. The two
  hashes are not a complete method manifest and do not provide full method
  replay.
- A method manifest is an additive run envelope. The envelope holds input
  artifact hashes,
  settings fragments, runtime/backend, CLI invocation when available, output
  pointers, and schema versions.
- A generated timestamp, config hash, output path, or successful process exit
  is not a canonical processing-attempt or delivery identity.
- A point-in-time validation packet must distinguish the evaluated revision
  from the revision that sealed the reviewed evidence of the packet.
- A packet replay resolves each seal against the declared frozen Git object of
  that seal. A packet replay must not reinterpret the current checkout of the
  caller as historical evidence. Current CI and a new validation decision
  judge current source evolution.
- Provenance writers render run context only. They must not recompute domain
  evidence or change selected peaks, areas, confidence, counted detections,
  workbook values, or matrix values.
- A command diary is not a durable replay contract. If a run matters, the
  stable command shape and artifact contract must move into a repo owner.

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
  delivery-cell lineage, or delivery sealing. The processing-lineage contract
  owns these four concerns.
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
