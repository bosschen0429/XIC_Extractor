# ruff: noqa: E501
from __future__ import annotations

import json
import re
import sys

from xic_hook_policy import CONTROL_PLANE_PATH, HANDOFF_CURRENT_DIR

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bOPENAI_API_KEY\s*=\s*['\"]?[^'\"\s]+", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|token|password)\s*[:=]\s*['\"]?[^'\"\s]{12,}", re.IGNORECASE),
]

ARCHITECTURE_PATTERNS = [
    r"\bRAW\b",
    r"85\s*-?\s*RAW",
    r"8\s*-?\s*RAW",
    r"matrix",
    r"activation",
    r"value[\s_-]?delta",
    r"CID[\s_-]?NL",
    r"HCD[\s_-]?PI",
    r"Delta Mass",
    r"evidence",
    r"diagnostic",
    r"架構|診斷|證據",
]

# Performance work has a dedicated diagnosis skill that must run before
# architecture-preflight, so route it explicitly instead of letting the broad
# architecture bucket claim it. Bare "optimize" is excluded unless a perf noun
# is nearby, so ordinary "optimize docs/skills" prompts do not fire this.
PERFORMANCE_PATTERNS = [
    r"performance|perf\b|bottleneck",
    r"(optimi[sz]e|optimi[sz]ation|speed\s*up|faster|加速|優化|优化)[^\n]{0,40}"
    r"(perf|performance|bottleneck|瓶頸|效能|timing|runtime|batch|cache|locality|worker|耗時|call[\s_-]?cost)",
    r"timing\.(json|live)|wall[\s-]?clock|profil(e|ing)|worker\s+budget|call[\s_-]?cost",
    r"效能|耗時|瓶頸",
]

# RAW-backed validation launch/acceptance is its own preflight skill. Match run
# or acceptance intent (not a bare RAW mention) so RAW code edits still route to
# architecture-preflight.
RAW_VALIDATION_PATTERNS = [
    r"(8|85)\s*-?\s*RAW[^\n]{0,40}(valid|驗證|跑|run|rerun|launch|benchmark|accept|gate|heartbeat)",
    r"(valid|驗證|跑|run|rerun|launch|benchmark|accept|gate|heartbeat)[^\n]{0,40}(8|85)\s*-?\s*RAW",
    r"\bRAW\b[^\n]{0,40}(valid|validation|驗證|跑|run|rerun|launch|benchmark|accept|gate|heartbeat|timing\s+sidecar)",
    r"(valid|validation|驗證|跑|run|rerun|launch|benchmark|accept|gate|heartbeat|timing\s+sidecar)[^\n]{0,40}\bRAW\b",
]

NO_RAW_PATTERNS = [
    r"\bno[\s-]?RAW\b",
    r"\b(?:without|do\s+not|don't|must\s+not)\s+"
    r"(?:read(?:ing)?|open(?:ing)?|us(?:e|ing)|run(?:ning)?|rerun(?:ning)?|launch(?:ing)?)?"
    r"\s*(?:any\s+)?RAW\b",
    r"不要[^\n]{0,12}(?:讀|開|跑|使用)[^\n]{0,6}\bRAW\b",
]

EDIT_INTENT_PATTERNS = [
    r"\b(change|modify|edit|implement|fix|refactor|rewrite|add|remove)\b",
    r"修改|改動|實作|修正|修復|重構|重寫|新增|移除",
]

PR_REVIEW_PATTERNS = [
    r"\bPR\b",
    r"pull request",
    r"review",
    r"code review",
]

# Stacked/superseded PR CI trouble is a boundary problem, not an ordinary
# review. It takes precedence over the general PR-review bucket.
PR_STACK_PATTERNS = [
    r"stack(ed)?\s+PR|PR\s+stack|PR\s+series|split\s+PR",
    r"superseded\s+(branch|PR)|stale\s+base|retarget|rebuild[^\n]{0,20}(PR|branch|分支)",
    r"stack[\s-]?boundary|clean[\s-]?checkout",
    r"堆疊[^\n]{0,6}PR|PR[^\n]{0,6}堆疊|拆[^\n]{0,6}PR|重建[^\n]{0,6}(branch|分支|PR)|過時[^\n]{0,6}base|superseded",
]

GOAL_PATTERNS = [
    r"\bgoal\b",
    r"\$goal-execution",
    r"設定.*goal",
    r"用.*goal",
    r"目標",
]

PRODUCTIZATION_PATTERNS = [
    r"producti[sz]e|producti[sz]ation",
    r"production[_\s-]?(surface|ready|candidate)",
    r"shadow_ready|diagnostic_only|maturity\s+tier",
    r"\bpromotion\b|\bpromote\b",
    r"method_manifest|review_roundtrip|sample_metadata_contract|alignment_output_contract",
    r"control\s+plane",
    r"產品化|推到產品|正式推|正式產品|正式功能|正式輸出|正式矩陣",
    r"成熟度|控制板|控制台",
]

HANDOFF_PATTERNS = [
    r"\bhandoff\b",
    r"worktree[-_\s]?report",
    r"context\s+(window|compaction)",
    r"\bcompaction\b",
    r"compact\s+context",
    r"long\s+pause",
    r"next\s+(agent|session)",
    r"收尾|交接|交接文檔|交接文件|下一個\s*(agent|session|會話)",
    r"壓縮|上下文|長會話|長暫停",
]


def emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    prompt = str(event.get("prompt", ""))
    if not prompt:
        return 0

    if any(pattern.search(prompt) for pattern in SECRET_PATTERNS):
        emit(
            {
                "decision": "block",
                "reason": "Prompt appears to include a secret-like value. Remove the secret and use environment variables or a secret manager.",
            }
        )
        return 0

    def matches(patterns: list[str]) -> bool:
        return any(re.search(pattern, prompt, re.IGNORECASE) for pattern in patterns)

    contexts: list[str] = []

    perf = matches(PERFORMANCE_PATTERNS)
    raw_validation = matches(RAW_VALIDATION_PATTERNS) and not matches(NO_RAW_PATTERNS)
    edit_intent = matches(EDIT_INTENT_PATTERNS)

    if perf:
        contexts.append(
            "XIC performance context detected: use xic-performance-diagnosis first. "
            "Inspect timing artifacts, rank bottlenecks, classify the optimization lane "
            "(exact_safe / expected_diff / fast_mode_candidate / diagnostic_only), and name a "
            "correctness oracle and validation gate before edits. Then use xic-architecture-preflight "
            "before changing performance-sensitive code."
        )

    if raw_validation:
        contexts.append(
            "XIC RAW validation context detected: use xic-raw-validation before launching or accepting "
            "8RAW/85RAW, alignment, benchmark, timing/heartbeat, or production-equivalent runs. "
            "State the decision the run can close, sample set, documented runner/DLL/RAW paths, output "
            "level, heartbeat/timeout, and why existing artifacts cannot answer it. Do not launch 85RAW "
            "via background Start-Process from the Codex shell without explicit approval."
        )

    # Pure performance/RAW execution prompts use their focused preflight. Any
    # mixed behavior edit keeps architecture preflight as well; a run or
    # performance gate must not hide the pre-edit contract.
    if matches(ARCHITECTURE_PATTERNS) and (
        edit_intent or not (perf or raw_validation)
    ):
        contexts.append(
            "XIC high-risk work detected: before edits, use xic-architecture-preflight. "
            "Name existing owner/helper reuse, evidence-provider role, call-cost model, "
            "public contracts at risk, validation gate, and stop rule."
        )

    if matches(PR_STACK_PATTERNS):
        contexts.append(
            "XIC stacked/superseded PR context detected: use xic-pr-stack-repair before more CI fixes. "
            "Map base/head, stale base, superseded status, repeated ledgers/handoffs, and artifact "
            "ownership first. Do not fix CI on top of a stale base or depend on a later cleanup PR to "
            "make an earlier PR pass."
        )
    elif matches(PR_REVIEW_PATTERNS):
        contexts.append(
            "XIC PR/review context detected: use xic-large-pr-review for large or diagnostics-heavy PRs. "
            "Start read-only, map blast radius, lead with findings, and label strongest evidence reviewed."
        )

    if matches(GOAL_PATTERNS):
        contexts.append(
            "XIC goal context detected: use the global goal-execution contract for phase-sized or drifting work. "
            "Keep one objective and name context, constraints, verification, done condition, stop rules, and handoff."
        )

    if matches(PRODUCTIZATION_PATTERNS):
        contexts.append(
            "XIC productization context detected: before claiming a feature is productized, read "
            f"{CONTROL_PLANE_PATH}. "
            "Name the current/proposed maturity tier and, if the tier or active lane changes, update the board "
            "or explicitly state why no control-plane update is needed."
        )

    if any(re.search(pattern, prompt, re.IGNORECASE) for pattern in HANDOFF_PATTERNS):
        contexts.append(
            "XIC handoff/closeout context detected: first resolve the active branch handoff from the goal, PR workflow, "
            f"or a branch-scoped file under {HANDOFF_CURRENT_DIR}<branch-slug>-<topic>.md. "
            "Do not default to another branch's handoff; only update an existing handoff when its Branch/status matches this work. "
            "Rewrite and prune that active snapshot when branch status, validation evidence, productization tier, or next action changed. "
            "Do not append chronological notes; keep only current objective/state, active decisions, validation, blockers, "
            "rejected paths still likely to recur, and next 1-3 actions. "
            "For PR closeout, condense the current handoff into the PR body and archive only completed phase summaries that must remain in repo. "
            "The global $handoff skill writes a temporary conversation handoff; it is not the repo branch handoff. "
            f"If productization tier or active lane changed, sync {CONTROL_PLANE_PATH} too. "
            "Hooks only remind; the executing agent owns the handoff rewrite."
        )

    if not contexts:
        return 0

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "\n".join(contexts),
            }
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
