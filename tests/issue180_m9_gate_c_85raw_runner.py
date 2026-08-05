from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from tests import issue180_m9_raw_validation_fixture as gate_c_fixture
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata

EXPECTED_SAMPLE_COUNT = 85
_CODE_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


@dataclass(frozen=True)
class GateCLaunchInputs:
    raw_files: tuple[Path, ...]
    sample_metadata: tuple[SampleMetadata, ...]
    metadata_sha256: str
    launch_manifest: dict[str, object]


def build_launch_inputs(
    *,
    metadata_path: Path,
    raw_dir: Path,
    dll_dir: Path,
    manifest_root: Path,
    output_dir: Path,
    launch_manifest_path: Path,
    code_sha: str,
) -> GateCLaunchInputs:
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
    for target in (output_dir, launch_path):
        try:
            target.relative_to(manifest_root)
        except ValueError as exc:
            raise ValueError("Gate C outputs must stay below manifest root") from exc

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
    manifest: dict[str, object] = {
        "schema_version": "issue180_m9_gate_c_launch_manifest_v1",
        "launch_contract": gate_c_fixture.gate_c_launch_contract(),
        "code_sha": code_sha,
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
        "receipt_targets": {
            "proposal": str(output_dir / "proposal" / "proposal.json"),
            "proposal_receipt": str(output_dir / "proposal" / "proposal_receipt.json"),
            "runtime": str(output_dir / "runtime.json"),
            "timing_live": str(output_dir / "timing.live.json"),
        },
    }
    return GateCLaunchInputs(
        raw_files=ordered_raws,
        sample_metadata=ordered_rows,
        metadata_sha256=metadata_sha256,
        launch_manifest=manifest,
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
    return gate_c_fixture.run_gate_c(
        raw_files=inputs.raw_files,
        sample_metadata=inputs.sample_metadata,
        dll_dir=dll_dir.resolve(),
        manifest_root=manifest_root.resolve(),
        output_dir=output_dir.resolve(),
        code_sha=code_sha,
        metadata_sha256=inputs.metadata_sha256,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    parser.add_argument("--sample-metadata", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--dll-dir", required=True, type=Path)
    parser.add_argument("--manifest-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--launch-manifest", required=True, type=Path)
    parser.add_argument("--code-sha", required=True)
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
