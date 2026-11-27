from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.matrix_curation_fixtures import (
    SAMPLES,
    _run,
    alignment_output,
    sample_payload,
    sample_rows,
)
from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS
from xic_extractor.tabular_io import read_tsv_with_header, write_tsv

OUTPUT_FILES = {
    "analysis_matrix.tsv",
    "feature_decisions.tsv",
    "sample_metadata.tsv",
    "matrix_curation_summary.json",
}


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    source = alignment_output(tmp_path / "alignment")
    samples = tmp_path / "samples.tsv"
    write_tsv(samples, sample_rows(), SAMPLE_METADATA_COLUMNS)
    return source, samples


def _files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.iterdir())


def _curate_nucleosides(source, output, listing=None):
    from scripts.run_matrix_curation import main

    args = [
        "--source-dir",
        str(source),
        "--output-dir",
        str(output),
        "--known-nucleosides",
    ]
    if listing is not None:
        args.extend(["--nucleoside-list", str(listing)])
    return main(args)


def _write_features(tmp_path, observations):
    from tests.matrix_review_fixtures import current_matrix_review_row
    from xic_extractor.output.alignment.matrix_output_contract import (
        MATRIX_REVIEW_COLUMNS,
        public_matrix_mz_text,
    )

    matrix, review = [], []
    for i, (mz, rt) in enumerate(observations, 1):
        matrix.append(
            {"Mz": public_matrix_mz_text(str(mz)), "RT": str(rt), "S": str(i)}
        )
        review.append(
            current_matrix_review_row(
                index=i, mz=str(mz), rt=str(rt), feature_id=f"F{i:06d}"
            )
        )
    write_tsv(tmp_path / "alignment_matrix.tsv", matrix, ("Mz", "RT", "S"))
    write_tsv(tmp_path / "alignment_review.tsv", review, MATRIX_REVIEW_COLUMNS)
    return matrix


def test_cli_excludes_only_matching_nucleosides_without_sample_data(tmp_path):
    source = alignment_output(tmp_path / "alignment")
    listing = tmp_path / "windows.tsv"
    listing.write_text(
        "label\tmz\texpected_rt_min\nB\t101\t1.75\nA\t101\t3.25\nB\t101\t2.5\n"
        "late\t102\t3.26\n",
        encoding="utf-8",
    )
    output = tmp_path / "result"
    result = _run(
        source,
        None,
        output,
        "--nucleoside-list",
        str(listing),
        "--known-nucleosides",
        threshold=None,
    )
    assert result.returncode == 0, result.stderr
    _, decisions = read_tsv_with_header(output / "feature_decisions.tsv")
    assert [r["known_nucleosides_label"] for r in decisions] == ["A;B", "", "", ""]
    assert [r["decision"] for r in decisions] == ["drop", "keep", "keep", "keep"]
    assert decisions[0]["decision_reason"] == "dropped_by:known_nucleosides"
    assert not decisions[0]["istd_label"]


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ("missing", "windows.tsv"),
        ("", "沒有資料列"),
        (" \t101\t1\t3\t", "label"),
        ("x\t0\t1\t3\t", "mz"),
        ("x\tNaN\t1\t3\t", "mz"),
        ("x\t101\tinf\t3\t", "rt_min"),
        ("x\t101\t1\t\t2", "rt_max"),
        ("x\t101\t\t3\t2", "rt_min"),
        ("x\t101\t3\t3\t2", "rt_max"),
        ("x\t101\t\t\t", "expected_rt_min"),
        ("x\t101\t\t\tbogus", "expected_rt_min"),
    ],
)
def test_enabled_nucleoside_step_rejects_bad_list_before_export(
    tmp_path, row, problem, capsys
):

    source = alignment_output(tmp_path / "alignment")
    output = tmp_path / "result"
    listing = tmp_path / "windows.tsv"
    if row != "missing":
        listing.write_text(
            "label\tmz\trt_min\trt_max\texpected_rt_min\n" + row, encoding="utf-8"
        )
    assert _curate_nucleosides(source, output, listing) == 2
    assert problem in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    ("method", "bound"),
    [
        ({"DNA_dR": 116.0474}, True),
        ({"RNA_ribose": 132.0423}, True),
        ({"RNA_MeR": 146.0579}, True),
        ({"DNA_dR": 116.0474, "RNA_ribose": 132.0423}, True),
        ({"DNA_dR": 116.0474}, False),
        (None, False),
    ],
)
def test_nucleoside_windows_follow_published_method_unless_replaced(
    tmp_path, method, bound
):
    from tests.alignment_explanation_fixtures import explanation_summary
    from xic_extractor.alignment_explanation.writer import ExplanationReportWriter
    from xic_extractor.tabular_io import file_sha256
    from xic_workbench.matrix_curation import describe_source

    _write_features(tmp_path, [(268.1040, 14.5), (268.1040, 18.5)])
    if method is not None:
        with ExplanationReportWriter(
            tmp_path / "alignment_explanation.sqlite"
        ) as writer:
            writer.finalize(
                explanation_summary(
                    {
                        "neutral_loss_by_tag": method,
                        "samples": [{"sample_key": "S"}],
                        "matrix_sha256": file_sha256(
                            tmp_path / "alignment_matrix.tsv", uppercase=False
                        )
                        if bound
                        else "0" * 64,
                    },
                )
            )
    available = bound and method == {"DNA_dR": 116.0474}
    info = describe_source(tmp_path, {"source_dir": str(tmp_path)})
    step = next(s for s in info["steps"] if s["key"] == "known_nucleosides")
    assert (not step["unavailable_reason"]) == available
    if available:
        assert "DNA dR" in step["list_label"]
    custom = tmp_path / "late.tsv"
    custom.write_text(
        "label\tmz\texpected_rt_min\nlate\t268.1040\t18.5\n", encoding="utf-8"
    )
    for name, listing, labels in [
        ("builtin", None, ["dG", ""]),
        ("override", custom, ["", "late"]),
    ]:
        output = tmp_path / name
        status = _curate_nucleosides(tmp_path, output, listing)
        if name == "builtin" and not available:
            assert status == 2 and not output.exists()
            continue
        assert status == 0
        _, decisions = read_tsv_with_header(output / "feature_decisions.tsv")
        assert [r["known_nucleosides_label"] for r in decisions] == labels
        assert [r["decision"] for r in decisions] == [
            "drop" if label else "keep" for label in labels
        ]


@pytest.mark.parametrize("reorder", [False, True])
def test_membership_uses_bound_full_precision_mass_and_rt_not_id_or_order(
    tmp_path, reorder
):
    observations = [
        (1562.484375, 4),
        (1562.515625, 6),
        (1562.484374, 5),
        (1562.515626, 5),
        (1562.5, 6.01),
        (1562.5, 12),
    ]
    if reorder:
        observations.reverse()
    matrix = _write_features(tmp_path, observations)
    listing = tmp_path / "windows.tsv"
    listing.write_text(
        "label\tmz\trt_min\trt_max\texpected_rt_min\nx\t1562.5\t4\t6\t12\n",
        encoding="utf-8",
    )
    output = tmp_path / "result"
    assert _curate_nucleosides(tmp_path, output, listing) == 0
    _, selected = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert selected == [
        r
        for r, observation in zip(matrix, observations, strict=True)
        if observation not in {(1562.484375, 4), (1562.515625, 6)}
    ]


def test_cli_keeps_original_rows_and_records_each_decision(tmp_path):
    source, samples = _inputs(tmp_path)
    inputs = [*_files(source), samples]
    original_bytes = [path.read_bytes() for path in inputs]
    output = tmp_path / "curated"
    result = _run(source, samples, output)
    assert result.returncode == 0, result.stderr
    assert [path.read_bytes() for path in inputs] == original_bytes
    assert {path.name for path in output.iterdir()} == OUTPUT_FILES
    source_header, source_rows = read_tsv_with_header(source / "alignment_matrix.tsv")
    header, selected = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert header == source_header
    assert selected == [source_rows[0], source_rows[2]]
    assert selected[0]["S1"] == "0"
    decision_header, decisions = read_tsv_with_header(output / "feature_decisions.tsv")
    assert decision_header == (
        "source_matrix_row_index",
        "feature_id",
        "Mz",
        "RT",
        "istd_label",
        "finite_count",
        "eligible_sample_count",
        "finite_fraction",
        "finite_fraction_decision",
        "finite_fraction_reason",
        "decision",
        "decision_reason",
    )
    assert [row["source_matrix_row_index"] for row in decisions] == [
        "1",
        "2",
        "3",
        "4",
    ]
    assert [row["feature_id"] for row in decisions] == [
        "F000001",
        "F000002",
        "F000003",
        "F000004",
    ]
    assert [row["Mz"] for row in decisions] == [row["Mz"] for row in source_rows]
    assert [row["finite_count"] for row in decisions] == ["2", "1", "3", "0"]
    assert {row["eligible_sample_count"] for row in decisions} == {"3"}
    assert [float(row["finite_fraction"]) for row in decisions] == [
        2 / 3,
        1 / 3,
        1,
        0,
    ]
    assert [row["finite_fraction_reason"] for row in decisions] == [
        "finite_fraction_at_or_above_minimum",
        "finite_fraction_below_minimum",
        "finite_fraction_at_or_above_minimum",
        "finite_fraction_below_minimum",
    ]
    assert [row["decision"] for row in decisions] == ["keep", "drop", "keep", "drop"]
    assert [row["decision_reason"] for row in decisions] == [
        "kept_by_all_steps",
        "dropped_by:finite_fraction",
        "kept_by_all_steps",
        "dropped_by:finite_fraction",
    ]
    assert {row["istd_label"] for row in decisions} == {""}
    _, matched = read_tsv_with_header(output / "sample_metadata.tsv")
    assert [row["matrix_column"] for row in matched] == list(SAMPLES)
    assert [row["sample_name"] for row in matched] == [
        f"sample_{name}" for name in SAMPLES
    ]
    summary = json.loads(
        (output / "matrix_curation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["steps"] == [
        {
            "key": "finite_fraction",
            "parameters": {"min_finite_fraction": 2 / 3},
            "kept_feature_count": 2,
            "input_feature_count": 4,
            "removed_feature_count": 2,
            "eligible_matrix_columns": ["S1", "S2", "S3"],
        }
    ]
    assert summary["istd"] == {
        "source": "",
        "standard_count": 0,
        "protected_feature_count": 0,
    }
    assert summary["inputs"]["sample_metadata"] == str(samples)
    assert (summary["feature_count"], summary["kept_feature_count"]) == (4, 2)
    assert summary["sample_count"] == len(SAMPLES)


@pytest.mark.parametrize("threshold", ["nan", "-0.1", "1.1", None])
def test_cli_rejects_invalid_or_missing_threshold(tmp_path, threshold):
    source, samples = _inputs(tmp_path)
    output = tmp_path / "curated"
    result = _run(source, samples, output, threshold=threshold)
    assert result.returncode == 2
    assert not output.exists()


def test_cli_rejects_unbound_samples_and_no_eligible_samples(tmp_path):
    source, samples = _inputs(tmp_path)
    header, metadata = read_tsv_with_header(samples)
    write_tsv(samples, [row for row in metadata if row["raw_stem"] != "S1"], header)
    output = tmp_path / "curated"
    result = _run(source, samples, output)
    assert result.returncode == 2
    assert "S1" in result.stderr
    assert not output.exists()
    for row in metadata:
        row["sample_role"] = "unknown"
    write_tsv(samples, metadata, header)
    result = _run(source, samples, output)
    assert result.returncode == 2
    assert "sample_role=sample" in result.stderr
    assert not output.exists()


def test_cli_does_not_overwrite_existing_output(tmp_path):
    source, samples = _inputs(tmp_path)
    output = tmp_path / "curated"
    output.mkdir()
    marker = output / "analysis_matrix.tsv"
    marker.write_text("existing result")
    result = _run(source, samples, output)
    assert result.returncode == 2
    assert marker.read_text() == "existing result"


def test_describe_steps_reads_only_a_valid_header(tmp_path):
    import csv

    from xic_workbench.matrix_curation import describe_source

    folder = alignment_output(tmp_path / "alignment")
    matrix = folder / "alignment_matrix.tsv"
    with matrix.open("a", encoding="utf-8") as stream:
        stream.write("9" * 200 + "\n")
    previous = csv.field_size_limit(100)
    try:
        assert describe_source(tmp_path / "config", {"source_dir": str(folder)})[
            "steps"
        ]
        matrix.write_text("Mz\tSample A\n")
        with pytest.raises(ValueError, match="missing required columns: RT"):
            describe_source(tmp_path / "config", {"source_dir": str(folder)})
    finally:
        csv.field_size_limit(previous)


def test_workbench_sample_data_never_uses_imported_reader_paths(tmp_path, monkeypatch):
    from xic_workbench.matrix_curation import describe_source
    from xic_workbench.samples import save_batch_samples

    config = tmp_path / "config"
    raw_dir = tmp_path / "raw"
    source = alignment_output(tmp_path / "alignment", raw_dir=raw_dir)
    monkeypatch.setattr(
        "xic_extractor.raw_sequence.read_analysis_set",
        lambda *_args, **_kwargs: pytest.fail("Workbench opened imported RAW metadata"),
    )

    def detect(**values):
        return describe_source(config, {"source_dir": str(source), **values})

    missing = detect()
    assert missing["sample_source"] is None

    def finite(detection):
        return next(
            step for step in detection["steps"] if step["key"] == "finite_fraction"
        )

    assert "找不到本次分析的樣本資訊" in finite(missing)["unavailable_reason"]

    raw_dir.mkdir()
    still_missing = detect()
    assert still_missing["sample_source"] is None
    assert "找不到本次分析的樣本資訊" in finite(still_missing)["unavailable_reason"]

    confirmed = save_batch_samples(config, raw_dir, sample_payload())["path"]
    assert detect()["sample_source"] == {"kind": "confirmed", "label": confirmed}
    run = detect(samples=sample_payload())
    assert run["sample_source"]["kind"] == "run"
    samples = tmp_path / "samples.tsv"
    write_tsv(samples, sample_rows(), SAMPLE_METADATA_COLUMNS)
    chosen = detect(samples=sample_payload(), sample_metadata_path=str(samples))
    assert chosen["sample_source"] == {"kind": "file", "label": str(samples)}
    assert Path(chosen["output_dir"]).parent == source


def test_run_samples_requires_an_explicit_reader_directory(tmp_path, monkeypatch):
    from xic_extractor.matrix_curation.export import read_run_samples
    from xic_extractor.matrix_curation.source import locate_curation_source
    from xic_workbench.samples import from_payload

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    folder = alignment_output(tmp_path / "alignment", raw_dir=raw_dir)
    source = locate_curation_source(folder)
    imported_dll_dir = Path(
        json.loads((folder / "alignment_run_metadata.json").read_text())["dll_dir"]
    )
    header_reads = []

    def read_analysis_set(root, dll_dir, **_kwargs):
        header_reads.append((root, dll_dir))
        return SimpleNamespace(sample_metadata=from_payload(sample_payload()))

    monkeypatch.setattr(
        "xic_extractor.raw_sequence.read_analysis_set", read_analysis_set
    )

    assert read_run_samples(source) is None
    trusted_dll_dir = tmp_path / "trusted-reader"
    assert trusted_dll_dir != imported_dll_dir
    rows = read_run_samples(source, dll_dir=trusted_dll_dir)

    assert rows == from_payload(sample_payload())
    assert header_reads == [(raw_dir, trusted_dll_dir)]


def test_saved_run_samples_are_reused_without_raw_or_current_batch_settings(tmp_path):
    from xic_extractor.matrix_curation.export import read_run_samples
    from xic_extractor.matrix_curation.source import locate_curation_source
    from xic_workbench.matrix_curation import describe_source, resolve_samples

    folder = alignment_output(tmp_path / "alignment")
    saved = sample_rows()
    saved[0]["group"] = "original batch"
    write_tsv(folder / "sample_metadata.tsv", saved, SAMPLE_METADATA_COLUMNS)
    source = locate_curation_source(folder)
    rows, origin = resolve_samples(tmp_path / "config", source, samples=[])
    assert origin["kind"] == "run"
    assert rows == read_run_samples(source)
    assert rows[0].group == "original batch"
    detected = describe_source(tmp_path / "config", {"source_dir": str(folder)})
    assert detected["sample_count"] == len(saved)
    assert detected["study_sample_count"] == 3


def test_workbench_job_matches_cli_and_writes_inside_the_source(tmp_path):
    import time

    from tests.workbench_lifecycle_fixtures import _launch, _request
    from xic_workbench.samples import save_batch_samples

    config = tmp_path / "config"
    raw_dir = tmp_path / "raw"
    source = alignment_output(tmp_path / "alignment", raw_dir=raw_dir)
    save_batch_samples(config, raw_dir, sample_payload())
    with _launch(config) as (_process, url):
        status, state = _request(
            url,
            "/api/analysis/start",
            {
                "kind": "curation",
                "values": {
                    "source_dir": str(source),
                    "steps": {"finite_fraction": {"min_finite_fraction": 0.6}},
                },
            },
        )
        assert status == 202, state
        deadline = time.monotonic() + 20
        while state["job"]["status"] in {"running", "cancelling"}:
            assert time.monotonic() < deadline, state
            time.sleep(0.02)
            status, state = _request(url, "/api/analysis")
            assert status == 200, state
        job = state["job"]
    assert job["status"] == "succeeded", job
    output = Path(job["result"]["output_dir"])
    assert output.parent == source
    assert (job["result"]["feature_count"], job["result"]["kept_feature_count"]) == (
        4,
        2,
    )
    samples = tmp_path / "samples.tsv"
    write_tsv(samples, sample_rows(), SAMPLE_METADATA_COLUMNS)
    cli_output = tmp_path / "cli"
    result = _run(source, samples, cli_output, threshold="0.6")
    assert result.returncode == 0, result.stderr
    for name in OUTPUT_FILES - {"matrix_curation_summary.json"}:
        assert (output / name).read_bytes() == (cli_output / name).read_bytes()


@pytest.mark.parametrize("protected", [False, True])
def test_preview_matches_export_without_writing_or_changing_inputs(tmp_path, protected):
    from xic_extractor.matrix_curation.export import (
        prepare_matrix_curation,
        read_sample_metadata_file,
    )
    from xic_extractor.matrix_curation.mass_rt_windows import MzRtWindow
    from xic_extractor.matrix_curation.source import locate_curation_source

    folder, samples = _inputs(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    evaluation = prepare_matrix_curation(
        source=locate_curation_source(folder),
        sample_metadata=read_sample_metadata_file(samples),
        sample_source=str(samples),
        steps={"finite_fraction": {"min_finite_fraction": 2 / 3}},
        istd_windows=(MzRtWindow("blank-only", 104, 2, 3),) if protected else (),
    )
    preview = evaluation.preview
    assert {
        str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before
    assert preview["kept"] == 2 + protected
    assert preview["removed"] == 2 - protected
    step = preview["steps"][0]
    assert (step["minimum_finite_count"], step["eligible_sample_count"]) == (2, 3)
    assert step["protected"] == int(protected)
    assert step["undetermined"] == 0
    output = tmp_path / "export"
    evaluation.write_to(output)
    _, rows = read_tsv_with_header(output / "analysis_matrix.tsv")
    assert len(rows) == preview["kept"]
    assert [row["Mz"] for row in rows] == (
        ["101.0000", "103.0000", "104.0000"] if protected else ["101.0000", "103.0000"]
    )


def test_published_curation_reads_the_export_and_says_why_each_row_left(tmp_path):
    from xic_extractor.matrix_curation.published import read_published_curation

    source, samples = _inputs(tmp_path)
    output = tmp_path / "curated"
    assert _run(source, samples, output, threshold="0.5").returncode == 0

    curation = read_published_curation(output)

    assert curation.source_dir == source.resolve()
    assert curation.matrix_path == output / "analysis_matrix.tsv"
    assert curation.removed == {
        "F000002": "有效值比例 1/3，低於門檻 0.5",
        "F000004": "有效值比例 0/3，低於門檻 0.5",
    }


def test_same_compound_reasons_name_the_representative_and_source_rows(tmp_path):
    from tests.matrix_curation_fixtures import published_curation
    from xic_extractor.matrix_curation.published import read_published_curation

    def removed(feature_id, reason, representative, relation, via):
        return {
            "feature_id": feature_id,
            "same_compound_reason": reason,
            "same_compound_representative_feature_id": representative,
            "same_compound_relation": relation,
            "same_compound_via_feature_ids": via,
            "decision": "drop",
            "decision_reason": "dropped_by:same_compound",
        }

    folder = published_curation(
        tmp_path / "curated",
        tmp_path,
        [
            {"feature_id": "F1", "decision": "keep"},
            removed("F2", "derived_through_group", "F1", "13C_M+1|[M+Na]+", "F4;F56"),
            removed(
                "F3",
                "derived_representative_undetermined",
                "",
                "[M+Mg+HCOO]+|[M+Na]+",
                "F20;F181",
            ),
            removed(
                "F5",
                "non_protonated_ion_family",
                "F72",
                "ion_family:[3M-H+Mg]+",
                "F822",
            ),
            removed(
                "F6", "non_protonated_ion_family", "", "ion_family:[M+Na]+", "F181;F225"
            ),
        ],
    )

    assert read_published_curation(folder).removed == {
        "F2": "F1 的 13C 同位素、[M+Na]+（來自 F4、F56）",
        "F3": "[M+Mg+HCOO]+、[M+Na]+，代表特徵未定（來自 F20、F181）",
        "F5": "F72 的 [3M-H+Mg]+（來自 F822）",
        "F6": "[M+Na]+，沒有保留的 [M+H]+（來自 F181、F225）",
    }
