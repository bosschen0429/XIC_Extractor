# Script And Diagnostic Tool Lifecycle

This document owns the durable classification and retirement rules for
`scripts/` and `tools/diagnostics/`. It does not preserve dated censuses,
construction history, command diaries, or filename-by-filename snapshots.

## Authority

- `pyproject.toml` owns installed command names and entry points.
- [`tools/diagnostics/INDEX.md`](../../tools/diagnostics/INDEX.md) owns the
  current entry-by-entry diagnostic index.
- Product, architecture, validation, and agent owners define the semantics and
  public contracts consumed by a script or diagnostic.
- Imports, tests, CI, GUI wiring, release configuration, and artifact
  regeneration paths provide current referrer evidence; they do not create a
  semantic owner by themselves.

Executable counts and membership lists are derived evidence. Generate them for
a bounded review when needed and keep them under ignored `output/`; do not
commit a dated snapshot as a second registry.

## What Is Public

[Public Contracts](../architecture-contract.md#public-contracts) defines a
public command. Use [Module Naming](../architecture-contract.md#module-naming)
for module, command, help and artifact names.

## Diagnostic Boundaries

[Diagnostics Contract](../architecture-contract.md#diagnostics-contract) owns
where diagnostic code lives and what a diagnostic writer may do.

RAW-reading diagnostics require an explicit acquisition owner, call-cost model,
cache/locality boundary and method-appropriate oracle. Similar CLI flags are not
evidence that two RAW paths are interchangeable.

XIC Inspector is the human-facing chromatogram viewer. A new review need adds
Inspector rendering over existing evidence, or a producer that writes curves,
peak bounds, and quality flags for Inspector to read offline; it does not add a
Matplotlib or other static-image review script under `tools/diagnostics/`.
Inspector does not read RAW; RAW acquisition stays with the producing
diagnostic.

## Retention And Retirement

A missing import or `rg` hit is not sufficient retirement evidence because an
operator command may be invoked manually or outside the repository. A bounded
retirement review checks:

1. installed commands, package exports, imports, and tests;
2. GUI, CLI, presets, workflows, CI, release, and documentation referrers;
3. current artifact regeneration and checker consumers;
4. known manual or off-repository operator use;
5. unique scientific, compatibility, validation, or recovery capability.

Retire a script when every current consumer is absent or retargeted and any
affected public artifact has parity or an approved expected diff. Update the
diagnostic index in the same change. When off-repository use cannot be ruled
out, ask the owner instead of keeping the script indefinitely.
