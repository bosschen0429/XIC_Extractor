# Issue 191 Gate C persisted-boundary field contract v1

Status: `diagnostic_only`. This contract bounds the ModeTrack Gate C C1/C2
persisted boundary; it does not grant ProductWriter, selected-value,
counted-detection, Matrix, preset, or default authority.

## Outcome

The unchanged enumeration reports 587 semantic claims across 15 artifact
roles:

- `fixed=256`
- `verified_unaffected=331`
- `unknown=0`

The current closed writer has no remaining reachable caller/persisted-value
authority gap in this slice. C2 rebuilds the scientific projection with the
fresh RAW owner, rehydrates only the exact reviewed subjects, derives the
checked manifest and QMV inside the finalizer, validates expected diff and
rollback, and publishes the QMV, final receipt, and transaction receipt in one
final rename. No production owner or public schema change was required.

The field contract is retained as
[`field_contract.tsv`](field_contract.tsv). Each row records artifact/schema,
canonical field path, ingress and consumer, semantic claim, one of
`primitive` / `locally_rederivable` / `raw_owner_only` / `audit_only`, owner,
verification source, completeness relation, semantic failure, exact
fail-closed point, contradiction oracle, and resolution. `rt`, `intensity`,
and `mz` each have separate numeric-value and observation-content-identity
claims. Dynamic Matrix sample columns collapse to `{sample_column}` while the
completeness relation remains the exact current header/row closure.

Every role is also materialized by the current no-RAW producers and compared
as a complete canonical `path -> JSON type set` mapping. Typed observation
subtrees delegate type authority directly to the existing
`PeakObservationContent` and `IntegrationResult` dataclass annotations. Thus
optional numeric fields normalize to `null|number`, while an incompatible
same-path `string` fails closed. Empty arrays contribute container evidence but
do not invent a second element type. Retained 85RAW values remain comparison
evidence; they are not schema authority.

Matrix field roles are likewise owner-derived. The proposal's
`matrix_header`, interpreted by the existing QuantMatrixVersion sample-column
owner, is carried into terminal QMV enumeration. Only owner-declared sample
columns collapse to `{sample_column}`; fixed coordinates or metadata retain
their names. Matrix `Mz` and `RT` are recorded as
`matrix_coordinate_numeric_value`, never as peak-observation identity.

Specialized semantic claims are owner-declared. Typed observation subtrees,
Matrix roles, and the exact source `physical_ids` contract have named owners.
Untyped scalar envelope fields emit the neutral `field_value` claim regardless
of their spelling or JSON scalar type; their artifact policy still records the
owner, verification source, failure, oracle, and resolution. The checker does
not infer numeric, identity, locator, digest, count, or status semantics from a
field name.

## Bounded universe

The 15 roles cover the current runner's complete named-field boundary:

- C1/C2 launch manifest (72 claims)
- C1 ModeTrack source, proposal, and proposal receipt (199 claims)
- timing payload (3 claims)
- manual review, physical replay, and review-transfer receipt (86 claims)
- transferred review checked manifest (34 claims)
- fresh ModeTrack source and fresh-owner receipt (97 claims)
- terminal checked manifest, QMV, final receipt, and transaction receipt
  (96 claims)

The launch manifest is represented by private-value-free C1/C2 structural
witnesses. A focused test materializes both current runner stages with 85 fake
file identities and requires exact canonical-field equality, so the witness
cannot silently drift from `build_launch_inputs`.

The RAW files, Thermo DLL contents, sample-metadata contents, and execution
runtime ledger remain delegated external owners and are not recursively
inventoried. Their Gate C boundary claims are still covered: the launch
manifest enumerates RAW identity/path/size/mtime/universe, DLL tree, metadata
path/hash, runtime receipt target, checkout, executable, command, and all C2
input path/hash bindings. Expanding into those owners would leave the bounded
Gate C slice.

## Authority decisions

- Persisted source subject identity, observation content identity, and raw
  area are accepted only after equality with the fresh C2 owner. Retained
  observation content separately re-derives content ID, apex, height, and raw
  trapezoid area.
- Source lineage fields not needed by the reviewed subject — artifact IDs,
  owner/acquisition IDs, activation/support/count projections, and candidate
  assignment/peak-hypothesis lineage — remain `audit_only`. Their envelope is
  retained, but their literals cannot select or value a writer cell.
- The transferred checked manifest is `audit_only`. The finalizer ignores it
  as writer input and constructs a new checked manifest from fresh pending
  rows.
- `result_qmv.json` has no root schema marker. The contract truthfully records
  its existing `QuantMatrixVersionRows` component schemas; it does not invent
  a root schema or require a public schema bump because the file is a fresh
  terminal output, never rehydrated as writer authority.
- Human reviewer identity/time/notes/evidence locator are primitive operator
  claims. Subject bindings, completeness, and the required `pass` verdict are
  validated platform-side before the private review-set type can be minted.
- Terminal counts, statuses, digests, rollback, and authority markers are
  locally derived after owner validation. Atomic abort remains the only
  failure mode; no partial-success or per-cell-exclusion state exists.

Promoting any current `audit_only` lineage field into an authenticated product
fact would require a separately authorized owner/schema transaction. It is not
an implicit follow-up inside this contract.

## Reproduction

No RAW or DLL load is performed:

```powershell
.venv\Scripts\python.exe scripts\check_gate_c_persisted_boundary_contract.py
.venv\Scripts\python.exe -m pytest tests\test_issue191_gate_c_persisted_boundary_contract.py -q
```

Use `--write` only when deliberately refreezing the enumerated contract after
an authorized boundary change. The default command regenerates in memory and
fails on missing, stale, changed, duplicate, unresolved, or unresolvable
owner/oracle rows. Focused tests also require complete live field/type equality
for all 15 roles and exercise both nullable-union and incompatible-type
counterexamples.

This closes only the enumerated ModeTrack Gate C slice of ledger Class A.
Global Class A remains open until its full 36-instance census is rerun and
satisfies its own closure rule.

The live type witness closes only this bounded Gate C instance of ledger Class
E. Global Class E remains open.

The Matrix field-role witness closes only this bounded Gate C instance of
ledger Class I. Global Class I remains open.
