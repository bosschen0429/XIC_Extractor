# Instrument QC and Calibration

Instrument QC and calibration artifacts diagnose method and instrument
behavior. These artifacts are evidence and review surfaces. Only a separate
activation contract can promote a correction or gate.

## Contract

- Clean standards diagnose clean-matrix instrument/method behavior. Clean
  standards alone cannot represent biological matrix effects.
- Biological QC ISTDs are the primary anchors for real-matrix RT/response
  transfer evidence when they exist.
- HCD/product-ion audit is a human review and plausibility surface. It must
  not become a hard pass/fail production gate without an explicit activation
  contract.
- Method evidence remains method-specific:
  - SDO/LEK interpretation uses HCD.
  - MixSTDs can use CID or HCD.
  - A Targeted method consumes the evidence provider configured for that
    method.
- Architecture convergence must not replace these method choices with a
  universal CID-NL or QC rule.
- Level 3 RT calibration remains `NO-GO`. Leave-one-anchor-out residuals,
  transfer support, extrapolated rows, and blocked rows do not yet support
  production correction.
- RT-aware midterm preview artifacts are shadow/readiness evidence only.
  Preview artifacts do not activate RT correction, response correction,
  normalization, matrix mutation, or default gating.
- The next safe calibration direction is shadow/readiness evidence. The next
  safe calibration direction is not value mutation.
- `scripts/add_istd_rt_trend.py` is a maintained operator diagnostic for
  visualizing measured ISTD RT drift. The observed ISTD trend does not justify
  inferring, correcting, or gating other-feature RT behavior.
- A future ModeTrack sensitivity packet must not assume one batch-wide
  monotonic shift. The packet must:
  - permit feature-specific directions and adjacent-injection jumps;
  - examine several high-detection unknown features and method-appropriate
    ISTDs;
  - report interpolation/extrapolation support;
  - retain unassessable cases.
- `scripts/run_instrument_qc.py` is a contract-bearing product adapter. The
  method-document path of `run_instrument_qc.py` already produces sequence,
  injection-order, and `sample_metadata` projections.
- A future downstream handoff must reuse these projections. The handoff must
  not reimplement them.

## Surfaces

| Surface | Role |
| --- | --- |
| Instrument QC trend TSV/workbook | Clean-standard and biological QC review surface |
| `run_instrument_qc.py` method-document outputs | Sequence manifest, injection-order projection, and additive sample metadata |
| `add_istd_rt_trend.py` visualization | Direct ISTD drift review; not an other-feature RT model |
| HCD audit TSV/JSON/workbook sheet | Product-ion audit evidence |
| Calibration maturity gate | Readiness and blocker summary |
| Biological ISTD transfer audit | Real-matrix transfer evidence |
| Calibration preview outputs | Shadow or diagnostic evidence only |

## Boundaries

- Owns: clean-vs-biological QC evidence roles, HCD audit output format,
  calibration maturity gate classification, and transfer audit structure.
- Does not own: a specific run's QC acceptance, RT/response/normalization
  activation, DNP or downstream normalization behavior, or matrix activation.
- Dated QC/calibration notes and specs become history stubs after this page
  absorbs their stable claims.
- SDOLEK trend rows carry `injection_order`. The rows remain in
  filename/target production order.
- The TSV/workbook writers do not sort by injection order. A sorted
  compatibility projection is a known product gap. The gap relates to the
  Untargeted downstream handoff in #137.

## Verification

- Schema tests for QC and HCD audit outputs.
- Parity tests when adding metadata or sidecars.
- Blocker/readiness tests for calibration gates.
- Expected-diff packet for value-changing corrections.

## Pitfalls

- Treating clean-standard behavior as proof of biological matrix performance.
- Making HCD support a hard gate from audit evidence alone.
- Applying Level 3 RT correction while the maturity gate remains `NO-GO`.
- Treating a global LOESS/ISTD trend as identity or fill authority for every
  unknown feature.
- Hiding calibration assumptions in private notes without a repo owner.

## See Also

- [Sample metadata and QC](sample-metadata-qc.md)
- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Evidence spine](evidence-spine.md)
- [Review roundtrip](review-roundtrip.md)
- [Productization](productization.md)
- [Historical compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md)
