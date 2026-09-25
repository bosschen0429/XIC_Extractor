# Local Execution

This file owns stable XIC runner, local-input, and expensive-run constraints.
CLI help owns flag inventories; Issues and validation owners own task-specific
values and artifacts.

## Read Routes

| Need | Read |
| --- | --- |
| Python, worktree, sandbox | [Runner And Environment](#runner-and-environment) |
| RAW/DLL/local inputs | [Local Inputs](#local-inputs) |
| validation scale | [Validation Scale](#validation-scale) |
| Alignment or 85RAW | [Alignment And Handoff](#alignment-and-handoff) |
| PowerShell failure | [Shell Traps](#shell-traps) |

Before repeating expensive validation, check docs/diagnostic-ledger.md and the
active validation owner. Rerun only when current code, inputs, freshness, or the
decision oracle changed.

## Runner And Environment

Use Windows Native and the current checkout's .venv\Scripts\python.exe for
project tests, scripts, and RAW commands. Each worktree owns its .venv; linked
worktrees share only .uv-cache. Do not borrow another checkout's interpreter.

Check an existing worktree with:

    uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py --check --local --json
    .venv\Scripts\python.exe -m scripts.agent_sandbox_doctor --strict --probe-write

Bootstrap a new worktree once with:

    uv run --cache-dir .uv-cache --no-project python scripts/bootstrap_worktree.py --local

The bootstrap uses locked dev dependencies and creates a worktree-local copy of
the ignored local env. Treat a failed runtime check as environment uncertainty,
not a product finding.

## Local Inputs

Machine-specific values live in ignored .env.xic-local; config/env.example is
the public template.

| Environment name | Owns |
| --- | --- |
| XIC_RAW_ROOT | 85RAW tissue root |
| XIC_RAW_VALIDATION_DIR | lane-specified validation subset |
| THERMO_RAWFILE_READER_DLL_DIR | Thermo DLL directory |
| XIC_MANUAL_2RAW_ROOT | manual-truth calibration inputs |
| XIC_ACQUISITION_CACHE_DIR | shared Alignment RAW acquisition cache; keep it on a fast local disk |

Stable accepted-input paths and hashes live in
docs/diagnostic-ledger.md#stable-inputs. Verify the named path and the owning
CLI's preflight-only result before use. A missing path is a blocker; an older
sibling path is not an automatic fallback.

## Validation Scale

Validation data is method-specific, not a universal ladder.

| Scale | Meaning |
| --- | --- |
| synthetic/unit | engineering regression only |
| manual-2RAW | manual-truth or resolver calibration |
| lane subset | smoke, parity, or benchmark evidence named by the owner |
| 85RAW | expensive full-tissue evidence explicitly required by the owner |

Use xic-raw-validation before launching or accepting RAW-backed evidence.
An inconclusive smaller gate closes its named blocker before a larger run.

For current CID-NL tissue validation, use the shared maintained entry point:

    .venv\Scripts\python.exe -m scripts.run_tissue_validation --dataset 8 --preflight-only

Use `--dataset 85` for the full cohort. Preflight resolves local inputs and
prints the effective plan without opening RAW files or running analysis.
After the RAW launch gate is satisfied, the same command without
`--preflight-only` runs Discovery followed by Alignment. `--help` owns runtime
overrides; the printed plan owns the actual paths and effective settings.

Both datasets use the same scientific preset, background library, ISTD input,
and shared [execution profile](../product/execution-runtime.md#worker-settings).
`--help` lists the worker-budget options, and the preflight plan prints the
effective worker counts. The runner reads the ignored local environment file;
explicit CLI arguments and process environment take precedence. It creates a
fresh run directory and does not select historical Discovery indexes or
workbooks implicitly. The older targeted validation harness remains a separate
method, not this mainline's launch recipe. Scripts under `output/` and old
command transcripts are historical evidence, not defaults.

## Alignment And Handoff

The current command surface is:

    .venv\Scripts\python.exe -m scripts.run_alignment --help

Output and handoff semantics live in docs/product/alignment.md; preset
compatibility lives in docs/product/presets.md. Task-specific paths and accepted
parameter values stay in the Issue or validation owner.

Alignment requires a bound pure CID-NL preset. Its retained runtime owns
acquisition and scheduling; CLI help defines supported execution options.
For method-authorized RAW validation, resolve the configured local inputs and
run the current command's preflight before acquisition. Keep final and live
timing outputs in the task-specific output directory.

The live timing file is the heartbeat for interrupted runs; the final timing
file is the completed history. Performance interpretation belongs to
xic-performance-diagnosis. Current CLI help owns exact spelling and available
flags.

## Shell Traps

- Use PowerShell command syntax. Bash heredocs, export, and Unix continuations
  fail before Python runs.
- Invoke a PowerShell helper from the active shell with `& <script.ps1> ...`.
  The command policy rejects nested `powershell`/`pwsh` wrappers, including
  copied `-File` examples; keep the helper and its arguments unchanged.
- Use one current-worktree command at a time when a hook or policy is involved.
- Prefer CLI help over copying command examples into docs.
- Keep run outputs under ignored output/ and local inputs under ignored
  local_validation_artifacts/.
