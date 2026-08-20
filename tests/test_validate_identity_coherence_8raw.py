from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import validate_identity_coherence_8raw as validation_script
from scripts.validate_identity_coherence_8raw import (
    V04_ACCEPTANCE_PASS_PREFIX,
    build_alignment_command,
    run_validation,
)
from xic_extractor.alignment.identity_coherence_validation.models import (
    DiagnosticBundle,
    ValidationResult,
    ValidationRow,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _bundle(root: Path, *, suffix: str = "") -> DiagnosticBundle:
    return DiagnosticBundle(
        requests_tsv=root / f"untargeted_identity_coherence_requests{suffix}.tsv",
        decisions_tsv=root / f"untargeted_identity_coherence_decisions{suffix}.tsv",
        cell_evidence_tsv=(
            root / f"untargeted_identity_coherence_cell_evidence{suffix}.tsv"
        ),
        controls_tsv=root / f"untargeted_identity_coherence_controls{suffix}.tsv",
        summary_md=root / f"untargeted_identity_coherence_summary{suffix}.md",
    )


def _write_bundle(root: Path, *, decision_rows: str) -> DiagnosticBundle:
    bundle = _bundle(root)
    _write(bundle.requests_tsv, "request_id\tseed_candidate_id\nICR-1\tC1\n")
    _write(bundle.decisions_tsv, "decision_id\tdecision\n" + decision_rows)
    _write(bundle.cell_evidence_tsv, "decision_id\tsample_id\nICD-1\tS2\n")
    _write(bundle.controls_tsv, "control_id\tcontrol_pass\n")
    _write(bundle.summary_md, "# Summary\n")
    return bundle


# This fixture intentionally uses the proposal writer's minimum input surface.
# Do not consolidate it with _write_bundle(); this test needs seed_gate_class.
def _write_proposal_source_bundle(root: Path) -> DiagnosticBundle:
    bundle = _bundle(root)
    _write(
        bundle.requests_tsv,
        "request_id\tdecision_id\tseed_candidate_id\tseed_sample\t"
        "fragment_observation_mode\tprecursor_mz\tproduct_mz\tfragment_tags\t"
        "fragment_tag_match_policy\tfragment_profile_id\tfragment_profile_hash\t"
        "precursor_tolerance_ppm\tproduct_tolerance_ppm\tcid_observed_loss_da\t"
        "cid_observed_loss_tolerance_ppm\trequest_identity_completeness_status\t"
        "request_candidate_identity_status\tprecursor_error_ppm\tproduct_error_ppm\t"
        "cid_observed_loss_error_ppm\tcid_observed_loss_error_da\t"
        "request_builder_flags\n"
        "ICR-1\tICD-1\tC1\tS1\tcid_neutral_loss\t500.0\t384.0\tDNA_dR\t"
        "all_request_tags_supported\tdefault\tunavailable\t20.0\t20.0\t"
        "116.0474\t20.0\tcomplete\tmatch\t0.1\t0.2\t0.3\t0.0001\t\n",
    )
    _write(
        bundle.decisions_tsv,
        "decision_id\tidentity_family_id\tseed_candidate_id\tseed_sample\t"
        "seed_gate_class\tdecision\n"
        "ICD-1\tICF-1\tC1\tS1\tcoherent_seed\t"
        "would_primary_provisional_identity_family_support\n",
    )
    _write(bundle.cell_evidence_tsv, "decision_id\tsample_id\nICD-1\tS2\n")
    _write(bundle.controls_tsv, "control_id\tcontrol_type\tcontrol_pass\n")
    _write(bundle.summary_md, "# Summary\n")
    return bundle


def _validation_result_with_rows(rows: tuple[ValidationRow, ...]) -> ValidationResult:
    return ValidationResult(rows=rows)


def test_build_serial_command_omits_validation_fast_profile(tmp_path: Path) -> None:
    command = build_alignment_command(
        mode="serial",
        discovery_batch_index=tmp_path / "batch.csv",
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out" / "serial",
        controls_manifest=None,
    )

    joined = " ".join(str(part) for part in command)
    assert "--emit-identity-coherence-diagnostic" in joined
    assert "--identity-coherence-output-dir" not in command
    assert "--emit-alignment-cells" not in command
    assert "--timing-output" not in command
    assert "--performance-profile" not in command
    assert "validation-fast" not in command
    assert command[command.index("--raw-workers") + 1] == "1"
    assert command[command.index("--raw-xic-batch-size") + 1] == "1"


def test_build_process_command_uses_validation_fast_profile(tmp_path: Path) -> None:
    command = build_alignment_command(
        mode="process",
        discovery_batch_index=tmp_path / "batch.csv",
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out" / "process",
        controls_manifest=tmp_path / "controls.tsv",
    )

    assert "--performance-profile" in command
    assert "validation-fast" in command
    assert "--identity-coherence-output-dir" not in command
    assert "--emit-alignment-cells" not in command
    assert "--timing-output" not in command
    assert "--identity-coherence-controls-manifest" in command
    assert str(tmp_path / "controls.tsv") in command


def test_run_validation_uses_fake_runner_and_compares_outputs(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        _write_bundle(
            output_dir / "identity_coherence",
            decision_rows=(
                "ICD-1\twould_primary_provisional_identity_family_support\n"
            ),
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = run_validation(
        discovery_batch_index=tmp_path / "batch.csv",
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_root=tmp_path / "validation",
        controls_manifest=None,
        runner=fake_runner,
    )

    assert result.failed_count == 0
    assert [command[command.index("--output-dir") + 1] for command in commands] == [
        str(tmp_path / "validation" / "serial"),
        str(tmp_path / "validation" / "process"),
    ]
    assert result.run_metadata[0].mode == "serial"
    assert result.run_metadata[1].mode == "process"


def test_run_validation_raises_when_child_fails(tmp_path: Path) -> None:
    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            13,
            stdout="partial stdout",
            stderr="boom",
        )

    try:
        run_validation(
            discovery_batch_index=tmp_path / "batch.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_root=tmp_path / "validation",
            controls_manifest=None,
            runner=fake_runner,
        )
    except RuntimeError as exc:
        assert "serial alignment failed with exit code 13" in str(exc)
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_run_validation_clears_previous_mode_outputs(tmp_path: Path) -> None:
    stale = (
        tmp_path
        / "validation"
        / "serial"
        / "identity_coherence"
        / "untargeted_identity_coherence_decisions.tsv"
    )
    stale.parent.mkdir(parents=True)
    stale.write_text("decision_id\tdecision\nSTALE\told\n", encoding="utf-8")

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        output_dir = Path(command[command.index("--output-dir") + 1])
        _write_bundle(
            output_dir / "identity_coherence",
            decision_rows=(
                "ICD-1\twould_primary_provisional_identity_family_support\n"
            ),
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = run_validation(
        discovery_batch_index=tmp_path / "batch.csv",
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_root=tmp_path / "validation",
        controls_manifest=None,
        runner=fake_runner,
    )

    assert result.failed_count == 0
    assert "STALE" not in stale.read_text(encoding="utf-8")


def test_run_validation_does_not_write_proposal_when_parity_fails(
    tmp_path: Path,
) -> None:
    proposal = tmp_path / "proposal.tsv"
    proposal.write_text("stale\n", encoding="utf-8")

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        output_dir = Path(command[command.index("--output-dir") + 1])
        _write_proposal_source_bundle(output_dir / "identity_coherence")
        if output_dir.name == "process":
            (output_dir / "identity_coherence" / bundle_name()).write_text(
                "different\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    def bundle_name() -> str:
        return "untargeted_identity_coherence_decisions.tsv"

    result = run_validation(
        discovery_batch_index=tmp_path / "batch.csv",
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_root=tmp_path / "out",
        controls_manifest=None,
        controls_manifest_proposal=proposal,
        runner=fake_runner,
    )

    assert result.failed_count > 0
    assert not proposal.exists()


def test_main_require_v04_acceptance_returns_one_when_not_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch = _write(tmp_path / "batch.csv", "sample_stem,raw_file,candidate_csv\n")
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    output_root = tmp_path / "out"
    result = _validation_result_with_rows(
        (
            ValidationRow("requests_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("decisions_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("cell_evidence_tsv_exact", "pass", "8", "8", "ok"),
            ValidationRow("controls_tsv_parity_only", "pass", "0", "0", "ok"),
            ValidationRow(
                "controls_manifest_assessment",
                "not_assessed",
                "not_provided",
                "not_provided",
                "no manifest",
            ),
            ValidationRow(
                "positive_control_pass_fraction",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
            ValidationRow(
                "decoy_coherent_seed_count",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
            ValidationRow(
                "decoy_correctly_rejected_count",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
            ValidationRow("summary_md_presence", "pass", "present", "present", "ok"),
        )
    )
    monkeypatch.setattr(validation_script, "run_validation", lambda **_: result)

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(output_root),
            "--require-v04-acceptance",
        ]
    )

    assert code == 1
    stderr = capsys.readouterr().err
    assert "FAIL identity_coherence_v04_acceptance" in stderr
    assert "reviewed_controls_manifest" in stderr
    assert (output_root / "identity_coherence_v04_acceptance.tsv").exists()


def test_main_without_strict_acceptance_prints_no_go_but_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch = _write(tmp_path / "batch.csv", "sample_stem,raw_file,candidate_csv\n")
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    output_root = tmp_path / "out"
    result = _validation_result_with_rows(
        (
            ValidationRow("requests_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("decisions_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("cell_evidence_tsv_exact", "pass", "8", "8", "ok"),
            ValidationRow("controls_tsv_parity_only", "pass", "0", "0", "ok"),
            ValidationRow(
                "controls_manifest_assessment",
                "not_assessed",
                "not_provided",
                "not_provided",
                "no manifest",
            ),
            ValidationRow(
                "positive_control_pass_fraction",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
            ValidationRow(
                "decoy_coherent_seed_count",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
            ValidationRow(
                "decoy_correctly_rejected_count",
                "not_assessed",
                "not_assessed",
                "not_assessed",
                "no manifest",
            ),
            ValidationRow("summary_md_presence", "pass", "present", "present", "ok"),
        )
    )
    monkeypatch.setattr(validation_script, "run_validation", lambda **_: result)

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(output_root),
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    assert "PASS identity_coherence_sidecar_parity" in stdout
    assert "NO-GO identity_coherence_v04_acceptance" in stdout
    assert "reviewed_controls_manifest" in stdout


def test_main_require_v04_acceptance_passes_when_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch = _write(tmp_path / "batch.csv", "sample_stem,raw_file,candidate_csv\n")
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    controls = tmp_path / "controls.reviewed.tsv"
    raw_dir.mkdir()
    dll_dir.mkdir()
    controls.write_text("reviewed\n", encoding="utf-8")
    output_root = tmp_path / "out"
    result = _validation_result_with_rows(
        (
            ValidationRow("requests_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("decisions_tsv_exact", "pass", "3", "3", "ok"),
            ValidationRow("cell_evidence_tsv_exact", "pass", "8", "8", "ok"),
            ValidationRow("controls_tsv_parity_only", "pass", "2", "2", "ok"),
            ValidationRow(
                "controls_manifest_assessment",
                "not_assessed",
                "provided",
                "provided",
                "manifest provided",
            ),
            ValidationRow(
                "positive_control_pass_fraction",
                "pass",
                "1.000",
                "1.000",
                "all positives passed",
            ),
            ValidationRow(
                "decoy_coherent_seed_count",
                "pass",
                "0",
                "0",
                "no decoy promotion",
            ),
            ValidationRow(
                "decoy_correctly_rejected_count",
                "pass",
                "3/3",
                "3/3",
                "all decoys rejected",
            ),
            ValidationRow("summary_md_presence", "pass", "present", "present", "ok"),
        )
    )
    monkeypatch.setattr(validation_script, "run_validation", lambda **_: result)

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(output_root),
            "--controls-manifest",
            str(controls),
            "--require-v04-acceptance",
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    assert "PASS identity_coherence_sidecar_parity" in stdout
    assert V04_ACCEPTANCE_PASS_PREFIX in stdout


def test_main_rejects_missing_controls_manifest(tmp_path: Path, capsys) -> None:
    batch = tmp_path / "batch.csv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    batch.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir.mkdir()
    dll_dir.mkdir()

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(tmp_path / "out"),
            "--controls-manifest",
            str(tmp_path / "missing.tsv"),
        ]
    )

    assert code == 2
    assert "controls manifest does not exist" in capsys.readouterr().err


def test_main_passes_controls_manifest_proposal_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch = tmp_path / "batch.csv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    proposal = tmp_path / "proposal.tsv"
    batch.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir.mkdir()
    dll_dir.mkdir()
    seen: dict[str, object] = {}

    def fake_run_validation(**kwargs) -> ValidationResult:
        seen.update(kwargs)
        return _validation_result_with_rows(
            (
                ValidationRow("requests_tsv_exact", "pass", "1", "1", "same"),
                ValidationRow("decisions_tsv_exact", "pass", "1", "1", "same"),
                ValidationRow("cell_evidence_tsv_exact", "pass", "1", "1", "same"),
                ValidationRow("controls_tsv_parity_only", "pass", "0", "0", "same"),
                ValidationRow(
                    "controls_manifest_assessment",
                    "not_assessed",
                    "not_provided",
                    "not_provided",
                    "no manifest",
                ),
                ValidationRow(
                    "positive_control_pass_fraction",
                    "not_assessed",
                    "not_assessed",
                    "not_assessed",
                    "no manifest",
                ),
                ValidationRow(
                    "decoy_coherent_seed_count",
                    "not_assessed",
                    "not_assessed",
                    "not_assessed",
                    "no manifest",
                ),
                ValidationRow(
                    "decoy_correctly_rejected_count",
                    "not_assessed",
                    "not_assessed",
                    "not_assessed",
                    "no manifest",
                ),
                ValidationRow(
                    "summary_md_presence",
                    "pass",
                    "present",
                    "present",
                    "ok",
                ),
            )
        )

    monkeypatch.setattr(validation_script, "run_validation", fake_run_validation)

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(tmp_path / "out"),
            "--write-controls-manifest-proposal",
            str(proposal),
        ]
    )

    assert code == 0
    assert seen["controls_manifest_proposal"] == proposal


def test_main_rejects_proposed_controls_manifest(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    batch = tmp_path / "batch.csv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    proposed = tmp_path / "identity_coherence_controls_manifest.proposed.tsv"
    batch.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    proposed.write_text("control_id\n", encoding="utf-8")
    raw_dir.mkdir()
    dll_dir.mkdir()

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(tmp_path / "out"),
            "--controls-manifest",
            str(proposed),
        ]
    )

    assert code == 2
    assert "must be reviewed and renamed" in capsys.readouterr().err


def test_main_rejects_uppercase_proposed_controls_manifest(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    batch = tmp_path / "batch.csv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    proposed = tmp_path / "identity_coherence_controls_manifest.PROPOSED.TSV"
    batch.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    proposed.write_text("control_id\n", encoding="utf-8")
    raw_dir.mkdir()
    dll_dir.mkdir()

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(tmp_path / "out"),
            "--controls-manifest",
            str(proposed),
        ]
    )

    assert code == 2
    assert "must be reviewed and renamed" in capsys.readouterr().err


def test_main_returns_one_when_parity_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch = tmp_path / "batch.csv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    batch.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir.mkdir()
    dll_dir.mkdir()

    def fake_run_validation(**kwargs) -> ValidationResult:
        return ValidationResult(
            rows=(
                ValidationRow(
                    check_name="decisions_tsv_exact",
                    status="fail",
                    serial_value="a",
                    process_value="b",
                    details="different",
                ),
            )
        )

    monkeypatch.setattr(validation_script, "run_validation", fake_run_validation)

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(tmp_path / "out"),
        ]
    )

    assert code == 1


def test_main_returns_two_when_validation_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch = tmp_path / "batch.csv"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    batch.write_text("sample_stem,raw_file,candidate_csv\n", encoding="utf-8")
    raw_dir.mkdir()
    dll_dir.mkdir()

    def fake_run_validation(**kwargs) -> ValidationResult:
        raise RuntimeError("serial alignment failed")

    monkeypatch.setattr(validation_script, "run_validation", fake_run_validation)

    code = validation_script.main(
        [
            "--discovery-batch-index",
            str(batch),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-root",
            str(tmp_path / "out"),
        ]
    )

    assert code == 2
