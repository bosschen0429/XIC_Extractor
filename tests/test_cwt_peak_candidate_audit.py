import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook

from tools.diagnostics.cwt_peak_candidate_audit import main


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "tools" / "diagnostics" / "cwt_peak_candidate_audit.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--peak-candidates-tsv" in result.stdout
    assert "--output-dir" in result.stdout


def test_module_style_cli_help_preserves_public_module_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.diagnostics.cwt_peak_candidate_audit",
            "--help",
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--peak-candidates-tsv" in result.stdout
    assert "--output-dir" in result.stdout


def test_cwt_peak_candidate_audit_classifies_agreement_and_far_alternatives(
    tmp_path: Path,
) -> None:
    candidate_tsv = tmp_path / "peak_candidates.tsv"
    output_dir = tmp_path / "diagnostics"
    _write_peak_candidates(candidate_tsv)

    code = main(
        [
            "--peak-candidates-tsv",
            str(candidate_tsv),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    payload = json.loads(
        (output_dir / "cwt_peak_candidate_audit.json").read_text(encoding="utf-8")
    )
    assert payload["summary"] == {
        "candidate_row_count": 10,
        "candidate_group_count": 5,
        "cwt_row_count": 4,
        "cwt_only_row_count": 1,
        "selected_cwt_agreed_group_count": 1,
        "selected_cwt_nearby_group_count": 1,
        "selected_cwt_far_alternative_group_count": 2,
        "selected_without_cwt_group_count": 1,
        "cwt_selected_support_group_count": 2,
        "cwt_far_unconfirmed_group_count": 1,
        "cwt_far_chemically_plausible_group_count": 1,
    }

    group_rows = _read_tsv(output_dir / "cwt_peak_candidate_groups.tsv")
    with (output_dir / "cwt_peak_candidate_audit_summary.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "candidate_row_count",
            "candidate_group_count",
            "cwt_row_count",
            "cwt_only_row_count",
            "selected_cwt_agreed_group_count",
            "selected_cwt_nearby_group_count",
            "selected_cwt_far_alternative_group_count",
            "selected_without_cwt_group_count",
            "cwt_selected_support_group_count",
            "cwt_far_unconfirmed_group_count",
            "cwt_far_chemically_plausible_group_count",
        ]
    with (output_dir / "cwt_peak_candidate_groups.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "group_id",
            "sample_name",
            "target_label",
            "target_mz",
            "resolver_mode",
            "cwt_agreement_class",
            "cwt_conditioned_class",
            "candidate_count",
            "cwt_row_count",
            "cwt_only_row_count",
            "selected_candidate_id",
            "selected_rt_apex_min",
            "selected_proposal_sources",
            "selected_ms2_present",
            "selected_nl_match",
            "selected_ms2_trace_strength",
            "nearest_cwt_candidate_id",
            "nearest_cwt_rt_apex_min",
            "nearest_cwt_delta_min",
            "nearest_cwt_ms2_present",
            "nearest_cwt_nl_match",
            "nearest_cwt_ms2_trace_strength",
            "selected_confidence",
            "selected_raw_score",
            "selected_reason",
        ]
    assert {row["group_id"]: row["cwt_agreement_class"] for row in group_rows} == {
        "SampleA|TargetAgreed|region_first_safe_merge": "selected_cwt_agreed",
        "SampleA|TargetFarAlternative|region_first_safe_merge": (
            "selected_cwt_far_alternative"
        ),
        "SampleA|TargetChemFar|region_first_safe_merge": (
            "selected_cwt_far_alternative"
        ),
        "SampleA|TargetNearby|region_first_safe_merge": "selected_cwt_nearby",
        "SampleB|TargetNoCwt|region_first_safe_merge": "selected_without_cwt",
    }
    assert {row["group_id"]: row["cwt_conditioned_class"] for row in group_rows} == {
        "SampleA|TargetAgreed|region_first_safe_merge": "cwt_selected_support",
        "SampleA|TargetFarAlternative|region_first_safe_merge": "cwt_far_unconfirmed",
        "SampleA|TargetChemFar|region_first_safe_merge": "cwt_far_chemically_plausible",
        "SampleA|TargetNearby|region_first_safe_merge": "cwt_selected_support",
        "SampleB|TargetNoCwt|region_first_safe_merge": "no_cwt_proposal",
    }
    far_alternative = next(
        row
        for row in group_rows
        if row["group_id"] == "SampleA|TargetFarAlternative|region_first_safe_merge"
    )
    assert far_alternative["selected_rt_apex_min"] == "7.00000"
    assert far_alternative["nearest_cwt_rt_apex_min"] == "7.30000"
    assert far_alternative["nearest_cwt_delta_min"] == "0.30000"
    assert far_alternative["nearest_cwt_nl_match"] == "FALSE"
    assert far_alternative["nearest_cwt_ms2_trace_strength"] == "none"

    chemically_plausible = next(
        row
        for row in group_rows
        if row["group_id"] == "SampleA|TargetChemFar|region_first_safe_merge"
    )
    assert chemically_plausible["nearest_cwt_nl_match"] == "TRUE"
    assert chemically_plausible["nearest_cwt_ms2_trace_strength"] == "moderate"

    cwt_only_rows = _read_tsv(output_dir / "cwt_peak_candidate_cwt_only.tsv")
    with (output_dir / "cwt_peak_candidate_cwt_only.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "group_id",
            "sample_name",
            "target_label",
            "target_mz",
            "resolver_mode",
            "candidate_id",
            "rt_apex_min",
            "confidence",
            "raw_score",
            "reason",
        ]
    assert len(cwt_only_rows) == 1
    assert cwt_only_rows[0]["target_label"] == "TargetFarAlternative"
    assert (output_dir / "cwt_peak_candidate_audit_summary.tsv").is_file()
    far_rows = _read_tsv(output_dir / "cwt_peak_candidate_far_alternatives.tsv")
    assert {row["group_id"]: row["cwt_conditioned_class"] for row in far_rows} == {
        "SampleA|TargetFarAlternative|region_first_safe_merge": "cwt_far_unconfirmed",
        "SampleA|TargetChemFar|region_first_safe_merge": "cwt_far_chemically_plausible",
    }
    assert (output_dir / "cwt_peak_candidate_audit.md").is_file()


def test_cwt_peak_candidate_audit_rejects_missing_required_columns(
    tmp_path: Path,
) -> None:
    candidate_tsv = tmp_path / "peak_candidates.tsv"
    output_dir = tmp_path / "diagnostics"
    candidate_tsv.write_text(
        "sample_name\ttarget_label\nSampleA\tTargetA\n",
        encoding="utf-8",
    )

    code = main(
        [
            "--peak-candidates-tsv",
            str(candidate_tsv),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 2
    assert not (output_dir / "cwt_peak_candidate_audit.json").exists()


def test_cwt_peak_candidate_audit_accepts_utf8_sig_tsv(tmp_path: Path) -> None:
    candidate_tsv = tmp_path / "peak_candidates.tsv"
    output_dir = tmp_path / "diagnostics"
    _write_peak_candidates(candidate_tsv, encoding="utf-8-sig")

    code = main(
        [
            "--peak-candidates-tsv",
            str(candidate_tsv),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    assert (output_dir / "cwt_peak_candidate_audit.json").is_file()


def test_cwt_peak_candidate_audit_enriches_target_mz_from_workbook(
    tmp_path: Path,
) -> None:
    candidate_tsv = tmp_path / "peak_candidates.tsv"
    workbook = tmp_path / "xic_results.xlsx"
    output_dir = tmp_path / "diagnostics"
    _write_peak_candidates(candidate_tsv)
    _write_target_workbook(workbook)

    code = main(
        [
            "--peak-candidates-tsv",
            str(candidate_tsv),
            "--targeted-workbook",
            str(workbook),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    group_rows = _read_tsv(output_dir / "cwt_peak_candidate_groups.tsv")
    agreed = next(
        row
        for row in group_rows
        if row["group_id"] == "SampleA|TargetAgreed|region_first_safe_merge"
    )
    assert agreed["target_mz"] == "269.12345"

    cwt_only_rows = _read_tsv(output_dir / "cwt_peak_candidate_cwt_only.tsv")
    assert cwt_only_rows[0]["target_mz"] == "300.11111"


def _write_peak_candidates(path: Path, *, encoding: str = "utf-8") -> None:
    header = (
        "sample_name",
        "target_label",
        "resolver_mode",
        "candidate_id",
        "proposal_sources",
        "rt_apex_min",
        "selected",
        "confidence",
        "raw_score",
        "reason",
        "ms2_present",
        "nl_match",
        "ms2_trace_strength",
    )
    rows = (
        (
            "SampleA",
            "TargetAgreed",
            "region_first_safe_merge",
            "A1",
            "local_minimum;centwave_cwt",
            "4.00000",
            "TRUE",
            "HIGH",
            "135",
            "selected with CWT support",
            "TRUE",
            "TRUE",
            "strong",
        ),
        (
            "SampleA",
            "TargetAgreed",
            "region_first_safe_merge",
            "A2",
            "legacy_savgol",
            "4.10000",
            "FALSE",
            "",
            "",
            "non-selected",
            "",
            "",
            "",
        ),
        (
            "SampleA",
            "TargetFarAlternative",
            "region_first_safe_merge",
            "D1",
            "local_minimum",
            "7.00000",
            "TRUE",
            "MEDIUM",
            "80",
            "selected without CWT",
            "TRUE",
            "TRUE",
            "moderate",
        ),
        (
            "SampleA",
            "TargetFarAlternative",
            "region_first_safe_merge",
            "D2",
            "centwave_cwt",
            "7.30000",
            "FALSE",
            "",
            "",
            "CWT-only proposal",
            "TRUE",
            "FALSE",
            "none",
        ),
        (
            "SampleA",
            "TargetChemFar",
            "region_first_safe_merge",
            "C1",
            "local_minimum",
            "8.00000",
            "TRUE",
            "HIGH",
            "130",
            "selected peak",
            "TRUE",
            "TRUE",
            "moderate",
        ),
        (
            "SampleA",
            "TargetChemFar",
            "region_first_safe_merge",
            "C2",
            "local_minimum;centwave_cwt",
            "8.45000",
            "FALSE",
            "",
            "",
            "far CWT with chemistry",
            "TRUE",
            "TRUE",
            "moderate",
        ),
        (
            "SampleA",
            "TargetNearby",
            "region_first_safe_merge",
            "B1",
            "local_minimum",
            "5.00000",
            "TRUE",
            "HIGH",
            "120",
            "selected near CWT",
            "TRUE",
            "TRUE",
            "strong",
        ),
        (
            "SampleA",
            "TargetNearby",
            "region_first_safe_merge",
            "B2",
            "legacy_savgol;centwave_cwt",
            "5.05000",
            "FALSE",
            "",
            "",
            "nearby CWT support",
            "TRUE",
            "TRUE",
            "moderate",
        ),
        (
            "SampleB",
            "TargetNoCwt",
            "region_first_safe_merge",
            "N1",
            "local_minimum",
            "2.00000",
            "TRUE",
            "HIGH",
            "120",
            "selected without CWT",
            "TRUE",
            "TRUE",
            "strong",
        ),
        (
            "SampleB",
            "TargetNoCwt",
            "region_first_safe_merge",
            "N2",
            "legacy_savgol",
            "2.10000",
            "FALSE",
            "",
            "",
            "non-selected",
            "",
            "",
            "",
        ),
    )
    path.write_text(
        "\t".join(header) + "\n" + "\n".join("\t".join(row) for row in rows) + "\n",
        encoding=encoding,
    )


def _write_target_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Targets"
    sheet.append(["Label", "m/z"])
    sheet.append(["TargetAgreed", 269.12345])
    sheet.append(["TargetFarAlternative", 300.11111])
    sheet.append(["TargetChemFar", 300.22222])
    sheet.append(["TargetNearby", 301.22222])
    sheet.append(["TargetNoCwt", 302.33333])
    workbook.save(path)
    workbook.close()


def _read_tsv(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"), strict=True)) for line in lines[1:]]
