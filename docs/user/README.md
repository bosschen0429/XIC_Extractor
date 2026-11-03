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
| [Core modules](core-modules-explainer.html) | Shared-processing map, followed by expandable workflow and evidence explanations |
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
| [`Matrix curation`](../product/matrix-curation.md) | GUI/CLI curation of a published Matrix: switchable steps, explicit thresholds, per-feature decisions |

## Operator Validation

Targeted workbook validation is an operator CLI rather than a separate docs
lane. Run:

    .venv\Scripts\python.exe scripts\validation_harness.py --help

The command owns suite selection, dry-run, baseline comparison, outputs, and
the opt-in 85RAW stop rule. Untargeted Alignment validation uses
`scripts.run_alignment` and the route in
[`docs/agent/parameter-settings.md`](../agent/parameter-settings.md).

## Maintain The Embedded Maps

The human-facing HTML guides use topic maps to supplement their explanations;
they are not separate product contracts. The reading index has a native HTML
navigation map: each page node is a relative link to that guide, usable by mouse
or keyboard without JavaScript. Edit those links in `readers-index.html`, not
in an Archify specification. The other guides embed static SVG topic maps.

| Guide | Diagram asset stem | Question |
| --- | --- | --- |
| Reading index | Native links in `readers-index.html` | Which guide should I read next? |
| Core guide | `core-processing-map` | What processing and decisions are shared? |
| Targeted | `targeted-workflow` | How do I prepare, run, and review a batch? |
| Untargeted | `untargeted-modes` | Where does each run mode start and stop? |
| Processing | `evidence-decisions` | How do peak evidence, membership, and numeric answers differ? |
| System | `execution-boundaries` | Who coordinates, decides, publishes, and reads? |
| RAW story | `raw-matrix-journey` | How does evidence reach Matrix, and where does a diagnostic branch stop? |

Each `assets/<stem>.svg` is a static, light-theme diagram derived from
[Archify 3.1.0](https://github.com/tt-a1i/archify) and the adjacent
`<stem>.architecture.json`. Only the JSON, lightweight SVG, and guide styles
are tracked; complete interactive HTML, receipts, and screenshots stay under
ignored `output/`. Reading the guides requires no JavaScript, Node.js, server,
or remote assets. SVGs use system fonts rather than embedded font bundles;
font appearance can vary across computers. Archify-derived paint and geometry
are covered by [`assets/archify-LICENSE.txt`](assets/archify-LICENSE.txt).
[`assets/guide-maps.css`](assets/guide-maps.css) styles the additional embeds.

Edit the JSON, not the generated SVG. When a represented responsibility
changes, recheck its source ranges and set `meta.repository.revision` to the
committed source revision being described. For web source links, use a
revision already published to the remote; otherwise
select `link_mode: "local-only"`. From the repository root, with
`ARCHIFY_ROOT` pointing to an installed Archify 3.1.0 skill package, generate
and verify in ignored storage, then export the SVG:

```powershell
$map = 'core-processing-map' # Choose a stem from the table above.
node "$env:ARCHIFY_ROOT/bin/archify.mjs" finalize architecture `
  "docs/user/assets/$map.architecture.json" `
  "output/core-map-review/$map.html" `
  --repo-root . --quality showcase --out-dir output/core-map-review --json
if ($LASTEXITCODE -ne 0) { throw 'Archify verification failed' }
.venv\Scripts\python.exe tools/export_guide_svg.py `
  "output/core-map-review/$map.html" "docs/user/assets/$map.svg"
if ($LASTEXITCODE -ne 0) { throw 'SVG export failed' }
```

The documentation-only exporter supports the classic Architecture preset in
Archify 3.1.0 for the current node-and-edge maps. It retains authored geometry,
colors, labels, tooltips and static paint, but removes viewer metadata,
animation steps, unused styles/variables/definitions and embedded fonts. Node
outlines and edge-label masks keep their static paint without viewer selectors.
Structural-frame layouts require an exporter update and are rejected rather
than silently losing their context-sensitive paint.
The SVG is not an interactive source inspector; source references remain in
the JSON and local full HTML. Check the tool's format assumptions before
upgrading Archify.

Commit the JSON and generated SVG together. Use a fresh `--out-dir` for a
later candidate. Inspect the embedded and standalone SVG in a browser for
colors, labels, arrows, and narrow-screen containment; check that the reading
index nodes still open the corresponding HTML guides. Archify's HTML checks
do not validate the exported SVG. The guide text remains the detailed,
accessible explanation, and the standalone SVG link allows browser zoom.
