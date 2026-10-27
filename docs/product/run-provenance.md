# Run Provenance

Run provenance records method and artifact context so a product run can be
reviewed or compared without relying on branch-local command
diaries.

[`processing-lineage.md`](processing-lineage.md) owns the workflow-neutral
request/attempt/artifact/delivery identity and retention contract. This page
owns Targeted workbook run metadata. A workbook target config hash is not a
complete lineage seal.

## Contract

- `target_config_hash` is a target-list display fragment. It is not a complete
  method record and does not reproduce a run.
- A generated timestamp, target config hash, output path, or successful process exit
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
- Normal Targeted delivery is one XLSX. `Run Metadata` records actual method
  settings, runtime configuration, and config fragments. `Targets` records target identity, ranges, pairing, and sample
  applicability. No HTML, CSV/TSV, or manifest sidecar is published, and
  Targeted runs have no replay entrypoint.
- A saved Targeted method is reusable intent, not a run receipt. Admission freezes
  settings, targets, and supplied injection-order input in a private temporary
  workspace. The caller retains it while queued/running and cleans it on completion
  or failure. Editing an active method cannot change the admitted inputs.
- A nonempty supplied sample context table is embedded as a hidden workbook
  sheet for reviewing the inputs behind
  decisions. This preserves values, not a byte-for-byte replay archive. No empty
  evidence sheet is added. RAW bytes are not snapshotted into the workbook.
- A Targeted run is one extraction pass; only its workbook crosses the
  publication transaction.
- A command diary is not a durable replay contract. If a run matters, the
  stable command shape and artifact contract must move into a repo owner.

## Surfaces

| Surface | Role |
| --- | --- |
| `Run Metadata` workbook sheet | Human-readable run metadata |
| `target_config_hash` | Target-list display fragment |
| CLI arguments and runner config | Run inputs and behavior surface |
| Output schema versions | Contract for produced CSV/TSV/workbook sidecars |

## Boundaries

- Owns: run metadata keys, config hashes, and output schema version tracking.
- Does not own: workflow-neutral processing identity, evidence retention,
  delivery-cell lineage, or delivery sealing. The processing-lineage contract
  owns these four concerns.
- Does not own: sample metadata role behavior, review action import, matrix activation authority, or
  productization tier decisions.

## Verification

- `Run Metadata` key tests.
- Output-schema tests when schema metadata changes.
- Expected-diff framing if product values, selected peaks, areas, counted
  detections, or workbook sheets can change.

## Pitfalls

- Treating a config hash as a complete method record.
- Treating a path, timestamp, or diagnostic `run_id` as a sealed delivery
  lineage.
- Hiding run-critical command shape only in a branch diary.
- Letting provenance sidecars recompute evidence or decisions.

## See Also

- [Agent parameter settings](../agent/parameter-settings.md)
- [Sample metadata and QC](sample-metadata-qc.md)
- [Processing identity, lineage, and retention](processing-lineage.md)
- [Productization](productization.md)
- [Product Validation](../agent/product-validation-contract.md)
