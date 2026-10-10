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

### Targeted

- The primary Targeted path is RAW folder, target list, then Run.
- The standard method is the default. Advanced method settings stay collapsed
  until the operator opens them. The separate DLL section expands when its
  value needs attention.
- A persisted setting that differs from the canonical method is labelled
  `自訂參數 · N 項調整`. Only the 11 scalar parameters owned by
  `xic_extractor.settings_schema.METHOD_KEYS` contribute to the count; batch, environment,
  workers, output, targets, and resources do not. **回到預設** restores those
  scalar parameters. Gaussian is fixed information, not a method choice.
- `settings.csv` is the sole active Targeted settings file. Reset preserves
  batch, resource and execution settings. GUI and CLI use the existing settings
  parser; the Workbench only presents its method values and differences.
  Full replay remains manifest/input-bound.
- Legacy DLL preferences are converted with backups when the workspace opens.
  Ordinary configuration reads do not migrate or write files. The workspace
  supplies the shared DLL value to both workflows.
- `count_no_ms2_as_detected` affects legacy fallback rows without formal
  projection fields, not formal Detection %. New saves use only the canonical
  smoothing-window key; legacy aliases load, but conflicting values fail.
- The target table supports CSV, TSV, and Excel import plus in-workspace edit.
  Unknown source columns and numeric text survive edits where the target owner
  permits them.
- Save writes `settings.csv` and `targets.csv` in the selected config
  directory. Run calls the canonical Targeted application and publishes its
  workbook and result summary.
- **開始新批次** changes the output paths for another run and keeps the current
  method and target list. It does not silently reset a custom method.

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
  visibility. Role corrections and the source locator persist in
  `discovery_gui.json`.
- The internal-standard list uses only rows marked `is_istd=TRUE`. An empty
  input opts out; a non-empty list without an internal standard is invalid.

### Results and exports

- Targeted and Untargeted results appear in the same HTML workbench that
  launched the run. Available artifacts remain links to owner-published files.
- A completed Alignment result can start Feature Quality export with its bound
  Matrix identity and an explicit sample-metadata file and threshold. The
  source Matrix remains unchanged.
- A completed evidence locator can open the Inspector review view directly or
  generate a custom standalone Inspector HTML report.
- Inspector selection and rendering use `xic_inspector`. On-demand XIC uses
  the saved observation binding and does not choose peaks, integrate, or write
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
| `xic_workbench/workspace.py` | Completed evidence selection and on-demand XIC jobs |
| `xic_workbench/assets/` | Unified analysis and review HTML, JavaScript, and CSS |

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
