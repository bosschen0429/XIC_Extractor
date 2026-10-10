# XIC Extractor

XIC Extractor turns Thermo `.raw` LC-MS data into chromatographic measurements,
quantitative results, and evidence you can inspect. Use **Targeted** when you
have a compound list, or **Untargeted** to discover and align features across
samples.

## Start Here

| What do you want to do? | Start with | What you will get |
| --- | --- | --- |
| Run an analysis | [Use the application](#use-the-application) | Choose a workflow, prepare inputs, and find the first result to inspect |
| Understand how results are produced | [Follow the evidence](#follow-the-evidence) | Trace candidates through peak measurement, grouping, decisions, and output |
| Something went wrong or a feature is missing | [Get help](#get-help) | Find troubleshooting guidance or report an issue |

## Use The Application

### Choose a workflow

| Workflow | Bring | Main result | Step-by-step guide |
| --- | --- | --- | --- |
| **Targeted Extraction** | RAW files and a targets CSV with m/z, RT windows, and optional neutral-loss/ISTD settings | `xic_results_YYYYMMDD_HHMM.xlsx` | [Targeted guide](docs/user/targeted-extraction.md) |
| **Untargeted Discovery** | RAW files and a method preset | Discovery candidates, then `alignment_matrix.tsv` for supported CID-NL Alignment | [Untargeted guide](docs/user/untargeted-discovery.md) |

### Run the HTML workbench

You need Windows 10 or 11, .NET 6 or later, and the Thermo RawFileReader DLLs
(from Xcalibur or [Thermo Fisher](https://github.com/thermofishersci/RawFileReader)).

From an installed package, run `xic-extractor`, `xic-workbench`, or
`python -m xic_workbench`. The process starts a loopback server and opens the
same local HTML workspace for analysis and result review. Use `--no-open` when
you only want the printed URL, `--port` to choose a port, and `--config-dir` to
choose the writable settings directory.

For Targeted, select the RAW folder, load or edit the targets, and run. The
standard method is the default; optional advanced settings stay collapsed.
Persisted changes are labelled **自訂方法**, and **回到預設** restores the
standard method values. Untargeted provides full, Discovery-only, and
Alignment-only modes, plus method import/edit/export, sample metadata and role
correction. Both workflows show progress, cancellation, outputs, and result
actions in this workspace.

Retained CID-NL saves completed evidence alongside its Matrix. Additional
review TSVs, galleries, and workbooks require explicit output options.
For settings, column meanings, and troubleshooting, continue to the
[user guide index](docs/user/README.md). HTML guides in that index can be opened
locally in a browser; the Markdown guides can be read directly on GitHub.

### Review a completed batch

From an installed Python package, run `xic-workbench <batch-output-folder>`
(or `python -m xic_workbench <batch-output-folder>`). The local browser workspace
opens saved CID-NL results, searches the full review population, and acquires
XIC only for the selected group and observed samples. Choose the RAW and
RawFileReader DLL folders in the workspace, then download the current view as
an independent HTML report. See the [workspace guide](docs/product/xic-inspector.md#local-html-workspace).
The analysis and review views share the same local process and navigation.
The frozen Windows build is part of the packaging contract, but its final
release artifact still requires the release gate; source behavior alone does
not prove that artifact.

## Follow The Evidence

```text
RAW measurements → candidates and peak evidence → cross-sample groups
                 → cell decisions → workflow output → read-only inspection
```

Targeted and Untargeted share scientific owners while retaining different
search policies and output contracts. A candidate, a numeric value, and a
counted detection are different facts; a review plot does not decide what the
product writes.

For a guided explanation, start with the [core modules guide](docs/user/core-modules-explainer.html),
then follow its links to processing or system behavior. The
[HTML reading index](docs/user/readers-index.html) lets you enter at a specific question.

To verify or change a rule, use the [product topic index](docs/product/README.md).
It points to the maintained behavior contracts. The
[LC-MS/MS evidence rules](docs/lc-msms-evidence-rules.md) own the scientific
interpretation and area policy; HTML pages explain those owners.

## Get Help

Start with the troubleshooting section in your workflow guide:
[Targeted](docs/user/targeted-extraction.md#troubleshooting) or
[Untargeted](docs/user/untargeted-discovery.md#troubleshooting).

If the problem remains, [open an issue](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/new/choose).
Include the app version, workflow, steps to reproduce, expected versus actual
result, and the error message or a relevant screenshot. For a feature request,
describe the analysis task you want to complete.

## Repository Reference

For source work, see the [repository tree](docs/project-layout.md#repository-tree)
and [architecture guide](docs/architecture-contract.md#reading-the-code).
Coding agents start at [AGENTS.md](AGENTS.md); checkout setup is documented in
[Local Execution](docs/agent/parameter-settings.md#runner-and-environment).
