import subprocess
from pathlib import Path

import pytest

from scripts import agent_sandbox_doctor


def _codes(command: str, workspace_root: Path, allowed_root: Path) -> set[str]:
    findings = agent_sandbox_doctor.classify_command(
        command,
        workspace_root=workspace_root,
        allowed_write_roots=(allowed_root,),
    )
    return {finding.code for finding in findings}


def test_classify_powershell_antipatterns(tmp_path: Path) -> None:
    codes = _codes(
        "export FOO=bar && python - <<'PY'",
        workspace_root=tmp_path,
        allowed_root=tmp_path,
    )

    assert "powershell_export" in codes
    assert "powershell_chain_operator" in codes
    assert "powershell_bash_heredoc" in codes


def test_classify_85raw_background_and_bare_python_raw_runner(
    tmp_path: Path,
) -> None:
    command = (
        "Start-Process python -ArgumentList "
        "'-m scripts.run_alignment --expected-sample-count 85 "
        "--raw-dir C:\\raw --dll-dir C:\\dll'"
    )

    codes = _codes(command, workspace_root=tmp_path, allowed_root=tmp_path)

    assert "background_long_raw_run" in codes
    assert "raw_runner_not_venv" in codes


def test_classify_output_outside_allowed_roots(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    allowed_root = workspace_root
    outside = tmp_path / "external-output"
    command = (
        f'.venv\\Scripts\\python.exe -m scripts.run_alignment --output-dir "{outside}"'
    )

    findings = agent_sandbox_doctor.classify_command(
        command,
        workspace_root=workspace_root,
        allowed_write_roots=(allowed_root,),
    )

    assert [finding.code for finding in findings] == ["output_outside_writable_roots"]
    assert findings[0].severity == "review"


def test_network_command_routes_to_bounded_approval(tmp_path: Path) -> None:
    findings = agent_sandbox_doctor.classify_command(
        "git push origin HEAD",
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
    )

    assert [finding.code for finding in findings] == [
        "network_or_github_approval_needed"
    ]
    assert findings[0].disposition == "APPROVAL_REQUIRED"

    report = agent_sandbox_doctor.build_environment_report(
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
        command="git push origin HEAD",
    )

    assert report.status == "APPROVAL_REQUIRED"
    assert report.to_dict()["findings"][0]["disposition"] == "APPROVAL_REQUIRED"

    gh_findings = agent_sandbox_doctor.classify_command(
        "gh auth status",
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
    )
    assert [finding.disposition for finding in gh_findings] == ["APPROVAL_REQUIRED"]


@pytest.mark.parametrize(
    "command",
    [
        "git -c http.version=HTTP/1.1 pull --ff-only",
    ],
)
def test_git_network_command_with_prompt_prefix_requires_approval(
    command: str, tmp_path: Path
) -> None:
    findings = agent_sandbox_doctor.classify_command(
        command,
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
    )

    assert [finding.code for finding in findings] == [
        "network_or_github_approval_needed"
    ]
    assert findings[0].disposition == "APPROVAL_REQUIRED"


@pytest.mark.parametrize(
    "command",
    [
        "git -C C:\\repo push origin HEAD",
        "git --git-dir C:\\repo\\metadata push origin HEAD",
        "git --work-tree=C:\\repo --git-dir=C:\\repo\\metadata fetch origin",
        "git -C C:\\repo pull --ff-only",
        "git -C C:\\repo clone https://example.invalid/repo.git",
        "git -C C:\\repo ls-remote origin",
    ],
)
def test_git_network_command_without_prompt_prefix_requires_rewrite(
    command: str, tmp_path: Path
) -> None:
    findings = agent_sandbox_doctor.classify_command(
        command,
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
    )

    assert [finding.code for finding in findings] == [
        "git_network_prompt_prefix_mismatch"
    ]
    assert findings[0].disposition == "REWRITE_COMMAND"


@pytest.mark.parametrize(
    "command",
    [
        "cmd /c if exist .git git -C C:\\repo push origin HEAD",
        "$result = git -C C:\\repo push origin HEAD",
        'pwsh -Command "$result = git -C C:\\repo push origin HEAD"',
        "git --unknown value push origin HEAD",
        "git.exe push origin HEAD",
        "git.cmd reset --hard",
        "git.bat push origin HEAD",
        "git.ps1 clean -fd",
        "git.com status --short",
        "git.sh push origin HEAD",
        "GIT push origin HEAD",
        '"git" push origin HEAD',
        "/usr/bin/git push origin HEAD",
        'FOO=1 bash -lc "git push origin HEAD"',
        'nice bash -lc "git push origin HEAD"',
        "git push>output.log",
        'Write-Output "$(git push origin HEAD)"',
        'rg --pre "git" PATTERN push',
        "git PUSH origin HEAD",
        "git RESET --hard",
        "git CLEAN -fd",
        "git STATUS --short",
        "env -u CI git -C C:\\repo push origin HEAD",
        "FOO=1 command git --git-dir C:\\repo\\metadata push origin HEAD",
        "Write-Host ready\ngit -C C:\\repo push origin HEAD",
        'env -S "git -C C:\\repo push origin HEAD"',
        'env --split-string="git -C C:\\repo fetch origin"',
        'env -u CI -S "git -C C:\\repo push origin HEAD"',
        'env -S "git -C C:\\repo" push origin HEAD',
        "env -u CI git -C C:\\repo status --short",
        'env -S "git -C C:\\repo status --short"',
        'env --split-string="git -C C:\\repo status --short"',
        "env -S",
        "wsl echo ready",
        'bash -lc "gh pr list"',
        '& bash -lc "echo ready"',
        'FOO=1 bash -lc "echo ready"',
        'FOO="a b" bash -lc "echo ready"',
        'FOO=a\\ b bash -lc "echo ready"',
        'FOO=`echo` bash -lc "echo ready"',
        'FOO="a b" C:\\bin\\bash.exe -lc "echo ready"',
        'bash -lc "unterminated',
    ],
)
def test_unsupported_command_shape_requires_rewrite(
    command: str, tmp_path: Path
) -> None:
    findings = agent_sandbox_doctor.classify_command(
        command,
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
    )

    assert len(findings) == 1
    assert findings[0].code in {
        "ambiguous_git_command",
        "unsupported_shell_wrapper",
    }
    assert findings[0].disposition == "REWRITE_COMMAND"


@pytest.mark.parametrize(
    "command",
    [
        "git -C C:\\repo status --short",
        "Write-Host C:\\repo\\.git push",
        'Select-String -Pattern "git -C C:\\repo push" -Path README.md',
        'Select-String -Pattern "prefix\ngit -C C:\\repo push origin HEAD" '
        "-Path README.md",
        'Select-String -Pattern "env -S git -C C:\\repo push origin HEAD" '
        "-Path README.md",
        'Write-Host env -S "git -C C:\\repo push origin HEAD"',
        'Write-Host bash -lc "git -C C:\\repo push origin HEAD"',
        'Write-Output "git push origin HEAD"',
        'echo "git push origin HEAD"',
        "Get-ChildItem Env:PATH",
        "rg command docs",
        "python env\\script.py",
        "FOO=1 python env\\script.py",
        "python tool.py --shell=bash",
        "python tool.py --label=env",
        "SHELL=bash python tool.py",
        "$result = python tool.py --shell=bash",
        "& python tool.py --shell=bash",
        "[ $x = bash ]",
        "[[ $x = bash ]]",
        '$runner = "bash"',
        '[string]$runner = "bash"',
        '$runner="bash"',
        'python tool.py ";" bash',
        'python tool.py --separator=";" bash',
        '$runner = bash -lc "echo ready"',
        '[string]$runner = & "bash" -lc "echo ready"',
        'Write-Host ready; bash -lc "echo ready"',
        'true; 2>&1 bash -lc "echo ready"',
        '>trace.log bash -lc "echo ready"',
        '&>trace.log bash -lc "echo ready"',
        "Get-Command bash",
    ],
)
def test_non_network_git_text_does_not_require_approval(
    command: str, tmp_path: Path
) -> None:
    findings = agent_sandbox_doctor.classify_command(
        command,
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
    )

    assert findings == ()


@pytest.mark.parametrize(
    "command",
    [
        "uv lock",
        "uv sync --extra dev --group dev",
        "uv run --with playwright python --version",
        "uv run python -m playwright install chromium",
    ],
)
def test_documented_approval_first_commands_require_approval(
    command: str, tmp_path: Path
) -> None:
    findings = agent_sandbox_doctor.classify_command(
        command,
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
    )

    assert [finding.code for finding in findings] == [
        "network_or_github_approval_needed"
    ]
    assert findings[0].disposition == "APPROVAL_REQUIRED"


def test_documented_venv_alignment_command_is_clean(tmp_path: Path) -> None:
    command = (
        ".venv\\Scripts\\python.exe -m scripts.run_alignment "
        "--raw-dir C:\\raw --dll-dir C:\\dll --output-dir output\\alignment "
        "--timing-live-output output\\timing.live.json"
    )

    findings = agent_sandbox_doctor.classify_command(
        command,
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
    )

    assert findings == ()


def test_main_strict_json_output_reports_blockers(
    tmp_path: Path,
    capsys,
) -> None:
    output = tmp_path / "doctor.json"

    code = agent_sandbox_doctor.main(
        [
            "--workspace-root",
            str(tmp_path),
            "--allowed-write-root",
            str(tmp_path),
            "--command",
            "python -m scripts.run_alignment --raw-dir C:\\raw --dll-dir C:\\dll",
            "--json-output",
            str(output),
            "--command-only",
            "--strict",
        ]
    )

    stdout = capsys.readouterr().out
    assert code == 2
    assert "REWRITE_COMMAND" in stdout
    assert "raw_runner_not_venv" in output.read_text(encoding="utf-8")


def test_default_allowed_write_roots_are_absolute(tmp_path: Path) -> None:
    roots = agent_sandbox_doctor._default_allowed_write_roots(tmp_path)

    assert roots == (tmp_path.resolve(),)
    assert all(root.is_absolute() for root in roots)


def test_probe_write_does_not_clobber_existing_probe_file(tmp_path: Path) -> None:
    existing = tmp_path / ".agent_sandbox_doctor_probe"
    existing.write_text("keep me\n", encoding="utf-8")

    report = agent_sandbox_doctor.build_environment_report(
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
        probe_write=True,
    )

    assert report.status == "READY"
    assert existing.read_text(encoding="utf-8") == "keep me\n"


def test_runtime_owner_rejects_wsl_for_windows_owned_checkout() -> None:
    findings = agent_sandbox_doctor._inspect_runtime_owner(
        os_name="posix",
        environ={"WSL_DISTRO_NAME": "Ubuntu"},
    )

    assert [finding.code for finding in findings] == ["runtime_owner_mismatch"]
    assert findings[0].severity == "blocker"


def test_python_runtime_requires_supported_worktree_interpreter(
    tmp_path: Path,
) -> None:
    expected = tmp_path / ".venv" / "Scripts" / "python.exe"

    outside_findings = agent_sandbox_doctor._inspect_python_runtime(
        workspace_root=tmp_path,
        os_name="nt",
        executable=Path("C:/Python314/python.exe"),
        version=(3, 14, 0),
    )
    supported_findings = agent_sandbox_doctor._inspect_python_runtime(
        workspace_root=tmp_path,
        os_name="nt",
        executable=expected,
        version=(3, 12, 8),
    )

    assert {finding.code for finding in outside_findings} == {
        "python_outside_worktree_venv",
        "unsupported_python_version",
    }
    assert supported_findings == ()


def test_git_runtime_classifies_global_config_acl_failure(
    tmp_path: Path,
) -> None:
    def denied_git(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr=(
                "fatal: unable to read config file "
                "'C:/Users/operator/.gitconfig': Permission denied"
            ),
        )

    findings = agent_sandbox_doctor._inspect_git_runtime(
        workspace_root=tmp_path,
        probe_write=False,
        run=denied_git,
    )

    assert [finding.code for finding in findings] == ["git_global_config_unreadable"]
    assert findings[0].severity == "blocker"
    assert findings[0].disposition == "APPROVAL_REQUIRED"
    assert "approve for me" in findings[0].next_action.lower()
    assert "repo-local permission profile" in findings[0].next_action.lower()


def test_github_probe_skips_fail_first_when_sandbox_network_is_disabled(
    tmp_path: Path,
) -> None:
    def unexpected_git(*args, **kwargs):
        raise AssertionError("network-disabled runtime must not launch git ls-remote")

    findings = agent_sandbox_doctor._inspect_github_runtime(
        workspace_root=tmp_path,
        environ={"CODEX_SANDBOX_NETWORK_DISABLED": "1"},
        run=unexpected_git,
    )

    assert [finding.code for finding in findings] == [
        "github_network_approval_required"
    ]
    assert findings[0].disposition == "APPROVAL_REQUIRED"
    assert "credential" in findings[0].next_action.lower()


def test_github_probe_distinguishes_dns_from_auth_failure(tmp_path: Path) -> None:
    outcomes = iter(
        (
            subprocess.CompletedProcess(
                ["git", "ls-remote"],
                128,
                stdout="",
                stderr=(
                    "fatal: unable to access 'https://github.com/org/repo.git/': "
                    "Could not resolve host: github.com"
                ),
            ),
            subprocess.CompletedProcess(
                ["git", "ls-remote"],
                128,
                stdout="",
                stderr=(
                    "remote: Invalid username or password.\n"
                    "fatal: Authentication failed for "
                    "'https://github.com/org/private-repo.git/'"
                ),
            ),
        )
    )

    def fake_git(*args, **kwargs):
        return next(outcomes)

    dns_findings = agent_sandbox_doctor._inspect_github_runtime(
        workspace_root=tmp_path,
        environ={},
        run=fake_git,
    )
    auth_findings = agent_sandbox_doctor._inspect_github_runtime(
        workspace_root=tmp_path,
        environ={},
        run=fake_git,
    )

    assert [finding.code for finding in dns_findings] == ["github_dns_failed"]
    assert dns_findings[0].disposition == "ENVIRONMENT_REPAIR_REQUIRED"
    assert "credential" in dns_findings[0].next_action.lower()
    assert [finding.code for finding in auth_findings] == [
        "github_auth_or_access_failed"
    ]
    assert auth_findings[0].disposition == "HUMAN_DECISION_REQUIRED"


def test_github_connection_failure_does_not_claim_invalid_credentials(
    tmp_path: Path,
) -> None:
    def connection_failed(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            128,
            stdout="",
            stderr=(
                "fatal: unable to access "
                "'https://github.com/Chao-hu-Lab/XIC_Extractor.git/': "
                "Failed to connect to github.com port 443: Could not connect "
                "to server"
            ),
        )

    findings = agent_sandbox_doctor._inspect_github_runtime(
        workspace_root=tmp_path,
        environ={},
        run=connection_failed,
    )

    assert [finding.code for finding in findings] == ["github_connection_failed"]
    assert findings[0].disposition == "ENVIRONMENT_REPAIR_REQUIRED"
    assert "do not change credentials" in findings[0].next_action.lower()


def test_git_runtime_bounds_status_canary(tmp_path: Path) -> None:
    def timed_out_git(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=10)

    findings = agent_sandbox_doctor._inspect_git_runtime(
        workspace_root=tmp_path,
        probe_write=False,
        run=timed_out_git,
    )

    assert [finding.code for finding in findings] == ["git_canary_timed_out"]


def test_git_runtime_requires_shared_local_identity(tmp_path: Path) -> None:
    def git_without_identity(*args, **kwargs):
        command = args[0]
        return subprocess.CompletedProcess(
            command,
            0 if command[1] == "status" else 1,
            stdout="",
            stderr="",
        )

    findings = agent_sandbox_doctor._inspect_git_runtime(
        workspace_root=tmp_path,
        probe_write=False,
        run=git_without_identity,
    )

    assert [finding.code for finding in findings] == ["git_local_identity_missing"]


def test_git_write_probe_has_hard_timeout(tmp_path: Path) -> None:
    def timed_out_probe(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=10)

    findings = agent_sandbox_doctor._run_bounded_git_write_probe(
        tmp_path,
        run=timed_out_probe,
    )

    assert [finding.code for finding in findings] == ["git_metadata_write_timed_out"]


def test_runtime_report_blocks_when_bootstrap_is_not_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        agent_sandbox_doctor,
        "_inspect_runtime_owner",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        agent_sandbox_doctor,
        "_inspect_python_runtime",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        agent_sandbox_doctor,
        "_inspect_git_runtime",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        agent_sandbox_doctor,
        "_inspect_bootstrap_state",
        lambda workspace_root: (
            agent_sandbox_doctor.Finding(
                severity="blocker",
                code="bootstrap_not_ready",
                message="bootstrap stamp is missing or invalid",
                next_action="Run the canonical bootstrap command.",
            ),
        ),
    )

    report = agent_sandbox_doctor.build_environment_report(
        workspace_root=tmp_path,
        allowed_write_roots=(tmp_path,),
        runtime_check=True,
    )

    assert report.status == "ENVIRONMENT_REPAIR_REQUIRED"
    assert [finding.code for finding in report.findings] == ["bootstrap_not_ready"]
