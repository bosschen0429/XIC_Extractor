# CID-NL default activation gallery review v1

Date: 2026-06-21

Status: `production_candidate_activation_adopt_gate` for the narrowed 95-cell
CID-NL activation bundle. The generated Gallery, expected-diff contracts,
activated-copy matrix, acceptance gate, and adopt gate remain non-default
evidence. They do not install a new active ProductWriter/default matrix output.

Version-control policy: this directory keeps the human-readable report only.
Full gallery HTML, TSV indexes, browser-smoke screenshots, trace JSON/TSV, and
PNG overlays are written under
`output/validation/cid_nl_default_activation_gallery_review/`, which is
intentionally ignored by git to keep PR diffs reviewable.

Reader guide: `docs/superpowers/validation/evidence_overlay_interpretation_guide.html`
is the maintained plain-language guide for the historical Backfill and CID-NL
Discovery overlay evidence recorded by this packet.

## Plain-Language Decision

This packet is now interpreted through two separate gates:

1. Feature inclusion: does CID-NL/MS2 evidence plus MS1 chromatographic support
   justify carrying a successor hypothesis forward as an untargeted feature?
2. Identity authority: should that successor replace, merge with, dedupe, or
   migrate cells from an older source row?

The existing successor-authority packet remains a useful diagnostic input, but
its old `write_authorized` wording must not be read as active ProductWriter
authority. It rebuilt the old 511 accepted Backfill cells in a CID-NL
row-identity space:

- 147 cells are candidate feature-inclusion / future-adoption cells.
- 337 cells already have detected-baseline successor feature context.
- 27 cells are omitted because no safe successor target exists.

This gallery packet makes those decisions reviewable with the same Gallery/PNG
interaction shell already used by Backfill review, but with CID-NL-specific
review semantics. It is evidence for an adopt / hold / reject decision, not
writer authority by itself.

CID-NL rows use `Candidate / Existing / Omit` impact labels in the HTML, not
the generic Backfill `NL / Candidate-only / Review` labels and not the older
`Write / Preserve` adoption wording. Overlay PNG orange detected/rescued traces
are MS1 trace status only; they do not prove NL-tag coverage, do not force
source/successor replacement, and do not grant ProductWriter authority.

Each CID-NL detail row now separates the review into two questions:

- `Feature inclusion question`: does CID-NL/MS2 evidence plus MS1 trace context
  support carrying the successor as an untargeted feature candidate?
- `Identity authority question`: should source and successor be replaced,
  merged, deduped, migrated, or kept as co-existing features?

The CID-specific differential TSV and HTML detail cards expose
`source_peak_hypothesis_id`, `successor_peak_hypothesis_id`, and
`successor_decision`, so old-to-successor Discovery changes are visible as
explicit `old -> successor` mappings instead of being hidden in free text. The
shared representative-cells TSV intentionally stays on the existing Backfill
Gallery v0 schema.

For paired differential overlays, source/successor m/z values are allowed to
differ. The plot is not asking whether the same ion was drawn twice. It asks
whether the successor has MS1-backed feature support, and separately whether
the source/successor relationship justifies replacement, merge, or dedupe. If
both source and successor have peak shapes, that can mean co-existing features
or unresolved identity, not automatic successor failure.

## Historical Artifact Receipt

The counts below describe the retained validation run. The standalone Gallery
renderer and its commands have since been retired; these rows are provenance,
not current regeneration instructions.

| Artifact | Rows / Count | Meaning |
| --- | ---: | --- |
| Retired standalone reconciliation Gallery HTML | 90 groups | Historical CID-NL Discovery identity review rendering. Current completed-evidence review uses XIC Inspector. |
| `backfill_evidence_reconciliation_groups.tsv` | 90 | Deterministic group index for the gallery. |
| `backfill_evidence_reconciliation_representative_cells.tsv` | 529 | Representative row/sample/provenance cells behind the review groups. |
| `cid_nl_default_activation_overlay_review_queue.tsv` | 85 | Queue consumed by the existing RAW-backed family MS1 overlay batch renderer. |
| `cid_nl_discovery_identity_differential_review.tsv` | 87 | No-RAW old-to-successor identity transition index for differential review / paired-overlay planning. |
| `overlays/family_ms1_overlay_batch_summary.tsv` | 85 success rows | RAW-backed overlay batch summary linked back into the gallery. |
| `differential_overlays/cid_nl_differential_overlay_gallery.html` | 78 transitions | Paired old/source vs successor PeakHypothesis overlay review Gallery for the ready source/successor transitions. |
| `differential_overlays/cid_nl_differential_overlay_review_summary.tsv` | 78 success rows | Machine-readable status, PNG/trace paths, Gaussian15 source/successor intensity summaries, feature-inclusion gate, identity-authority gate, and candidate/existing counts. |
| `differential_overlays/*.png` / `*_trace_data.json` | 78 / 78 | Paired source/successor RAW traces batched by sample for human review; PNGs display Gaussian15-smoothed traces while JSON preserves raw trace arrays. |
| `feature_inclusion_gate/cid_nl_feature_inclusion_gate_summary.tsv` | 1 | Product-gate diagnostic summary that splits the 147 legacy candidate cells into supported, review-required, and current-bundle-blocked buckets. |
| `feature_inclusion_gate/cid_nl_identity_expected_diff_queue.tsv` | 14 transitions | Supported candidate transitions that may advance to expected-diff design, still without ProductWriter authority. |
| `feature_inclusion_gate/cid_nl_supported_candidate_expected_diff_contract.tsv` | 73 cells | Cell-level expected-diff design contract for supported candidates, with sample identity, source/successor identity, tag state, candidate value, and explicit no-product-write authority gate. |
| `feature_inclusion_gate/cid_nl_feature_inclusion_review_queue.tsv` | 12 transitions | Mixed, close, or guardrail-sensitive candidate transitions that must not enter expected-diff design yet. |
| `feature_inclusion_gate/cid_nl_agent_resolved_expected_diff_contract.tsv` | 9 cells | Second-pass agent-resolved expected-diff design rows from the former review queue. |
| `feature_inclusion_gate/cid_nl_agent_resolved_hold_queue.tsv` | 6 transitions | Former review transitions held out of the current bundle because source/no-support evidence is stronger than successor support. |
| `cid_nl_manual_feature_inclusion_review.tsv` | 4 transitions | Versioned human/domain verdicts for the formerly unresolved user-review transitions. |
| `feature_inclusion_gate/cid_nl_manual_resolved_expected_diff_contract.tsv` | 13 cells | Human-reviewed expected-diff design rows; still diagnostic-only and still not ProductWriter authority. |
| `feature_inclusion_gate/cid_nl_manual_resolved_hold_queue.tsv` | 0 transitions | Human-reviewed transitions held out of the current bundle. |
| `feature_inclusion_gate/cid_nl_user_review_queue.tsv` | 0 transitions | Remaining hard cases after the manual review verdicts are applied. |
| `feature_inclusion_gate/cid_nl_feature_inclusion_blocked_queue.tsv` | 17 transitions | Candidate transitions excluded from the current activation bundle by the current paired overlay evidence. |
| `activation_copy_candidate/alignment_matrix_activated_copy.tsv` | 95 changed cells | Validation-only activated matrix copy for supported, agent-resolved, and manual-resolved expected-diff rows; not the default matrix. |
| `activation_copy_candidate/cid_nl_activation_copy_value_delta.tsv` | 95 rows | Cell-level value delta proving every activated-copy write came from the expected-diff contract and wrote into a blank matrix cell. |
| `activation_copy_candidate/cid_nl_activation_copy_candidate_summary.tsv` | 1 | Copy-only activation checker summary with explicit false flags for ProductWriter/default matrix/workbook/GUI authority. |
| `activation_copy_candidate/acceptance/cid_nl_activation_copy_acceptance_summary.tsv` | 1 | Acceptance gate summary proving the activated-copy diff exactly matches the 95-cell contract and still is not production-ready/default authority. |
| `activation_copy_candidate/acceptance/cid_nl_activation_copy_matrix_diff.tsv` | 95 rows | Exact input-vs-copy matrix diff used by the acceptance gate. |
| `activation_adopt_gate/cid_nl_activation_adopt_gate_summary.tsv` | 1 | Adopt/hold gate summary for the accepted 95-cell validation copy; adopt-ready but still not production-ready/default authority. |
| `activation_adopt_gate/cid_nl_activation_adopt_manifest.tsv` | 20 transitions | Transition-level adopt manifest grouped by contract source; avoids versioning the full 95-cell table. |

Generated location:

- `output/validation/cid_nl_default_activation_gallery_review/`

## Current Producers And Review Entry

The no-RAW transition TSV is produced by
`tools.diagnostics.cid_nl_default_activation_gallery_review.write_transition_review_evidence`.
The paired RAW evidence is produced by
`tools.diagnostics.cid_nl_differential_overlay_review.write_differential_evidence`.
Both are callable artifact producers; the retired Gallery packet CLI is not a
compatibility surface.

Generate RAW-backed overlays when the overlay summary is missing:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.family_ms1_overlay_batch --review-queue-tsv output/validation/cid_nl_default_activation_gallery_review/cid_nl_default_activation_overlay_review_queue.tsv --alignment-cells output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3/alignment_backfill_cell_evidence.tsv --raw-dir $env:XIC_RAW_ROOT --dll-dir $env:THERMO_RAWFILE_READER_DLL_DIR --output-dir output/validation/cid_nl_default_activation_gallery_review/overlays --limit 85 --no-pdf --reuse-existing
```

For completed evidence databases, use the installed read-only Inspector:

```powershell
xic-inspector --report cid_nl_explanation.sqlite --output review.html --strategy attention
```

Build the no-RAW feature-inclusion gate from the existing differential,
paired-overlay, AI-triage artifacts, and versioned manual review verdicts:

```powershell
uv run python -m tools.diagnostics.cid_nl_feature_inclusion_gate --manual-review-tsv docs/superpowers\validation\cid_nl_default_activation_gallery_review\cid_nl_manual_feature_inclusion_review.tsv --require-pass
```

Build the validation-only activated-copy candidate:

```powershell
uv run python -m tools.diagnostics.cid_nl_activation_copy_candidate --require-pass
```

Accept or reject the activated-copy candidate:

```powershell
uv run python -m tools.diagnostics.cid_nl_activation_copy_acceptance --require-pass
```

Build the adopt/hold decision for the accepted validation copy:

```powershell
uv run python -m tools.diagnostics.cid_nl_activation_adopt_gate --require-pass
```

## Result

- `packet_build_status=pass`
- `overall_status=pass`
- `requires_overlay_batch=false`
- review groups: `90`
- representative cells: `529`
- overlay queue rows: `85`
- overlay-linked groups: `85`
- cellless groups skipped for overlay: `5`
- missing overlay groups after linking: `5`
- old-to-successor differential transitions: `87`
- ready for paired differential overlay: `78`
- no-successor transitions: `9`
- successor candidate cells from the legacy `write_authorized` field: `147`
- detected-baseline no-write cells: `337`
- omitted no-write cells: `27`

Paired differential overlay metrics:

- `overall_status=pass`
- validation label: `diagnostic_only`
- transitions rendered: `78/78`
- failed transitions: `0`
- source/successor decision cells covered: `484`
- write-authorized cells covered: `147`
- detected-baseline preserved cells covered: `337`
- RAW samples opened: `85`
- XIC requests: `968` (`source` + `successor` per reviewed cell)
- PNG overlays: `78`
- trace JSON files: `78`
- Browser smoke: built-in Browser loaded the localhost-served gallery, verified
  232 PNG lightbox links, visible collapsed `source->successor` identity, detail
  `source -> successor` identity, no stale "No mapping supplied" warning, and
  visible ProductWriter/default-matrix boundary text. The differential links are
  labeled `HYPOTHESIS DIFFERENTIAL OVERLAY`, not family context.

Overlay batch metrics:

- requested rows: `85`
- successful rows: `85`
- RAW opens: `85`
- XIC requests: `5872`
- RAW chromatogram calls: `647`
- trace points: `633815`
- fast path used: `true`

Feature-inclusion gate metrics:

- `overall_status=pass`
- validation label: `diagnostic_only`
- transition count: `87`
- overlay-ready transitions: `78`
- AI-adjudicated transitions: `78`
- candidate cells: `147`
- supported candidate cells: `73`
- review-required candidate cells: `46`
- current-bundle-blocked candidate cells: `28`
- expected-diff contract cells: `73`
- agent-resolved expected-diff cells: `9`
- agent-resolved hold cells: `24`
- manual-resolved expected-diff cells: `13`
- manual-resolved hold cells: `0`
- user-review cells: `0`
- agent-resolved expected-diff contract cells: `9`
- manual-resolved expected-diff contract cells: `13`
- existing successor context cells: `337`
- omitted no-target cells: `27`
- expected-diff queue: `14` transitions
- agent-resolved expected-diff queue: `2` transitions
- agent-resolved hold queue: `6` transitions
- manual-resolved expected-diff queue: `4` transitions
- manual-resolved hold queue: `0` transitions
- user-review queue: `0` transitions
- original review queue: `12` transitions
- blocked queue: `17` transitions

Activated-copy candidate metrics:

- `activation_copy_status=pass`
- validation label: `diagnostic_only_activated_copy_candidate`
- contract cells: `95`
- changed matrix cells: `95`
- candidate transitions: `20`
- ProductWriter changed: `FALSE`
- default matrix changed: `FALSE`
- workbook/GUI changed: `FALSE`
- candidate rows are matrix rows: `FALSE`

Activated-copy acceptance metrics:

- `acceptance_status=pass`
- validation label: `diagnostic_only_activated_copy_acceptance`
- contract cells: `95`
- value-delta cells: `95`
- matrix changed cells: `95`
- candidate transitions: `20`
- forbidden overlap: `0`
- unexpected matrix changes: `0`
- missing matrix changes: `0`
- production ready: `FALSE`
- next action: `promote_requires_explicit_adopt_gate`

Activation adopt gate metrics:

- `adopt_gate_status=adopt_ready`
- validation label: `production_candidate_activation_adopt_gate`
- activation bundle adopt-ready: `TRUE`
- contract cells: `95`
- changed matrix cells in validation copy: `95`
- candidate transitions: `20`
- primary expected-diff cells: `73`
- agent-resolved expected-diff cells: `9`
- manual-resolved expected-diff cells: `13`
- agent-held cells: `24`
- manual-held cells: `0`
- user-review cells: `0`
- current-bundle-blocked cells: `28`
- existing successor context cells preserved outside the bundle: `337`
- omitted no-target cells preserved outside the bundle: `27`
- forbidden overlap: `0`
- unexpected matrix changes: `0`
- missing matrix changes: `0`
- ProductWriter changed: `FALSE`
- default matrix changed: `FALSE`
- workbook/GUI changed: `FALSE`
- candidate rows are matrix rows: `FALSE`
- production ready: `FALSE`
- recommended action: `prepare_explicit_default_activation_change`

## Boundary Statement

Unchanged:

- ProductWriter default output
- active default QuantMatrix bundle
- workbook and GUI behavior
- selected peak/area/counting behavior
- broad Backfill authority

The adapter does not create a second CID-NL review HTML system. It maps the
successor decisions into the established Gallery row model and links existing
family MS1 overlay artifacts. The reused part is the review shell, table,
filters, lightbox, and browser-smoke contract; the Backfill domain semantics
are not reused. Candidates are still not matrix rows, and CID-NL/MS2 evidence
is still not direct ProductWriter authority.

Overlay linking is provenance-checked. A supplied
`family_ms1_overlay_batch_summary.tsv` row must match the current review queue
by `feature_family_id`, `seed_group_id`, m/z, RT window, family-center RT, and
output prefix before its PNG/trace JSON can appear in the gallery. Family-id
alone is not enough to link an overlay.

The differential review TSV is also diagnostic only. It joins each old
`source_peak_hypothesis_id` to its `successor_peak_hypothesis_id`, source and
successor m/z/RT/product/tag/identity state, decision counts,
`feature_inclusion_gate`, `identity_authority_gate`,
`source_successor_relationship`, and a `differential_overlay_readiness` flag.
The paired differential overlay pass then renders source and successor
PeakHypothesis MS1 traces for the 78 ready transitions. The PNGs use the
existing Gaussian15 visual convention so the review surface is comparable to
the established MS1 overlay reports. Those PNGs are human-review evidence only;
they do not promote the 147 candidate cells or make the 337 existing-successor
cells part of the active ProductWriter contract.

The shared `backfill_evidence_reconciliation_representative_cells.tsv` stays on
the existing Backfill Gallery v0 schema. CID-NL-specific old-to-successor
machine identity lives in `cid_nl_discovery_identity_differential_review.tsv`
and in the HTML detail cards, not as unversioned columns in the shared
representative-cells TSV.

## Human Review Meaning

Use this packet to inspect feature inclusion first and identity authority
second. The review page exposes row identity, provenance, tag state, source
state, representative cells, and overlay links. The important guardrails remain
visible in the packet:

- `300.1605 -> 184.113` is recovered.
- `301.165 -> 185.116` is preserved as its own dR-tag pair.

The 5 groups without overlay PNGs are not hidden approvals. They are recorded
as missing alignment-cell evidence for overlay generation and need an explicit
human/product decision if they matter for adoption.

The 87-row differential index is the answer to the Discovery-specific question:
which old row identities moved, disappeared, or became successor production
families. For example, `FAM000380 -> FAM000736` keeps the old source row
visible (`Mz=243.099`, `RT=23.6623`, `product=127.052`,
`identity_decision=audit_family`, `accepted_cell_count=0`) beside the successor
row (`Mz=246.136`, `RT=12.2645`, `product=130.089`,
`identity_decision=production_family`, `accepted_cell_count=54`). That is
Discovery identity evidence, not direct matrix authority.

Target guardrail rows such as `300.1605 -> 184.113` and
`301.165 -> 185.116` are Discovery target context. They verify that Discovery
recovered or preserved the target family. They are not, by themselves,
old-to-successor Backfill write candidates.

## AI Triage Result

The 78 paired differential overlays were triaged with a diagnostic-only AI
adjudication sidecar under
`output/validation/cid_nl_default_activation_gallery_review/differential_overlays/ai_adjudication/`.
The sidecar reads the existing paired-overlay summary, the existing
old-to-successor identity TSV, and the existing trace JSON files. It does not
create ProductWriter authority, change the default matrix, or create a second
Discovery source of truth.

These labels were produced before the feature-inclusion/identity-authority
split was made explicit. They should now be read as identity-relationship
triage, not as final feature-inclusion verdicts:

- `accept_successor_identity_clear`: `39` transitions, covering `73` write
  cells and `248` preserved no-write cells.
- `reject_successor_identity_clear`: `23` transitions, covering `28` write
  cells and `68` preserved no-write cells.
- `human_review_needed`: `16` transitions, covering `46` write cells and `21`
  preserved no-write cells.

The full machine-readable sidecar is
`cid_nl_differential_ai_adjudication.tsv`. It is now consumed by
`tools.diagnostics.cid_nl_feature_inclusion_gate`, which writes the current
product-gate diagnostic queues under
`feature_inclusion_gate/`. Obvious source-empty / successor-dominant cases can
advance to expected-diff design; mixed, close, or guardrail-sensitive cases stay
in review; current successor-unsupported cases are excluded from the current
activation bundle.

Target guardrail handling in this triage:

- `FAM011440 -> FAM015713` is flagged as
  `target_guardrail_300_184_source_context` by AI triage. Manual review now
  supports successor feature inclusion, while identity authority still requires
  expected-diff review.
- `FAM011837 -> FAM016144` is flagged as
  `target_guardrail_301_185_exact_source_context` by AI triage. Manual review
  notes the successor double peak but accepts the right-side peak for feature
  inclusion.
- `FAM011803 -> FAM016107` is flagged as
  `target_guardrail_301_185_near_source_context`, not exact `185.116`; its
  overlay is source-empty / successor-clear and was auto-accepted by the
  diagnostic sidecar.

Manual review also resolves `FAM018342 -> FAM026285` as successor-supported and
resolves `FAM020176 -> FAM030972` as successor-supported while keeping
`FAM020176` source evidence explicitly `unjudgeable_bad_trace`. These reviews
advance only feature-inclusion expected-diff design, not source-row deletion,
replacement, migration, or ProductWriter authority.

## Next Product Gate

The old 147-cell successor-authority bundle is not ready for direct activation
as-is, and it should no longer be treated as one bundle. The current
feature-inclusion gate splits it into four product actions:

- `73` supported candidate cells, across `14` transitions, have a primary
  cell-level expected-diff design contract.
- `9` formerly reviewed cells, across `2` transitions, have a second-pass
  agent-resolved expected-diff design contract.
- `13` formerly user-review cells, across `4` transitions, have a
  manual-resolved expected-diff design contract.
- `24` formerly reviewed cells, across `6` transitions, are held out by agent
  triage because source/no-support evidence is stronger than successor support.
- `0` candidate cells remain in the user/domain review queue.
- `28` candidate cells, across `17` transitions, are excluded from the current
  activation bundle by the current paired overlay evidence.

The next product gate is therefore not "activate 147 cells." The current
validation copy applies `95` cells: 73 primary supported cells, 9
agent-resolved expected-diff cells, and 13 manual-resolved expected-diff cells.
The acceptance gate proves the copy changed exactly those 95 cells, with no
forbidden review/hold/blocked overlap and no unexpected or missing matrix
changes. The adopt gate now closes the adopt/hold decision for this narrowed
bundle as `adopt_ready`.

That still does not mean default activation has happened. The 24 agent-held
cells and 28 blocked cells stay out of the current activation bundle. The 337
existing-successor context cells and 27 omitted no-target cells are preserved
outside the bundle. The next gate, if we choose to continue, is an explicit
public-surface default activation change that intentionally mutates the default
matrix/ProductWriter contract and proves the exact same 95-cell diff,
provenance, preserved existing/omitted behavior, and no unrelated output drift.
