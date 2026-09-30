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

### Run the packaged app

You need Windows 10 or 11, .NET 6 or later, and the Thermo RawFileReader DLLs
(from Xcalibur or [Thermo Fisher](https://github.com/thermofishersci/RawFileReader)).
The packaged app includes its Python runtime.

1. Download the Windows ZIP from [Releases](https://github.com/Chao-hu-Lab/XIC_Extractor/releases), unzip it, and run `XIC_Extractor.exe`.
2. Set the RAW data and Thermo DLL directories, then follow the workflow guide above to supply targets or a preset.
3. After Targeted finishes, open **Overview**, then **Review Queue** in the workbook. After Untargeted Alignment finishes, start with the Matrix; use **Export XIC Inspector** to inspect the retained CID-NL evidence as offline HTML.

Retained CID-NL saves completed evidence alongside its Matrix. Additional
review TSVs, galleries, and workbooks require explicit output options.
For settings, column meanings, and troubleshooting, continue to the
[user guide index](docs/user/README.md). HTML guides in that index can be opened
locally in a browser; the Markdown guides can be read directly on GitHub.

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
