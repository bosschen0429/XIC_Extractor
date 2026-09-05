from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests import mode_track_raw_validation_fixture as gate_c_fixture
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata

EXPECTED_SAMPLE_COUNT = 85
MODULE_NAME = "tests.mode_track_85raw_validation_runner"
_CODE_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


@dataclass(frozen=True)
class GateCLaunchInputs:
    stage: str
    raw_files: tuple[Path, ...]
    sample_metadata: tuple[SampleMetadata, ...]
    metadata_sha256: str
    launch_manifest: dict[str, object]
    output_dir: Path
    proposal_dir: Path | None = None
    manual_review_path: Path | None = None
    manual_review_replay_path: Path | None = None
    review_transfer_receipt_path: Path | None = None
    checked_manifest_path: Path | None = None
    final_output_dir: Path | None = None


def build_launch_inputs(
    *,
    metadata_path: Path,
    raw_dir: Path,
    dll_dir: Path,
    manifest_root: Path,
    output_dir: Path,
    launch_manifest_path: Path,
    code_sha: str,
    stage: str = "c1",
    proposal_dir: Path | None = None,
    manual_review_path: Path | None = None,
    manual_review_replay_path: Path | None = None,
    review_transfer_receipt_path: Path | None = None,
    checked_manifest_path: Path | None = None,
    final_output_dir: Path | None = None,
) -> GateCLaunchInputs:
    if stage not in {"c1", "c2"}:
        raise ValueError("Gate C stage must be c1 or c2")
    paths = tuple(
        path.resolve()
        for path in (
            metadata_path,
            raw_dir,
            dll_dir,
            manifest_root,
            output_dir,
            launch_manifest_path,
        )
    )
    metadata_path, raw_dir, dll_dir, manifest_root, output_dir, launch_path = paths
    if not _CODE_SHA_RE.fullmatch(code_sha):
        raise ValueError("code SHA must be a lowercase 40- or 64-character digest")
    if not raw_dir.is_dir() or not dll_dir.is_dir() or not manifest_root.is_dir():
        raise ValueError("RAW, DLL, or manifest root does not exist")
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    c2_paths: tuple[Path, Path, Path, Path, Path, Path] | None = None
    if stage == "c2":
        if any(
            value is None
            for value in (
                proposal_dir,
                manual_review_path,
                manual_review_replay_path,
                review_transfer_receipt_path,
                checked_manifest_path,
                final_output_dir,
            )
        ):
            raise ValueError(
                "Gate C C2 requires proposal, review, transfer, "
                "manifest, and final paths"
            )
        assert proposal_dir is not None
        assert manual_review_path is not None
        assert manual_review_replay_path is not None
        assert review_transfer_receipt_path is not None
        assert checked_manifest_path is not None
        assert final_output_dir is not None
        c2_paths = (
            proposal_dir.resolve(),
            manual_review_path.resolve(),
            manual_review_replay_path.resolve(),
            review_transfer_receipt_path.resolve(),
            checked_manifest_path.resolve(),
            final_output_dir.resolve(),
        )
        (
            proposal_dir,
            manual_review_path,
            manual_review_replay_path,
            review_transfer_receipt_path,
            checked_manifest_path,
            final_output_dir,
        ) = c2_paths
        if not proposal_dir.is_dir() or any(
            not path.is_file()
            for path in (
                manual_review_path,
                manual_review_replay_path,
                review_transfer_receipt_path,
                checked_manifest_path,
            )
        ):
            raise ValueError("Gate C C2 frozen inputs do not exist")
        if final_output_dir.exists():
            raise ValueError(
                f"final output directory already exists: {final_output_dir}"
            )
    output_targets = [output_dir, launch_path]
    if final_output_dir is not None:
        output_targets.append(final_output_dir)
    for target in output_targets:
        try:
            target.relative_to(manifest_root)
        except ValueError as exc:
            raise ValueError("Gate C outputs must stay below manifest root") from exc
    if c2_paths is not None:
        for source in c2_paths[:-1]:
            try:
                source.relative_to(manifest_root)
            except ValueError as exc:
                raise ValueError(
                    "Gate C C2 inputs must stay below manifest root"
                ) from exc
    executable = _current_executable()
    expected_executable = (manifest_root / ".venv" / "Scripts" / "python.exe").resolve()
    if executable != expected_executable or not expected_executable.is_file():
        raise ValueError("Gate C must run from the worktree .venv Python")
    if _checkout_sha(manifest_root) != code_sha:
        raise ValueError("Gate C code SHA does not match the current checkout")
    if _worktree_status(manifest_root):
        raise ValueError("Gate C requires a clean Git worktree")

    rows = tuple(row for row in load_sample_metadata(metadata_path) if not row.excluded)
    if len(rows) != EXPECTED_SAMPLE_COUNT:
        raise ValueError("Gate C requires exactly 85 non-excluded metadata rows")
    orders = tuple(row.injection_order for row in rows)
    if set(orders) != set(range(1, EXPECTED_SAMPLE_COUNT + 1)):
        raise ValueError("Gate C injection orders must close exactly over 1..85")
    if any(not row.raw_stem for row in rows):
        raise ValueError("Gate C metadata requires every RAW stem")

    raw_paths = tuple(
        sorted(
            (
                path.resolve()
                for path in raw_dir.iterdir()
                if path.suffix.lower() == ".raw"
            ),
            key=lambda path: path.stem,
        )
    )
    raw_by_stem = {path.stem: path for path in raw_paths}
    metadata_stems = {row.raw_stem for row in rows}
    if len(raw_by_stem) != EXPECTED_SAMPLE_COUNT or set(raw_by_stem) != metadata_stems:
        raise ValueError("Gate C metadata and RAW stem universes differ")
    ordered_rows = tuple(sorted(rows, key=lambda row: row.injection_order or 0))
    ordered_raws = tuple(raw_by_stem[row.raw_stem] for row in ordered_rows)
    metadata_sha256 = _sha256(metadata_path)
    raw_identities = [
        {
            "raw_stem": path.stem,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in ordered_raws
    ]
    argv = [
        str(executable),
        "-m",
        MODULE_NAME,
        "--stage",
        stage,
        "--sample-metadata",
        str(metadata_path),
        "--raw-dir",
        str(raw_dir),
        "--dll-dir",
        str(dll_dir),
        "--manifest-root",
        str(manifest_root),
        "--output-dir",
        str(output_dir),
        "--launch-manifest",
        str(launch_path),
        "--code-sha",
        code_sha,
    ]
    c2_inputs: dict[str, object] | None = None
    if c2_paths is not None:
        assert proposal_dir is not None
        assert manual_review_path is not None
        assert manual_review_replay_path is not None
        assert review_transfer_receipt_path is not None
        assert checked_manifest_path is not None
        assert final_output_dir is not None
        argv.extend(
            (
                "--proposal-dir",
                str(proposal_dir),
                "--manual-review",
                str(manual_review_path),
                "--manual-review-replay",
                str(manual_review_replay_path),
                "--review-transfer-receipt",
                str(review_transfer_receipt_path),
                "--checked-manifest",
                str(checked_manifest_path),
                "--final-output-dir",
                str(final_output_dir),
            )
        )
        proposal_path = proposal_dir / "proposal.json"
        proposal_receipt_path = proposal_dir / "proposal_receipt.json"
        if not proposal_path.is_file() or not proposal_receipt_path.is_file():
            raise ValueError("Gate C C2 proposal packet is incomplete")
        c2_inputs = {
            "proposal_dir": str(proposal_dir),
            "proposal_sha256": _sha256(proposal_path),
            "proposal_receipt_sha256": _sha256(proposal_receipt_path),
            "manual_review": {
                "path": str(manual_review_path),
                "sha256": _sha256(manual_review_path),
            },
            "manual_review_replay": {
                "path": str(manual_review_replay_path),
                "sha256": _sha256(manual_review_replay_path),
            },
            "review_transfer_receipt": {
                "path": str(review_transfer_receipt_path),
                "sha256": _sha256(review_transfer_receipt_path),
            },
            "checked_manifest": {
                "path": str(checked_manifest_path),
                "sha256": _sha256(checked_manifest_path),
            },
            "final_output_dir": str(final_output_dir),
        }
    if stage == "c1":
        receipt_targets = {
            "proposal": str(output_dir / "proposal" / "proposal.json"),
            "proposal_receipt": str(output_dir / "proposal" / "proposal_receipt.json"),
            "runtime": str(output_dir / "runtime.json"),
            "timing_live": str(output_dir / "timing.live.json"),
        }
    else:
        assert final_output_dir is not None
        receipt_targets = {
            "fresh_owner_receipt": str(output_dir / "fresh_owner_receipt.json"),
            "runtime": str(output_dir / "runtime.json"),
            "timing_live": str(output_dir / "timing.live.json"),
            "final_receipt": str(final_output_dir / "final_receipt.json"),
            "transaction_receipt": str(final_output_dir / "transaction_receipt.json"),
        }
    manifest: dict[str, object] = {
        "schema_version": "mode_track_85raw_validation_gate_c_launch_manifest_v2",
        "stage": stage,
        "launch_contract": gate_c_fixture.gate_c_launch_contract(),
        "code_sha": code_sha,
        "execution": {
            "executable": str(executable),
            "working_directory": str(manifest_root),
            "argv": argv,
            "command_sha256": _json_sha256(argv),
            "worktree_status": "clean",
        },
        "metadata": {"path": str(metadata_path), "sha256": metadata_sha256},
        "raw": {
            "root": str(raw_dir),
            "sample_count": len(ordered_raws),
            "identities": raw_identities,
            "universe_sha256": _json_sha256(raw_identities),
        },
        "dll": {"root": str(dll_dir), "tree_sha256": _tree_sha256(dll_dir)},
        "manifest_root": str(manifest_root),
        "output_dir": str(output_dir),
        "launch_manifest_path": str(launch_path),
        "c2_inputs": c2_inputs,
        "receipt_targets": receipt_targets,
    }
    return GateCLaunchInputs(
        stage=stage,
        raw_files=ordered_raws,
        sample_metadata=ordered_rows,
        metadata_sha256=metadata_sha256,
        launch_manifest=manifest,
        output_dir=output_dir,
        proposal_dir=proposal_dir,
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
        review_transfer_receipt_path=review_transfer_receipt_path,
        checked_manifest_path=checked_manifest_path,
        final_output_dir=final_output_dir,
    )


def freeze_preflight(path: Path, payload: dict[str, object]) -> Path:
    if path.exists():
        raise ValueError(f"launch manifest already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, payload)
    return path


def run_frozen_launch(
    *,
    inputs: GateCLaunchInputs,
    launch_manifest_path: Path,
    dll_dir: Path,
    manifest_root: Path,
    output_dir: Path,
    code_sha: str,
) -> Path:
    frozen = json.loads(launch_manifest_path.read_text(encoding="utf-8"))
    if frozen != inputs.launch_manifest:
        raise ValueError("Gate C launch inputs changed after no-RAW preflight")
    if inputs.output_dir != output_dir.resolve():
        raise ValueError("Gate C output changed after no-RAW preflight")
    execution = frozen.get("execution")
    if not isinstance(execution, dict) or (
        execution.get("executable") != str(_current_executable())
        or execution.get("worktree_status") != "clean"
        or _checkout_sha(manifest_root.resolve()) != code_sha
        or bool(_worktree_status(manifest_root.resolve()))
    ):
        raise ValueError("Gate C execution identity changed after preflight")
    if inputs.stage == "c2":
        _validate_frozen_c2_inputs(inputs, frozen)
    if inputs.stage == "c1":
        receipt = gate_c_fixture.run_gate_c(
            raw_files=inputs.raw_files,
            sample_metadata=inputs.sample_metadata,
            dll_dir=dll_dir.resolve(),
            manifest_root=manifest_root.resolve(),
            output_dir=output_dir.resolve(),
            code_sha=code_sha,
            metadata_sha256=inputs.metadata_sha256,
        )
        terminal_target = "proposal_receipt"
        terminal_label = "proposal"
    else:
        if any(
            value is None
            for value in (
                inputs.proposal_dir,
                inputs.manual_review_path,
                inputs.manual_review_replay_path,
                inputs.review_transfer_receipt_path,
                inputs.checked_manifest_path,
                inputs.final_output_dir,
            )
        ):
            raise RuntimeError("Gate C frozen C2 inputs are incomplete")
        assert inputs.proposal_dir is not None
        assert inputs.manual_review_path is not None
        assert inputs.manual_review_replay_path is not None
        assert inputs.review_transfer_receipt_path is not None
        assert inputs.checked_manifest_path is not None
        assert inputs.final_output_dir is not None
        receipt = gate_c_fixture.run_gate_c_fresh_finalization(
            raw_files=inputs.raw_files,
            sample_metadata=inputs.sample_metadata,
            dll_dir=dll_dir.resolve(),
            manifest_root=manifest_root.resolve(),
            replay_output_dir=output_dir.resolve(),
            final_output_dir=inputs.final_output_dir,
            proposal_dir=inputs.proposal_dir,
            manual_review_path=inputs.manual_review_path,
            manual_review_replay_path=inputs.manual_review_replay_path,
            review_transfer_receipt_path=inputs.review_transfer_receipt_path,
            manifest_path=inputs.checked_manifest_path,
            code_sha=code_sha,
            metadata_sha256=inputs.metadata_sha256,
        )
        terminal_target = "transaction_receipt"
        terminal_label = "transaction"
    targets = frozen.get("receipt_targets")
    if not isinstance(targets, dict):
        raise RuntimeError("Gate C frozen receipt targets are missing")
    expected_receipt = Path(str(targets.get(terminal_target, ""))).resolve()
    if receipt.resolve() != expected_receipt or not expected_receipt.is_file():
        raise RuntimeError(
            f"Gate C terminal {terminal_label} receipt is missing or misplaced"
        )
    return receipt


def _validate_frozen_c2_inputs(
    inputs: GateCLaunchInputs,
    frozen: dict[str, object],
) -> None:
    c2 = frozen.get("c2_inputs")
    if not isinstance(c2, dict) or inputs.proposal_dir is None:
        raise ValueError("Gate C frozen C2 inputs are incomplete")
    file_bindings = (
        (inputs.proposal_dir / "proposal.json", "proposal_sha256"),
        (
            inputs.proposal_dir / "proposal_receipt.json",
            "proposal_receipt_sha256",
        ),
    )
    if any(_sha256(path) != c2.get(key) for path, key in file_bindings):
        raise ValueError("Gate C C2 proposal changed after no-RAW preflight")
    for name, path in (
        ("manual_review", inputs.manual_review_path),
        ("manual_review_replay", inputs.manual_review_replay_path),
        ("review_transfer_receipt", inputs.review_transfer_receipt_path),
        ("checked_manifest", inputs.checked_manifest_path),
    ):
        binding = c2.get(name)
        if (
            path is None
            or not isinstance(binding, dict)
            or binding.get("path") != str(path)
            or binding.get("sha256") != _sha256(path)
        ):
            raise ValueError("Gate C C2 reviewed input changed after no-RAW preflight")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _current_executable() -> Path:
    return Path(sys.executable).resolve()


def _checkout_sha(root: Path) -> str:
    result = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _worktree_status(root: Path) -> str:
    result = subprocess.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise ValueError("Thermo DLL directory is empty")
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("c1", "c2"), default="c1")
    parser.add_argument("--sample-metadata", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--dll-dir", required=True, type=Path)
    parser.add_argument("--manifest-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--launch-manifest", required=True, type=Path)
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--proposal-dir", type=Path)
    parser.add_argument("--manual-review", type=Path)
    parser.add_argument("--manual-review-replay", type=Path)
    parser.add_argument("--review-transfer-receipt", type=Path)
    parser.add_argument("--checked-manifest", type=Path)
    parser.add_argument("--final-output-dir", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    inputs = build_launch_inputs(
        metadata_path=args.sample_metadata,
        raw_dir=args.raw_dir,
        dll_dir=args.dll_dir,
        manifest_root=args.manifest_root,
        output_dir=args.output_dir,
        launch_manifest_path=args.launch_manifest,
        code_sha=args.code_sha,
        stage=args.stage,
        proposal_dir=args.proposal_dir,
        manual_review_path=args.manual_review,
        manual_review_replay_path=args.manual_review_replay,
        review_transfer_receipt_path=args.review_transfer_receipt,
        checked_manifest_path=args.checked_manifest,
        final_output_dir=args.final_output_dir,
    )
    if args.preflight_only:
        result = freeze_preflight(
            args.launch_manifest.resolve(),
            inputs.launch_manifest,
        )
    else:
        result = run_frozen_launch(
            inputs=inputs,
            launch_manifest_path=args.launch_manifest.resolve(),
            dll_dir=args.dll_dir,
            manifest_root=args.manifest_root,
            output_dir=args.output_dir,
            code_sha=args.code_sha,
        )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
