# Guide Assets

Maintenance reference for the diagrams embedded in the [user guides](../README.md).

The human-facing HTML guides use topic maps to supplement their explanations;
they are not separate product contracts. The reading index has a native HTML
navigation map: each page node is a relative link to that guide, usable by mouse
or keyboard without JavaScript. Edit those links in `../readers-index.html`, not
in an Archify specification. The other guides embed static SVG topic maps.

| Guide | Diagram asset stem | Question |
| --- | --- | --- |
| Reading index | Native links in `../readers-index.html` | Which guide should I read next? |
| Core guide | `core-processing-map` | What processing and decisions are shared? |
| Targeted | `targeted-workflow` | How do I prepare, run, and review a batch? |
| Untargeted | `untargeted-modes` | Where does each run mode start and stop? |
| Processing | `evidence-decisions` | How do peak evidence, membership, and numeric answers differ? |
| System | `execution-boundaries` | Who coordinates, decides, publishes, and reads? |
| RAW story | `raw-matrix-journey` | How does evidence reach Matrix, and where does a diagnostic branch stop? |

Each `<stem>.svg` is a static, light-theme diagram derived from
[Archify 3.1.0](https://github.com/tt-a1i/archify) and the adjacent
`<stem>.architecture.json`. Only the JSON, lightweight SVG, and guide styles
are tracked; complete interactive HTML, receipts, and screenshots stay under
ignored `output/`. Reading the guides requires no JavaScript, Node.js, server,
or remote assets. SVGs use system fonts rather than embedded font bundles;
font appearance can vary across computers. Archify-derived paint and geometry
are covered by [`archify-LICENSE.txt`](archify-LICENSE.txt).
[`guide-maps.css`](guide-maps.css) styles the additional embeds.

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
