# Presets

Presets are named Discovery methods with effective tuning and the retained
alignment runtime option. Presets are reproducibility and public behavior
surfaces. Presets are not product authority by themselves.

## Contract

- Changing a default preset or silent resolver behavior changes public
  behavior. Such a change needs
  [expected-diff framing](../agent/product-validation-contract.md#promotion-packet-contract),
  focused tests, and relevant evidence.
- Targeted and profile-bound Untargeted processing use the shared physical
  signal owner.
- Direct resolver-based diagnostic callers retain their explicit `local_minimum` or
  `savgol` configuration (including saved `legacy_savgol` input). Those settings
  do not choose the formal Targeted physical algorithm.
- Application profiles can expose explicit preset choices. Local profiles
  must not silently overwrite user intent.
- A preset run must be reproducible from documented CLI/config surfaces and
  metadata. A branch-local command diary is not a reproducibility source.
- `serialize_preset` and `save_preset` own portable method export.
- Export resolves the effective Discovery values and the preset-owned runtime
  option. Export then writes the existing TOML shape.
- Export validates the written TOML through the same parser and apply
  functions that intake uses.
- An invalid method leaves an existing destination intact.
- Export preserves tag order and combination.
- `source` is derived on load. `source` is never a TOML field or a scientific
  identity.
- The TOML format carries method intent. The format does not carry RAW/output
  paths, worker counts, or application state.
- `dna_dr_product_ready` is a historical compatibility name for the
  `raw_superwindow` Owner Build profile. A successful run does not assert
  product readiness and does not invoke Standard Peak or a publication checker.
- Discovery method or preset labels can wrap strategy families for usability.
  For example, `pt_pattern` means `delta_mass(pattern_id=pt)`. The preset layer
  must not turn that label into a separate primitive strategy or ProductWriter
  authority.

## Surfaces

| Surface | Role |
| --- | --- |
| `xic_extractor/presets/` | Preset loading and code surface |
| `xic_extractor/presets/serialization.py` | Validated effective-method TOML export and atomic saving |
| `xic_extractor/presets/data/` | Built-in TOML preset resources |
| `scripts/run_discovery.py --preset ...` | Discovery CLI using the same recipe binder; only explicit tuning flags override the method |
| `scripts/run_alignment.py --preset ...` | CLI path that activates alignment presets |
| `scripts/run_tissue_validation.py --dataset {8,85}` | Shared tissue validation launch defaults; delegates Discovery and Alignment to their current CLIs |
| Signal profile settings in config/HTML workbench | Shared physical signal configuration |
| Runner metadata and sidecars | Provenance for preset-backed runs |

## CID-NL Workflow Composition

### Tissue Validation Defaults

The 8RAW validation set is a subset of the 85RAW tissue cohort. Both use the
same `dna_dr` method and effective scientific settings, including disabled
blank-background qualification. Dataset selection changes the sample scope.
Dataset selection does not change the method.

The maintained tissue validation runner owns paths, timing, and stage launch
order. The runner consumes the shared
[execution profile](execution-runtime.md#worker-settings) for worker budgets.
The runner does not maintain a second scientific preset.

Neither scale resolves or loads a blank library by default. An explicit
`--blank-library` opts in and binds the same library in Discovery and
Alignment. The suitability of that library must be established separately.
ISTD input follows the normal Alignment targets CSV. A historical reference
workbook is an explicit override. Resolved inputs and commands are visible
before RAW work.
See [local validation commands](../agent/parameter-settings.md#validation-scale).

### Recipe Binding

A CID-NL preset is bound once, before expensive work, to one immutable
in-memory recipe. The recipe contains the existing typed Discovery and
Alignment settings plus the preset-owned owner-build choice. The recipe has no
Standard Peak or Backfill-expansion post-stage choice. The recipe does not
contain RAW/output paths, sample metadata, worker counts, review inputs, a
persisted recipe identity, or a second invocation schema.

The current routes remain the existing entry-point modes:

| Existing route | Preset-backed behavior |
| --- | --- |
| `discovery_only` | RAW to the existing Discovery terminal |
| `full` | RAW -> Discovery -> retained finalized CID-NL batch -> canonical Matrix writer |
| `align_only` | Existing Discovery index -> retained finalized CID-NL batch -> canonical Matrix writer |

The retained finalized CID-NL batch closes the Matrix loop for neutral-loss
presets, including multi-tag `union` (OR) and `intersection` (AND).

- OR accepts any configured tag on the same physical peak.
- AND requires all configured tags on that peak within one sample.
- Alignment and Backfill use the same method requirement.
- Different tag hits across samples can share a row when the existing physical
  peak compatibility gates pass. Labels alone do not establish identity.
- The route receipt of the batch preserves any configured legacy owner-build
  backend for compatibility. The route receipt reports no effective legacy
  backend.

Other routes and retained artifacts:

- Normal Alignment rejects methods outside this CID-NL route.
- Standard Peak is not reachable from normal CLI/HTML/preset execution.
- The source-Matrix republisher and its authority/productization chain are
  deleted.
- Retained Standard Peak helpers are explicit offline falsifiers/renderers
  only. They do not select CID-NL `HOLD` results or grant product authority.
- The historical clean-target packet retains diagnostic context and
  current-file self-consistency checks only. The packet has no independent
  historical seal. The ownerless writer and registered scope of the packet are
  retired.
- The provisional full-universe runner continues to stop after its existing
  provisional diagnostic projection.

These route names are existing in-memory selectors. The route names are not
new preset TOML fields or a user-authored workflow graph. The delta-mass
Discovery path owns the Pt and Pt + CID-NL composite presets. CID-NL-only
binders reject those composite presets.

### Resolver Presets

| Preset | Role |
| --- | --- |
| `savgol` | Savitzky-Golay smoothing and prominence-based candidate resolver |
| `local_minimum` | Canonical local-minimum candidate resolver |

New resolver settings use `savgol`. Saved `legacy_savgol` input is accepted and
normalized to `savgol` before candidate search and preset export. This read alias
serves existing user settings/presets; remove it only after those consumers have
migrated and an announced compatibility window has ended. Formal Targeted still
uses physical candidates rather than selecting a resolver from settings.
The emitted `legacy_savgol` proposal-source label stays stable because it is
part of retained peak identities; it describes the savgol resolver source.

### Alignment Presets

Code: `xic_extractor/presets/`; built-in TOML: `xic_extractor/presets/data/`.

| Preset | Role |
| --- | --- |
| `dna_dr.toml` | DNA dR alignment preset |
| `dna_dr_product_ready.toml` | Historical compatibility profile selecting `raw_superwindow` Owner Build; not readiness authority |

Rules:

- For repeatable validation, prefer preset surfaces over hand-written long
  command recipes.
- A built-in product-facing preset must emit the expected seed audit and
  `alignment_backfill_cell_evidence.tsv` where the runner contract requires
  them.
- Normal recipes reject the former Standard Peak and Backfill expansion preset
  keys. No replacement publication choice is exposed.
- Non-standard peaks and candidate-only evidence stay outside automatic
  publication policy unless the authority manifest says otherwise.

### Performance Anchors

- The 2026-06-22 `dna_dr_product_ready` 8RAW performance pass is retained as an
  exact-output-preserving preset/runtime anchor. Total wall time improved from
  about 252.5s to 170.5s. Public TSV hashes and product-ready preset checks
  stayed matched.
- The durable lesson is call-shape discipline. The lesson is not a new product
  contract. Call-shape discipline means:
  - reuse generated overlays where provenance matches;
  - batch or slice existing summaries instead of regenerating them per chunk;
  - keep RAW/render worker caps conservative;
  - keep payload validation cheap for scalar leaves.
- Performance archives and timing packets do not change preset defaults,
  matrix authority, Backfill scope, or application behavior. Performance archives and
  timing packets are evidence for safe runtime implementation choices under
  the same preset contract.

## Boundaries

- **Owns**: preset loading, resolver/alignment preset behavior definitions,
  immutable CID-NL recipe binding, TOML resource format, and reproducibility
  guarantees for preset-backed runs.
- **Does not own**: accepted publication scope (see
  [productization.md](productization.md)), exact command recipes for every
  validation run (see runner docs), release readiness (see status index and
  product validation contract).

## Verification

Before changing preset behavior, require the relevant subset of:

- Focused tests for preset loading and default selection.
- Runner smoke check for documented CLI/config surfaces.
- Product-ready preset checker when a preset claims product-readiness support.
- Productization owner update if preset behavior changes matrix authority,
  selected values, schema, or publication scope.

## Pitfalls

- Replacing documented preset surfaces with branch-local command recipes.
- Assuming Backfill expansion replay is included just because a preset is
  product-facing.

## See Also

- [Parameter settings](../agent/parameter-settings.md)
- [Preset code](../../xic_extractor/presets/)
- [Preset TOML data](../../xic_extractor/presets/data/)
- [Historical compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Status index](../superpowers/validation/productization_status_index.tsv)
- [Authority manifest](../superpowers/productization/status/productization_authority_manifest.json)
- [Diagnostic ledger](../diagnostic-ledger.md)
