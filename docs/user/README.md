# User Guides

This directory serves people operating XIC Extractor or learning how its
results are produced. Start with the [HTML reading index](readers-index.html)
for a visual route, or choose the detailed Markdown workflow below.

Markdown workflow and reference pages own operation instructions. HTML pages
explain those workflows and concepts; they do not define product behavior or
grant validation or writer authority. Formal product rules remain in the
[product topic index](../product/README.md). To change implementation, start
with the [architecture reading guide](../architecture-contract.md#reading-the-code).

## Read In Browser

| Guide | Scope |
| --- | --- |
| [Reading index](readers-index.html) | Choose operation, processing, or system explanations |
| [Targeted Extraction](targeted-extraction.html) | Steps, inputs, workbook, and review starting points |
| [Untargeted Discovery](untargeted-discovery.html) | Steps, preset, candidate outputs, and alignment matrix |
| [Core modules](core-modules-explainer.html) | Visual overview of both workflows and their shared processing core |
| [Processing guide](processing-guide.html) | Discovery, peaks, Alignment, Backfill, and matrix decisions |
| [System guide](system-guide.html) | Module responsibilities, execution lifecycle, and read-only review |
| [RAW to Final Matrix](raw-to-final-matrix-product-story.html) | A worked explanation of the evidence-to-result flow |

## Workflow Owners

| Guide | Scope |
| --- | --- |
| [`targeted-extraction.md`](targeted-extraction.md) | Targeted Extraction GUI/CLI workflow, settings, targets, workbook outputs, and troubleshooting |
| [`untargeted-discovery.md`](untargeted-discovery.md) | Untargeted Discovery GUI workflow, modes, inputs, outputs, and review starting points |

## Reference

| Reference | Scope |
| --- | --- |
| [`targeted-config-reference.md`](targeted-config-reference.md) | Field-level reference for `settings.csv` and `targets.csv` |
| [`targeted-output-reference.md`](targeted-output-reference.md) | Column reference for every sheet in the Targeted Excel workbook |
| [`untargeted-output-reference.md`](untargeted-output-reference.md) | Column reference for discovery CSVs and alignment TSVs |
| [`untargeted-preset-guide.md`](untargeted-preset-guide.md) | Preset TOML structure, available presets, discovery/alignment defaults |
| [`Feature quality and selection`](../product/feature-quality.md) | GUI/CLI Matrix selection, explicit availability threshold, and traceable analysis-set output |

## Operator Validation

Targeted workbook validation is an operator CLI rather than a separate docs
lane. Run:

    .venv\Scripts\python.exe scripts\validation_harness.py --help

The command owns suite selection, dry-run, baseline comparison, outputs, and
the opt-in 85RAW stop rule. Untargeted Alignment validation uses
`scripts.run_alignment` and the route in
[`docs/agent/parameter-settings.md`](../agent/parameter-settings.md).
