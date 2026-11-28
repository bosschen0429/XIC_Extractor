# Targeted Selection

Targeted selection connects observed evidence to the Targets requested for a
batch: it constrains the shared lifecycle with an exact target list, binds
finalized groups to fixed Target rows, and closes inclusion, counted detection
and review reasons. A target-list entry is a search request, not evidence that
the Target was observed. The code and its tests own the method columns,
tolerances, ranking order, label codes and position rules; this page keeps the
boundaries, reasons and limits. For operation and result reading, start with
the [Targeted guide](../user/targeted.md#看結果).

## Where Each Decision Lives

| Decision | Owner | Tests |
| --- | --- | --- |
| Per-Target specificity (MS1 only, CID-NL, Delta Mass) | `configuration.targeted_identification` | [`test_targeted_specificity.py`](../../tests/test_targeted_specificity.py) |
| Retained population comparison and retention uncertainty | `peak_detection.targeted_context` | [`test_target_extraction.py`](../../tests/test_target_extraction.py) |
| Candidate assessment, recorded-precursor rule, scoring, MS2 window | `extraction.targeted_peak_selection` | [`test_targeted_peak_selection.py`](../../tests/test_targeted_peak_selection.py) |
| Tie-break order | `select_candidate_by_evidence` | [`test_candidate_evidence_selection.py`](../../tests/test_candidate_evidence_selection.py) |
| Batch selection among retained regions | `extraction.targeted_batch_selection` | [`test_target_extraction.py`](../../tests/test_target_extraction.py) |
| Paired-ISTD identity and rejection, counting, labels | `extraction.targeted_product_projection` (`paired_istd_positions`) | [`test_targeted_product_projection.py`](../../tests/test_targeted_product_projection.py) |
| Row binding and cell subject; post-cell inclusion | `alignment.projection.target.pre_cell`, `post_cell` | [`test_target_pre_cell_projection.py`](../../tests/test_target_pre_cell_projection.py), [`test_target_post_cell_projection.py`](../../tests/test_target_post_cell_projection.py) |
| Cell value or typed no-write | `ProductionCellDecision` | [`test_alignment_cell_decision.py`](../../tests/test_alignment_cell_decision.py) |

## Boundaries

- **One lifecycle.** Targeted reuses the shared capture, grouping, Backfill
  and cell-decision owners; a second Target-only implementation is not
  justified because the output rows differ.
- **Selection does not create authority.** The cell decision owns the value;
  Targeted projection renders it and closes inclusion, counting and reasons
  without rerunning selection or integration. A counted value is never
  manufactured after a no-write, and a not-counted value is still rendered.
- **Shared evidence informs, Targeted decides.** Shared providers do not
  choose Targeted product state, and Targeted and Untargeted authority do not
  extend to each other.
- **Retained candidates.** Every acquired candidate, method product and NL
  answer stays diagnostic evidence; rejected interpretations keep their curves
  and evaluations for inspection.
- **Every Target, every sample.** Each configured Target is evaluated for
  each selected analysis sample; sample names do not gate Targets or
  paired-area references, and Untargeted neutral-loss selection does not
  restrict the list.
- **Counting needs a positive MS1 peak** (finite RT, positive area); without
  one a row is never counted by implication. A configured ISTD's plausible
  dropout can count as `detected_flagged`, while an analyte NL fail or missing
  MS2 needs [paired-ISTD identity](#paired-istd-identity). Paired-ISTD RT
  mismatch without strict NL support demotes candidate localization,
  independently of counting.
- **Review is not a second ranking.** Reason labels describe evidence; RT and
  score labels alone create no review action.

## Why It Works This Way

- **The whole acquisition is the candidate space.** An expected-RT window, DDA
  anchor crop or reacquisition never truncates the physical candidate
  population; expected RT is an identity prior, not a search window or
  verified truth. There is no universal 0.75-minute or 20-minute acceptance
  rule.
- **Measurement, identity and integration uncertainty are separate
  decisions.** A complete region with acquired body support stays measurable
  despite a ragged or split top; top quality describes risk rather than erasing
  area. Neither a shared total nor a positive NL establishes pure constituent
  area, and target count does not force peak count.
- **Only individually attributed, recorded-precursor CID-NL ranks.** A
  product + loss inference shows mass and isolation compatibility, not which
  coisolated ion fragmented, so it cannot select or identify a Target peak.
  No MS2 and MS2 without qualified support are both unconfirmed, and neither
  outranks the other.
- **Weak reference-supported bodies are kept** beside prominent alternatives,
  so a duplicate sample cannot turn an abundance filter into an identity veto.
- **ISTDs compare whole bodies.** Weak reference residues must not displace a
  confirmed standard, while genuine drift stays possible; this is neither a
  fixed RT gate nor a nearest-apex rule.
- **The RT reference is per sample.** A credible paired ISTD transfers the
  sample's observed shift while keeping the configured analyte–ISTD offset;
  external RT-prior files and other samples cannot replace missing
  current-sample identity support. Humans supply identities, pairings and
  expected RTs, which are not portable across batches or methods.
- **Cross-sample drift is not added twice.** Displacement intervals come from
  corroborated reference-linked bodies, and the same drift must not be added
  again as a symmetric allowance.

### Candidate selection

Selection consumes the CID-NL relation computed over the complete candidate
universe, not a candidate-local best MS2 product, so a neighbouring
precursor's matching loss cannot lend a positive label. Positive CID-NL is
evidence, not permission to accept an incompatible chromatographic
interpretation. When different Targets can only share one ion region, both
keep provisional values with linked reasons and no independent counted
assignment.

### Paired-ISTD identity

An isotope-labelled ISTD co-elutes with its analyte, so a counted ISTD confers
chemical identity on the analyte's MS1 peak at the paired position, and the
analyte's own neutral loss is then not required. The same position also
rejects: with a counted ISTD, an analyte peak outside it is another signal and
is ND, even if its own NL had counted it; no other peak is silently selected.
The result records `paired_istd_identity`, so it stays distinguishable from
NL-confirmed detection, and morphology or a missing NL still read as doubts.
Contradictory counted peaks cannot calibrate away their own mismatch, and an
uncounted or missing ISTD imposes no rejection. Cohort
[Backfill](backfill.md#boundaries-across-modules) is a separate recovery and
never promotes HOLD to confirmation.

## Limits

- XIC windows establish bounded mass support, not measured centroids or zero
  mass error. Configurability and synthetic regression coverage do not
  establish chemical identity or readiness (`diagnostic_only`).
- `No target-matched MS2 in peak` describes acquisition opportunity, not
  chemical absence.
- Without enough RT calibration a measurable candidate stays provisional and
  reports that RT plausibility is not established; this is an operational
  model, not a chemical-identification or calibrated detection claim.
- Changes that move a selected peak, area, reason, product state, counted
  detection or Matrix value need expected-diff review and a matching RAW-tier
  evidence label; passing tests do not establish scientific readiness.

See also [CID-NL support relations](discovery.md#cid-nl-support-relations) ·
[Quantitation context](signal-processing.md#quantitation-context) ·
[Workbench](workbench.md)
