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
- `dna_dr_product_ready` is the CLI alignment preset for replaying the
  registered productization tail, including the current clean-target 84-cell
  Backfill activation when sample universe, expected-diff packet, and authority
  scope match. It does not create GUI behavior, broaden Backfill or CID-NL
  authority, or make candidate-only evidence publishable.
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
| `dna_dr_product_ready.toml` | Product-readiness DNA dR preset with stronger checker expectations |

Rules:

- Prefer preset surfaces over hand-written long command recipes for repeatable
  validation.
- Built-in product-facing presets should emit the expected seed audit and
  `alignment_backfill_cell_evidence.tsv` where required by the runner contract.
- Backfill expansion replay is enabled only when sample universe, expected-diff,
  and authority constraints match the documented gate.
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
  TOML resource format, reproducibility guarantees for preset-backed runs.
- **Does not own**: ProductWriter authority or accepted publication scope (see
  [productization.md](productization.md)), exact command recipes for every
  validation run (see runner docs), release readiness (see control plane and
  status index).

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
- [Productization control plane](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Status index](../superpowers/validation/productization_status_index_v1.tsv)
- [Authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json)
- [Diagnostic ledger](../diagnostic-ledger.md)
