# XIC Extractor

Extracted-ion chromatogram (XIC) toolkit for LC-MS metabolomics. Reads Thermo
`.raw` instrument files, extracts chromatographic peaks for targeted or
untargeted workflows, and produces Excel workbooks and HTML reports ready for
human review.

<table>
  <tr>
    <td><img src="assets/screenshots/targeted-workspace.png" alt="Targeted Extraction workspace"></td>
    <td><img src="assets/screenshots/untargeted-workspace.png" alt="Untargeted Discovery workspace"></td>
  </tr>
  <tr>
    <td align="center"><strong>Targeted Extraction</strong></td>
    <td align="center"><strong>Untargeted Discovery</strong></td>
  </tr>
</table>

## How It Works

XIC Extractor provides two workflows. Both start from Thermo `.raw`
instrument files and produce outputs you can review directly — no
post-processing scripts required.

### Targeted Extraction

Use this when you already know the compounds you want to quantify.

```
.raw files + target list ──→ XIC extraction ──→ peak scoring ──→ Excel workbook
```

You provide a CSV target list with compound names, m/z values, RT windows,
ppm tolerances, and optional neutral-loss masses and ISTD pairings. The
pipeline extracts ion chromatograms for each target, scores candidate peaks,
checks MS2 neutral-loss confirmation, and writes an Excel workbook containing:

| Sheet | Content |
| --- | --- |
| Overview | Run summary, detection rates, review priorities |
| Review Queue | Rows needing human attention |
| XIC Results | Per-sample RT, area, NL status, confidence, and reason |
| Summary | Per-target detection, area, and RT statistics |

**Primary output:** `output/xic_results_YYYYMMDD_HHMM.xlsx`

### Untargeted Discovery

Use this when you want to find features across samples without a predefined
target list.

```
.raw files + method preset ──→ feature discovery ──→ cross-sample alignment ──→ matrix + gallery
```

You select a built-in method preset (resolver, alignment, and post-alignment
settings bundled as a TOML file). The pipeline discovers candidate features
per sample using seed evidence (MS2 neutral loss, MS1 traces), aligns them
across samples into a cross-sample matrix, and generates HTML gallery pages
for visual review.

| Output | Content |
| --- | --- |
| `discovery_candidates.csv` | Per-sample candidate features with evidence detail |
| `discovery_batch_index.csv` | Handoff index linking discovery to alignment |
| `alignment_matrix.tsv` | Cross-sample aligned feature matrix |
| `alignment_review.tsv` | Review context for aligned features |
| Gallery HTML | Visual inspection of candidates and backfill context |

## System Requirements

- Windows 10 or 11
- .NET 6 or later
- Thermo RawFileReader DLLs (installed with Thermo Xcalibur, or available from
  [Thermo Fisher's developer portal](https://github.com/thermofishersci/RawFileReader))

## Quick Start

### Packaged App

1. Download `XIC_Extractor-Windows-vX.Y.Z.zip` from
   [Releases](https://github.com/Chao-hu-Lab/XIC_Extractor/releases).
2. Unzip it and run `XIC_Extractor.exe`.
3. Point the GUI at your RAW data directory and Thermo DLL directory.

### Developer Checkout

```powershell
git clone https://github.com/Chao-hu-Lab/XIC_Extractor.git
cd XIC_Extractor

uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py
uv run python -m gui.main
```

Run tests with:

```powershell
uv run pytest
```

## Guides

| I want to... | Read this |
| --- | --- |
| Run targeted extraction step by step | [Targeted Extraction guide](docs/user/targeted-extraction.md) |
| Run untargeted discovery from the GUI | [Untargeted Discovery guide](docs/user/untargeted-discovery.md) |

## CLI

Targeted extraction from a developer checkout:

```powershell
uv run python -m scripts.run_extraction --base-dir .
```

Installed entry point:

```powershell
uv run xic-extractor-cli --base-dir .
```

## Documentation

| Audience | Document | Purpose |
| --- | --- | --- |
| Users | [User guide index](docs/user/README.md) | GUI/CLI workflow walkthroughs |
| Maintainers | [Product topic index](docs/product/README.md) | Product contracts, output schemas, authority boundaries |
| Developers | [Architecture contract](docs/architecture-contract.md) | Package ownership, dependency rules, refactoring discipline |
| Developers | [Project layout](docs/project-layout.md) | Directory structure, naming conventions, file placement rules |
| Domain experts | [LC-MS/MS evidence rules](docs/lc-msms-evidence-rules.md) | Evidence semantics, peak selection, area ownership |
