from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from xic_extractor.alignment.identity_coherence_validation import (
    decoy_manifest_proposal as _decoy_manifest_proposal,
)
from xic_extractor.alignment.identity_coherence_validation import (
    outputs as _outputs,
)
from xic_extractor.alignment.identity_coherence_validation.acceptance import (
    sidecar_parity_failed_count,
)
from xic_extractor.alignment.identity_coherence_validation.bundle import (
    bundle_from_output_dir,
)
from xic_extractor.alignment.identity_coherence_validation.compare import (
    compare_identity_coherence_bundles,
)
from xic_extractor.alignment.identity_coherence_validation.models import (
    V04_ACCEPTANCE_PASS_PREFIX,
    CommandRunner,
    RunMetadata,
    ValidationResult,
)

write_decoy_manifest_proposal = _decoy_manifest_proposal.write_decoy_manifest_proposal
write_validation_outputs = _outputs.write_validation_outputs


def build_alignment_command(
    *,
    mode: str,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    controls_manifest: Path | None,
) -> list[str]:
    if mode not in {"serial", "process"}:
        raise ValueError("mode must be 'serial' or 'process'")
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "run_alignment.py"),
        "--discovery-batch-index",
        str(discovery_batch_index),
        "--raw-dir",
        str(raw_dir),
        "--dll-dir",
        str(dll_dir),
        "--output-dir",
        str(output_dir),
        "--emit-identity-coherence-diagnostic",
        "--output-level",
        "validation",
    ]
    if mode == "serial":
        command.extend(
            [
                "--raw-workers",
                "1",
                "--raw-xic-batch-size",
                "1",
            ]
        )
    else:
        command.extend(["--performance-profile", "validation-fast"])
    if controls_manifest is not None:
        command.extend(
            ["--identity-coherence-controls-manifest", str(controls_manifest)]
        )
    return command


def run_validation(
    *,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    output_root: Path,
    controls_manifest: Path | None,
    controls_manifest_proposal: Path | None = None,
    runner: CommandRunner | None = None,
) -> ValidationResult:
    output_root.mkdir(parents=True, exist_ok=True)
    runner = runner or _run_command
    serial_output = output_root / "serial"
    process_output = output_root / "process"
    _reset_run_dir(serial_output)
    _reset_run_dir(process_output)
    metadata: list[RunMetadata] = []
    for mode, output_dir in (
        ("serial", serial_output),
        ("process", process_output),
    ):
        command = build_alignment_command(
            mode=mode,
            discovery_batch_index=discovery_batch_index,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            output_dir=output_dir,
            controls_manifest=controls_manifest,
        )
        completed = runner(command)
        metadata.append(
            RunMetadata(
                mode=mode,
                command_line=subprocess.list2cmdline(command),
                output_dir=output_dir,
                returncode=completed.returncode,
            )
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"{mode} alignment failed with exit code {completed.returncode}: "
                f"command={subprocess.list2cmdline(command)} "
                f"stdout={_tail(completed.stdout)} "
                f"stderr={_tail(completed.stderr)}"
            )
    result = compare_identity_coherence_bundles(
        bundle_from_output_dir(serial_output),
        bundle_from_output_dir(process_output),
        controls_manifest=controls_manifest,
    )
    if controls_manifest_proposal is not None:
        if result.failed_count:
            # Proposal generation stays conservative: if any existing validation
            # row is already failed, do not emit a new proposed controls manifest.
            controls_manifest_proposal.unlink(missing_ok=True)
        else:
            write_decoy_manifest_proposal(
                bundle_from_output_dir(serial_output),
                controls_manifest_proposal,
            )
    return ValidationResult(rows=result.rows, run_metadata=tuple(metadata))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run serial-vs-process 8RAW validation for the identity coherence "
            "diagnostic sidecar."
        )
    )
    parser.add_argument("--discovery-batch-index", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--controls-manifest", type=Path)
    parser.add_argument("--write-controls-manifest-proposal", type=Path)
    parser.add_argument(
        "--require-v04-acceptance",
        action="store_true",
        help=(
            "Exit non-zero unless serial/process parity, reviewed controls, "
            "positive controls, and identity decoys pass V0.4 acceptance."
        ),
    )
    args = parser.parse_args(argv)

    if not args.discovery_batch_index.is_file():
        print(
            f"{args.discovery_batch_index}: discovery batch index does not exist",
            file=sys.stderr,
        )
        return 2
    if not args.raw_dir.is_dir():
        print(f"{args.raw_dir}: raw directory does not exist", file=sys.stderr)
        return 2
    if not args.dll_dir.is_dir():
        print(f"{args.dll_dir}: dll directory does not exist", file=sys.stderr)
        return 2
    if (
        args.controls_manifest is not None
        and ".proposed." in args.controls_manifest.name.lower()
    ):
        print(
            "proposal manifests must be reviewed and renamed before use as "
            "--controls-manifest",
            file=sys.stderr,
        )
        return 2
    if args.controls_manifest is not None and not args.controls_manifest.is_file():
        print(
            f"{args.controls_manifest}: controls manifest does not exist",
            file=sys.stderr,
        )
        return 2

    try:
        result = run_validation(
            discovery_batch_index=args.discovery_batch_index,
            raw_dir=args.raw_dir,
            dll_dir=args.dll_dir,
            output_root=args.output_root,
            controls_manifest=args.controls_manifest,
            controls_manifest_proposal=args.write_controls_manifest_proposal,
        )
        acceptance = write_validation_outputs(
            output_root=args.output_root,
            result=result,
            controls_manifest=args.controls_manifest,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sidecar_failed_count = sidecar_parity_failed_count(result)
    if sidecar_failed_count:
        print(
            "FAIL identity_coherence_sidecar_parity "
            f"failed={sidecar_failed_count}",
            file=sys.stderr,
        )
        return 1
    acceptance_rows = {row.criterion: row for row in acceptance.rows}
    final_acceptance = acceptance_rows["v04_acceptance"]
    if args.require_v04_acceptance and not acceptance.accepted:
        print(
            "FAIL identity_coherence_v04_acceptance "
            f"reason={final_acceptance.evidence} "
            f"summary={args.output_root / 'identity_coherence_v04_acceptance.md'}",
            file=sys.stderr,
        )
        return 1
    print(
        "PASS identity_coherence_sidecar_parity "
        f"summary={args.output_root / 'identity_coherence_8raw_validation_report.md'}"
    )
    if acceptance.accepted:
        print(
            f"{V04_ACCEPTANCE_PASS_PREFIX} "
            f"summary={args.output_root / 'identity_coherence_v04_acceptance.md'}"
        )
    else:
        print(
            "NO-GO identity_coherence_v04_acceptance "
            f"reason={final_acceptance.evidence} "
            f"summary={args.output_root / 'identity_coherence_v04_acceptance.md'}"
        )
    if args.write_controls_manifest_proposal is not None:
        print(
            "Controls manifest proposal: "
            f"{args.write_controls_manifest_proposal}"
        )
    return 0


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
    )


def _reset_run_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _tail(text: str | None, *, limit: int = 1000) -> str:
    if not text:
        return ""
    return text[-limit:]


if __name__ == "__main__":
    raise SystemExit(main())
