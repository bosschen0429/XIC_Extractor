# CID-NL product-ready alignment validation v1

Date: 2026-06-20

Purpose: validate that the existing A/owner-deepened CID-NL Discovery path can
materialize the recovered `300.1605 -> 184.113` row through alignment without
deleting or demoting the valid `301.165 -> 185.116` dR-tag row.

This packet is evidence for a later default-activation expected-diff goal. It
does not grant ProductWriter authority, does not update the tracked default
QuantMatrix activation bundle, and does not change Backfill writer authority.

## Historical Code Contract (retired)

This packet recorded the former claim-registry and primary-consolidation
heuristics. Issue #302 retired those paths because a valid canonical population
left them no owner-closed mutation and the remaining Matrix-value winner rule
was a second disposition owner. The old focused commands were removed with the
implementation and must not be reused as a current contract.

The retained 8RAW/85RAW observations below are historical evidence only. Current
behavior is pinned by the CrossSample identity/lineage tests and by
`tests/test_alignment_pipeline.py::test_pipeline_writes_owner_matrix_without_retired_post_grouping_heuristics`.

## 8RAW Validation

Input Discovery artifact:

`output/discovery/cid_nl_product_ready_8raw_20260620_fix2`

Alignment rerun:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --discovery-batch-index output/discovery/cid_nl_product_ready_8raw_20260620_fix2/discovery_batch_index.csv `
  --raw-dir <raw-data>/validation `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir output/discovery/cid_nl_product_ready_alignment_8raw_20260620_fix3 `
  --expected-sample-count 8 `
  --output-level validation-minimal `
  --backfill-scope production-equivalent `
  --audit-evidence-mode none `
  --timing-output output/discovery/cid_nl_product_ready_alignment_8raw_20260620_fix3/timing.json `
  --timing-live-output output/discovery/cid_nl_product_ready_alignment_8raw_20260620_fix3/timing.live.json
```

Observed acceptance:

- `300.1605 -> 184.113`: matrix row `Mz=300.16`, `RT=23.4313`, `8/8`
  nonblank; identity `FAM001386`, `accepted_cell_count=8`,
  `evidence_status=product_matrix_identity_complete`,
  `identity_confidence=high`.
- `301.165 -> 185.116`: matrix row `Mz=301.165`, `RT=23.4261`, `8/8`
  nonblank; identity `FAM001414`, `accepted_cell_count=8`,
  `evidence_status=product_matrix_identity_complete`,
  `identity_confidence=high`, `consolidation_state=not_consolidated`.
- The shifted `301.171 -> 185.123` row remains a separate matrix identity
  (`FAM001415`) instead of absorbing the exact `301.165 -> 185.116` row.

## 85RAW Validation

Input Discovery artifact:

`output/discovery/cid_nl_product_ready_85raw_20260620_fix2`

Discovery summary:

- sample count: `85`;
- parser smoke: pass;
- candidate rows: `317036`;
- `ms1_feature_nl_rescued`: `66092`;
- `ms1_feature_nl_supported`: `13351`;
- `review_only_orphan_nl`: `237593`;
- duplicate `ms1_feature_row_id`: `0`;
- `300.1605 -> 184.113` MS1-backed hits: `161`, spanning all `85` samples;
- `301.165 -> 185.116` MS1-backed hits: `153`, spanning all `85` samples.

Alignment rerun:

```powershell
.venv\Scripts\python.exe -m scripts.run_alignment `
  --discovery-batch-index output/discovery/cid_nl_product_ready_85raw_20260620_fix2/discovery_batch_index.csv `
  --raw-dir $env:XIC_RAW_ROOT `
  --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR `
  --output-dir output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3 `
  --expected-sample-count 85 `
  --output-level validation-minimal `
  --backfill-scope production-equivalent `
  --audit-evidence-mode none `
  --performance-profile validation-fast `
  --raw-workers 11 `
  --owner-backfill-window-strategy super-window `
  --owner-backfill-superwindow-span-factor 2 `
  --timing-output output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3/timing.json `
  --timing-live-output output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3/timing.live.json
```

Observed acceptance:

- `300.1605 -> 184.113`: matrix row `Mz=300.161`, `RT=23.3493`, `85/85`
  nonblank; identity `FAM011499`, `accepted_cell_count=85`,
  `accepted_sample_count=85`,
  `evidence_status=product_matrix_identity_complete`,
  `identity_confidence=high`.
- `301.165 -> 185.116`: matrix row `Mz=301.165`, `RT=23.3413`, `83/85`
  nonblank; identity `FAM011783`, `accepted_cell_count=83`,
  `accepted_sample_count=83`,
  `evidence_status=product_matrix_identity_complete`,
  `identity_decision=production_family`. This row remains centered at
  `family_center_mz=301.165`, `family_product_mz=185.116`; the earlier
  erroneous `301.171 / 185.123` winner collapse is gone.
- Residual 85RAW caution for the preserved `301.165 -> 185.116` row:
  `identity_confidence=review`; the review row carries
  `backfill_cell_evidence_required`, `backfill_rescue_review_only`, and
  `missing_independent_backfill_identity_evidence`. This is acceptable for
  preservation/row-identity evidence, but it is not enough to claim default
  activation readiness by itself.
- TumorBC2312 provenance for the preserved row:
  `source_candidate_id=TumorBC2312_DNA#19561@mz301.164978_p185.115845`,
  `neutral_loss_tag=DNA_dR`, `write_matrix_value=TRUE`,
  `include_in_primary_matrix=TRUE`.
- TumorBC2312 Discovery source row:
  `discovery_candidate_state=ms1_feature_nl_rescued`,
  `ms1_feature_row_id=TumorBC2312_DNA|DNA_dR|301.164978|23.341692`,
  seed scans `19261;19358;19417;19561;19563`.

## Boundary

Readiness for this packet is `production_candidate` for the CID-NL
Discovery-to-alignment row-identity path. It is not
`product_ready_default_matrix_activated` because the tracked default activation
bundle still belongs to the current 511-cell Backfill authority and was not
regenerated from these 85RAW artifacts.

The next product step is a separate expected-diff/default-activation goal that
explicitly decides whether and how this CID-NL alignment evidence should change
the released default matrix. That future goal must keep candidates out of
matrix-row authority and must not turn CID-NL/MS2 evidence directly into
ProductWriter authority.
