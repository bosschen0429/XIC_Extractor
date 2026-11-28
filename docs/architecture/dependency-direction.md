# Dependency Direction

Startup-sensitive entry points and lightweight readers keep heavy,
branch-specific dependencies inside the path that needs them. Importing a shared
model or reading completed evidence must not load scientific computation or
RAW/DLL code through an unused capability. Delaying an import does not permit a
forbidden dependency.

`[tool.importlinter]` in `pyproject.toml` is the source for every enforced edge;
this section states the shape and does not restate the contracts.

- Domain algorithms depend only on arrays, typed configuration/context and small
  domain models; never on UI, workbook builders, CLI scripts, process backends,
  report renderers, or RAW/CSV adapters. Adapters, I/O, rendering and entry
  points depend inward.
- Main chain: applications → untargeted_execution／extraction → alignment →
  discovery → peak_detection. `acquisition/` stays inner.
- `applications/` is the outermost composition layer and owns a run from
  configuration to publication; `extractor.run` is the public root entry into
  it. Entry points pass typed intent, not flattened execution arguments.
- `execution/` and `untargeted_execution/` schedule, store and clean up. Runtime
  wraps scientific completion without changing its result.
- `xic_inspector` reads completed evidence only; scientific packages never
  import it.
- Keep a compatibility facade only for a real external consumer.
- Shared models or protocols are justified when they prevent an import cycle or
  schema drift.

## Repository Gates

- `[tool.importlinter]` in `pyproject.toml` owns the static dependency floor.
- `scripts/check_architecture_census.py` owns what import contracts cannot
  express: versioned identifiers, documented names that no source defines, ppm
  arithmetic outside its owner, tests that patch or read production source,
  frozen entry points calling `freeze_support` first, internal-defense growth,
  and redundant type guards. In `xic_extractor`, a raising branch must not
  reject an annotated parameter or dataclass field with `not isinstance` or
  `type(...) is not ...` when the guard already covers the declared types.
  Unparsed input is typed as `object` or `Any`, or admitted at its parser.
- A dependency ignore is temporary migration debt: it needs a named owner and
  rationale, and unmatched-ignore alerting must remove it when the edge closes.

### Construction Validation

`scripts/check_architecture_census.py` rejects a `raise` in `__post_init__`
or in a known same-module function or method it calls directly. Parse input
at its ingress and check computed results at the decision owner; a separate
helper called from the hook does not change ownership.

With `--internal-defense-base REF`, the gate also blocks a changed
`__init_subclass__` body with more statements than its counterpart in REF. A
hook keeps its counterpart when it keeps its qualified name, moves with that
name to another changed module, or is renamed with an identical body; a copy
counts as new. The subclass growth check does not trace helpers or inspect
plain functions or `__init__` as hooks. There is no suppression flag.
A blocked change removes the unnecessary
machinery or gets an explicit maintainer decision; the agent does not grant its
own exception.
