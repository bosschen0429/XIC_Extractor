# CID-NL NL116 ISTD Calibration 2RAW v1

Validation status: `diagnostic_only`

This packet records the first bounded RAW-backed calibration loop for Issue
#125. It is not a formal holdout and does not contribute to tracer performance
metrics.

## Scope

- Method scope: NL116-only Untargeted Discovery evidence calibration.
- Expected-positive anchors: the six isotope-labelled internal standards with
  `neutral_loss_da=116.0474`.
- Samples:
  - `Breast_Cancer_Tissue_pooled_QC3` as pooled-QC context;
  - `TumorBC2263_DNA` as a biological challenge context.
- Case count: 12 sample-target cases.
- Runtime: Windows repo `.venv`, serial backend, one worker.
- Output mode: CSV only; workbook generation was skipped.
- Targeted MS1 rescue policy: `explicit_support_tsv`, with no support TSV.
- Integration audit baseline: AsLS.

The six-target config was derived from the then-current target template
(retained in Git); the current packaged template is
`xic_extractor/configuration/data/targets.csv`. The NL132
`[13C,15N2]-8-oxo-Guo` anchor is intentionally excluded because it is outside
the current NL116-only proposal scope.

## What completed

RAW extraction completed for both selected samples and emitted 12 target rows,
peak-candidate diagnostics, selected-envelope diagnostics, and method
provenance. A separate review surface was then generated from the same bounded
2RAW subset.

The eligible blinded calibration surface is `blind_review`. It exposes:

- a full-context raw XIC so neighboring peaks remain visible;
- a fixed 1.6-minute-or-narrower local view;
- the AsLS baseline and the active `ChromPeakSegment` boundary morphology:
  positive-clipped AsLS residual followed by Gaussian15 smoothing with 15 points;
- expected precursor, neutral-loss, and product coordinates;
- unclassified scan-level precursor/product observations.

It does not expose machine scores, candidate rankings, machine calls, selected
apexes, selected boundaries, areas, confidence, legacy output, or Targeted
comparison output. `calibration_case_manifest.tsv` binds the 12 case locators
and plot hashes.

The eligible local review files are:

- `output/validation/cid_nl_nl116_istd_calibration_2raw/blind_review/blind_calibration_gallery.html`
- `output/validation/cid_nl_nl116_istd_calibration_2raw/blind_review/blind_calibration_review.tsv`
- `output/validation/cid_nl_nl116_istd_calibration_2raw/blind_review/blind_calibration_ms2_scan_evidence.tsv`
- `output/validation/cid_nl_nl116_istd_calibration_2raw/blind_review/blind_calibration_plot_methods.tsv`

The original `blind_review` surface is retained for audit but is superseded and
ineligible for human labels. Its target-window crop clipped valid trace context
in at least one case, and its unsmoothed residual did not match the active
Gaussian15 boundary owner. No labels should be entered against that version.

The machine-facing extraction and selected-envelope gallery remain under the
same ignored task root but must not be opened before the human calibration
labels are sealed.

## Runtime correction

An initial WSL-Python attempt produced 12 file errors because `pythonnet`
searched for a Linux Mono runtime. It did not open the RAW files and is retained
at:

`output/validation/cid_nl_nl116_istd_calibration_2raw/failed_wsl_runtime_attempt/`

The successful run used the repository's documented Windows `.venv` and
Windows/.NET Thermo runtime. This is an execution correction, not a method or
threshold change.

## Human calibration result

The owner completed and sealed all 12 calibration cases before machine reveal.
All 12 were judged `evidence_sufficient=sufficient`,
`feature_identity_call=accept`, and `peak_boundary_call=observed`, with explicit
human left/right boundaries in `blind_calibration_review_completed.tsv`.

The provisional human acceptance heuristic from this calibration is: accept a
well-bounded Gaussian15-smoothed MS1 peak without repeated up/down morphology
when at least one MS2 scan beneath that human peak boundary supports NL116.
Boundary placement follows return toward the local baseline. The owner later
clarified that 5% was only an uncertain recollection of a percentage-based
machine rule, not a proposed value. The human coordinates approximate the full
visibly attributable lobe near baseline and intentionally form a conservative
outer envelope rather than an exact threshold crossing.

## Human-vs-machine comparison

The post-seal comparison found observation agreement in 12/12 cases and machine
`Counted Detection=TRUE` in 12/12. Eleven machine rows were `detected_clean`.
C010 was `detected_flagged` / `LOW` only because of `targeted_rt_review`; it
still carried complete MS1/MS2 support and a counted detection, so it is
classified as machine-more-cautious rather than a detection disagreement.

All 12 machine intervals were strictly inside the conservative owner envelopes. Median
absolute left/right differences were 0.0940 and 0.0907 minutes; the maximum
left/right differences were 0.1624 and 0.1427 minutes. Machine intervals were
shorter by a median 0.1914 minutes, with median boundary IoU 0.8070. Because the
human coordinates were intentionally coarse and wide, this is expected
granularity rather than evidence of an identity or boundary failure.

The active machine `baseline_return_fraction` is 2%. The human review does not
assert a competing numerical threshold and therefore provides no evidence for
changing that value. Its role is to check that machine boundaries remain inside
the attributable lobe without cutting meaningful signal.

## Human-boundary area sensitivity

The same two RAW files were reopened once each and the six XIC requests per RAW
were extracted in one batch. The sealed human boundaries and machine boundaries
were reintegrated with the same `gaussian15_positive_asls_residual` owner and
15-point morphology window.

The recomputed machine areas matched the recorded machine areas with maximum
relative error `9e-10`. Across the 12 cases, machine boundaries retained a
median `99.2360%` and minimum `98.9616%` of the conservative human-envelope
area. The extra human-envelope tails contributed a median `0.7640%` and maximum
`1.0384%` of area.

This calibration supports containment plus area retention, rather than exact
coordinate equality, as the boundary comparison. It provides no evidence that
the active 2% return rule is materially truncating these clean NL116 ISTD peaks.
The owner accepted these tail-area differences and preferred the reproducible
machine policy over repeated manual boundary placement.

For formal holdout, the boundary/area comparison is now frozen as:

- the human coordinates form a conservative acceptable outer envelope;
- the machine interval must be contained inside that envelope;
- the machine area must retain at least `98%` of the area obtained from the
  human envelope using the same frozen morphology and deterministic integrator.

The `98%` floor is a validation tolerance, not a new runtime threshold. Exact
coordinate equality, fixed minute deltas, and IoU remain descriptive only. The
active 2% sustained baseline-return rule is unchanged.

The two frozen rules are recorded in
`calibration_gate_table.tsv`. Missing boundaries, non-positive human-envelope
area, or unavailable reintegration evidence fail closed as `inconclusive`;
they are not silently omitted from the eligible denominator.

The sealed human labels, human-versus-machine comparison, and area-sensitivity
rows are now retained byte-for-byte in this tracked packet. The 12 blind plot
bytes remain hash-bound in ignored local output and do not yet have a durable
portable archive. `calibration_evidence_retention_status.json` therefore keeps
the formal portability gate `inconclusive`. Closing it requires copying the
existing plots to an approved durable artifact store and recording the locator
and aggregate hash; it does not require another RAW replay.

## What remains

The calibration labels, deterministic integration method, and boundary/area
tolerance are frozen. Issue #125 now owns the complete review/adjudication,
sampling, reference-universe, narrow-scope, gate/verdict, and synthetic
contracts. Values for the named statistical parameters, populated
tested-tracer/reference/holdout/review manifests, #123 lineage bindings, #128
performance budget, and portable visual-evidence archive remain formal-
execution prerequisites. No additional RAW run is required.

## Authority boundary

This packet grants no Matrix, Backfill writer, counted-detection, selected-area,
workbook, GUI, activation, export, or `ProductWriter` authority. Expected ISTD
presence does not grant paired-analyte presence or compound identification.
