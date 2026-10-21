# Local HTML Analysis Workbench

The local HTML workbench is the human surface for Targeted and Untargeted
analysis, result actions, and completed-evidence review. It orchestrates the
existing application, writer, Feature Quality, and Inspector owners. It does
not create another scientific or product-authority lane. This page keeps its
historical filename as the stable topic locator.

## Contract

### Launch and workspace

- `xic-extractor`, `xic-workbench`, and `python -m xic_workbench` start the
  same workbench through `xic_workbench.cli`.
- The server binds to `127.0.0.1`. The default port is chosen by the operating
  system, and the default browser opens the printed URL.
- `--no-open` suppresses browser opening. `--port` selects the local port.
  `--config-dir` selects the writable settings directory.
- The navigation exposes Targeted, Untargeted, and completed-result review in
  one HTML application. Theme and last view are machine-local preferences.
- One analysis or export job runs at a time. The workbench reports stage,
  progress, elapsed time, terminal status, and outputs.
- Targeted, Untargeted, and Inspector jobs accept cooperative cancellation.
  Feature Quality and sample export finish their active write.
- Completed results and the review header expose direct downloads of the
  published Targeted XLSX or bound Untargeted Matrix TSV. These links serve the
  existing files without rerunning analysis or rebuilding them from Inspector.

### Targeted

- The primary path is samples, method/target intent, Run, then result review.
- Target rows contain name, m/z, expected RT in minutes, CID-NL tag, role and
  ISTD pairing. Tags use the same built-in chemistry recipes as Discovery.
- A portable method saves the target intent and strategy version. There is no
  advanced Targeted method panel, parameter reset, Dirty Matrix branch or user
  worker/backend selector. Scientific policy is recorded in run metadata.
- `workspace.json` stores application settings (DLL and worker count), batch
  inputs (RAW and sample metadata), and target rows in one atomic generation.
  Reading configuration never writes it; unsupported settings are reported,
  not preserved as hidden effective overrides. No schema conversion is offered.
- Explicit CSV/TSV and `Targets`-sheet XLSX imports produce editable target intent. Numeric text and
  descriptive metadata survive a validated save. Scientific legacy fields do not.
- Save validates settings and targets before writing. Run invokes the canonical
  Targeted application and publishes one finalized workbook; value, count and
  review stay distinct.
- **開始新批次** clears the RAW directory, injection order and displayed results,
  preserving the method, targets and machine DLL path.

### Untargeted modes

| Mode | Inputs | Runs |
| --- | --- | --- |
| `full` | RAW directory, DLL directory, output directory | Batch Discovery, then Alignment |
| `discovery_only` | One RAW file, DLL directory, output directory | Single-file Discovery |
| `align_only` | RAW directory, `discovery_batch_index.csv`, DLL directory, output directory | Alignment |

Batch Discovery-only remains a CLI surface. The HTML workbench uses `full` for
directory-level Discovery followed by Alignment.

### Untargeted method and sample setup

- Built-in and imported TOML methods use `xic_extractor.presets`. The HTML
  editor supports pure CID-NL methods, including tag selection, custom neutral
  losses, `union` or `intersection`, and the exposed tuning values.
- The editor supports import, create, edit, Apply, and Save As. Apply stores the
  effective method as `current_discovery_method.toml`; Save As writes a portable
  TOML accepted by the CLI.
- A run uses the displayed method snapshot. A later external file edit cannot
  change the in-flight request.
- Invalid or unsupported imports preserve the selected method. The workbench
  defaults to each RAW file's full acquisition range read from instrument
  metadata, matching the CLI. Optional RT cropping preserves explicit finite,
  non-negative bounds; an omitted end remains automatic. Acquisition limits
  are resolved per RAW, not from the first file in a batch. Targeted compound
  search windows remain independent.
- The advanced method section stays closed until needed. Explicit tuning values
  are overrides on the selected method.
- **讀取 RAW 樣本** reads names, roles, and injection order through the shared
  RAW-header metadata owner. Optional Word, Excel, CSV, or TSV acquisition
  metadata overrides matched fields; missing injection order is completed by
  that same header owner. Saved acquisition metadata and role corrections also
  apply when an analysis starts without opening the sample editor first.
- The sample editor retains undisplayed metadata fields. Filters change only
  visibility. Role, group, and exclusion edits save at once to the RAW folder's
  confirmed sample metadata, shared with Targeted (see
  [sample metadata](sample-metadata-qc.md#workbench-sample-step)); the source
  locator persists in `discovery_gui.json`.
- The internal-standard list shares Targeted XLSX (`Targets`, with optional
  `Method`), CSV and TSV intake. Choose a file, browse its local path, or drop
  it onto the list area; the adjacent link downloads the shared XLSX example.
  Only `role=istd` rows whose configured neutral loss is enabled by the active
  Untargeted method are used for reference extraction. Editable tag names do not
  change chemistry matching. With multiple tags, a standard may match any
  enabled recipe; this does not change Discovery's union/intersection rule.
- The preview shows each standard's CID-NL tag and method applicability. Changing
  the method refreshes the preview. Incompatible standards are skipped, not ND;
  if none apply, the run proceeds without an ISTD reference. The same selection
  owner governs preview and execution. Targeted method settings are not applied.
- Browser uploads are validated before a workspace copy is retained. Invalid
  uploads preserve the active list; Save or Run persists the selected locator.
  An empty input opts out; a non-empty list without an internal standard is invalid.

### Results and exports

- Targeted and Untargeted results appear in the same HTML workbench that
  launched the run. Available artifacts remain links to owner-published files.
- A completed Alignment result can start Feature Quality export with its bound
  Matrix identity and an explicit sample-metadata file and threshold. The
  source Matrix remains unchanged.
- A completed evidence locator opens the shared Inspector review view directly
  in the workbench, without an iframe or intermediate HTML. Saved results and
  loaded curves use the same renderer as standalone Inspector HTML.
- A view download packages its captured data and sample scope on demand; later
  queries or another opened source do not replace that snapshot. The downloaded
  single HTML file works without the server or network. Custom Inspector export
  and the existing Inspector CLI remain available.
- Inspector selection and rendering use `xic_inspector`. Untargeted on-demand
  XIC uses the saved observation binding. In Targeted review, each sample row's
  **看 XIC** loads only that sample's target and paired ISTD, including ND rows
  with known target coordinates. Select the RAW and DLL directories first.
  Curves open in a side drawer without replacing the saved results table;
  closing restores row focus, and revisiting a loaded sample reuses its curves.
  Targeted charts show original scan points and smoothed lines together.
- Targeted curves use the workbook's extraction tolerance and signal-processing
  settings over the full acquisition trace; saved peak bounds are overlays.
  XLSX sample names do not certify the original RAW: the view and exported HTML
  explicitly mark that binding as unverified. New RAW fingerprints identify
  this diagnostic extraction only.
- Plot RT controls change the loaded view without rereading RAW. Neither mode
  chooses peaks, reintegrates, changes counted detection, or writes workbook or
  Matrix values.
- Sample metadata export supports TSV and CSV. Custom sample-role corrections
  are included in the submitted and exported population.

### Local persistence and packaging

- `xic_workbench.config_io` owns Targeted settings and targets.
  `xic_workbench.discovery_config_io` owns Untargeted local state.
- `workbench.json` stores the last view, theme, and DLL directory. A supplied
  `--config-dir` relocates these writable files together.
- `xic_extractor.spec` starts `xic_workbench/__main__.py`, excludes Qt, and
  declares the HTML/JavaScript/CSS, Inspector, preset, and package-data closure.
- The spec and packaging tests define the frozen-build contract. A final frozen
  artifact is not considered verified until the release build and smoke gate
  pass for that artifact.

## Surfaces

| Surface | Role |
| --- | --- |
| `xic_workbench/cli.py` | Loopback launch, browser behavior, port, config directory |
| `xic_workbench/server.py` | Local routes and packaged assets |
| `xic_workbench/analysis_workspace.py` | Settings, method, sample, and artifact operations |
| `xic_workbench/jobs.py` | Single-job lifecycle and owner dispatch |
| `xic_workbench/targeted.py` | Targeted form metadata, canonical application adapter, result summary |
| `xic_workbench/untargeted.py` | Untargeted mode dispatch and application adapter |
| `xic_workbench/methods.py` | CID-NL method editing and portable preset serialization |
| `xic_workbench/samples.py` | Sample metadata matching, role correction, and export |
| `xic_workbench/workspace.py` | Completed evidence selection, session view snapshots, lazy HTML export, and on-demand XIC jobs |
| `xic_workbench/targeted_curves.py` | Read-only Targeted and paired-ISTD RAW acquisition |
| `xic_workbench/assets/` | Unified analysis/review host and its interaction with the shared Inspector renderer |

## Boundaries

- **Owns:** local navigation, form state, run/stop feedback, writable config,
  owner dispatch, and output presentation.
- **Does not own:** peak selection, grouping, Backfill, counted detection,
  Matrix publication, Feature Quality rules, Inspector evidence meaning,
  product maturity, or release readiness.
- [Discovery](discovery.md), [Alignment](alignment.md),
  [Targeted Selection](targeted-selection.md), [Feature Quality](feature-quality.md),
  [XIC Inspector](xic-inspector.md), and [Productization](productization.md)
  remain the respective behavior and authority owners.

## Verification

| Contract | Focused checks |
| --- | --- |
| Launch, loopback routes, and review | `tests/test_xic_workbench.py`, `tests/test_workbench_analysis.py` |
| Targeted config, labels, reset, and execution adapter | `tests/test_workbench_config.py`, `tests/test_workbench_targeted.py`, `tests/test_targeted_service_contract.py` |
| Untargeted config, methods, samples, and three modes | `tests/test_workbench_config.py`, `tests/test_workbench_untargeted.py`, `tests/test_processing_run_spec_entrypoints.py` |
| Frozen resource and entry-point contract | `tests/test_pyinstaller_spec.py` |

Source tests verify the current Python and resource contract. Only a completed
artifact build and frozen smoke run verify the distributable application.

## Pitfalls

- Treating the browser UI or its local JSON as a second scientific owner.
- Treating a custom-method label as a new method schema or readiness claim.
- Treating an acquired review curve as a replacement product measurement.
- Claiming a frozen release from source tests without its build and smoke
  evidence.

## See Also

- [XIC Inspector](xic-inspector.md)
- [Presets](presets.md)
- [Feature Quality](feature-quality.md)
- [Architecture contract](../architecture-contract.md)
