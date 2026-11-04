# Architecture And Public Contracts

Read this file when work changes an owner boundary, persisted decision,
runtime lifecycle, writer authority, schema, or public surface. It records the
current contract only. Migration history belongs in Issues, PRs, and Git.

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
  materialization and atomic writing. The Untargeted
  execution adapter never writes. Formal Alignment completion binds the candidate
  views, Matrix m/z lookup, config and completed ISTD provenance before handoff;
  publication does not reconstruct them from independent caller inputs. Low-level
  runtime results remain available to explicit-input diagnostic consumers.
  The completed result owns private-store retention and release, shared across
  its immutable views; prepared Discovery resources transfer to that result.
  Application publication owns cancellation and explanation lifetime through
  writing and closes the result on every exit. Acquisition worker/cache lifetime
  remains separate. GUI and CLI receive durable published outputs only.
  `tests/test_docs_architecture_census.py` pins these execution boundaries.
- Shared models or protocols are justified when they prevent an actual cycle
  or schema drift.
- Formal Untargeted partitions consume the pure CID-NL input contract in
  `alignment/cid_nl_inputs.py` and require a resolved signal-processing profile
  with Discovery physical facts.
- Separate real-data validation from normal unit tests.

## Boundary Projection Closure

For a non-trivial source-to-target conversion, identify the source owner,
target consumer, identity/cardinality, preserved facts, deliberate loss, and
the oracle that protects the consumer. Durable binding needs a current
persisted/replay consumer or an actual admission/authority boundary; internal
handoffs reuse established owner facts. Apply
[Change Locality](../architecture-contract.md#change-locality) before adding
identity, proof, or construction machinery.

- Preserve composite identity through maps and joins using the owner's domain keys.
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
`xic_extractor.alignment.backfill.evidence_contract`; the strict TSV reader
alone mints `EvidenceQueryResult`. The public label is compatibility
metadata, not canonical identity proof. Exact headers, identity tuples and
validation rules are owned by that reader, the writer and their tests.

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

## Runtime Execution

`xic_extractor.execution.partitions` schedules partitioned work and decides the
batch outcome: a batch completes every partition and its global closure, or it
fails or cancels as a whole. Runtime completion grants no scientific, artifact,
writer, or publication authority.

- A real execution failure outranks cancellation observed by sibling work.
- Missing timing observations remain unavailable with a reason. They never
  become inferred zero.
- Timing counts describe cost and locality only; they do not select a cell,
  value, state, or writer decision.

## Executable Census And Dependency Gate

The architecture census is disposable evidence, not an authority registry. It
derives current module paths, static imports, declared console scripts, and
package patterns from source.

- `[tool.importlinter]` in `pyproject.toml` owns the coarse static dependency floor.
- `scripts/check_architecture_census.py` owns the census integrity, naming,
  and internal-defense growth gates.
- Dynamic loaders, installed entry points, frozen applications, workflow
  checks, diagnostics indexes, and test selection remain with their respective
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
