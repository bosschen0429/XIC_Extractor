# Untargeted Method

This page is the durable product-method owner for stable conclusions formerly
spread across the dated 2026-05-14 Untargeted method/literature reports and
public `deepresearch` stubs. The original long-form reports are historical
private context after their stable claims are represented here and in the
linked owner pages.

## Current Position

- The Untargeted lane runs Discovery, Alignment, and Backfill, with
  conservative primary-matrix delivery. The Untargeted lane is not a full LC-MS
  preprocessing, normalization, artifact annotation, blank/background
  filtering, or batch-correction system.
- Untargeted is the general application of the shared scientific lifecycle, not
  a separate core that Targeted must call into wholesale. Targeted and
  Untargeted share capture, CrossSample, ModeTrack, ObservationRevisit, and
  ProductionCellDecision implementations. The two applications differ in search
  scope and output projection.
- A broad candidate-discovery policy is available to both applications.
  Untargeted's distinguishing search behavior is that every group qualifying
  under the configured method remains eligible for its projection, whereas
  Targeted retains only configured `TargetKey` rows.
- Product-facing `Matrix` output stays conservative. Backfill-only (`rescue_only`), ambiguous,
  diagnostic, and review-only evidence belongs in Review/Audit surfaces until a
  ProductWriter authority record and expected-diff packet explicitly promote it.
- Untargeted projection derives a typed `UntargetRowKey` from each finalized
  canonical group. Standard Matrix rows require `established` disposition.
  `provisional` groups stay in a compact machine archive/review surface;
  `unresolved` and `rejected` groups remain AuditTrail facts. Disposition and
  row inclusion are separate typed decisions.
- Untargeted delivery uses one output-level seal, not per-cell delivery
  revisions. See the
  [Untargeted delivery decision](processing-lineage.md#untargeted-delivery-decision).
- `FAM...`, row order, m/z/RT display coordinates, and current Matrix text are
  compatibility/display facts, not canonical cell identity.
- Exact equality to old upstream feature tables is not the success definition.
  The accepted method direction is owner-centered Backfill plus duplicate-claim
  control while preserving a clean primary matrix.
- QC, blank, calibrator, injection-order, and batch metadata must become
  first-class inputs before any QC correction, blank filtering, or normalization
  behavior can write product values.

## Literature Grounding

The literature supports the product shape, but does not grant product authority
by itself:

| External support | Product implication |
| --- | --- |
| XCMS introduced an LC-MS processing workflow around peak detection, nonlinear retention-time alignment, peak matching, and downstream identification ([Smith et al. 2006](https://doi.org/10.1021/ac051437y)). | Discovery and alignment are distinct responsibilities; a sample-local feature hit is not automatically a cross-sample matrix row. |
| centWave describes feature detection as estimating boundaries, centers, and intensities of LC/MS raw-data signals, followed by alignment of corresponding features across samples ([Tautenhahn et al. 2008](https://doi.org/10.1186/1471-2105-9-504)). | Discovery methods should find feature candidates with evidence, then hand off to alignment. |
| MZmine 2 describes a typical workflow of raw import, optional filtering/smoothing, peak picking, deisotoping, alignment, gap filling, and normalization; it also separates processing modules from core behavior ([Pluskal et al. 2010](https://doi.org/10.1186/1471-2105-11-395)). | XIC should keep Discovery, Alignment, Backfill, and Matrix authority separated instead of letting one method own the whole pipeline. |
| asari argues that feature correspondence across samples is a core problem and uses mass tracks, retention-time correction, isotopic landmarks, and composite tracks to improve correspondence ([Li et al. 2023](https://doi.org/10.1038/s41467-023-39889-1)). | Cross-sample behavior is evidence for alignment/review after a detected seed exists; it is not the first Discovery gate by itself. |
| CAMERA groups LC-MS signals into compound spectra and annotates isotope/adduct relationships ([Kuhl et al. 2012](https://doi.org/10.1021/ac202450g)). | MS1 pattern evidence such as isotope/adduct/delta relationships is a legitimate evidence provider, but remains candidate/review evidence until projected. |
| Reviews of LC-MS/MS metabolite data mining list extracted-ion chromatograms, mass-defect filters, product-ion filters, neutral-loss filters, and isotope-pattern filters as complementary post-acquisition strategies ([Dhurjad et al. 2017](https://doi.org/10.4155/bio-2017-0046); [Zhang et al. 2009](https://doi.org/10.1002/jms.1610)). | CID-NL, HCD-PI, and Delta Mass fit the same broad class: configurable Discovery methods that find candidate features matching diagnostic evidence. |
| Isotope-pattern and stable-isotope mass-shift filtering work shows that specific MS1 mass-shift/isotope patterns can mine candidate ions from complex LC-MS data ([Yang et al. 2009](https://doi.org/10.1021/ac900626d); [Uchida et al. 2013](https://doi.org/10.3390/ijms141019716)). | A Pt-specific delta-mass pattern is methodologically plausible as a first `delta_mass` pattern, but first-version hard gates should stay simple and evidence-based. |
| XCMS `fillPeaks` integrates raw data inside aligned peak-group regions for samples missing a peak, and uses retention-time correction when available ([XCMS documentation](https://sneumann.github.io/xcms/reference/fillPeaks.chrom-methods.html)). Missing-value reviews warn that missing values arise from biological, technical, and preprocessing causes, and that filling/imputation choices can bias downstream analysis ([Wei et al. 2018](https://doi.org/10.1038/s41598-017-19120-0); [Hrydziuszko and Viant 2018/2019](https://pubmed.ncbi.nlm.nih.gov/30830398/)). | Backfill must mean an evidence-based revisit of raw traces or approved sidecars, not blind statistical imputation. Statistical imputation belongs downstream and must not be counted as detection. |

## Absorbed 2026-05-14 Conclusions

| Stable conclusion | Durable owner |
| --- | --- |
| Untargeted LC-MS literature expects pooled QC, injection-order tracking, blank/background handling, batch drift checks, sample-matrix-effect awareness, missingness classification, and artifact/adduct/isotope review. | [Sample Metadata And QC](sample-metadata-qc.md), [Instrument QC And Calibration](instrument-qc-calibration.md), [Productization](productization.md) |
| XIC's current Untargeted value is narrower: sample-local owner finding, owner-centered Backfill, RT-aware alignment, duplicate-claim control, and Matrix/Review/Audit separation. | [Discovery](discovery.md), [Alignment](alignment.md), [Backfill](backfill.md), [Quant Matrix](quant-matrix.md) |
| Targeted ISTD recall is a useful benchmark gate, but historical 8RAW/85RAW results are evidence snapshots, not permanent product authority. Current validation verdicts must come from the diagnostic ledger, validation packets, status index, and authority manifest. | [Diagnostic ledger](../diagnostic-ledger.md), [Productization](productization.md) |
| Pre-Backfill consolidation and primary-promotion rules reduce matrix pollution only when tied to explicit writer authority and expected-diff tests. | [Backfill](backfill.md), [Decision Policy](decision-policy.md), [Productization](productization.md) |
| Old reports, research notes, and deepresearch imports may explain why a direction was chosen, but they do not override current product owners. | This page, [Product Topic Index](README.md), [Project Layout](../project-layout.md) |

## In Scope

- Feature discovery and review surfaces.
- Owner-centered alignment and Backfill evidence.
- Conservative primary-matrix promotion rules.
- Duplicate-claim and identity-family hygiene.
- Targeted benchmark, replay, and expected-diff gates that validate the above.

## Out Of Scope Until Activated

- QC-based signal correction, SERRF-style correction, or post-acquisition
  normalization.
- Blank/background subtraction as a writer rule.
- Sample-matrix-effect correction as a product claim.
- Broad artifact/adduct/isotope/in-source fragment deconvolution.
- Treating review-only candidates as product matrix values.

## Promotion Rule

Any future expansion from method evidence to product output must name:

1. the public output/schema that changes,
2. the ProductWriter or authority manifest record that allows the write,
3. the expected matrix diff,
4. the focused tests and validation packet proving the diff,
5. the current productization tier after the change.

Structural adoption of the shared owners is parity work and does not itself
promote provisional groups, change Matrix row identity, fill a cell, or activate
downstream preprocessing. Public `UntargetRowKey` cutover, ModeTrack sensitivity
changes, and analytical-handoff activation each require their own packet.

## See Also

- [Discovery](discovery.md)
- [Alignment](alignment.md)
- [Backfill](backfill.md)
- [Quant Matrix](quant-matrix.md)
- [Productization](productization.md)
- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
