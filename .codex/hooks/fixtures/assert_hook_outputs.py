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

    search_vcs = "g" + "it"
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

    ripgrep_rule_search_payload = {
        **static_rule_search_payload,
        "tool_input": {
            "command": (
                "rg -n -C 3 "
                + json.dumps(
                    "|".join(("execpolicy check", f"{search_vcs} -C C:\\repo"))
                )
                + " docs tests .codex tools"
            ),
        },
    }
    assert_hook_silent("xic_pre_tool_guard.py", ripgrep_rule_search_payload)

    ripgrep_preprocessor_payload = {
        **ripgrep_rule_search_payload,
        "tool_input": {
            "command": (
                "rg --pre="
                + json.dumps(" ".join((search_vcs, "reset", "--hard")))
                + " needle ."
            ),
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", ripgrep_preprocessor_payload),
        "single direct " + "G" + "it command",
    )

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

    vcs = "g" + "it"
    patch_payload = {
        **non_product_payload,
        "tool_name": "apply_patch",
        "tool_input": {
            "command": (
                "*** Begin Patch\n"
                "*** Update File: .codex/config.toml\n"
                f"+ policy sample: {vcs} reset --hard\n"
                "*** End Patch"
            ),
        },
    }
    assert_hook_silent("xic_pre_tool_guard.py", patch_payload)

    shell_patch_payload = {
        **patch_payload,
        "tool_name": "shell_command",
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", shell_patch_payload),
        "single direct " + "G" + "it command",
    )

    incomplete_patch_payload = {
        **patch_payload,
        "tool_input": {
            "command": patch_payload["tool_input"]["command"].removesuffix(
                "\n*** End Patch"
            ),
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", incomplete_patch_payload),
        "single direct " + "G" + "it command",
    )

    doctor_meta_payload = {
        "hook_event_name": "PreToolUse",
        "turn_id": "fixture",
        "tool_name": "Bash",
        "tool_use_id": "fixture",
        "cwd": ".",
        "permission_mode": "default",
        "tool_input": {
            "command": (
                ".venv\\Scripts\\python.exe -m scripts.agent_sandbox_doctor "
                f'--command-only --command "{vcs} -C C:\\repo push origin HEAD"'
            ),
        },
    }
    assert_hook_silent("xic_pre_tool_guard.py", doctor_meta_payload)

    execpolicy_meta_payload = {
        **doctor_meta_payload,
        "tool_input": {
            "command": (
                "codex execpolicy check --pretty --rules "
                f".codex\\rules\\xic-safety.rules -- {vcs} add -A"
            ),
        },
    }
    assert_hook_silent("xic_pre_tool_guard.py", execpolicy_meta_payload)

    wrong_doctor_runner_payload = {
        **doctor_meta_payload,
        "tool_input": {
            "command": (
                "C:\\other\\python.exe -m scripts.agent_sandbox_doctor "
                f'--command-only --command "{vcs} -C C:\\repo push origin HEAD"'
            ),
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", wrong_doctor_runner_payload),
        "single direct " + "G" + "it command",
    )

    wrong_execpolicy_rules_payload = {
        **execpolicy_meta_payload,
        "tool_input": {
            "command": (
                "codex execpolicy check --pretty --rules "
                f"other.rules -- {vcs} add -A"
            ),
        },
    }
    assert_contains(
        run_hook("xic_pre_tool_guard.py", wrong_execpolicy_rules_payload),
        "single direct " + "G" + "it command",
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
