---
status: accepted
---

# Interpret numerical regressions against an independent reference

A fixture can establish what a calculation should return; a result from the
implementation under test cannot. Keep fixture inputs, reference calculations and
assertions executable. Distinguish a mathematical identity, a human reading of an
acquisition, and a characterization of the current implementation before changing
an expected value. None alone establishes readiness on a new batch.

| Reference | What it distinguishes |
| --- | --- |
| [Peak area](../../tests/test_ms1_morphology.py) and [physical boundaries](../../tests/test_physical_peak_evaluation_behavior.py) | For an isolated Gaussian on a regular RT grid, the whole area in counts·seconds is `60 A σ √(2π)` for σ in minutes. The interval above one tenth of the apex retains `erf(√ln 10) ≈ 0.968` of that area; counting the bracketing acquired scans can increase the fraction. A shared valley is not necessarily the tenth-height return. On a sloping baseline the reference integrates the same Gaussian **after subtraction**, not the raw envelope. |
| [AsLS](../../tests/test_baseline_integration.py) and [solver equivalence](../../tests/test_baseline_asls_banded_equivalence.py) | Equal recovered area across flat, linear and curved drift is the invariant (observed spread <0.05%; tolerance 0.5%). Recovering about 89% on the 401-point fixture at ~16 points/σ is characterization, not the target: at ~4–8 points/σ the observed bias was about 1–4%. The banded solver is compared to the independent sparse solve at relative tolerance 1e−9; near zero only the absolute tolerance is meaningful. |
| [Acquired valley controls](../../tests/test_chrom_peak_segments.py) | The classes come from visual review in [#1049](https://github.com/bosschen0429/XIC_Extractor/issues/1049), not from the splitter. O29: NormalBC2312, m/z 553.141, doublet with a valley ~0.36 of the smaller lobe. O07: one component interrupted by zeros. O28: NormalBC2312, m/z 359.045, a three-minute plateau before the 6.13 min peak; the last plateau hump never returns below its valley. Intensities are in thousands of counts. In the physical-boundary fixtures, the d3-5-hmdC trace in TumorBC2264 is scaled by 100; its sparse tail does not return to exactly zero. |
| [Precursor charge](../../tests/test_discovery_physical_measurement.py) | The positive tissue control was read by eye: TumorBC2263, m/z 622.295 at 34.28 min. The negative was BenignfatBC1151, m/z 894.452 at 24.46 min: the +1 isotope was 26%, the +0.5 noise centroid only 2%. The reviewed doubly charged peaks had the half-spacing companion at 38–88%; spacing alone cannot establish charge. |
| [Isotope envelopes](../../xic_extractor/evidence/pt_envelope.py) and [compound families](../../tests/test_same_compound.py) | Use [NIST isotope data](https://physics.nist.gov/PhysRefData/Compositions/index.html), not the implementation's constants, for reference masses and abundance. Pt envelopes use nominal offsets from ¹⁹⁴Pt, ³⁵Cl and ³²S; at most eight ¹³C atoms reach +4 from ¹⁹⁰Pt. The fixture independently enumerates isotope combinations. One/two Mg or two K give overlapping isotope ratios (~7.174, 3.587, 6.929); they do not identify an element. The neutral-loss sugar residue is C₅H₈O₃, not free deoxyribose. |
| [RT calibration](../../tests/test_sample_rt_calibration.py) and [membership](../../tests/test_window_feature_membership.py) | The rounded 13.27/13.31 min pair is from one 85RAW Tumor injection where the shift falls from ~40 s to ~0 s. Reduced m298 has two calibrated populations 43.6 s apart, with at most 2.0 s spread within a population. The reduced m/z 659.346 case has valid 160–175 s **cross-sample** shifts: the same-sample pair supplies the separation evidence. Do not use an RT gap alone as its oracle. |
| [Manual comparison](../../tests/test_targeted_manual_validation.py) | The two manual RAW files include tailing and double peaks. A 50% larger area or a 0.5 min later apex flags disagreement for review, not an identity failure. A cohort reproducing the manual integration is not independent ground truth. |

Synthetic boundary cases keep their reference arithmetic in the tests: use the
declared reference mass for ppm, both measured masses for neutral-loss error,
and acquired scan positions rather than rounded RT-window endpoints. For
serialized results compare to the original owner's measured values, not values
read back by the same serializer. The [signal contract](../product/signal-processing.md)
and [validation contract](../agent/product-validation-contract.md) own the method and
the evidence required to change it.
