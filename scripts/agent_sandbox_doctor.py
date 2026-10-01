from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from scripts import bootstrap_worktree

_APPROVAL_REQUIRED_CODES = frozenset(
    {
        "git_global_config_unreadable",
        "github_network_approval_required",
    }
)
_HUMAN_DECISION_REQUIRED_CODES = frozenset(
    {
        "github_auth_or_access_failed",
    }
)


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    next_action: str

    @property
    def disposition(self) -> str:
        if self.code in _APPROVAL_REQUIRED_CODES:
            return "APPROVAL_REQUIRED"
        if self.code in _HUMAN_DECISION_REQUIRED_CODES:
            return "HUMAN_DECISION_REQUIRED"
        if self.severity == "blocker":
            return "ENVIRONMENT_REPAIR_REQUIRED"
        return "INCONCLUSIVE"


@dataclass(frozen=True)
class DoctorReport:
    status: str
    workspace_root: str
    allowed_write_roots: tuple[str, ...]
    findings: tuple[Finding, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["findings"] = [
            {**asdict(finding), "disposition": finding.disposition}
            for finding in self.findings
        ]
        return payload


def build_environment_report(
    *,
    workspace_root: Path,
    allowed_write_roots: Sequence[Path],
    probe_write: bool = False,
    runtime_check: bool = False,
    probe_git_write: bool = False,
    probe_github: bool = False,
) -> DoctorReport:
    resolved_workspace = workspace_root.resolve()
    resolved_roots = tuple(root.resolve() for root in allowed_write_roots)
    findings: list[Finding] = []
    if probe_write:
        findings.extend(_probe_write_roots(resolved_roots))
    if runtime_check:
        findings.extend(
            _collect_runtime_findings(
                workspace_root=resolved_workspace,
                probe_git_write=probe_git_write,
                probe_github=probe_github,
            )
        )

    status = _report_status(findings)

    return DoctorReport(
        status=status,
        workspace_root=str(resolved_workspace),
        allowed_write_roots=tuple(str(root) for root in resolved_roots),
        findings=tuple(findings),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    workspace_root = args.workspace_root.resolve()
    allowed_write_roots = (
        tuple(root.resolve() for root in args.allowed_write_root)
        if args.allowed_write_root
        else _default_allowed_write_roots(workspace_root)
    )
    report = build_environment_report(
        workspace_root=workspace_root,
        allowed_write_roots=allowed_write_roots,
        probe_write=args.probe_write,
        runtime_check=True,
        probe_git_write=args.probe_git_write,
        probe_github=args.probe_github,
    )
    _print_report(report)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    if not args.strict:
        return 0
    if any(finding.severity == "blocker" for finding in report.findings):
        return 2
    if any(finding.severity == "review" for finding in report.findings):
        return 1
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check the active worktree environment, Python runner, and "
            "filesystem access before project execution."
        )
    )
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--allowed-write-root",
        action="append",
        type=Path,
        default=None,
        help=(
            "Allowed write root. Repeatable. Defaults to the active workspace."
        ),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional JSON report path.",
    )
    parser.add_argument(
        "--probe-write",
        action="store_true",
        help="Create and remove tiny probe files in allowed write roots.",
    )
    parser.add_argument(
        "--probe-git-write",
        action="store_true",
        help=(
            "Create and remove a uniquely named probe file in the Git common "
            "directory after the read-only Git canary passes."
        ),
    )
    parser.add_argument(
        "--probe-github",
        action="store_true",
        help=(
            "Check whether the current Codex runtime can reach the GitHub "
            "origin. Skips fail-first network access when the runtime advertises "
            "that sandbox networking is disabled."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero for blocker/review findings.",
    )
    return parser.parse_args(argv)


def _print_report(report: DoctorReport) -> None:
    print(f"Runtime doctor status: {report.status}")
    print(f"Workspace root: {report.workspace_root}")
    print("Allowed write roots:")
    for root in report.allowed_write_roots:
        print(f"  - {root}")
    if not report.findings:
        print("Findings: none")
        return
    print("Findings:")
    for finding in report.findings:
        print(
            f"  - [{finding.severity}/{finding.disposition}] "
            f"{finding.code}: {finding.message}"
        )
        print(f"    next: {finding.next_action}")


def _report_status(findings: Sequence[Finding]) -> str:
    dispositions = {finding.disposition for finding in findings}
    for status in (
        "HUMAN_DECISION_REQUIRED",
        "ENVIRONMENT_REPAIR_REQUIRED",
        "APPROVAL_REQUIRED",
        "INCONCLUSIVE",
    ):
        if status in dispositions:
            return status
    return "READY"


def _default_allowed_write_roots(workspace_root: Path) -> tuple[Path, ...]:
    # External temp roots vary across the desktop app, CLI, elevated Windows
    # sandbox users, and WSL. Treat only the active worktree as portable; callers
    # can opt into an additional root when their current session advertises one.
    return (workspace_root.resolve(),)


def _probe_write_roots(roots: Sequence[Path]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for root in roots:
        try:
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                prefix=".agent_sandbox_doctor_probe_",
                dir=root,
                delete=False,
            ) as handle:
                probe = Path(handle.name)
                handle.write("ok\n")
            probe.unlink()
        except OSError as exc:
            findings.append(
                Finding(
                    severity="review",
                    code="write_probe_failed",
                    message=f"Cannot write probe file under {root}: {exc}",
                    next_action=(
                        "Move outputs into the active worktree or request a narrow "
                        "approval for the specific external write."
                    ),
                )
            )
    return tuple(findings)


def _collect_runtime_findings(
    *,
    workspace_root: Path,
    probe_git_write: bool,
    probe_github: bool,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    findings.extend(_inspect_runtime_owner(os_name=os.name, environ=os.environ))
    findings.extend(
        _inspect_python_runtime(
            workspace_root=workspace_root,
            os_name=os.name,
            executable=Path(sys.executable),
            version=sys.version_info[:3],
        )
    )
    findings.extend(_inspect_bootstrap_state(workspace_root))
    git_findings: tuple[Finding, ...] = ()
    git_findings = _inspect_git_runtime(
        workspace_root=workspace_root,
        probe_write=probe_git_write,
    )
    findings.extend(git_findings)
    if probe_github and (
        not git_findings or _codex_sandbox_network_disabled(os.environ)
    ):
        findings.extend(
            _inspect_github_runtime(
                workspace_root=workspace_root,
                environ=os.environ,
            )
        )
    return tuple(findings)


def _codex_sandbox_network_disabled(environ: Mapping[str, str]) -> bool:
    return environ.get("CODEX_SANDBOX_NETWORK_DISABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _inspect_runtime_owner(
    *,
    os_name: str,
    environ: Mapping[str, str],
) -> tuple[Finding, ...]:
    if os_name == "nt":
        return ()
    runtime = (
        "WSL"
        if any(name in environ for name in ("WSL_DISTRO_NAME", "WSL_INTEROP"))
        else "non-Windows"
    )
    return (
        Finding(
            severity="blocker",
            code="runtime_owner_mismatch",
            message=(
                f"Detected {runtime}, but this checkout's local operator runtime "
                "is Windows native."
            ),
            next_action=(
                "Run the acceptance command from Windows PowerShell. Use WSL only "
                "for a task-specific canary with its own environment and paths."
            ),
        ),
    )


def _inspect_python_runtime(
    *,
    workspace_root: Path,
    os_name: str,
    executable: Path,
    version: Sequence[int],
) -> tuple[Finding, ...]:
    expected = (
        workspace_root / ".venv" / "Scripts" / "python.exe"
        if os_name == "nt"
        else workspace_root / ".venv" / "bin" / "python"
    )
    findings: list[Finding] = []
    if os.path.normcase(str(executable.resolve())) != os.path.normcase(
        str(expected.resolve())
    ):
        findings.append(
            Finding(
                severity="blocker",
                code="python_outside_worktree_venv",
                message=(
                    f"Doctor is running with {executable}, not the active "
                    f"worktree interpreter {expected}."
                ),
                next_action=(
                    "Run `.venv\\Scripts\\python.exe -m "
                    "scripts.agent_sandbox_doctor --strict` from this worktree."
                ),
            )
        )
    major_minor = tuple(version[:2])
    if not ((3, 11) <= major_minor < (3, 14)):
        findings.append(
            Finding(
                severity="blocker",
                code="unsupported_python_version",
                message=(
                    "Python "
                    + ".".join(str(part) for part in version[:3])
                    + " is outside the project contract >=3.11,<3.14."
                ),
                next_action=(
                    "Run the canonical worktree bootstrap, then use that "
                    "worktree's .venv interpreter."
                ),
            )
        )
    return tuple(findings)


def _inspect_bootstrap_state(workspace_root: Path) -> tuple[Finding, ...]:
    try:
        state = bootstrap_worktree.inspect_state(workspace_root)
    except (OSError, ValueError) as exc:
        return (
            Finding(
                severity="review",
                code="bootstrap_check_inconclusive",
                message=f"Could not inspect the worktree bootstrap state: {exc}",
                next_action=(
                    "Confirm pyproject.toml and uv.lock are readable, then rerun "
                    "the canonical bootstrap check once."
                ),
            ),
        )
    if state.ready:
        return ()
    return (
        Finding(
            severity="blocker",
            code="bootstrap_not_ready",
            message=state.reason,
            next_action=(
                "Run `uv run --cache-dir .uv-cache --no-project python "
                "scripts/bootstrap_worktree.py` once, then rerun the doctor."
            ),
        ),
    )


_CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _inspect_git_runtime(
    *,
    workspace_root: Path,
    probe_write: bool,
    run: _CommandRunner = subprocess.run,
) -> tuple[Finding, ...]:
    try:
        status = run(
            ["git", "status", "--short", "--untracked-files=no"],
            cwd=workspace_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return (
            Finding(
                severity="blocker",
                code="git_canary_timed_out",
                message="git status did not finish within 10 seconds.",
                next_action=(
                    "Inspect Git config, filesystem ownership, and sandbox ACLs; "
                    "do not rerun the same command without a new hypothesis."
                ),
            ),
        )
    except OSError as exc:
        return (
            Finding(
                severity="blocker",
                code="git_unavailable",
                message=f"Git canary could not start: {exc}",
                next_action="Install or repair Git on PATH before product work.",
            ),
        )
    if status.returncode != 0:
        detail = (status.stderr or status.stdout).strip()
        lowered = detail.lower()
        if ".gitconfig" in lowered and (
            "permission denied" in lowered or "access is denied" in lowered
        ):
            code = "git_global_config_unreadable"
            next_action = (
                "Request Approve for me for the same exact Git command. "
                "Do not add a repo-local permission profile or shell-environment "
                "override."
            )
        else:
            code = "git_canary_failed"
            next_action = (
                "Classify the exact Git/cwd/worktree error before retrying; do "
                "not infer a source-code regression."
            )
        return (
            Finding(
                severity="blocker",
                code=code,
                message=detail or f"git status exited {status.returncode}",
                next_action=next_action,
            ),
        )
    try:
        identity = run(
            [
                "git",
                "config",
                "--local",
                "--get-regexp",
                r"^user\.(name|email)$",
            ],
            cwd=workspace_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            Finding(
                severity="blocker",
                code="git_local_identity_check_failed",
                message=f"Local Git identity check failed: {exc}",
                next_action="Inspect Git runtime and shared local config once.",
            ),
        )
    identity_keys = {
        line.split(maxsplit=1)[0]
        for line in identity.stdout.splitlines()
        if line.strip()
    }
    if identity.returncode != 0 or identity_keys != {"user.name", "user.email"}:
        return (
            Finding(
                severity="blocker",
                code="git_local_identity_missing",
                message=(
                    "Shared local Git config does not contain both user.name and "
                    "user.email."
                ),
                next_action=(
                    "Outside the sandbox, copy the existing global identity into "
                    "this repository's shared local Git config; do not commit "
                    "identity values to the repository."
                ),
            ),
        )
    if not probe_write:
        return ()
    return _probe_git_common_dir_write(workspace_root=workspace_root, run=run)


def _inspect_github_runtime(
    *,
    workspace_root: Path,
    environ: Mapping[str, str],
    run: _CommandRunner = subprocess.run,
) -> tuple[Finding, ...]:
    if _codex_sandbox_network_disabled(environ):
        return (
            Finding(
                severity="review",
                code="github_network_approval_required",
                message=(
                    "The current Codex runtime advertises disabled sandbox "
                    "networking, so the GitHub canary was not launched."
                ),
                next_action=(
                    "Request narrow network approval for the intended GitHub "
                    "command and run that same command once. Do not refresh or "
                    "replace credentials based on an offline canary."
                ),
            ),
        )

    try:
        completed = run(
            ["git", "ls-remote", "--heads", "origin"],
            cwd=workspace_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return (
            Finding(
                severity="blocker",
                code="github_probe_timed_out",
                message="GitHub connectivity canary exceeded 15 seconds.",
                next_action=(
                    "Inspect the active network profile or sandbox log once. Do "
                    "not change GitHub credentials based on a timeout."
                ),
            ),
        )
    except OSError as exc:
        return (
            Finding(
                severity="blocker",
                code="github_probe_unavailable",
                message=f"GitHub connectivity canary could not start: {exc}",
                next_action=(
                    "Repair the local Git runtime before diagnosing network or "
                    "credentials."
                ),
            ),
        )

    if completed.returncode == 0:
        return ()

    detail = (completed.stderr or completed.stdout).strip()
    lowered = detail.lower()
    if "could not resolve host" in lowered or "could not resolve hostname" in lowered:
        code = "github_dns_failed"
        next_action = (
            "Inspect the active sandbox network profile, DNS, or proxy and rerun "
            "this bounded canary only after that state changes. Do not change "
            "credentials for a name-resolution failure."
        )
    elif any(
        marker in lowered
        for marker in (
            "authentication failed",
            "invalid username or password",
            "could not read username",
            "terminal prompts disabled",
            "repository not found",
        )
    ):
        code = "github_auth_or_access_failed"
        next_action = (
            "Verify the origin exists and the current GitHub credential has "
            "repository access. This is not evidence of a sandbox network block."
        )
    elif any(
        marker in lowered
        for marker in (
            "failed to connect",
            "could not connect to server",
            "connection refused",
            "connection timed out",
        )
    ):
        code = "github_connection_failed"
        next_action = (
            "Networking was expected but GitHub could not be reached. Restart "
            "Codex or inspect the sandbox log before one bounded retry; do not "
            "change credentials for a connection failure."
        )
    else:
        code = "github_probe_failed"
        next_action = (
            "Classify the exact GitHub origin error before retrying. Do not infer "
            "credential failure from an unclassified canary."
        )
    return (
        Finding(
            severity=(
                "blocker" if code != "github_auth_or_access_failed" else "review"
            ),
            code=code,
            message=detail or f"GitHub canary exited {completed.returncode}.",
            next_action=next_action,
        ),
    )


def _probe_git_common_dir_write(
    *,
    workspace_root: Path,
    run: _CommandRunner,
) -> tuple[Finding, ...]:
    try:
        common_dir_result = run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=workspace_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return (
            Finding(
                severity="blocker",
                code="git_common_dir_timed_out",
                message="Git common-directory lookup exceeded 10 seconds.",
                next_action="Inspect worktree Git metadata and sandbox ACLs.",
            ),
        )
    except OSError as exc:
        return (
            Finding(
                severity="blocker",
                code="git_common_dir_unresolved",
                message=f"Git common directory lookup failed: {exc}",
                next_action="Inspect the worktree .git pointer and Git metadata.",
            ),
        )
    if common_dir_result.returncode != 0:
        detail = (common_dir_result.stderr or common_dir_result.stdout).strip()
        return (
            Finding(
                severity="blocker",
                code="git_common_dir_unresolved",
                message=detail or "Git common directory could not be resolved.",
                next_action="Inspect the worktree .git pointer and Git metadata.",
            ),
        )

    common_dir = Path(common_dir_result.stdout.strip())
    return _run_bounded_git_write_probe(common_dir)


def _run_bounded_git_write_probe(
    common_dir: Path,
    *,
    run: _CommandRunner = subprocess.run,
) -> tuple[Finding, ...]:
    probe = common_dir / f"codex-runtime-write-probe-{uuid.uuid4().hex}"
    probe_script = (
        "from pathlib import Path; import sys; "
        "p=Path(sys.argv[1]); "
        "p.write_text('ok\\n', encoding='utf-8'); "
        "p.unlink()"
    )
    try:
        completed = run(
            [sys.executable, "-c", probe_script, str(probe)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return (
            Finding(
                severity="blocker",
                code="git_metadata_write_timed_out",
                message=(
                    f"Disposable Git metadata write did not finish within 10 "
                    f"seconds under {common_dir}."
                ),
                next_action=(
                    "Treat this as a Windows sandbox/ACL block. Verify the exact "
                    "Git common-dir grant outside the current nested sandbox."
                ),
            ),
        )
    except OSError as exc:
        return (
            Finding(
                severity="blocker",
                code="git_metadata_write_denied",
                message=f"Cannot write a disposable probe in {common_dir}: {exc}",
                next_action=(
                    "Grant write only to this repository's Git common directory; "
                    "do not broaden the sandbox globally."
                ),
            ),
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return (
            Finding(
                severity="blocker",
                code="git_metadata_write_denied",
                message=(
                    detail
                    or f"Disposable Git metadata write exited {completed.returncode}."
                ),
                next_action=(
                    "Grant write only to this repository's Git common directory; "
                    "do not broaden the sandbox globally."
                ),
            ),
        )
    return ()


if __name__ == "__main__":
    raise SystemExit(main())
