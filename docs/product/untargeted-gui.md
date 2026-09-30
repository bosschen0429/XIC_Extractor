# Untargeted GUI

![Untargeted Discovery workspace](../../assets/screenshots/untargeted-workspace.png)

![Untargeted Discovery in Graphite / Amber](../../assets/screenshots/untargeted-workspace-dark.png)

The Untargeted workspace is the human GUI surface for Discovery, alignment,
and reviewable output access. The workspace orchestrates existing product
workflows. The workspace does not create a separate product-authority lane.

## Contract

### Workspaces and theme

- The main window exposes **Targeted Extraction** and **Untargeted Discovery**
  as first-class workspaces.
- The application provides Porcelain / Indigo (light) and Graphite / Amber
  (dark) themes. The operator switches themes with the button below the
  sidebar brand, **檢視 → 深色模式**, or `Ctrl+D`.
- Both workspaces update immediately after a theme switch. The theme choice
  survives restart through the existing `QSettings` preference of the main
  window.
- `gui/styles.py` owns the shared palette, including method-editor actions.
- Untargeted settings are local GUI state in `config/discovery_gui.json`. That
  file stays ignored.

### Batch setup

- Batch setup uses a scrollable overview with shared run feedback below it.
- Sample and target tables open in a full-height detail workspace.
  **返回批次設定** (`Alt+Left`) restores the overview without rebuilding data or
  discarding edits.
- Table filtering, role correction, import, and export retain their existing
  owners.
- DLL and internal-standard inputs show compact status summaries when valid.
  Invalid restored inputs expand for correction. **查看問題** reveals the
  relevant setup section.
- Method selection and editing share one row. Import and creation are
  available under **更多**.

### DLL directory memory

- An existing DLL directory entered in either workspace is remembered
  immediately in the machine-local `QSettings` of the main window. Saving or
  running is not required.
- Startup reuses the remembered DLL directory in both workspaces, ahead of
  batch-config defaults.
- A valid existing workspace path seeds this preference on first use.
- A missing remembered path remains visible for correction. The GUI does not
  silently select another path.

### New batch and reset

- **開始新批次** preserves the selected method, explicit overrides, internal
  standards, DLL, and mode.
- **開始新批次** clears RAW/batch-index/output inputs, sample roles,
  acquisition-source binding, and displayed results.
- **清空來源** retains the existing reset behavior, including resetting the
  method to dR.

### Run feedback and stop

- Completion, failure, and stopping feedback survive readiness refreshes.
- Editing settings after a run labels the results of that run as previous
  results. Starting another run or batch clears them.
- Stop remains disabled while cancellation is pending.
- These feedback states describe worker feedback, not scientific acceptance.
- Stop requests cancellation, then waits for the current worker stage to
  yield.

### Method selection and editing

- Method settings come from built-in presets or imported TOML methods through
  the same `xic_extractor.presets` loader that the CLI uses.
- The method section exposes create, import, edit, Apply, and Save As
  operations for CID-NL methods.
- The starting choices are dR, R, and MeR. The editor selects and combines
  their tags, or adds custom neutral losses.
- This GUI does not expose Delta Mass, HCD-PI, or mixed methods. An
  unsupported import leaves the selected method intact.
- The operator configures Discovery tags, their combination, Discovery tuning,
  and dataset inputs. Alignment and Backfill strategy remain platform-owned.
- Advanced values are explicit user overrides layered on the selected preset.
- The GUI uses an explicit RT range of 0–60 minutes for built-in starting
  methods. This override travels with the request and the exported TOML. CLI
  defaults are unchanged.
- An imported out-of-range method is rejected, not clipped.
- Save As materializes the effective method values through the preset owner.
  The result can be used directly with CLI `--preset`.
- Apply stores an effective method through the same owner in local
  `config/current_discovery_method.toml`, without a Save As dialog.
- Cancelling the editor or rejecting an import preserves the current method.
- The GUI stores only the selected locator and overrides in its local config.
  The local config is not a second method format.
- A run uses the method snapshot that the GUI displays. External edits to the
  source file do not silently change that run. Re-import selects the new
  content.
- On restart, the GUI loads the locator normally. A missing or invalid
  persisted method remains visible and blocks Run until the operator selects a
  valid method.

### Backfill gallery

- Automatic Backfill gallery / MS1 overlay generation is parked outside the
  normal GUI route. That generation cannot change the Matrix.

### XIC Inspector export

- A completed alignment with a published evidence locator starts the default
  report in the background and writes `xic_inspector.html` beside the evidence.
  **開啟 Inspector** opens it once ready; the default report does not open a
  browser by itself.
- **自訂匯出…** opens the settings dialog for another selection or path.
- The settings dialog reads source sample metadata in the background. The
  dialog displays stems and submits canonical sample IDs.
- The dialog offers:
  - Sample, Attention, and Query selection;
  - a 1–50 limit (default 30) and a seed (default 0);
  - query candidate/group IDs;
  - m/z and RT bounds (minutes);
  - a query offset;
  - an HTML output path.
- **Export and Open** opens the completed HTML in the default browser.
  **開啟 Inspector** can later reopen the latest HTML without the source files.
- The report uses the [shared Inspector API](xic-inspector.md), including its
  source binding and error/cancellation rules.
- The GUI does not re-read RAW or alter the Matrix.
- A new run or batch clears the current locator and the Open target.
- A running report keeps its original locator. The completion message
  identifies the original request/attempt of that report.

### Packaging

- Frozen builds bundle preset TOMLs and the Inspector modules that manual
  export uses.

### Sample sequence and metadata

- Selecting a RAW directory lists files without assigning injection order.
- The imported acquisition-information document supplies injection order
  through the shared sample-metadata loader and matcher.
- The GUI persists the source of that document and reloads it before explicit
  role corrections. A folder change clears the old sequence binding.
- Matching uses the RAW files enumerated from the selected directory. Existing
  sequence rows are never treated as RAW files.
- Without enumerated RAW files, importing another source replaces the
  sequence table.
- The sample table edits a view of the complete imported metadata record.
- Undisplayed fields, including exclusions and their reasons, survive visible
  edits, filtering, request construction, and CSV export.
- Filters change only visibility, not the submitted population.
- Importing a replacement source uses the records of that source. Clearing or
  changing the RAW source removes the previous records.
- A failed explicit import preserves the current data.
- A failed persisted-source restore still clears injection order, so a run
  cannot use stale acquisition order.
- Generic CSV/TSV and XLSX sequences share the finite header aliases of the
  sample-metadata owner.
- Header matching normalizes case, spaces, underscores, and punctuation before
  exact matching. `Sample ID`, `Injection Order`, `Sample Type`, and
  `Batch ID` have distinct meanings.
- Missing sample identity, duplicate semantic columns, and header-only inputs
  are import errors.
- Word inputs retain the shared document parser.
- Unsupported decorated headers are not guessed by substring.

### Internal-standard targets

- The shared targets example is loaded by default.
- Untargeted uses only rows marked `is_istd=TRUE` through
  `read_istd_targets`. Alignment CLI consumes the same selection.
- The GUI reports the selected and skipped row counts.
- Other analytes do not become expected detections or constrain Discovery.
- A supplied list without internal standards is invalid. An empty input opts
  out.
- Opening the bundled targets example for external editing first saves a user
  copy. Reload validates external edits through the shared targets reader.

## Modes

| Mode | GUI input | Runs |
| --- | --- | --- |
| `full` | RAW directory, DLL directory, output directory | Batch discovery, alignment |
| `discovery_only` | Single RAW file, DLL directory, output directory | Single-file discovery |
| `align_only` | RAW directory, `discovery_batch_index.csv`, DLL directory, output directory | Alignment |

Batch Discovery-only remains a CLI surface. To run directory-level Discovery
plus alignment from the GUI, use `full`.

## Surfaces

| Surface | Role |
| --- | --- |
| `gui/views/untargeted_view.py` | Workspace state and run lifecycle |
| `gui/ui.py` | Shared overview/detail layout; moves the same editor widget and keeps execution feedback visible |
| `gui/sections/discovery_source_section.py` | RAW, DLL, output, mode, batch-index inputs, and the shared internal-standard list |
| `gui/sections/discovery_samples_section.py` | Acquisition-document matching, displayed sequence, and role corrections |
| `gui/sections/discovery_method_section.py` | Method selection/import, displayed snapshot, and advanced overrides |
| `gui/method_editor.py` | Name, tag, and composition editing; delegates TOML saving to the preset owner |
| `gui/sections/discovery_results_section.py` | Output links, run summary, and manual Inspector Export/Open state |
| `gui/inspector_export_dialog.py` | One-report selection and output settings |
| `gui/workers/inspector_report_worker.py` | Background Inspector metadata/report calls and cooperative cancellation |
| `gui/workers/discovery_worker.py` | Threaded mode dispatch; passes the published evidence locator and reports the gallery route as parked |
| `gui/discovery_config_io.py` | Local persisted Untargeted settings |
| `xic_extractor.spec` | Frozen-release resource bundle contract |

User-visible outputs surfaced by the GUI:

- `discovery_candidates.csv`
- `discovery_review.csv`
- `discovery_batch_index.csv`
- `alignment_matrix.tsv`

The default GUI Alignment route surfaces only `alignment_matrix.tsv`. Alignment
review TSV, HTML, XLSX, and other sidecars remain explicit non-default outputs
for machine, validation, or diagnostic workflows.

## Boundaries

- Owns: workspace layout, run/stop lifecycle, local config persistence,
  preset loading, mode dispatch, and output link presentation.
- Does not own: product authority, selected Backfill values, counted
  detections, matrix publication, maturity tier, or RAW-tier production
  readiness. Those belong to [Discovery](discovery.md),
  [Alignment](alignment.md), [Presets](presets.md), and
  [Productization](productization.md).
- If a GUI change alters matrix values, schemas, selected peaks, counted
  detections, or product authority, update the owning product docs first.

## Verification

| Change area | Focused tests |
| --- | --- |
| Main-window routing | `tests/test_gui_main.py`, `tests/test_main_window_layout.py` |
| Local GUI config | `tests/test_gui_config_io.py` |
| Method editing and portable TOML | `tests/test_gui_method_editor.py`, `tests/test_presets_serialization.py` |
| Untargeted run/stop behavior | `tests/test_untargeted_view.py` |
| Worker mode dispatch | `tests/test_discovery_worker.py` |
| Shared run controls | `tests/test_pipeline_worker.py` |
| Parked GUI gallery route | `tests/test_discovery_worker.py` |
| Manual Inspector Export/Open | `tests/test_gui_inspector_export.py` and source/frozen GUI browser receipts |
| Alignment output surfaces | `tests/test_alignment_output_levels.py`, `tests/test_alignment_pipeline_outputs.py` |
| Frozen preset bundling | `tests/test_pyinstaller_spec.py` |

Full PR gate:

```powershell
$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests
$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py
$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards --check
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards docs-config -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards gui -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards targeted-core -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards alignment-core -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards product-gates-activation -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards product-gates-evidence -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards diagnostics-tools -- -v --tb=short -x
```

## Pitfalls

- Adding a preset but forgetting the PyInstaller data bundle.
- Committing machine-specific GUI state.
- Starting Inspector or browser automatically on science completion.
- Re-enabling controls immediately after Stop while the worker is still
  alive.
- Letting the GUI recompute domain evidence or override Discovery/Alignment
  ownership rules.
