# User Guides

This directory holds public user-facing workflow guides. These pages explain how
to operate the application, which files users edit, and which outputs to inspect.
They do not replace product-topic source-of-truth documents under
[`docs/product/`](../product/README.md).

## Workflow Guides

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

## Operator Validation

Targeted workbook validation is an operator CLI rather than a separate docs
lane. Run:

    .venv\Scripts\python.exe scripts\validation_harness.py --help

The command owns suite selection, dry-run, baseline comparison, outputs, and
the opt-in 85RAW stop rule. Untargeted Alignment validation uses
`scripts.run_alignment` and the route in
[`docs/agent/parameter-settings.md`](../agent/parameter-settings.md).
