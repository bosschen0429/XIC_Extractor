# CID-NL Legacy Baseline v1

Status: `diagnostic_only`; the provenance-v2 retained pair is sealed and is the
canonical legacy comparison target for issue #130.

This packet freezes the observed targeted CID-NL / NL116 behavior at commit
`6d3b18efd88971ffb6d96412a8d8ee47e8ed4742`. It is characterization evidence,
not scientific truth or product authority.

## Result

- Both runs used the same clean detached worktree, 8 RAW files, config, vendor
  bridge, Python runtime, package inventory, and command shape.
- Both runs processed 8/8 RAW files and passed the method expected-diff gate
  (1 row, 6 matrix cells).
- Run 1 took 67.300027 seconds; Run 2 took 64.827012 seconds. These are
  reproducibility observations, not a performance baseline.
- Ten CSV/TSV artifacts were byte-identical. The two
  `method_manifest.json` files matched after only the preregistered run-local
  metadata normalization.
- The retained packet contains 12 canonical files, 2 config files, 8 evidence
  payload files, and the retained copy of the summary.

[`baseline_summary.json`](baseline_summary.json) is the canonical pointer. It
records the source and inventory hashes, deterministic parity, retained-root
location, and `stable_copy_verified=true`.

[`comparator_contract.json`](comparator_contract.json) preregisters the
successor/strangler comparison dimensions and GO/NARROW/KILL rules.

[`candidate_summary_20260722.json`](candidate_summary_20260722.json) describes
the earlier pair whose post-run-only inventory could not prove the execution
boundary. It remains historical `candidate_unsealed` evidence and is not a
future comparison target.

## Replay and audit trail

The retained root named by `baseline_summary.json` contains:

- `evidence/run_1_{pre,post}_snapshot.json` and
  `evidence/run_2_{pre,post}_snapshot.json`, including the exact product argv;
- `evidence/source_inventory.json`, including RAW, config, vendor bridge,
  build, executable, and environment checksums;
- `evidence/run_{1,2}_receipt.json`, including per-output size and SHA-256;
- `evidence/comparison.json`, including exact versus normalized comparison
  results and the allowed manifest normalization; and
- `evidence/summary.json`, a retained copy of the tracked canonical pointer.

For a future replay, verify the frozen commit and source inventory, use the
recorded command shape with a fresh output root, and compare outputs against
the Run 1 receipt. CSV/TSV files remain byte exact; only the two method
manifests may use the JSON pointers listed in `comparator_contract.json`.
The baseline generator was intentionally retired after sealing because issue
#129 needs the immutable evidence packet, not a permanent replay framework.

## Boundary

Issue #130 can now use `baseline_summary.json` for hard-exact targeted CSV
semantics and consumer disposition. No targeted downstream analytical consumer
is currently required, so this packet does not fabricate a reader smoke. This
packet does not change selected peaks or areas, counted detections, Matrix
identity, workbook/output schemas, activation, export, or any productization
maturity tier. It authorizes no additional RAW run.
