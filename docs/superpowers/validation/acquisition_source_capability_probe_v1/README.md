# Acquisition Source And Reusable Artifact Capability Probe V1

Doc placement: formal_repo_doc
Doc kind: validation_artifact
Doc lifecycle: active
Repo owner: docs/superpowers/validation/acquisition_source_capability_probe_v1/README.md
Doc exit rule: Replace only after a source-qualified successor measures the same capability surface on a newer acquisition/artifact contract.

Status: `diagnostic_only`

This packet answers Issue #124. It records what current Thermo RAW access,
mzML support, MS1 scan indexes, and retained trace artifacts can actually do.
It does not select a production source format, storage engine, cache policy, or
persistence schema.

## Decision

1. Keep Thermo vendor XIC as the current acquisition oracle. Of the two
   vendor-backed extraction modes measured here, super-window was faster and
   still obtained XIC through the vendor API. This probe did not establish
   point-by-point trace parity between the two vendor-backed modes.
2. Do not promote the local MS1 scan index to a vendor-XIC replacement. It can
   support bounded scan-level analysis and warm no-RAW replay, but the measured
   peak/area differences are not exact and its persisted payload can be larger
   than the source RAW.
3. Keep mzML as an external-reference capability, not an XIC input fallback.
   The format can express the required concepts, but this repository has no
   mzML acquisition adapter and no RAW-to-mzML fidelity packet.
4. Reuse the fail-closed ideas in the existing overlay cache and Pt trace
   manifests when the later tracer needs retained evidence. Do not declare
   those diagnostic-specific schemas to be the universal `EvidenceArtifact`.
5. Route execution ownership, retry, cleanup, and cache lifecycle to #128.
   This packet supplies facts; it does not create another runtime owner.

## Current Capability Matrix

| Surface | Current state | Evidence and limit |
| --- | --- | --- |
| Thermo RAW open/session | Implemented | `raw_reader.open_raw` loads RawFileReader, selects `Device.MS, 1`, and disposes a successful context-managed handle. There is no typed `AcquisitionSource` implementation or central open counter. |
| Vendor XIC batching | Implemented and fake-tested | `RawFileHandle.extract_xic_many` batches requests with the same scan window; Alignment adds bounded super-window grouping. Chromatogram calls are counted, but scan/filter/RT calls and total RAW opens are not. |
| MS1 scan identity | Partial | `MS1Scan` retains scan number, RT, masses, and intensities. It lacks controller/native ID and profile/centroid representation. |
| MS2 partial failure | Partial | Per-scan `Ms2ScanEvent.parse_error` exists, but the Discovery consumer currently skips failed events without a durable failure count. |
| MS1 partial failure | Fail-closed | One malformed MS1 scan aborts the index build with `MS1ScanReadError`; no partial scientific result is produced. |
| Persistent MS1 index | Implemented, diagnostic | NPZ round-trip validates schema, numeric arrays, order, and optional source provenance. The writer is not atomic, and generic reading does not itself prove the current source still matches the stored provenance. |
| Overlay evidence cache | Implemented, diagnostic | Current v5 validates request keys, trace artifact hashes, provenance, and current `path_stat_v1` RAW identity before a no-RAW cache hit. Rendered runs intentionally ignore this cache. |
| Pt retained trace bundle | Implemented, diagnostic | Hash-bound manifests and trace points can render review galleries without opening RAW. This is the strongest current retained-evidence pattern, not a universal persistence schema. |
| Plot rebuild from retained trace | Fragmented | Individual renderers can draw from retained points, but there is no workflow-neutral verified loader that regenerates a missing plot and proves zero source opens. |
| mzML read | Not implemented | Current `.mzML` references are MZmine/legacy column-name compatibility, fixtures, or documentation. No acquisition adapter or reader dependency exists. |
| Corrupt/unsupported source | Partially characterized | DLL/runtime preflight and generic open failure exist. Real corrupt RAW, unsupported controller, in-acquisition source, and truncated data behavior remain unmeasured. |

## Current-Checkout Probe

The bounded probe ran on commit
`421814a9141200bd6bc3acf92ba93fac97e2d225` with one known RAW and the
100 owner-backfill requests selected at evenly spaced integer indices across
the complete eligible record sequence. Direct vendor extraction and vendor
super-window extraction used the identical selected requests. Both were
compared with local scan-index `max` and `sum` traces; the index was written and
loaded three times. `tools.diagnostics.ms1_index_backfill_audit` produced both
runs.

Input identity and locator:

The three tabular input paths below are relative to
`<INPUT_ARTIFACT_ROOT>`, the main checkout that held the ignored retained 8RAW
artifact bundle. The probe code ran from a separate branch worktree, represented
as `<RUN_CHECKOUT>`. Neither checkout gets these `output/` artifacts from Git.
The hashes identify the required source files if the external input bundle is
restored; they do not make this packet independently replayable.

| Input | Locator | SHA256 or fact |
| --- | --- | --- |
| Sample RAW | `<RAW_DIR>/BenignfatBC1055_DNA.raw` | `path_stat_v1`: size `197960732` bytes; `mtime_ns` `1768897726000000000` |
| Discovery batch index | `<INPUT_ARTIFACT_ROOT>/output/performance/dna_dr_product_ready_8raw_20260622_current_perf_fresh/discovery/discovery_batch_index.csv` | `1D902E48FED2B2715D332DAC294771FA1E8CB3DFA6EB63691A98A7E6850E05CA` |
| Alignment review | `<INPUT_ARTIFACT_ROOT>/output/performance/dna_dr_product_ready_8raw_validation_cells_ms1_cache_20260622/alignment_review.tsv` | `D50D7FEF55D1FC60B622F4C4ECA8D5F4020FCE9D2076F63271579813F6E60996` |
| Alignment cells | `<INPUT_ARTIFACT_ROOT>/output/performance/dna_dr_product_ready_8raw_validation_cells_ms1_cache_20260622/alignment_cells.tsv` | `71B96D088DFFBA7DE253136AF7353DEE461E42C1572C99E1471258D30A2616F1` |
| Probe parameters | CLI arguments below | `preferred_ppm=20`, `max_rt_sec=180`, `owner_backfill_min_detected_samples=1`, `request_count_per_sample=100`, `cache_warm_repeats=3` |
| Request selection | `_limited_records` | sample is first in batch order; 100 evenly spaced integer indices across its 3,676 eligible owner-backfill records: `np.linspace(0, 3675, num=100, dtype=int)` |
| Runner | Repository environment | Python 3.13.7 with `pythonnet` |

The batch index contains candidate-CSV locators, but this owner-backfill audit
does not open those files. `collect_owner_backfill_requests` derives requests
from batch sample order, Alignment review, and Alignment cells only, so a
candidate CSV is not listed as an evidence input.

Command template, run from `<RUN_CHECKOUT>` after restoring the hash-bound
inputs under `<INPUT_ARTIFACT_ROOT>`:

```powershell
python -m tools.diagnostics.ms1_index_backfill_audit `
  --discovery-batch-index <INPUT_ARTIFACT_ROOT>/output/performance/dna_dr_product_ready_8raw_20260622_current_perf_fresh/discovery/discovery_batch_index.csv `
  --raw-dir <RAW_DIR> `
  --dll-dir <THERMO_DLL_DIR> `
  --alignment-review <INPUT_ARTIFACT_ROOT>/output/performance/dna_dr_product_ready_8raw_validation_cells_ms1_cache_20260622/alignment_review.tsv `
  --alignment-cells <INPUT_ARTIFACT_ROOT>/output/performance/dna_dr_product_ready_8raw_validation_cells_ms1_cache_20260622/alignment_cells.tsv `
  --output-dir output/issue124_raw_artifact_capability_probe_20260724/<MODE_DIR> `
  --sample-count 1 `
  --request-count-per-sample 100 `
  --example-limit 20 `
  --max-rt-sec 180 `
  --preferred-ppm 20 `
  --owner-backfill-min-detected-samples 1 `
  --index-cache-dir output/issue124_raw_artifact_capability_probe_20260724/<MODE_DIR>/cache `
  --cache-warm-repeats 3 `
  --vendor-extraction-mode <VENDOR_MODE>
```

| Run | `<MODE_DIR>` | `<VENDOR_MODE>` |
| --- | --- | --- |
| Direct vendor | `direct` | `direct` |
| Vendor super-window | `superwindow` | `raw-superwindow` |

Results:

| Metric | Direct vendor | Vendor super-window | Local index `max` |
| --- | ---: | ---: | ---: |
| Vendor extraction elapsed | 2.315 s | 0.619 s | N/A |
| Cold index build + extraction | N/A | N/A | 1.907-3.830 s |
| Warm cache load + extraction | N/A | N/A | 0.472-0.513 s |
| Trace length / RT grid match | oracle | oracle | 100 / 100 |
| Peak status match | oracle | oracle | 99 / 100 |
| Apex within 0.05 min | oracle | oracle | 77 / 100 |
| Area relative delta median | oracle | oracle | 2.595% |
| Area relative delta p95 | oracle | oracle | 1048.47% |
| Apex delta p95 | oracle | oracle | 1.893 min |

The table shows local-index `max` because it was the closer area comparator.
The measured `sum` mode did not improve parity: it had the same 99/100 peak
status match and 77/100 apex-within-0.05-min count, while its area relative
delta was 264.498% at the median and 5775.637% at p95.

The NPZ payload is `325340836` bytes, or 1.6435 times the RAW size. Its
stored NumPy arrays occupy `325338488` bytes; mass and intensity each occupy
`162632936` bytes. Direct and super-window runs produced the same NPZ SHA256:
`FFCE49CF4ECAC7A384DAF7FE6258D0295BBD5278407F8C5429478FE8E2CFAEEE`.

Result artifact identity:

| Artifact locator | SHA256 |
| --- | --- |
| `<RUN_CHECKOUT>/output/issue124_raw_artifact_capability_probe_20260724/direct/ms1_index_backfill_audit.json` | `312E0F55A26F8841BBD3EF6A5216FF5969AFABA2CCF45A7D758CA99489CBF9EA` |
| `<RUN_CHECKOUT>/output/issue124_raw_artifact_capability_probe_20260724/superwindow/ms1_index_backfill_audit.json` | `F22592CAED7CD908A9D0481BA240603E4D00F2FDB6BE01290AB37D14351172BC` |
| Both `BenignfatBC1055_DNA.ms1_index.npz` payloads | `FFCE49CF4ECAC7A384DAF7FE6258D0295BBD5278407F8C5429478FE8E2CFAEEE` |

These result artifacts are also ignored local outputs. Their hashes bind the
reported metrics if the bundle is restored; the tracked packet does not claim
to contain the raw result payloads.

This one-sample result is sufficient to falsify exact backend parity and show
the cost shape. It is not population-wide performance evidence.

## Historical 8RAW Cross-Check

The 2026-06-22 diagnostic covered 8 samples and 30,176 requests. It had exact
trace-length and RT-grid agreement, but local `max` peak status matched
28,896/30,176 requests; area relative delta was 2.91% at the median and
403.62% at p95. Vendor super-window extraction took 11.24 s versus 373.50 s
for the direct shape used by that diagnostic. The eight NPZ files occupied
about 2.53 GiB versus about 1.49 GiB of source RAW.

That packet did not retain its producer commit, and relevant helpers changed
afterward. It is historical support for the same non-parity conclusion, not a
current-checkout acceptance run.

## Specification Capability Is Not Implementation Capability

Primary sources clarify the boundary:

- Thermo examples select an MS controller before using scan-number APIs, while
  the PSI native-ID vocabulary represents Thermo identity as
  `controllerType`, `controllerNumber`, and `scan`.
  [Thermo example](https://github.com/thermofisherlsms/RawFileReader/blob/f3c49e4e362b78c2674268082ce5862c0c245ed0/ExampleProgram/NetCoreRandom/Program.cs#L188-L233)
  [PSI-MS CV](https://github.com/HUPO-PSI/psi-ms-CV/blob/aae574b6b790d39243c88e5c1feaebd138f4e84a/psi-ms.obo#L5596-L5600)
- RawFileReader exposes centroid, segmented/profile, and chromatogram access.
  XIC currently uses segmented scan with simplified fallback and does not
  preserve the representation flag.
  [Thermo spectrum example](https://github.com/thermofisherlsms/RawFileReader/blob/f3c49e4e362b78c2674268082ce5862c0c245ed0/ExampleProgram/NetCoreRandom/Program.cs#L892-L934)
- mzML can represent native spectrum IDs, profile/centroid spectra,
  chromatograms, and binary arrays. That expressibility does not prove that
  XIC has an adapter or that conversion is lossless.
  [mzML XSD](https://github.com/HUPO-PSI/mzML/blob/440a8dd84b71626ff6489db6fe6ba261da2327f5/schema/schema_1.1/mzML1.1.1.xsd#L817-L913)
  [PSI-MS profile/centroid terms](https://github.com/HUPO-PSI/psi-ms-CV/blob/aae574b6b790d39243c88e5c1feaebd138f4e84a/psi-ms.obo#L1070-L1082)
- ProteoWizard conversion options can change numeric precision, apply
  centroiding, or alter zero samples. Any future comparison must pin the
  converter and all options.
  [msconvert options](https://github.com/ProteoWizard/pwiz/blob/e4e7a159427648dd791735c229c5662607045493/pwiz_tools/commandline/msconvert-help.txt#L25-L64)
  [peak picking](https://github.com/ProteoWizard/pwiz/blob/e4e7a159427648dd791735c229c5662607045493/pwiz_tools/commandline/msconvert-help.txt#L107-L113)

## Unknowns And Invalidation Triggers

| Unknown | Why it matters | Smallest reopening trigger |
| --- | --- | --- |
| Installed RawFileReader version and multi-controller identity | A bare scan number is not durable across all source namespaces | A tracer needs durable source-observation identity or another controller |
| Profile/centroid fidelity | Current models do not state which representation produced their arrays | A method needs scan-level intensity fidelity rather than vendor XIC |
| RAW-to-mzML crosswalk | Format expressibility is not conversion parity | A real downstream consumer requires mzML as an acquisition source |
| Corrupt/unsupported source behavior | Current failure classes are generic and workflow handling differs | #128 needs typed retry/partial-result policy |
| Peak memory and atomic persistent writes | NPZ bytes provide only a memory lower bound | A persistent scan-index design is seriously proposed |
| Current-v5 zero-RAW real cache hit | Existing real cache evidence predates v5 | A tracer claims v5 cache reuse rather than synthetic behavior |
| Verified retained-trace plot rebuild | Existing pieces are not one fail-on-open path | The dual-policy tracer requires regenerated review plots |

Do not widen to another sample, 8RAW, 85RAW, or mzML conversion merely to
collect more numbers. Reopen only when one unknown above can change an
implementation decision.

## Validation And Authority

- Current no-RAW focused tests:
  `87 passed` across RAW reader, MS1 index, Alignment scan-index source,
  Alignment timing, and overlay cache tests.
- Current bounded RAW probe: two completed one-sample runs, one direct and one
  super-window.
- No 8RAW or 85RAW rerun and no mzML conversion was performed.
- Product behavior and all public contracts are unchanged.
- No selected peak/area, counted detection, Matrix identity/value, workbook,
  Backfill-write, activation, publication, or export authority is granted.
