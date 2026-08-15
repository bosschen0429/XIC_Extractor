# Presets

Presets are explicit bundles of resolver settings, alignment settings, and
post-alignment stages. They are reproducibility and public behavior surfaces;
they are not product authority by themselves.

## Contract

- Changing a default preset or silent resolver behavior changes public
  behavior. It needs expected-diff framing, focused tests, and relevant
  evidence.
- The current tracked resolver token is `region_first_safe_merge`; alignment
  production maps it to `local_minimum`. `local_minimum` remains an explicit
  direct mode or validation slice, not a silent default unless config, CLI, or
  GUI selection says so.
- GUI local profiles can expose explicit preset buttons but must not silently
  overwrite user intent.
- Preset runs should be reproducible from documented CLI/config surfaces and
  metadata, not from branch-local command diaries.
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
| `xic_extractor/presets/data/` | Built-in TOML preset resources |
| `scripts/run_alignment.py --preset ...` | CLI path that activates alignment presets |
| Resolver settings in config/GUI | Public behavior surface for peak detection choices |
| Runner metadata and sidecars | Provenance for preset-backed runs |

## CID-NL V1 Workflow Composition

A CID-NL preset is bound once, before expensive work, to one immutable
in-memory recipe. The recipe contains the existing typed Discovery and
Alignment settings plus the preset-owned owner-build choice. It has no
Standard Peak or Backfill-expansion post-stage choice and does not contain
RAW/output paths, sample metadata, worker counts,
review inputs, a persisted recipe identity, or a second invocation schema.

The current V1 routes remain the existing entry-point modes:

| Existing route | Preset-backed behavior |
| --- | --- |
| `discovery_only` | RAW to the existing Discovery terminal |
| `full` | RAW -> Discovery -> generic Alignment -> Owner Backfill -> canonical Matrix |
| `align_only` | Existing Discovery index -> generic Alignment -> Owner Backfill -> canonical Matrix |
| bounded ModeTrack runner | Existing single-subject M8/M9 compatibility route |

Generic Alignment and Owner Backfill close the Matrix loop. Standard Peak is
not reachable from normal CLI/GUI/preset execution, and the source-Matrix
republisher and its authority/productization chain are deleted. Retained
Standard Peak helpers are explicit offline falsifiers/renderers only; T5D does
not invent a new HOLD selector or product authority. The separate, registered
bounded T5B scope remains active under its existing manifest and primary writer;
its exact cardinality belongs to the validation artifact, not this owner doc.
The provisional full-universe runner continues to stop after its
existing provisional diagnostic projection, and bounded ModeTrack retains its
precursor, metadata, injection-order, namespace, and reviewed-input owners.

These route names are existing in-memory selectors, not new preset TOML fields
or a user-authored workflow graph. Pt and Pt + CID-NL composite presets remain
owned by the delta-mass Discovery path and are rejected by CID-NL-only binders.

### Resolver Presets

| Preset | Role |
| --- | --- |
| `legacy_savgol` | Historical resolver behavior and compatibility baseline |
| `local_minimum` | Local-minimum resolver path used by newer validation slices |

### Alignment Presets

Code: `xic_extractor/presets/`; built-in TOML: `xic_extractor/presets/data/`.

| Preset | Role |
| --- | --- |
| `dna_dr.toml` | DNA dR alignment preset |
| `dna_dr_product_ready.toml` | Historical compatibility profile selecting `raw_superwindow` Owner Build; not readiness authority |

Rules:

- Prefer preset surfaces over hand-written long command recipes for repeatable
  validation.
- Built-in product-facing presets should emit the expected seed audit and
  `alignment_backfill_cell_evidence.tsv` where required by the runner contract.
- Former Standard Peak and Backfill expansion preset keys are rejected from
  normal recipes; no replacement publication choice is exposed.
- Non-standard peaks and candidate-only evidence stay outside automatic
  publication policy unless the authority manifest says otherwise.

### Performance Anchors

- The 2026-06-22 `dna_dr_product_ready` 8RAW performance pass is retained as an
  exact-output-preserving preset/runtime anchor: total wall time improved from
  about 252.5s to 170.5s while public TSV hashes and product-ready preset
  checks stayed matched.
- The durable lesson is call-shape discipline, not a new product contract:
  reuse generated overlays where provenance matches, batch or slice existing
  summaries instead of regenerating them per chunk, keep RAW/render worker caps
  conservative, and keep payload validation cheap for scalar leaves.
- Performance archives and timing packets do not change preset defaults,
  matrix authority, Backfill scope, or GUI behavior. They are evidence for safe
  runtime implementation choices under the same preset contract.

## Boundaries

- **Owns**: preset loading, resolver/alignment preset behavior definitions,
  immutable CID-NL recipe binding, TOML resource format, and reproducibility
  guarantees for preset-backed runs.
- **Does not own**: ProductWriter authority or accepted publication scope (see
  [productization.md](productization.md)), exact command recipes for every
  validation run (see runner docs), release readiness (see status index and
  product validation contract).

## Verification

Before changing preset behavior, require the relevant subset of:

- Focused tests for preset loading and default selection.
- Expected-diff framing for default or silent behavior changes.
- Runner smoke check for documented CLI/config surfaces.
- Product-ready preset checker when a preset claims product-readiness support.
- Productization owner update if preset behavior changes matrix authority,
  selected values, schema, or publication scope.

## Pitfalls

- Treating a preset as ProductWriter authority.
- Replacing documented preset surfaces with branch-local command recipes.
- Silently changing resolver defaults without expected-diff evidence.
- Assuming Backfill expansion replay is included just because a preset is
  product-facing.

## See Also

- [Parameter settings](../agent/parameter-settings.md)
- [Preset code](../../xic_extractor/presets/)
- [Preset TOML data](../../xic_extractor/presets/data/)
- [Historical compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Status index](../superpowers/validation/productization_status_index_v1.tsv)
- [Authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json)
- [Diagnostic ledger](../diagnostic-ledger.md)
