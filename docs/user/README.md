# User Guides

Start with the analysis you want to complete. Each workflow guide takes you
through inputs, execution, reading the result, and troubleshooting. You can
look up processing principles later when a result raises a question.

## Run An Analysis And Read The Result

| Task | Guide | First result to inspect |
| --- | --- | --- |
| Measure a known target list | [Targeted Extraction](targeted-extraction.md) | Workbook `摘要`, then `需檢查` when present |
| Discover and compare features across samples | [Untargeted Discovery](untargeted-discovery.md) | `alignment_matrix.tsv`, then feature/sample evidence in the workbench |
| Install and launch from source | [README quick start](../../README.md#run-the-html-workbench) | Local browser workspace |
| Prepare a Matrix for downstream analysis | [Read and use the Matrix](untargeted-discovery.md#read-and-use-the-matrix) | A separate curated Matrix; source results remain unchanged |

Prefer illustrated pages? Open the [browser reading index](readers-index.html)
locally, then choose the Targeted or Untargeted guide. GitHub displays HTML
source rather than the rendered guide; Markdown links work directly on GitHub.
The Markdown workflow pages own the operation instructions; the illustrated
pages explain the same workflow and link to the detailed references.

## Reader Setup

RAW analysis uses Windows, .NET 8 or later, and RawFileReader **8.0.42**. The
workbench setup notice checks the dependencies. Read the linked Thermo license,
accept it, then click **下載並設定 RawFileReader**. The installer sets the shared
DLL folder for both workflows. Xcalibur is not needed and is not modified.

If .NET is missing, run the notice's command
(`winget install Microsoft.DotNet.Runtime.10`), then restart the workbench.

For manual reader installation from the source checkout:

```powershell
uv run xic-install-thermo-reader --help
# Read the Thermo license linked in help before accepting it.
uv run xic-install-thermo-reader --accept-thermo-license
```

The default folder is `%LOCALAPPDATA%\XIC_Extractor\RawFileReader-8.0.42`;
`--destination` selects another folder. The installer downloads RawFileReader,
CommonCore.Data, and OpenMcdf from official sources, checks pinned hashes, and
replaces only those DLLs. Manual installation retains existing workspace
settings: select the installed folder in the workbench afterward.

The workbench button also updates local DLL settings and replaces a startup
override for that session. If you launch with `--dll-dir` or
`THERMO_RAWFILE_READER_DLL_DIR`, update or remove that external setting before
the next launch; installation does not change it.

Use `uv run xic-workbench --help` for startup options such as `--no-open`,
`--port`, and `--config-dir`. Console-free launcher logs are in
`output/launcher/`.

## Understand A Result

You do not need all of these pages. Choose the question that brought you here.

| Question | Read |
| --- | --- |
| What do the two workflows share, and what do they deliver? | [Short processing map](core-modules-explainer.html) |
| Why is a candidate, a matched feature, or an area not yet the same as a detection? | [Processing principles: the A/B/C sample story](processing-guide.html) |
| How does that reasoning apply to DNA adduct CID-NL analysis? | [Worked example](raw-to-final-matrix-product-story.html) |
| Which exact rule governs this result? | [Product topic index](../product/README.md) |
| How are implementation, execution, and review separated? | [System guide](system-guide.html), then the [architecture reading guide](../architecture-contract.md#reading-the-code) |

## Look Up A Field Or Method

| Reference | Use it for |
| --- | --- |
| [Targeted configuration](targeted-config-reference.md) | Method/target-table XLSX, local work state, identification method definitions |
| [Targeted output](targeted-output-reference.md) | Workbook sheets, columns, units, and saved evidence |
| [Untargeted presets](untargeted-preset-guide.md) | Method editing, TOML, and supported settings |
| [Untargeted output](untargeted-output-reference.md) | Discovery CSVs, Alignment TSVs, and explanation storage |
| [Matrix Curation](../product/matrix-curation.md) | Curation steps, inputs, interpretation, and separate outputs |

For operator validation, use the [Targeted validation route](targeted-extraction.md#validation)
or [local Alignment validation](../agent/parameter-settings.md#alignment-and-handoff).
For maintaining the diagrams, see [guide assets](assets/README.md).
