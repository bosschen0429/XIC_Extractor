# CID-NL default activation preflight v1

Date: 2026-06-20

Status: `blocked` for default activation replay. Target alignment evidence is
`pass`; product readiness remains `production_candidate` for this CID-NL
unblocker, not a default matrix promotion.

## Purpose

This packet checks whether the latest CID-NL 85RAW alignment output can be
used as the input for a default QuantMatrix activation while preserving the
current 511-cell Backfill writer authority. It is a no-write preflight.

It does not run RAW, update ProductWriter, regenerate the default matrix, change
workbooks or GUI behavior, grant CID-NL/MS2 direct write authority, or create a
second manifest/source of truth.

## Command

```powershell
python scripts/check_cid_nl_default_activation_preflight.py
```

Summary:

`cid_nl_default_activation_preflight_summary.json`

## Result

- `overall_status=blocked`.
- `target_alignment_evidence_status=pass`.
- `replay.status=blocked`.
- Current authority input: `511` accepted Backfill cells.
- Expected-diff input: `511` rows.
- Accepted cells missing from the new 85RAW matrix identity: `506`.
- First replay blocker:
  `FAM000380/BenignfatBC0980_DNA: peak_hypothesis_id missing from matrix identity`.

The existing activation owner therefore fails closed before writing output. The
old 511-cell authority bundle cannot be directly replayed onto the new CID-NL
alignment identity space.

## Target Evidence

The target rows are present and independently checked through matrix identity,
review row, TumorBC2312 provenance, tag, source candidate, and matrix value.
The provenance check requires a unique TumorBC2312 row, matching
`feature_family_id` / `group_hypothesis_id` / `public_family_id`, and exact
expected `source_candidate_id`; it is not only an m/z/product existence check.

- `300.1605 -> 184.113`: `FAM011499`, matrix row `Mz=300.161`,
  `RT=23.3493`, `85/85` nonblank, `identity_confidence=high`,
  `neutral_loss_tag=DNA_dR`,
  `source_candidate_id=TumorBC2312_DNA#19561@mz300.160635_p184.113235`.
- `301.165 -> 185.116`: `FAM011783`, matrix row `Mz=301.165`,
  `RT=23.3413`, `83/85` nonblank, `identity_confidence=review`,
  `neutral_loss_tag=DNA_dR`,
  `source_candidate_id=TumorBC2312_DNA#19561@mz301.164978_p185.115845`.

The preserved 301 row is not used as authority for the 300 row. It remains its
own dR-tag product row and carries review flags for backfill evidence / missing
independent backfill identity evidence.

## Heartbeat Audit

The prior alignment reruns were launched with live timing output:

- `output/discovery/cid_nl_product_ready_alignment_8raw_20260620_fix3/timing.live.json`
- `output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3/timing.live.json`

The Discovery input artifacts have timing summaries but no live heartbeat files:

- `output/discovery/cid_nl_product_ready_8raw_20260620_fix2/timing.json`
- `output/discovery/cid_nl_product_ready_85raw_20260620_fix2/timing.json`

So the accurate statement is: alignment 8/85RAW had heartbeat; Discovery
generation had timing summaries only.

## Product Interpretation

This preflight moves the decision forward: the alignment layer has the target
rows, but default activation is still blocked by replay identity compatibility.

The next product step is an explicit ID bridge / expected-diff contract that can
prove the current 511-cell Backfill authority is still exactly preserved while
the recovered `300.1605 -> 184.113` row is materialized in the released default
matrix. Until that bridge passes, the default tier remains the existing
`product_ready_default_matrix_activated` bundle, and CID-NL remains
`production_candidate` evidence only.
