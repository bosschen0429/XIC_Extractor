# Trigger Cases

These cases document the existing `xic-raw-validation` route. They do not widen
or narrow the skill trigger.

## Should Trigger

- "準備跑 85RAW alignment 驗證，先確認 runner 跟 DLL 路徑。" This prompt is
  exercised by `.codex/hooks/fixtures/assert_hook_outputs.py`; root `AGENTS.md`
  and `docs/agent/subagent-routing.md#hard-triggers` require RAW preflight for an
  85RAW launch.
- "Accept the existing RAW-backed validation artifacts without reading or
  rerunning RAW." The hook fixture exercises this prompt, and the skill
  description plus Branch B require the same gate when deciding whether
  existing RAW artifacts can satisfy acceptance.
- "Launch this production-equivalent validation; it will likely take more than
  30 minutes." `docs/agent/subagent-routing.md#hard-triggers` explicitly
  requires the skill for production-equivalent or likely long RAW runs.
- "Rerun the lane's 8RAW fixture because the current artifact packet lacks the
  required science oracle." The skill description covers RAW reruns; Branch A
  must prove why existing evidence cannot answer the bound question first.

## Should Not Trigger

- "Run alignment validation using synthetic fixtures; do not read RAW." The
  hook fixture deliberately keeps synthetic/no-RAW work outside this gate; use
  focused tests and `xic-architecture-preflight` only if behavior changes.
- "Validate the benchmark schema using tracked TSV fixtures." The hook fixture
  routes this to ordinary schema/tests work because it neither launches nor
  accepts RAW-backed validation.
- "Fix the heartbeat renderer unit test." The hook fixture routes generic
  heartbeat unit work to the ordinary implementation/test workflow; use this
  skill only when a real RAW launch or artifact acceptance is in scope.
- "Decide the Matrix extraction code change, but do not run or accept RAW
  evidence yet." Use `xic-architecture-preflight` for the behavior change; RAW
  validation starts only when launch, rerun, or RAW-artifact acceptance enters
  scope.

## Boundary Case

- "改 85RAW 的 Matrix 抽取邏輯。" Decision: the dataset name alone does not make
  this a RAW-validation task. The existing hook fixture routes the behavior
  edit to `xic-architecture-preflight` and keeps `xic-raw-validation` absent.
  If the request also says to launch, rerun, or accept 85RAW evidence, both
  skills apply under `docs/agent/subagent-routing.md#hard-triggers`.

## Format Decision

Markdown matches two of the three existing repo-local trigger-case fixtures and
does not imply an executable gate that the repository does not have. The JSON
fixture under `xic-pr-stack-repair` is paired with a semantic configuration;
these two skills have no trigger-case runner. If #223 later introduces an
outcome/eval mechanism, it may define and migrate to a machine-owned format.
