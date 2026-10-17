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
| [Core modules](core-modules-explainer.html) | Interactive shared-processing map, followed by expandable workflow and evidence explanations |
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
| [`targeted-config-reference.md`](targeted-config-reference.md) | Targeted method/target-table XLSX and program-managed JSON work state |
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

## Maintain The Embedded Maps

Each human-facing HTML guide embeds its own topic map. The diagrams supplement
the existing explanations and links; they are not separate product contracts.
The reading index uses a navigation map, not an analysis execution flow.

| Guide | Diagram asset stem | Question |
| --- | --- | --- |
| Reading index | `reading-routes` | Which guide should I read next? |
| Core guide | `core-processing-map` | What processing and decisions are shared? |
| Targeted | `targeted-workflow` | How do I prepare, run, and review a batch? |
| Untargeted | `untargeted-modes` | Where does each run mode start and stop? |
| Processing | `evidence-decisions` | How do peak evidence, membership, and numeric answers differ? |
| System | `execution-boundaries` | Who coordinates, decides, publishes, and reads? |
| RAW story | `raw-matrix-journey` | How does evidence reach Matrix, and where does a diagnostic branch stop? |

Each `assets/<stem>.html` is generated with
[Archify 3.1.0](https://github.com/tt-a1i/archify) from the adjacent
`<stem>.architecture.json`. The HTML includes the viewer; reading the local
guides requires no Node.js, server, or remote runtime assets. The bundled
viewer is covered by [`assets/archify-LICENSE.txt`](assets/archify-LICENSE.txt).
[`assets/guide-maps.css`](assets/guide-maps.css) styles the additional embeds.

Edit the JSON, not the generated HTML. When a represented responsibility
changes, recheck its source ranges and set `meta.repository.revision` to the
committed source revision being described. For web source links, use a
revision already published to the remote; otherwise
select `link_mode: "local-only"`. From the repository root, with
`ARCHIFY_ROOT` pointing to an installed Archify 3.1.0 skill package, generate
and verify in ignored storage before publishing the HTML:

```powershell
$map = 'core-processing-map' # Choose a stem from the table above.
node "$env:ARCHIFY_ROOT/bin/archify.mjs" finalize architecture `
  "docs/user/assets/$map.architecture.json" `
  "output/core-map-review/$map.html" `
  --repo-root . --quality showcase --out-dir output/core-map-review --json
if ($LASTEXITCODE -ne 0) { throw 'Archify verification failed' }
Copy-Item "output/core-map-review/$map.html" "docs/user/assets/$map.html"
```

Commit the JSON and generated HTML together. Keep receipts and screenshots
under ignored `output/`; use a fresh `--out-dir` for a later candidate. Check
the embedded guide and standalone viewer in a browser, including source links,
mode switching, keyboard navigation, and narrow-screen containment. The text
explanation remains readable when the iframe or JavaScript is unavailable.
