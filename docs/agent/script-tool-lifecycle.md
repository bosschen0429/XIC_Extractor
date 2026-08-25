# Script And Diagnostic Tool Lifecycle

Doc placement: formal_repo_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/agent/script-tool-lifecycle.md
Doc exit rule: Replace only when another durable engineering owner absorbs script and diagnostic lifecycle policy.

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

## Classify Exposure And Lifecycle Separately

Invocation exposure:

- **installed entry point**: declared in `[project.scripts]`;
- **contract-bearing command**: named by a product or downstream contract;
- **maintained operator interface**: directly runnable for development,
  validation, or review without being an end-user command;
- **internal helper**: imported by another entry point and not independently
  promised to users.

Lifecycle role:

- **product adapter**: converts product inputs or emits a contracted product
  projection;
- **evidence producer**: creates evidence or a reproducible source artifact;
- **diagnostic oracle**: checks parity, performance, review, or a bounded
  scientific claim without product-writing authority;
- **workflow gate**: enforces repository, CI, productization, or review state;
- **compatibility wrapper**: preserves an existing import or invocation while
  behavior moves inward;
- **protected unknown**: current evidence is insufficient to prove retirement;
- **retirement candidate**: current consumers and unique capability have a
  named replacement or are proven absent.

Having `main()`, living under `scripts/`, being importable, or having a test
does not by itself make a command public. Conversely, a product artifact may be
public even when its producer is not installed as a console command.

## Diagnostic Boundaries

`tools/diagnostics/` is maintained product-adjacent code. Entry points
orchestrate; reusable loading, classification, typed models, summaries,
plotting, and writers belong in package modules when they have current reuse.

Diagnostic writers render supplied typed evidence. They must not reconstruct a
semantic owner, rescan RAW, authorize Matrix values, or become an alternate
activation or publication control plane. A diagnostic artifact proves only the
claim its current checker or oracle validates.

RAW-reading diagnostics require an explicit acquisition owner, call-cost model,
cache/locality boundary, provenance contract, and method-appropriate oracle.
Similar CLI flags are not evidence that two RAW paths are interchangeable.

## Retention And Retirement

A missing import or `rg` hit is not sufficient retirement evidence because an
operator command may be invoked manually or outside the repository. A bounded
retirement review checks:

1. installed commands, package exports, imports, and tests;
2. GUI, CLI, presets, workflows, CI, release, and documentation referrers;
3. current artifact regeneration and checker consumers;
4. known manual or off-repository operator use;
5. unique scientific, compatibility, validation, or recovery capability.

Retirement is allowed only when every current consumer is absent or retargeted,
unique behavior has a named owner or explicit approved removal, affected public
artifacts have parity or an approved expected diff, and the diagnostic index
plus relevant semantic owner are updated in the same change.

When the search is bounded but off-repository use remains unknown, classify the
surface as `protected_unknown` and open a GitHub Issue if construction is
needed. Do not preserve the investigation as a dated repo document.

## Consolidation

Repeated low-level mechanics are not automatically one contract. Reuse an
existing package owner when at least two current callers share the same byte,
error, schema, and lifecycle semantics. Keep a thin artifact adapter when it
binds field order, newline, directory creation, formatting, provenance, or
other stable serialization policy.

Do not add a helper merely to reduce line count. A consolidation must preserve
consumer behavior, dependency direction, and semantic ownership, with focused
tests for any newly shared boundary.
