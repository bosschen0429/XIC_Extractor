# ruff: noqa: E501
from __future__ import annotations

import json
import re
import sys

from xic_hook_policy import (
    CONTROL_PLANE_PATH,
    PRODUCTIZATION_AUTHORITY_MANIFEST_REL,
    PRODUCTIZATION_STATUS_INDEX_REL,
)

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bOPENAI_API_KEY\s*=\s*['\"]?[^'\"\s]+", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|token|password)\s*[:=]\s*['\"]?[^'\"\s]{12,}", re.IGNORECASE
    ),
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
    r"(perf|performance|bottleneck|瓶頸|效能|timing|runtime|batch|cache|locality|worker|耗時|call[\s_-]?cost)[^\n]{0,40}"
    r"(optimi[sz]e|optimi[sz]ation|speed\s*up|faster|加速|優化|优化)",
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

# An explicit request to accept already-produced RAW-backed evidence still
# needs the RAW validation acceptance branch even when the same prompt says not
# to read or rerun RAW. This is narrower than generic "no RAW" validation.
RAW_ACCEPTANCE_ACTION_PATTERNS = [r"\baccept(?:ance)?\b|驗收|接受"]
RAW_ACCEPTANCE_EXISTING_PATTERNS = [
    r"\b(?:existing|current|already[-\s]?(?:produced|exists?))\b|既有|現有"
]
RAW_BACKED_EVIDENCE_PATTERNS = [
    r"\bRAW[-\s]?backed\b[^\n]{0,40}"
    r"(?:artifact|evidence|validation|result|產物|證據|結果)",
    r"(?:artifact|evidence|validation|result|產物|證據|結果)[^\n]{0,40}"
    r"\bRAW[-\s]?backed\b",
]

EDIT_INTENT_PATTERNS = [
    r"\b(change|modify|edit|implement|fix|refactor|rewrite|add|remove|design|plan|propose|optimi[sz]e)\b",
    r"修改|改動|改|實作|修正|修復|重構|重寫|新增|移除|設計|規劃|計畫|優化|优化",
]

# Stacked/superseded PR CI trouble is a boundary problem, not an ordinary
# review. It takes precedence over the general PR-review bucket.
PR_STACK_PATTERNS = [
    r"stack(ed)?\s+PR|PR\s+stack|PR\s+series|split\s+PR",
    r"superseded\s+(branch|PR)|stale\s+base|retarget|rebuild[^\n]{0,20}(PR|branch|分支)",
    r"stack[\s-]?boundary|clean[\s-]?checkout",
    r"堆疊[^\n]{0,6}PR|PR[^\n]{0,6}堆疊|拆[^\n]{0,6}PR|重建[^\n]{0,6}(branch|分支|PR)|過時[^\n]{0,6}base|superseded",
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
    explicit_raw_acceptance = (
        matches(RAW_ACCEPTANCE_ACTION_PATTERNS)
        and matches(RAW_ACCEPTANCE_EXISTING_PATTERNS)
        and matches(RAW_BACKED_EVIDENCE_PATTERNS)
    )
    raw_validation = matches(RAW_VALIDATION_PATTERNS) and (
        explicit_raw_acceptance or not matches(NO_RAW_PATTERNS)
    )
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
    if matches(ARCHITECTURE_PATTERNS) and edit_intent:
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
    if matches(PRODUCTIZATION_PATTERNS):
        contexts.append(
            "XIC productization context detected: before claiming a feature is productized, read "
            f"the productization control plane compatibility index at {CONTROL_PLANE_PATH}. "
            f"Read mutable lane state from {PRODUCTIZATION_STATUS_INDEX_REL}, writer authority from "
            f"{PRODUCTIZATION_AUTHORITY_MANIFEST_REL}, and readiness/promotion rules from "
            "docs/agent/product-validation-contract.md. Name the current/proposed maturity tier and "
            "update only the applicable canonical owner, or explicitly state why no routed owner update is needed. "
            "Change the compatibility index itself only when a stable lane key or owner route changes."
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
