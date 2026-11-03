from __future__ import annotations

import csv
import math
from datetime import datetime
from types import SimpleNamespace

import pytest

from tests.raw_header_fakes import install_raw_sample_headers
from xic_extractor.presets import load_preset
from xic_extractor.raw_reader import RawSampleHeader
from xic_workbench.discovery_config_io import (
    read_discovery_config,
    write_discovery_config,
)
from xic_workbench.methods import list_methods, method_details, save_method
from xic_workbench.samples import export_samples, from_payload, load_samples
from xic_workbench.untargeted import UntargetedRun, parse_request


def test_untargeted_config_round_trips_only_supported_fields(tmp_path) -> None:
    write_discovery_config(
        {
            "mode": "align_only",
            "backfill_enabled": False,
            "preset": "dna_dr",
            "raw_dir": "raws",
            "custom_roles": {"sample-a": "qc"},
            "unknown": "must not persist",
        },
        config_dir=tmp_path,
    )

    restored = read_discovery_config(config_dir=tmp_path)

    assert restored["mode"] == "align_only"
    assert restored["backfill_enabled"] is False
    assert restored["alignment_enabled"] is True
    # Roles now live in the folder's confirmed sample metadata.
    assert "custom_roles" not in restored
    assert "unknown" not in restored
    assert restored["output_dir"]


def test_samples_take_annotations_from_metadata_but_names_and_order_from_raw(
    tmp_path, monkeypatch
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    for stem in ("Sample-A", "Sample-B"):
        (raw_dir / f"{stem}.raw").write_bytes(b"")
    install_raw_sample_headers(
        monkeypatch,
        lambda path: SimpleNamespace(
            sample_name=f"Header {path.stem}",
            sample_type="Unknown",
            comment="",
            instrument_method="",
        ),
        lambda path: {"Sample-A": 0, "Sample-B": 10}[path.stem],
    )
    sequence = tmp_path / "sample_metadata.tsv"
    with sequence.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="	")
        writer.writerow(
            (
                "schema_version",
                "sample_name",
                "raw_stem",
                "injection_order",
                "sample_role",
                "batch_id",
                "prep_batch_id",
                "matrix_type",
                "group",
                "excluded",
                "exclusion_reason",
            )
        )
        writer.writerows(
            (
                (
                    "sample_metadata",
                    "Alpha",
                    "Sample-A",
                    "2",
                    "study_sample",
                    "B1",
                    "P1",
                    "plasma",
                    "case",
                    "true",
                    "operator exclusion",
                ),
                (
                    "sample_metadata",
                    "Beta",
                    "Sample-B",
                    "1",
                    "qc",
                    "B1",
                    "P2",
                    "plasma",
                    "QC",
                    "false",
                    "",
                ),
            )
        )

    result = load_samples(
        raw_dir,
        dll_dir=tmp_path,
        sequence_source=sequence,
        custom_roles={"Sample-A": "pooled_qc"},
    )

    assert [row["raw_stem"] for row in result["samples"]] == ["Sample-A", "Sample-B"]
    # The metadata numbers 2 and 1 and names Alpha/Beta never override the RAW.
    assert result["samples"][0] == {
        "sample_name": "Header Sample-A",
        "raw_stem": "Sample-A",
        "injection_order": 1,
        "sample_role": "pooled_qc",
        "batch_id": "B1",
        "prep_batch_id": "P1",
        "matrix_type": "plasma",
        "group": "case",
        "excluded": True,
        "exclusion_reason": "operator exclusion",
    }
    assert result["summary"]["matched_count"] == 2
    assert result["summary"]["raw_count"] == 2


def test_samples_use_raw_header_order_and_preserve_sequence_and_role_overrides(
    tmp_path, monkeypatch
) -> None:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    raw_a = raw_dir / "Sample-A.raw"
    raw_b = raw_dir / "Sample-B.raw"
    raw_a.write_bytes(b"")
    raw_b.write_bytes(b"")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    headers = {
        raw_a: RawSampleHeader(
            raw_path=str(raw_a),
            raw_stem="Sample-A",
            sample_name="Header Alpha",
            sample_id="A",
            row_number=2,
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 1, 10, 0),
            vial="A:02",
            comment="",
        ),
        raw_b: RawSampleHeader(
            raw_path=str(raw_b),
            raw_stem="Sample-B",
            sample_name="Header Beta",
            sample_id="B",
            row_number=1,
            sample_type="QC",
            creation_date=datetime(2026, 1, 1, 9, 0),
            vial="A:01",
            comment="",
        ),
    }
    install_raw_sample_headers(
        monkeypatch,
        lambda path: headers[path],
        lambda path: (headers[path].creation_date - datetime(2026, 1, 1)).seconds / 60,
    )
    sequence = tmp_path / "sample_metadata.tsv"
    sequence.write_text(
        "schema_version\tsample_name\traw_stem\tinjection_order\t"
        "sample_role\tbatch_id\tprep_batch_id\tmatrix_type\tgroup\t"
        "excluded\texclusion_reason\n"
        "sample_metadata\tSequence Alpha\tSample-A\t\tqc\tB1\tP1\t"
        "plasma\tcase\tfalse\t\n"
        "sample_metadata\tSequence Beta\tSample-B\t\tblank\tB1\tP2\t"
        "plasma\tcontrol\tfalse\t\n",
        encoding="utf-8",
    )

    result = load_samples(
        raw_dir,
        dll_dir=dll_dir,
        sequence_source=sequence,
        custom_roles={"Sample-B": "pooled_qc"},
    )

    assert [row["raw_stem"] for row in result["samples"]] == [
        "Sample-B",
        "Sample-A",
    ]
    assert [row["injection_order"] for row in result["samples"]] == [1, 2]
    assert [row["sample_name"] for row in result["samples"]] == [
        "Header Beta",
        "Header Alpha",
    ]
    assert [row["sample_role"] for row in result["samples"]] == [
        "pooled_qc",
        "qc",
    ]
    assert result["samples"][1]["prep_batch_id"] == "P1"


def test_sample_payload_and_export_preserve_owner_schema(tmp_path) -> None:
    rows = [
        {
            "sample_name": "Alpha",
            "raw_stem": "Sample-A",
            "injection_order": 2,
            "sample_role": "study_sample",
            "batch_id": "B1",
            "prep_batch_id": "P1",
            "matrix_type": "plasma",
            "group": "case",
            "excluded": True,
            "exclusion_reason": "operator exclusion",
        }
    ]

    converted = from_payload(rows)
    destination = tmp_path / "samples.tsv"
    receipt = export_samples(rows, destination)

    assert converted is not None and converted[0].excluded is True
    assert receipt == {"path": str(destination), "row_count": 1}
    with destination.open(newline="", encoding="utf-8-sig") as handle:
        written = next(csv.DictReader(handle, delimiter="\t"))
    assert written["schema_version"] == "sample_metadata"
    assert written["exclusion_reason"] == "operator exclusion"


def test_method_adapter_exposes_effective_controls_and_saves_owner_toml(
    tmp_path,
) -> None:
    names = {item["key"] for item in list_methods()}
    assert {"dna_dr", "rna_ribose", "rna_mer"} <= names
    details = method_details("dna_dr")
    controls = {item["key"]: item for item in details["tuning_controls"]}
    assert "maximum" not in controls["rt_max"]
    assert "maximum" not in controls["rt_min"]
    assert controls["resolver_mode"]["choices"]
    assert details["effective_values"]["rt_min"] is None
    assert details["effective_values"]["rt_max"] is None
    assert details["preview"]["rt_range_mode"] == "acquisition"
    assert controls["rt_min"]["optional"] is True
    assert controls["rt_max"]["optional"] is True

    draft = details["draft"]
    draft["name"] = "Browser dR"
    draft["tags"].append({"enabled": True, "name": "custom", "neutral_loss_da": 100.25})
    draft["combine_mode"] = "union"
    draft["overrides"]["nl_tolerance_ppm"] = 12.5
    destination = tmp_path / "browser-method.toml"
    receipt = save_method(draft, destination)

    saved = load_preset(destination)
    assert receipt["path"] == str(destination.resolve())
    assert saved.name == "Browser dR"
    assert [tag.name for tag in saved.tags] == ["DNA_dR", "custom"]
    assert saved.combine_mode == "union"
    assert "rt_min" not in saved.discovery_overrides
    assert "rt_max" not in saved.discovery_overrides
    assert method_details(destination)["preview"]["rt_range_mode"] == "acquisition"
    assert method_details(destination)["effective_values"]["nl_tolerance_ppm"] == 12.5


def test_method_adapter_rejects_unknown_override_without_writing(tmp_path) -> None:
    details = method_details("dna_dr")
    draft = details["draft"]
    draft["overrides"]["invented_knob"] = 1
    destination = tmp_path / "invalid.toml"

    with pytest.raises(ValueError, match="unsupported override"):
        save_method(draft, destination)

    assert not destination.exists()


def test_method_adapter_preserves_long_rt_windows(tmp_path) -> None:
    draft = method_details("dna_dr")["draft"]
    draft["overrides"].update(rt_min=65.0, rt_max=120.0)
    destination = tmp_path / "long-method.toml"
    save_method(draft, destination)
    restored = method_details(destination)
    assert restored["effective_values"]["rt_min"] == 65.0
    assert restored["effective_values"]["rt_max"] == 120.0
    assert restored["preview"]["rt_range_mode"] == "crop"
    request = _request_values(tmp_path, "full")
    request["preset"] = str(destination)
    request["overrides"] = {}
    parse_request(request)


def test_method_adapter_clears_explicit_rt_crop_to_automatic(tmp_path) -> None:
    draft = method_details("dna_dr")["draft"]
    draft["overrides"].update(rt_min=65.0, rt_max=120.0)
    cropped = tmp_path / "cropped.toml"
    save_method(draft, cropped)
    imported = method_details(cropped)["draft"]
    imported["overrides"].update(rt_min=None, rt_max=None)
    automatic = tmp_path / "automatic.toml"
    save_method(imported, automatic)
    restored = method_details(automatic)
    assert restored["preview"]["rt_range_mode"] == "acquisition"
    assert "rt_min" not in load_preset(automatic).discovery_overrides
    assert "rt_max" not in load_preset(automatic).discovery_overrides


@pytest.mark.parametrize("bound", ["rt_min", "rt_max"])
def test_method_adapter_preserves_one_sided_rt_crop(tmp_path, bound) -> None:
    draft = method_details("dna_dr")["draft"]
    draft["overrides"][bound] = 75.0
    destination = tmp_path / "one-sided.toml"
    save_method(draft, destination)
    restored = method_details(destination)
    other = "rt_max" if bound == "rt_min" else "rt_min"
    assert restored["effective_values"][bound] == 75.0
    assert restored["effective_values"][other] is None
    assert other not in load_preset(destination).discovery_overrides


@pytest.mark.parametrize(
    "overrides",
    [
        {"rt_min": -1.0},
        {"rt_min": 121.0, "rt_max": 120.0},
        {"rt_max": float("inf")},
        {"rt_min": float("nan")},
    ],
)
def test_method_adapter_rejects_invalid_owner_rt_without_writing(
    tmp_path, overrides
) -> None:
    draft = method_details("dna_dr")["draft"]
    draft["overrides"].update(overrides)
    destination = tmp_path / "invalid-rt.toml"
    with pytest.raises(ValueError):
        save_method(draft, destination)
    assert not destination.exists()


def _request_values(tmp_path, mode: str) -> dict:
    return {
        "mode": mode,
        "preset": "dna_dr",
        "overrides": {},
        "raw_dir": str(tmp_path / "raws"),
        "raw_file": str(tmp_path / "one.raw"),
        "dll_dir": str(tmp_path / "dll"),
        "output_dir": str(tmp_path / "output"),
        "discovery_batch_index": str(tmp_path / "batch.csv"),
        "targets_csv": "",
        "samples": None,
        "execution_profile": {"parallel_workers": 2, "alignment_workers": 3},
    }


def test_parse_request_preserves_sample_metadata_and_rejects_bad_method(
    tmp_path,
) -> None:
    values = _request_values(tmp_path, "full")
    values["samples"] = [
        {
            "sample_name": "Alpha",
            "raw_stem": "Sample-A",
            "injection_order": 2,
            "sample_role": "study_sample",
            "batch_id": "B1",
            "prep_batch_id": "P1",
            "matrix_type": "plasma",
            "group": "case",
            "excluded": True,
            "exclusion_reason": "operator exclusion",
        }
    ]

    request = parse_request(values)

    assert request.sample_metadata is not None
    assert request.sample_metadata[0].exclusion_reason == "operator exclusion"
    assert request.execution_profile.alignment_workers == 3

    values["overrides"] = {"invented_knob": 1}
    with pytest.raises(ValueError, match="unknown CID-NL recipe override"):
        parse_request(values)


def test_discovery_only_publishes_owner_outputs_and_progress(
    tmp_path, monkeypatch
) -> None:
    import xic_workbench.untargeted as module

    values = _request_values(tmp_path, "discovery_only")
    raw = tmp_path / "one.raw"
    raw.write_bytes(b"")
    output = tmp_path / "output"
    output.mkdir()
    candidates = output / "discovery_candidates.csv"
    review = output / "discovery_review.csv"
    candidates.write_text("candidate_id\na\n", encoding="utf-8")
    review.write_text("review_priority\nHIGH\nLOW\n", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "run_discovery",
        lambda *_args, **_kwargs: SimpleNamespace(
            candidates_csv=candidates,
            review_csv=review,
        ),
    )
    progress = []

    summary = UntargetedRun(
        parse_request(values),
        progress=lambda current, total, label: progress.append((current, total, label)),
        should_cancel=lambda: False,
    ).execute()

    assert summary["mode"] == "discovery_only"
    assert summary["candidate_counts"] == {"HIGH": 1, "MEDIUM": 0, "LOW": 1, "total": 2}
    assert summary["published_paths"] == [str(candidates), str(review)]
    assert progress == [(0, 1, "Discovery..."), (1, 1, "完成")]


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_alignment_mode_emits_review_evidence_without_full_audit(
    tmp_path, monkeypatch, backfill_enabled
) -> None:
    import xic_workbench.untargeted as module

    values = _request_values(tmp_path, "align_only")
    values["backfill_enabled"] = backfill_enabled
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    (tmp_path / "dll").mkdir()
    output = tmp_path / "output"
    output.mkdir()
    batch = tmp_path / "batch.csv"
    batch.write_text(
        "sample_stem,candidate_count,high_count,medium_count,low_count\nA,3,1,1,1\n",
        encoding="utf-8",
    )
    matrix = output / "alignment_matrix.tsv"
    review = output / "alignment_review.tsv"
    evidence = output / "cid_nl_explanation.sqlite"
    for path in (matrix, review, evidence):
        path.write_text("x", encoding="utf-8")
    locator = SimpleNamespace(report_path=evidence)
    captured = {}

    def align(request, context=None):
        captured.update(request=request, context=context)
        return SimpleNamespace(
            review_tsv=review,
            matrix_tsv=matrix,
            cells_tsv=None,
            run_metadata_json=None,
            evidence=locator,
        )

    monkeypatch.setattr(module, "run_untargeted_application", align)
    summary = UntargetedRun(
        parse_request(values), progress=lambda *_args: None, should_cancel=lambda: False
    ).execute()

    assert captured["request"].publication.emit_cid_nl_explanation is True
    assert captured["request"].alignment_config.backfill_enabled is backfill_enabled
    assert captured["request"].publication.explanation_full_audit is False
    assert summary["evidence"] is locator
    assert summary["published_paths"] == [
        str(review),
        str(matrix),
        str(evidence),
    ]


def test_full_mode_dispatches_discovery_then_alignment_and_honors_cancel(
    tmp_path, monkeypatch
) -> None:
    import xic_workbench.untargeted as module

    values = _request_values(tmp_path, "full")
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    raw = raw_dir / "A.raw"
    raw.write_bytes(b"")
    output = tmp_path / "output"
    output.mkdir()
    index = output / "discovery_batch_index.csv"
    index.write_text(
        "sample_stem,candidate_count,high_count,medium_count,low_count\nA,1,1,0,0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "read_analysis_set",
        lambda *_args: SimpleNamespace(raw_paths=(raw,)),
    )
    monkeypatch.setattr(
        module,
        "run_discovery_batch",
        lambda *_args, **_kwargs: SimpleNamespace(batch_index_csv=index),
    )
    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        lambda request, context=None, **_kwargs: SimpleNamespace(
            review_tsv=None,
            matrix_tsv=None,
            cells_tsv=None,
            run_metadata_json=None,
            evidence=None,
        ),
    )
    progress = []
    summary = UntargetedRun(
        parse_request(values),
        progress=lambda *args: progress.append(args),
        should_cancel=lambda: False,
    ).execute()
    assert summary["mode"] == "full"
    assert summary["published_paths"] == [str(index)]
    assert progress[-1] == (2, 2, "完成")

    with pytest.raises(InterruptedError, match="cancelled"):
        UntargetedRun(
            parse_request(values),
            progress=lambda *_args: None,
            should_cancel=lambda: True,
        ).execute()


def test_saved_custom_method_snapshot_preserves_full_effective_values(tmp_path):
    from xic_extractor.presets import apply_to_discovery

    draft = method_details("dna_dr")["draft"]
    path = tmp_path / "custom.toml"
    save_method(draft, path)
    # A full owner-written TOML includes fields without editor controls.
    loaded = method_details(path)["draft"]
    loaded["overrides"]["ms2_precursor_tol_da"] = 0.37
    values = _request_values(tmp_path, "full")
    values["preset_snapshot"] = loaded
    request = parse_request(values)
    assert apply_to_discovery(request.preset_snapshot).ms2_precursor_tol_da == 0.37
    save_method(loaded, path)
    assert load_preset(path).discovery_overrides["ms2_precursor_tol_da"] == 0.37


def test_raw_sample_refresh_preserves_filename_and_method_roles(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    names = ("Blank_01", "Sample001", "Sample002")
    for name in names:
        (raw_dir / f"{name}.raw").touch()

    def header(path, dll_dir):
        return SimpleNamespace(
            raw_stem=path.stem,
            sample_name="A01",
            sample_type="Unknown",
            row_number=names.index(path.stem) + 1,
            creation_date=None,
            comment="",
            instrument_method="wash.meth" if path.stem == "Sample001" else "study.meth",
        )

    install_raw_sample_headers(
        monkeypatch,
        lambda path: header(path, None),
        lambda path: names.index(path.stem),
    )
    rows = load_samples(raw_dir, dll_dir=tmp_path)["samples"]
    assert {r["raw_stem"]: r["sample_role"] for r in rows} == {
        "Blank_01": "blank",
        "Sample001": "solvent",
        "Sample002": "study_sample",
    }


def test_untargeted_dispatch_freezes_recipe_profile_and_targets(tmp_path, monkeypatch):
    from xic_workbench.config_io import write_settings
    from xic_workbench.jobs import AnalysisJobs

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_settings({"dll_dir": "original"}, config_dir=config_dir)
    draft = method_details("dna_dr")["draft"]
    draft["overrides"]["nl_tolerance_ppm"] = 12.5
    preset_path = tmp_path / "method.toml"
    save_method(draft, preset_path)
    targets_path = tmp_path / "targets.csv"
    targets_path.write_text("label,mz\nISTD,100\n")
    values = _request_values(tmp_path, "discovery_only")
    values.update(preset=str(preset_path), targets_csv=str(targets_path))
    values["execution_profile"] = {"parallel_workers": 3, "alignment_workers": 4}
    submitted = []
    jobs = AnalysisJobs(config_dir)
    monkeypatch.setattr(jobs._executor, "submit", lambda *args: submitted.append(args))
    try:
        jobs.start("untargeted", values)
        runner = jobs._runner
        draft["overrides"]["nl_tolerance_ppm"] = 25
        save_method(draft, preset_path)
        values["execution_profile"]["parallel_workers"] = 7
        targets_path.write_text("label,mz\nCHANGED,200\n")
        assert runner._build_settings().nl_tolerance_ppm == 12.5
        assert runner.request.execution_profile.parallel_workers == 3
        assert runner.request.execution_profile.alignment_workers == 4
        assert runner.request.targets_csv.read_text() == "label,mz\nISTD,100\n"
        # Exercise the dispatched runner; only RAW computation is substituted.
        monkeypatch.setattr(
            runner,
            "execute",
            lambda: {"method_ppm": runner._build_settings().nl_tolerance_ppm},
        )
        callback, *args = submitted[0]
        callback(*args)
        assert jobs.state()["results"]["untargeted"] == {
            "method_ppm": 12.5,
            "samples": None,
            "review_paths": {
                "raw_dir": str(tmp_path / "raws"),
                "dll_dir": str(tmp_path / "dll"),
                "sequence_source": "",
            },
        }
    finally:
        jobs.close()


def test_untargeted_missing_targets_rejects_before_queue_or_active_save(
    tmp_path, monkeypatch
):
    from xic_workbench.config_io import write_settings
    from xic_workbench.jobs import AnalysisJobs

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_settings({"dll_dir": "original"}, config_dir=config_dir)
    original = (config_dir / "workspace.json").read_bytes()
    values = _request_values(tmp_path, "discovery_only")
    values["targets_csv"] = str(tmp_path / "missing.csv")
    submitted = []
    jobs = AnalysisJobs(config_dir)
    monkeypatch.setattr(jobs._executor, "submit", lambda *args: submitted.append(args))
    try:
        with pytest.raises(FileNotFoundError):
            jobs.start("untargeted", values)
        assert not submitted
        assert (config_dir / "workspace.json").read_bytes() == original
        assert jobs.state()["job"] is None
    finally:
        jobs.close()


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_alignment_off_runs_actual_sample_local_batch_without_matrix(
    tmp_path, monkeypatch, backfill_enabled
):
    import xic_workbench.untargeted as module
    from tests.test_discovery_pipeline import _FakeRawHandle, _peak_kwargs, _scan_event
    from xic_extractor.discovery.csv_reader import read_discovery_candidates_csv

    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    paths = tuple(raw_dir / f"{stem}.raw" for stem in ("A", "B"))
    for path in paths:
        path.write_bytes(path.stem.encode())
    install_raw_sample_headers(
        monkeypatch,
        lambda path: SimpleNamespace(
            sample_name=path.stem,
            sample_type="Unknown",
            comment="",
            instrument_method="",
        ),
        lambda path: 0 if path.stem == "A" else 10,
    )
    opened = []
    from xic_extractor.raw_reader import open_raw as open_header

    def open_raw(path, dll_dir, *, expected_acquisition_source=None, **_kwargs):
        if expected_acquisition_source is None:
            return open_header(path, dll_dir=dll_dir)
        raw = _FakeRawHandle(
            [
                _scan_event(
                    scan_number=100,
                    rt=8.0,
                    precursor_mz=500.0,
                    masses=[383.9526],
                    intensities=[9000.0],
                )
            ],
            **_peak_kwargs(8.0, 1000.0 if path.stem == "A" else 2300.0),
        )
        raw.acquisition_source = expected_acquisition_source
        opened.append(raw)
        return raw

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", open_raw)
    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        lambda *_a, **_k: pytest.fail("disabled Alignment ran"),
    )
    values = _request_values(tmp_path, "full")
    values.update(
        alignment_enabled=False,
        backfill_enabled=backfill_enabled,
        targets_csv=str(tmp_path / "unused-missing-targets.xlsx"),
        execution_profile={"parallel_workers": 1, "alignment_workers": 1},
    )
    summary = UntargetedRun(
        parse_request(values), progress=lambda *_a: None, should_cancel=lambda: False
    ).execute()
    assert summary["modules"] == {"alignment": "not_run", "backfill": "not_run"}
    assert summary["matrix_tsv"] is None and summary["alignment_outputs"] == {}
    output = tmp_path / "output"
    assert not list(output.glob("alignment*"))
    with (output / "discovery_batch_index.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    for path in output.rglob("discovery_candidates.csv"):
        (candidate,) = read_discovery_candidates_csv(path)
        expected_height = {"A": 1000.0, "B": 2300.0}[candidate.sample_stem]
        assert candidate.ms1_area == pytest.approx(
            expected_height
            * 0.1
            * math.sqrt(2.0 * math.pi)
            * 60.0
            * math.erf(math.sqrt(math.log(10))),
            rel=0.01,
        )
        assert candidate.matched_tag_names == ("DNA_dR",)
    assert len(list(output.rglob("discovery_candidates.csv"))) == 2
    assert opened and all(raw.closed for raw in opened)


def test_invalid_module_switches_fail_before_execution(tmp_path):
    values = _request_values(tmp_path, "align_only")
    values["alignment_enabled"] = False
    with pytest.raises(ValueError, match="align_only requires"):
        parse_request(values)
    values["alignment_enabled"] = True
    values["backfill_enabled"] = "false"
    with pytest.raises(TypeError, match="backfill_enabled must be a boolean"):
        parse_request(values)


@pytest.mark.parametrize("policy", ("none", "easy_ic_probe", "easy_ic_restore"))
def test_alignment_istd_config_uses_recipe_mass_policy(tmp_path, policy):
    from dataclasses import replace

    from xic_extractor.configuration.mass_recalibration import (
        resolve_mass_recalibration,
    )
    from xic_extractor.presets import apply_to_discovery

    request = parse_request(
        dict(
            mode="discovery_only",
            raw_dir=str(tmp_path),
            dll_dir=str(tmp_path),
            output_dir=str(tmp_path / "out"),
            preset="dna_dr",
        )
    )
    run = UntargetedRun(
        request, progress=lambda *args: None, should_cancel=lambda: False
    )
    settings = replace(
        apply_to_discovery(load_preset("dna_dr")),
        mass_recalibration=resolve_mass_recalibration(policy),
    )
    assert (
        run._alignment_peak_config(settings).mass_recalibration
        is settings.mass_recalibration
    )
