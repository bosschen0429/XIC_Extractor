# Architecture And Public Contracts

Read this file when work changes an owner boundary, persisted decision,
runtime receipt, writer authority, schema, or public surface. It records the
current contract only. Migration history belongs in Issues, PRs, and Git.

## Read Routes

| Change | Read next |
| --- | --- |
| owner or dependency boundary | [`../architecture-contract.md`](../architecture-contract.md) and the affected package |
| persisted schema or evidence claim | the writer, strict reader, schema tests, and [`product-validation-contract.md`](product-validation-contract.md) |
| runtime lifecycle or receipt | `xic_extractor.execution.attempt` and `scripts/check_architecture_census.py` |
| public command or diagnostic | [`script-tool-lifecycle.md`](script-tool-lifecycle.md) and `[project.scripts]` |
| repository placement | [`../project-layout.md`](../project-layout.md) |

Use source, tests, and executable checks for exact field lists, allowlists,
schema versions, and command flags. Do not copy those inventories here.

## Architecture Guardrails

- Domain logic must not import GUI, workbook builders, CLI scripts, process
  backends, report renderers, or RAW/CSV adapters.
- Keep a compatibility facade only for a current public contract or real
  external consumer. Tests and historical artifacts are not consumer evidence.
- CLIs orchestrate. Reusable loading, classification, models, summaries,
  plotting, and writers belong in package modules.
- Writers render owner results; they do not recompute domain evidence or
  rescan RAW files.
- The Untargeted application invokes retained CID-NL execution; Alignment has
  no extraction imports. Alignment owns the pure finalized Matrix projection;
  `output/alignment/` owns artifact selection, formatting, streaming or
  materialization, atomic writing and fingerprint receipts. The Untargeted
  execution adapter never writes. Formal Alignment completion binds the candidate
  views, Matrix m/z lookup, config and completed ISTD provenance before handoff;
  publication does not reconstruct them from independent caller inputs. Low-level
  runtime results remain available to explicit-input diagnostic consumers.
  The completed result owns private-store retention and release, shared across
  its immutable views; prepared Discovery resources transfer to that result.
  Application publication owns cancellation and explanation lifetime through
  writing and closes the result on every exit. Acquisition worker/cache lifetime
  remains separate. GUI and CLI receive durable published outputs only.
  `tests/test_docs_architecture_census.py` pins these execution boundaries;
  `xic_extractor.alignment.owner_build_route_receipt` and
  `tests/test_untargeted_application.py` own the route receipt labels.
- Shared models or protocols are justified when they prevent an actual cycle
  or schema drift.
- Formal Untargeted partitions consume the pure CID-NL input contract in
  `alignment/cid_nl_inputs.py`. Golden Path artifacts, checked acceptance and
  versioned table projection belong to `diagnostics/quant_matrix/`.
  `alignment/quantitative_projection_authority.py` encapsulates the packaged
  promotion contract; its resource registration and HOLD policy remain its
  authority. It is not a numerical algorithm or an active application guard.
- Separate real-data validation from normal unit tests.

## Boundary Projection Closure

For a non-trivial source-to-target conversion, identify the source owner,
target consumer, identity/cardinality, preserved facts, deliberate loss, and
the oracle that protects the consumer. Add durable binding only when the
projection persists or certifies facts.

- Preserve composite identity through maps, joins, digests, and receipts.
- Validate cardinality at the semantic owner.
- Preserve terminal failure and provenance when a current consumer relies on
  them.
- Give persisted or canonical schemas a new semantic name when their meaning
  changes; content changes retain the name.
- Keep adapters translational: they may reject invalid projections but must
  not silently recompute, collapse, or invent owner facts.
- A lossy projection is valid only when its current consumer and reduced claim
  are explicit. Missing owner evidence means the claim is unavailable, not
  inferred zero or success.

Persisted bytes prove their own shape and binding, not every statement stored
inside them. Rehydrate only decision-bearing content: fields that select
identity or value, grant writer authority, change product output, or determine
a current domain/runtime result. Audit-only labels, counts, locators, and copied
hashes remain telemetry unless a current consumer uses them to decide.

Alignment Backfill records are owned by
`xic_extractor.alignment.backfill.evidence_contract`; strict TSV and Parquet
readers alone mint `EvidenceQueryResult`. The public label is compatibility
metadata, not canonical identity proof. Exact headers, identity tuples,
validation rules, and optional bundle format are owned by those readers,
writers, and their tests.

## Writer Transaction Authority

A finalizer must not reconstruct writer authority from caller-authored rows,
manifests, diffs, receipts, counts, or a surrounding hash chain.

- Rehydrate persisted review evidence once into an immutable typed review set
  with an explicit claim.
- The writer finalizer accepts the current typed scientific owner result, that
  owner-validated review set, and output-location mechanics.
- Identity, selected value, eligibility, manifest rows, counts, rollback, and
  terminal status are derived inside the transaction.
- Audit receipts may contribute lineage but cannot select a cell, value,
  state, or writer decision.
- Publish final output only after the writer, expected-diff, rollback, and
  terminal-receipt oracles succeed.

Targeted and Untargeted retain their existing writer owners and publication
mechanics. A new transaction shape requires a current consumer, authority
boundary, compatibility impact, and rollback oracle.

## Runtime Construction Authority

`xic_extractor.execution.attempt` is the sole runtime aggregate and
finalization authority. Consumers submit typed settlements, registrations,
owner-bound cancellation, acquisition receipts, and telemetry; the owner
derives lifecycle, counts, ledgers, dispositions, and final outcome.

- Consumers call `execute_runtime_attempt`. Partial settlement or finalization
  entry points are not compatibility APIs.
- Callers must not construct ledgers, aggregates, coordinator/supervisor
  records, finalization receipts, or canonical mechanical counts.
- Invalid manifests finalize through the owner as failed without dispatch or
  fabricated work.
- Missing observations remain unavailable. They never become inferred zero.
- A successful empty observation result is valid only when every owner-derived
  acquisition count is zero.
- Typed receipt replay is idempotent only when complete owner facts match;
  conflicting replay fails closed.
- RAW-open and peak-cost receipts are narrow diagnostic projections. They do
  not grant lifecycle, cleanup, scientific, artifact, writer, or finalization
  authority.
- Canonical metrics require the owner-minted ledger bound to the accepted
  finalization receipt; copied labels or mappings carry no authority.

Exact allowed calls, exports, construction sites, receipt bindings, and active
schema versions are enforced by `scripts/check_architecture_census.py`, the
owner modules, serializers, and focused tests.

## Executable Census And Dependency Gate

The architecture census is disposable evidence, not an authority registry. It
derives current module paths, static imports, declared console scripts, package
patterns, and runtime-construction violations from source.

- `.importlinter` owns the coarse static dependency floor.
- `scripts/check_architecture_census.py` owns the targeted construction and
  export gate.
- Dynamic loaders, installed entry points, frozen applications, workflow
  checks, diagnostics indexes, and pytest shards remain with their respective
  executable owners.
- A dependency ignore is temporary migration debt: it needs a named owner and
  rationale, and unmatched-ignore alerting must remove it when the edge closes.
- Optional census output belongs outside the repository or under an ignored
  path. It is review/debug evidence only.

## Public Contracts

A command is public when declared in `[project.scripts]` or named by an explicit
product/downstream contract. Being under `scripts/`, runnable, imported, tested,
or packaged is not sufficient.

Treat these as public unless an approved change says otherwise:

- declared console scripts and contract-bearing commands
- `xic_extractor.extractor.run`
- `xic_extractor.signal_processing.find_peak_and_area`
- `scripts.csv_to_excel.run`
- config keys and example/default settings
- TSV/CSV schemas
- workbook sheets, order, hidden states, and columns
- HTML report path naming and run metadata keys
- persisted identifiers and matrix row/cell identity
- domain-selected peaks, areas, values, and counted detections
- cross-repository handoff schemas and contracts
