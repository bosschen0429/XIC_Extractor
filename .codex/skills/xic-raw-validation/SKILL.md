---
name: xic-raw-validation
description: RAW validation gate for XIC Extractor. Use before launching or rerunning RAW-backed validation, or when deciding whether existing RAW-backed artifacts can satisfy a validation gate without rerun.
---

# XIC RAW Validation

Produce one source-qualified validation packet. `accept-existing` is a workflow
branch, not a CLI/config option or a new public schema. This skill does not grant
permission to launch RAW work or promote product authority.

## 1. Bind The Decision And Lane

Name:

- the exact decision this evidence can change;
- the method/lane and sample universe;
- the project type, active validation owner, and lane-owned acceptance oracle;
- the highest product authority the result could support.

Read `docs/agent/product-validation-contract.md`, the active validation owner,
and relevant entries in `tools/diagnostics/INDEX.md` and
`docs/diagnostic-ledger.md`. Treat 8RAW and 85RAW as lane-specific fixtures, not
universal Discovery gates. Science work uses the method/lane science oracle;
engineering, cleanup, performance, and execution work use the parity,
performance, or completion oracle owned by that project type.

Completion criterion: the requested decision, project-appropriate acceptance
oracle, sample universe, and authority ceiling are explicit. If the evidence
cannot change the next action, stop.

## 2. Select One Branch

Choose `launch-or-rerun` only when existing artifacts cannot answer the bound
question. Otherwise choose `accept-existing`.

### Branch A: Launch Or Rerun

1. State the smallest missing oracle and why the existing evidence cannot
   supply it.
2. Read `docs/agent/parameter-settings.md` and materialize its current launch
   contract: documented runner and RAW/DLL paths, input identity and expected
   sample count, foreground command, task-specific output, required artifacts,
   heartbeat/timing evidence, timeout, and stop condition.
3. For 85RAW, likely >30-minute, production-equivalent, or readiness-affecting
   validation, obtain the independent preflight review defined by the
   `validation / science evidence` lens in
   `docs/agent/subagent-routing.md`.
4. Launch only when the task authorizes execution. Monitor the canonical
   heartbeat and preserve exact command, code state, configuration, inputs, and
   partial outputs if execution stops.

Completion criterion before launch: every launch-contract field is grounded in
the current parameter owner, the command evaluates the project-appropriate
acceptance oracle, and the stop condition is observable. After execution,
evaluate the artifacts with Branch B before making a gate claim.

### Branch B: Accept Existing

Reconstruct a read-only evidence packet containing:

- artifact root and producing code ref/version;
- current checkout/ref and dirty-state snapshot, with compatibility assessed
  separately from the producing run;
- runner, command/config, input identities, hashes, and schema versions when
  retained;
- required outputs, completion/heartbeat evidence, integrity checks, and each
  missing or drifted item;
- lane-owned acceptance oracle and its actual verdict;
- contradictory or superseding evidence in current owners or artifacts.

Apply this evidence ladder:

- Establish `run_ok` for the producing run only when producing ref/version,
  completion evidence, and essential input/config provenance are present.
- Establish `gate_ok` only when the project-appropriate lane-owned acceptance
  oracle was evaluated and passed.
- Establish a current-checkout claim only with a repo-visible compatibility
  check or fresh reproduction; otherwise qualify the result as historical.
- When evidence is insufficient, report the smallest missing oracle instead of
  widening the run automatically.

Completion criterion: every run/gate statement names its source state, packet
integrity is separated from gate acceptance, and compatibility with the
current checkout is either demonstrated or left explicitly unresolved.

## 3. Close The Loop

Report:

```markdown
Decision / method / lane / sample universe:
Evidence source and producing code state:
Current-checkout compatibility:
run_ok for which source:
gate_ok and lane-owned acceptance oracle:
Validation evidence status (Evidence label from `docs/product/README.md`) and owner:
rerun_needed: true | false
Smallest missing oracle or reason no rerun is needed:
Contradictory or superseding evidence:
Authority boundary:
Stop / retry condition:
```

Use the validation evidence-status vocabulary owned by
`docs/product/README.md` and the independent acceptance review defined in
`docs/agent/subagent-routing.md`. A coherent artifact packet can establish a
historical run result without establishing current reproducibility, science
acceptance when required, or product readiness. Diagnostic sidecars and gate
results grant no counted-detection, selected-value, matrix, workbook,
activation, or export authority without the relevant explicit contract.

Completion criterion: claims are source-qualified, `rerun_needed` has one
reason, missing evidence has one smallest next probe, and authority does not
exceed the active product contract.
