# Matrix Curation

Matrix curation runs independently switchable steps on a published Alignment
Matrix and writes the result to a new folder. It reads only the published
files: it does not reopen RAW, rerun Untargeted analysis, or change Alignment
decisions, counted detection, peak identity or the source Matrix. The
[Untargeted guide](../user/untargeted.md#整理矩陣) owns how to use it; the code
and its tests own each step's rules, thresholds and output columns. This page
keeps the boundaries, reasons and limits no single module states.

## Boundaries

- **Row selection only.** The kept Matrix is a row selection of a finalized
  product, not a new Alignment finalization; no value, zero or missingness
  changes. QC drift correction and normalization belong to the analysis
  backend, and downstream analysis must not silently repeat global feature
  filtering.
- **Removed stays removed.** A derivative removed by same-compound collapse is
  not evaluated by later steps and is not promoted when its representative is
  filtered out, so a whole ion family can leave the curated Matrix.
- **Internal standards are always kept**, at every step.
- **Labels are not identity.** A nucleoside label means a window match; an
  ion-form label is evidence-compatible, not identified; `feature_id` is scoped
  to its Matrix.
- **A folder without its summary is an incomplete export.**

## Why It Works This Way

- **[M+H]⁺ is the representative.** The statistical Matrix should not
  substitute a stronger Na, K or other adduct as the quantitative row.
  Classifying a row as a derivative is separate from naming its element or
  parent: an unresolved parent does not rescue a supported derivative, and an
  unclassified feature is not assumed to be an artifact.
- **Direct detections first.** Backfill searches near each sample's expected
  RT, so a backfilled apex is weaker co-elution evidence; it only completes
  evidence that direct detections leave insufficient and never overturns it.
- **5.5 s co-elution** is two MS1 scans of the ~2.5 s CID-NL cycle. In 8RAW,
  mass-related pairs are as enriched over shifted-mass decoys at 3–5.5 s as at
  1–3 s.
- **No ratio check for adducts.** Adduct, salt-cluster and multimer yield
  follows each sample's salt content and concentration, so a varying ratio is
  no evidence of different compounds. In 8RAW, dropping the former adduct
  limit (0.15) added about one shifted-mass decoy pair per twenty new real
  pairs.
- **Root co-elution for representatives.** In 8RAW, decoy relations removed
  the same rows with or without it, while the requirement kept derivatives of
  saturated peaks whose apices scatter. Ambiguous roots are recorded, not
  merged or picked arbitrarily.
- **Ion families without [M+H]⁺** use the measured family, not the absence of
  [M+H]⁺, as evidence: an expected [M+H]⁺ outside the MS1 mass range was not
  measured, not shown absent.
- **The built-in nucleoside list** is offered only for the single DNA_dR
  channel, which identifies the channel, not the LC gradient; RNA, mixed and
  unidentified methods do not inherit it.
- **The finite-value fraction** counts numeric availability, not detection,
  and has no scientific default threshold.

## Limits

- Same-compound thresholds come from local 8RAW and a read-only dr95
  diagnostic, not cross-batch validation; the Zn ratios come from one 85RAW
  batch whose removed rows were checked against RAW, and the joint ion-family
  thresholds have bounded 8RAW diagnostic evidence.
- `M+2_unassigned` is an intensity- and mass-compatible isotope
  classification, not an elemental identification; overlapping envelopes or
  other elements can give the same evidence. Mg/Ca relation names describe
  compatible mass models, not confirmed identities.
- The isotope abundance check is a loose upper bound; the ratio screen uses
  published centre m/z, not per-sample mass evidence.
- Relations such as +CH₂, +H₂, +CH₂O, +CH₃OH and H₂O loss are only reviewed,
  since they can be real chemical differences; the step does not perform
  complete ion identification.
- Tests prove engineering behavior, not a method-independent threshold or
  biological readiness.
