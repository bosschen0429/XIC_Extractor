from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOKS = ROOT / ".codex" / "hooks"
sys.path.insert(0, str(HOOKS))

from xic_hook_policy import PRODUCT_SURFACE_PATHS, mentions_any_path  # noqa: E402


def run_hook(
    script: str,
    event: dict[str, object],
    *,
    env_extra: dict[str, str] | None = None,
) -> dict[str, object]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps(event),
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    output = result.stdout.strip()
    if not output:
        raise AssertionError(f"{script} emitted no output")
    return json.loads(output)


def assert_contains(value: object, expected: str) -> None:
    text = json.dumps(value, ensure_ascii=False)
    if expected not in text:
        raise AssertionError(f"expected {expected!r} in {text}")


def assert_hook_silent(script: str, event: dict[str, object]) -> None:
    result = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps(event),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip() or result.stderr.strip():
        raise AssertionError(
            f"expected silent {script} no-op, got "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )


def main() -> int:
    prompt_payload = {
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "prompt": "這個功能是不是已經正式產品化？我要確認 promotion tier。",
    }
    assert_contains(
        run_hook("xic_prompt_router.py", prompt_payload),
        "productization-control-plane.md",
    )

    handoff_prompt_payload = {
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "prompt": (
            "準備收尾，請更新交接文件，避免下一個 session "
            "因 context compaction 丟資訊。"
        ),
    }
    assert_contains(
        run_hook("xic_prompt_router.py", handoff_prompt_payload),
        "branch-scoped file",
    )

    def router_output(prompt: str) -> str:
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "turn_id": "fixture",
            "cwd": ".",
            "permission_mode": "default",
            "prompt": prompt,
        }
        return json.dumps(run_hook("xic_prompt_router.py", payload), ensure_ascii=False)

    def assert_absent(text: str, unexpected: str) -> None:
        if unexpected in text:
            raise AssertionError(f"did not expect {unexpected!r} in {text}")

    # Performance work routes to xic-performance-diagnosis, not the broad
    # architecture reminder.
    perf_out = router_output(
        "這個 preset 的 performance bottleneck 在哪？先看 timing.json"
    )
    assert_contains(perf_out, "xic-performance-diagnosis")
    assert_absent(perf_out, "high-risk work detected")

    # RAW-run/validation intent routes to xic-raw-validation and suppresses the
    # architecture reminder even though the prompt mentions 85RAW.
    raw_out = router_output("準備跑 85RAW alignment 驗證，先確認 runner 跟 DLL 路徑")
    assert_contains(raw_out, "xic-raw-validation")
    assert_absent(raw_out, "high-risk work detected")

    # A mixed behavior edit plus RAW validation needs both preflights.
    mixed_raw_edit_out = router_output(
        "Change matrix extraction behavior, then run 85RAW validation."
    )
    assert_contains(mixed_raw_edit_out, "xic-raw-validation")
    assert_contains(mixed_raw_edit_out, "xic-architecture-preflight")

    mixed_perf_raw_edit_out = router_output(
        "Optimize matrix extraction performance, then run 85RAW validation."
    )
    assert_contains(mixed_perf_raw_edit_out, "xic-performance-diagnosis")
    assert_contains(mixed_perf_raw_edit_out, "xic-raw-validation")
    assert_contains(mixed_perf_raw_edit_out, "xic-architecture-preflight")

    # Existing RAW-backed artifact acceptance is explicit RAW validation intent.
    raw_accept_out = router_output(
        "Accept the existing RAW-backed validation artifacts for this gate."
    )
    assert_contains(raw_accept_out, "xic-raw-validation")
    raw_accept_without_rerun_out = router_output(
        "Accept the existing RAW-backed validation artifacts without reading "
        "or rerunning RAW."
    )
    assert_contains(raw_accept_without_rerun_out, "xic-raw-validation")
    reordered_raw_accept_out = router_output(
        "Accept RAW-backed artifacts that already exist; do not rerun RAW."
    )
    assert_contains(reordered_raw_accept_out, "xic-raw-validation")

    # A bare RAW code edit still routes to architecture-preflight, not raw-validation.
    raw_code_out = router_output("改 85RAW 的 matrix 抽取邏輯")
    assert_contains(raw_code_out, "high-risk work detected")
    assert_absent(raw_code_out, "xic-raw-validation")

    # Synthetic/no-RAW validation and generic heartbeat/schema work must not be
    # turned into RAW/DLL launch preflight.
    no_raw_alignment_out = router_output(
        "Run alignment validation using synthetic fixtures; do not read RAW."
    )
    assert_absent(no_raw_alignment_out, "xic-raw-validation")
    assert_absent(
        router_output("Validate alignment with fixtures without reading RAW."),
        "xic-raw-validation",
    )

    def router_stdout(prompt: str) -> str:
        result = subprocess.run(
            [sys.executable, str(HOOKS / "xic_prompt_router.py")],
            input=json.dumps({"prompt": prompt}),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout

    assert_absent(
        router_stdout("Validate the benchmark schema using tracked TSV fixtures."),
        "xic-raw-validation",
    )
    assert_absent(
        router_stdout("Fix the heartbeat renderer unit test."),
        "xic-raw-validation",
    )

    # Stacked/superseded PR trouble routes to xic-pr-stack-repair, not large-pr-review.
    stack_out = router_output(
        "這個 stacked PR series 一直 red CI，先 map stale base 再修"
    )
    assert_contains(stack_out, "xic-pr-stack-repair")
    assert_absent(stack_out, "xic-large-pr-review")

    # Ordinary large PR review still routes to xic-large-pr-review.
    review_out = router_output("幫我 review 這個大 PR 的 diagnostics 改動")
    assert_contains(review_out, "xic-large-pr-review")

    # An ordinary "optimize skills" prompt must not trigger any routing context.
    skills_result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_prompt_router.py")],
        input=json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "turn_id": "fixture",
                "cwd": ".",
                "permission_mode": "default",
                "prompt": "幫我優化 repo local skills",
            }
        ),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if "performance" in skills_result.stdout or "xic-" in skills_result.stdout:
        raise AssertionError(
            "ordinary optimize-skills prompt emitted routing context: "
            f"{skills_result.stdout!r}"
        )

    raw_background_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": (
                "Start-Process python -ArgumentList "
                "'-m scripts.run_alignment --preset dna_dr'"
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", raw_background_payload),
        "Background RAW launch",
    )

    non_raw_background_payload = {
        **raw_background_payload,
        "tool_input": {"command": "Start-Process python -ArgumentList '-c print(1)'"},
    }
    assert_hook_silent("xic_pre_tool_guard.py", non_raw_background_payload)

    split_raw_background_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                "pwsh -Command \"$rawArgs='-m scripts.run_alignment --raw-dir "
                "$env:XIC_RAW_ROOT'; Start-Process python -ArgumentList $rawArgs\""
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", split_raw_background_payload),
        "Background RAW launch",
    )

    execution_config_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {"patch": "*** Update File: .codex/hooks.json\n"},
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", execution_config_payload),
        "Execution-affecting XIC agent config",
    )

    static_rule_search_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": (
                "rg -n " + json.dumps(" ".join(("git", "reset", "--hard"))) + " docs"
            )
        },
    }
    assert_hook_silent("xic_pre_tool_guard.py", static_rule_search_payload)

    wrapped_cleanup_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": ("bash -lc " + json.dumps(" ".join(("git", "clean", "-fd"))))
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", wrapped_cleanup_payload),
        "interactive approval",
    )

    wrapped_long_cleanup_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps(" ".join(("git", "clean", "--force", "-d")))
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", wrapped_long_cleanup_payload),
        "interactive approval",
    )

    multiline_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc 'Write-Host ok\n"
            + " ".join(("git", "reset", "--hard"))
            + "'"
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", multiline_reset_payload),
        "is blocked",
    )

    conditional_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps(
                "if true; then "
                + " ".join(("git", "reset", "--hard"))
                + "; fi"
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", conditional_reset_payload),
        "is blocked",
    )

    condition_list_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps(
                "if " + " ".join(("git", "reset", "--hard")) + "; then :; fi"
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", condition_list_reset_payload),
        "is blocked",
    )

    controlled_raw_background_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                "pwsh -Command \"if ($true) { Start-Process python -ArgumentList "
                "'-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT' }\""
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", controlled_raw_background_payload),
        "Background RAW launch",
    )

    assigned_process_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                "pwsh -Command \"$proc = Start-Process python -PassThru "
                "-ArgumentList '-m scripts.run_alignment --raw-dir "
                "$env:XIC_RAW_ROOT'\""
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", assigned_process_payload),
        "Background RAW launch",
    )

    env_option_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "env -u CI bash -lc "
            + json.dumps(" ".join(("git", "reset", "--hard")))
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", env_option_reset_payload),
        "is blocked",
    )

    combined_flag_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -xc "
            + json.dumps(" ".join(("git", "reset", "--hard")))
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", combined_flag_reset_payload),
        "is blocked",
    )

    nested_git_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps("command /usr/bin/git -C /tmp reset --hard")
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", nested_git_reset_payload),
        "is blocked",
    )

    cmd_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "cmd /c "
            + json.dumps(" ".join(("git", "reset", "--hard")))
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", cmd_reset_payload),
        "is blocked",
    )

    exec_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps(
                " ".join(
                    ("exec", "-a", "safe-name", "/usr/bin/git", "reset", "--hard")
                )
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", exec_reset_payload),
        "is blocked",
    )

    assignment_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps("FOO=1 " + " ".join(("git", "reset", "--hard")))
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", assignment_reset_payload),
        "is blocked",
    )

    aliased_raw_background_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                "pwsh -Command \"$a='-m scripts.run_alignment --raw-dir "
                "$env:XIC_RAW_ROOT'; $b=$a; Start-Process python -ArgumentList $b\""
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", aliased_raw_background_payload),
        "Background RAW launch",
    )

    pre_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "Set-Content xic_extractor\\output\\schema.py '# edit'",
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", pre_payload),
        "Product/public surface may be edited",
    )

    apply_patch_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": "*** Update File: xic_extractor/signal_processing.py\n",
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", apply_patch_payload),
        "Product/public surface may be edited",
    )

    status_index_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": (
                "*** Update File: docs/superpowers/validation/"
                "productization_status_index_v1.tsv\n"
            ),
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", status_index_payload),
        "Product/public surface may be edited",
    )

    post_subdir_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": "docs",
        "permission_mode": "default",
        "tool_input": {
            "command": "Set-Content ..\\xic_extractor\\output\\schema.py '# edit'",
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    assert_contains(
        run_hook("xic_post_tool_guard.py", post_subdir_payload),
        "productization control plane",
    )

    post_apply_patch_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": "*** Update File: scripts/run_any_public_cli.py\n",
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    assert_contains(
        run_hook("xic_post_tool_guard.py", post_apply_patch_payload),
        "productization control plane",
    )

    post_status_index_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": (
                "*** Update File: docs/superpowers/validation/"
                "productization_status_index_v1.tsv\n"
            ),
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    assert_contains(
        run_hook(
            "xic_post_tool_guard.py",
            post_status_index_payload,
            env_extra={
                "XIC_POST_TOOL_GUARD_CHANGED_PATHS_JSON": json.dumps(
                    ["docs/superpowers/validation/productization_status_index_v1.tsv"]
                ),
            },
        ),
        "productization control plane",
    )

    non_product_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "Set-Content docs\\scratch-note.md '# note'",
        },
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_pre_tool_guard.py")],
        input=json.dumps(non_product_payload),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("non-product path emitted productization context")

    lock_text_search_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": 'rg -n "index.lock|cannot lock ref" docs .codex',
        },
        "tool_response": {
            "stdout": "docs/agent/parameter-settings.md:149:.git\\index.lock\n"
            ".codex/hooks/xic_post_tool_guard.py:181:cannot lock ref",
            "stderr": "",
        },
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(lock_text_search_payload),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("searching lock text emitted git lock warning")

    git_commit_search_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": 'rg -n "git commit" docs .codex',
        },
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_pre_tool_guard.py")],
        input=json.dumps(git_commit_search_payload),
        cwd=ROOT,
        env={
            **os.environ,
            "XIC_DOCS_PLACEMENT_GUARD_EXIT_CODE": "1",
            "XIC_DOCS_PLACEMENT_GUARD_STDOUT": "should not run",
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("searching git commit text triggered commit guard")

    git_diff_lock_text_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git diff -- .codex/hooks",
        },
        "tool_response": {
            "stdout": (
                "+fatal: Unable to create '.git/index.lock': File exists.\n"
                "+cannot lock ref 'refs/heads/example'"
            ),
            "stderr": "",
        },
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(git_diff_lock_text_payload),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("git diff lock text emitted git lock warning")

    git_diff_string_response_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git diff -- .codex/hooks",
        },
        "tool_response": (
            "Exit code: 0\n"
            "Output:\n"
            "+fatal: Unable to create '.git/index.lock': File exists.\n"
            "+cannot lock ref 'refs/heads/example'"
        ),
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(git_diff_string_response_payload),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError(
            "successful git diff string response emitted git lock warning"
        )

    real_git_lock_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git status --short",
        },
        "tool_response": {
            "stdout": "",
            "stderr": (
                "fatal: Unable to create '.git/index.lock': File exists.\n"
                "Another git process seems to be running in this repository."
            ),
        },
    }
    assert_contains(
        run_hook("xic_post_tool_guard.py", real_git_lock_payload),
        "Git lock/ref-lock friction detected",
    )

    git_commit_autostage_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git commit -am docs",
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", git_commit_autostage_payload),
        "git commit with -a/--all or pathspec is blocked",
    )

    git_commit_global_autostage_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git -c user.name=bot commit -am docs",
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", git_commit_global_autostage_payload),
        "git commit with -a/--all or pathspec is blocked",
    )

    git_commit_pathspec_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git commit docs/superpowers/plans/example.md -m docs",
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", git_commit_pathspec_payload),
        "git commit with -a/--all or pathspec is blocked",
    )

    git_commit_pathspec_from_file_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git commit --pathspec-from-file=paths.txt -m docs",
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", git_commit_pathspec_from_file_payload),
        "git commit with -a/--all or pathspec is blocked",
    )

    git_commit_quoted_message_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": 'git commit -m "docs update"',
        },
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_pre_tool_guard.py")],
        input=json.dumps(git_commit_quoted_message_payload),
        cwd=ROOT,
        env={
            **os.environ,
            "XIC_DOCS_PLACEMENT_GUARD_EXIT_CODE": "0",
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("quoted git commit message was treated as pathspec")

    git_commit_pwsh_wrapped_autostage_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": (
                "& 'C:\\Program Files\\PowerShell\\7\\pwsh.exe' -NoLogo "
                "-NoProfile -Command 'git commit -am docs'"
            ),
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", git_commit_pwsh_wrapped_autostage_payload),
        "git commit with -a/--all or pathspec is blocked",
    )

    git_commit_bash_lc_wrapped_autostage_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "bash -lc 'git commit -am docs'",
        },
    }
    assert_contains(
        run_hook(
            "xic_pre_tool_guard.py",
            git_commit_bash_lc_wrapped_autostage_payload,
        ),
        "git commit with -a/--all or pathspec is blocked",
    )

    git_commit_docs_guard_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git commit -m docs",
        },
    }
    assert_contains(
        run_hook(
            "xic_pre_tool_guard.py",
            git_commit_docs_guard_payload,
            env_extra={
                "XIC_DOCS_PLACEMENT_GUARD_EXIT_CODE": "1",
                "XIC_DOCS_PLACEMENT_GUARD_STDOUT": (
                    "docs placement guard failed:\n"
                    "- docs/superpowers/plans/example.md: missing marker"
                ),
            },
        ),
        "Docs placement guard failed before git commit",
    )

    git_add_docs_guard_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git add docs/superpowers/plans/example.md",
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    assert_contains(
        run_hook(
            "xic_post_tool_guard.py",
            git_add_docs_guard_payload,
            env_extra={
                "XIC_DOCS_PLACEMENT_GUARD_EXIT_CODE": "1",
                "XIC_DOCS_PLACEMENT_GUARD_STDOUT": (
                    "docs placement guard failed:\n"
                    "- docs/superpowers/plans/example.md: missing marker"
                ),
            },
        ),
        "Docs placement guard failed after git add",
    )

    git_add_code_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "git add xic_extractor/extractor.py",
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(git_add_code_payload),
        cwd=ROOT,
        env={
            **os.environ,
            "XIC_DOCS_PLACEMENT_GUARD_EXIT_CODE": "0",
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("git add for code path emitted docs placement warning")

    git_add_formal_doc_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": (
                "git add docs/product/backfill.md "
                "docs/superpowers/handoffs/current/codex-example.md"
            ),
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(git_add_formal_doc_payload),
        cwd=ROOT,
        env={
            **os.environ,
            "XIC_DOCS_PLACEMENT_GUARD_EXIT_CODE": "0",
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("formal docs git add emitted docs placement warning")

    apply_patch_mentions_git_add_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": (
                "*** Update File: docs/agent/example.md\n"
                "+The hook checks git add after staging.\n"
            ),
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(apply_patch_mentions_git_add_payload),
        cwd=ROOT,
        env={
            **os.environ,
            "XIC_DOCS_PLACEMENT_GUARD_EXIT_CODE": "1",
            "XIC_DOCS_PLACEMENT_GUARD_STDOUT": "should not run",
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("apply_patch text triggered docs placement guard")

    unrelated_write_with_ambient_product_dirty_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": "Set-Content docs\\scratch-note.md '# note'",
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(unrelated_write_with_ambient_product_dirty_payload),
        cwd=ROOT,
        env={
            **os.environ,
            "XIC_POST_TOOL_GUARD_CHANGED_PATHS_JSON": json.dumps(
                ["xic_extractor/output/schema.py"]
            ),
            "XIC_POST_TOOL_GUARD_HANDOFF_LINE_COUNT": "250",
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError("unrelated write emitted ambient product dirty warning")

    product_with_control_plane_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": (
                "*** Update File: xic_extractor/output/schema.py\n"
                "*** Update File: docs/superpowers/plans/"
                "2026-06-15-productization-control-plane.md\n"
            ),
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    assert_contains(
        run_hook(
            "xic_post_tool_guard.py",
            product_with_control_plane_payload,
            env_extra={
                "XIC_POST_TOOL_GUARD_BRANCH": "codex/docs-cleanup",
                "XIC_POST_TOOL_GUARD_CHANGED_PATHS_JSON": json.dumps(
                    [
                        "xic_extractor/output/schema.py",
                        "docs/superpowers/plans/2026-06-15-productization-control-plane.md",
                    ]
                ),
            },
        ),
        "active branch handoff",
    )

    product_with_control_plane_and_handoff_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": (
                "*** Update File: xic_extractor/output/schema.py\n"
                "*** Update File: docs/superpowers/plans/"
                "2026-06-15-productization-control-plane.md\n"
                "*** Update File: docs/superpowers/handoffs/current/"
                "codex-example-branch-productization.md\n"
            ),
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(product_with_control_plane_and_handoff_payload),
        cwd=ROOT,
        env={
            **os.environ,
            "XIC_POST_TOOL_GUARD_HANDOFF_LINE_COUNT": "40",
            "XIC_POST_TOOL_GUARD_BRANCH": "codex/example-branch",
            "XIC_POST_TOOL_GUARD_CHANGED_PATHS_JSON": json.dumps(
                [
                    "xic_extractor/output/schema.py",
                    "docs/superpowers/plans/2026-06-15-productization-control-plane.md",
                    "docs/superpowers/handoffs/current/codex-example-branch-productization.md",
                ]
            ),
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError(
            "product/control-plane edit with handoff edit emitted warning"
        )

    product_with_control_plane_and_default_local_handoff_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": (
                "*** Update File: xic_extractor/output/schema.py\n"
                "*** Update File: docs/superpowers/plans/"
                "2026-06-15-productization-control-plane.md\n"
                "*** Update File: docs/superpowers/handoffs/current/"
                "ACTIVE.local.md\n"
            ),
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "xic_post_tool_guard.py")],
        input=json.dumps(product_with_control_plane_and_default_local_handoff_payload),
        cwd=ROOT,
        env={
            **os.environ,
            "XIC_POST_TOOL_GUARD_HANDOFF_LINE_COUNT": "40",
            "XIC_POST_TOOL_GUARD_BRANCH": "codex/example-branch",
            "XIC_POST_TOOL_GUARD_CHANGED_PATHS_JSON": json.dumps(
                [
                    "xic_extractor/output/schema.py",
                    "docs/superpowers/plans/2026-06-15-productization-control-plane.md",
                    "docs/superpowers/handoffs/current/ACTIVE.local.md",
                ]
            ),
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.stdout.strip():
        raise AssertionError(
            "product/control-plane edit with ACTIVE.local handoff emitted warning"
        )

    assert_contains(
        run_hook(
            "xic_post_tool_guard.py",
            product_with_control_plane_and_handoff_payload,
            env_extra={
                "XIC_POST_TOOL_GUARD_HANDOFF_LINE_COUNT": "250",
                "XIC_POST_TOOL_GUARD_BRANCH": "codex/example-branch",
                "XIC_POST_TOOL_GUARD_CHANGED_PATHS_JSON": json.dumps(
                    [
                        "xic_extractor/output/schema.py",
                        "docs/superpowers/plans/2026-06-15-productization-control-plane.md",
                        "docs/superpowers/handoffs/current/codex-example-branch-productization.md",
                    ]
                ),
            },
        ),
        "codex-example-branch-productization.md is 250 lines",
    )

    product_with_control_plane_and_anchor_payload = {
        "hook_event_name": "PostToolUse",
        "turn_id": "fixture",
        "tool_name": "apply_patch",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "patch": (
                "*** Update File: xic_extractor/output/schema.py\n"
                "*** Update File: docs/superpowers/plans/"
                "2026-06-15-productization-control-plane.md\n"
                "*** Update File: docs/superpowers/productization/status/"
                "cc-framework-improvements-productization.md\n"
            ),
        },
        "tool_response": {"stdout": "", "stderr": ""},
    }
    assert_contains(
        run_hook(
            "xic_post_tool_guard.py",
            product_with_control_plane_and_anchor_payload,
            env_extra={
                "XIC_POST_TOOL_GUARD_BRANCH": "codex/docs-cleanup",
                "XIC_POST_TOOL_GUARD_CHANGED_PATHS_JSON": json.dumps(
                    [
                        "xic_extractor/output/schema.py",
                        "docs/superpowers/plans/2026-06-15-productization-control-plane.md",
                        "docs/superpowers/productization/status/cc-framework-improvements-productization.md",
                    ]
                ),
            },
        ),
        "Productization status anchors do not satisfy",
    )

    for path in (
        "docs/user/targeted-extraction.md",
        "docs/superpowers/validation/productization_status_index_v1.tsv",
        "scripts/run_new_cli.py",
        "xic_extractor/extractor.py",
        "xic_extractor/signal_processing.py",
        "..\\xic_extractor\\output\\schema.py",
    ):
        if not mentions_any_path(path, PRODUCT_SURFACE_PATHS):
            raise AssertionError(f"public surface path did not match: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
