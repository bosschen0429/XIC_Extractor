# Untargeted Method

Untargeted analysis asks which sample-local observations can form comparable
features across a batch, and which measurements can be delivered for those
features. This page owns the method rationale and limits; the
[user guide](../user/untargeted.md) owns running and reading a batch.
Discovery, Alignment and bounded Backfill contribute evidence, but a candidate,
a cross-sample correspondence and a published numeric value remain distinct
results. Published features are not, by themselves, identified compounds.

## Current Position

- The Untargeted lane runs Discovery, Alignment, and Backfill, with
  conservative primary-matrix delivery. The Untargeted lane is not a full LC-MS
  preprocessing, normalization, artifact annotation, blank/background
  filtering, or batch-correction system.
- Untargeted is the general application of the shared scientific lifecycle, not
  a separate core that Targeted must call into wholesale. Targeted and
  Untargeted share capture, CrossSample, ObservationRevisit, and
  ProductionCellDecision implementations. The two applications differ in search
  scope and output projection.
- A broad candidate-discovery policy is available to both applications.
  Untargeted's distinguishing search behavior is that every group qualifying
  under the configured method remains eligible for its projection, whereas
  Targeted retains only configured `TargetKey` rows.
- Product-facing `Matrix` output stays conservative. Backfill-only, ambiguous,
  diagnostic, and review-only evidence belongs in Review/Audit surfaces until a
  writer path is explicitly contracted; see [Writer Authority](decision-policy.md#writer-authority).
- Untargeted projection derives a typed `UntargetRowKey` from each finalized
  canonical group. Standard Matrix rows require `established` disposition.
  `provisional` groups stay in a compact machine archive/review surface;
  `unresolved` and `rejected` groups remain AuditTrail facts. Disposition and
  row inclusion are separate typed decisions.
- Row order, m/z/RT display coordinates and Matrix text are display facts, not
  canonical cell identity.
- Exact equality to other tools' feature tables is not the success definition.
  The method direction is owner-centered Backfill plus duplicate-claim control
  while preserving a clean primary Matrix. The lane addresses false missingness,
  RT-driven splitting and duplicate claims, and keeps Review/Audit separate.
- Targeted ISTD recall is a useful benchmark, but a past 8RAW/85RAW result is an
  evidence snapshot, not lasting product authority.
- Untargeted LC-MS practice expects pooled QC, injection-order tracking,
  blank/background handling, batch drift checks, sample-matrix-effect awareness,
  missingness classification and artifact/adduct/isotope review. QC, blank,
  calibrator, injection-order and batch metadata must become first-class inputs
  before any QC correction, blank filtering or normalization can write values;
  see [Sample metadata and QC](sample-metadata-qc.md).

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
- Broad artifact/adduct/isotope/in-source fragment deconvolution. The optional
  post-Alignment [same-compound collapse](matrix-curation.md#same-compound-collapse)
  removes isotope, adduct and dimer rows from a curated copy of the Matrix only.
- Treating review-only candidates as product matrix values.

## See Also

- [Discovery](discovery.md)
- [Alignment](alignment.md)
- [Backfill](backfill.md)
- [Quant Matrix](quant-matrix.md)
- [Decision Policy](decision-policy.md)
