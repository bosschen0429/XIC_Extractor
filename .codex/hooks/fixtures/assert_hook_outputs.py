from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOKS = ROOT / ".codex" / "hooks"
sys.path.insert(0, str(HOOKS))


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
    assert_hook_silent("xic_prompt_router.py", handoff_prompt_payload)

    def router_output(prompt: str) -> str:
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "turn_id": "fixture",
            "cwd": ".",
            "permission_mode": "default",
            "prompt": prompt,
        }
        return json.dumps(run_hook("xic_prompt_router.py", payload), ensure_ascii=False)

    def assert_router_silent(prompt: str) -> None:
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "turn_id": "fixture",
            "cwd": ".",
            "permission_mode": "default",
            "prompt": prompt,
        }
        assert_hook_silent("xic_prompt_router.py", payload)

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

    reversed_perf_edit_out = router_output(
        "下一步針對 overlay RAW access 的 locality/reuse/batched extraction 繼續優化"
    )
    assert_contains(reversed_perf_edit_out, "xic-performance-diagnosis")
    assert_contains(reversed_perf_edit_out, "xic-architecture-preflight")

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
    assert_router_silent(
        "Run alignment validation using synthetic fixtures; do not read RAW."
    )
    assert_router_silent("Validate alignment with fixtures without reading RAW.")

    for ordinary_prompt in (
        "幫我 review 這個大 PR",
        "設定一個 goal 整理 focused tests",
    ):
        assert_router_silent(ordinary_prompt)

    assert_router_silent("Validate the benchmark schema using tracked TSV fixtures.")
    assert_router_silent("Fix the heartbeat renderer unit test.")

    # Stacked/superseded PR trouble keeps its narrow repo-specific route.
    stack_out = router_output(
        "這個 stacked PR series 一直 red CI，先 map stale base 再修"
    )
    assert_contains(stack_out, "xic-pr-stack-repair")

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
        "payload directly",
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
                "Select-String -Pattern "
                + json.dumps(" ".join(("git", "reset", "--hard")))
                + " -Path README.md"
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
        "single direct Git command",
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
        "single direct Git command",
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
        "single direct Git command",
    )

    conditional_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps(
                "if true; then " + " ".join(("git", "reset", "--hard")) + "; fi"
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", conditional_reset_payload),
        "single direct Git command",
    )

    condition_list_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps("if " + " ".join(("git", "reset", "--hard")) + "; then :; fi")
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", condition_list_reset_payload),
        "single direct Git command",
    )

    controlled_raw_background_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                'pwsh -Command "if ($true) { Start-Process python -ArgumentList '
                "'-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT' }\""
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", controlled_raw_background_payload),
        "payload directly",
    )

    later_wrapper_raw_background_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                'Write-Host ready; pwsh -Command "Start-Process python '
                "-ArgumentList '-m scripts.run_alignment --raw-dir "
                "$env:XIC_RAW_ROOT'\""
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", later_wrapper_raw_background_payload),
        "Background RAW launch",
    )

    evaluated_raw_background_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                "Write-Host $( Start-Process python -ArgumentList "
                "'-m scripts.run_alignment --raw-dir $env:XIC_RAW_ROOT' )"
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", evaluated_raw_background_payload),
        "Background RAW launch",
    )

    assigned_process_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                'pwsh -Command "$proc = Start-Process python -PassThru '
                "-ArgumentList '-m scripts.run_alignment --raw-dir "
                "$env:XIC_RAW_ROOT'\""
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", assigned_process_payload),
        "payload directly",
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
        "single direct Git command",
    )

    combined_flag_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -xc " + json.dumps(" ".join(("git", "reset", "--hard")))
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", combined_flag_reset_payload),
        "single direct Git command",
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
        "single direct Git command",
    )

    cmd_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "cmd /c " + json.dumps(" ".join(("git", "reset", "--hard")))
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", cmd_reset_payload),
        "single direct Git command",
    )

    exec_reset_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": "bash -lc "
            + json.dumps(
                " ".join(("exec", "-a", "safe-name", "/usr/bin/git", "reset", "--hard"))
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", exec_reset_payload),
        "single direct Git command",
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
        "single direct Git command",
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
        "payload directly",
    )

    ordered_raw_background_payload = {
        **raw_background_payload,
        "tool_input": {
            "command": (
                "pwsh -Command \"$a='-m scripts.run_alignment --raw-dir "
                "$env:XIC_RAW_ROOT'; Start-Process python -ArgumentList $a; "
                "$a='-c print(1)'\""
            )
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", ordered_raw_background_payload),
        "payload directly",
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
    assert_hook_silent("xic_pre_tool_guard.py", git_commit_quoted_message_payload)

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
        "single direct Git command",
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
        "single direct Git command",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
