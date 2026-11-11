from __future__ import annotations

import csv
from datetime import datetime
from types import SimpleNamespace

import pytest

from tests.raw_header_fakes import install_raw_sample_headers
from xic_extractor.discovery.models import DiscoveryBatchOutputs
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.presets import load_preset
from xic_extractor.raw_reader import RawSampleHeader
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata
from xic_workbench.methods import list_methods, method_details, save_method
from xic_workbench.samples import export_samples, from_payload, load_samples
from xic_workbench.untargeted import UntargetedRun, parse_request
from xic_workbench.untargeted_config import (
    read_untargeted_config,
    write_untargeted_config,
)


def test_untargeted_config_round_trips_only_supported_fields(tmp_path) -> None:
    write_untargeted_config(
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

    restored = read_untargeted_config(config_dir=tmp_path)

    assert "mode" not in restored
    assert "backfill_enabled" not in restored
    assert "alignment_enabled" not in restored
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
                    "pooled_qc",
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


def test_samples_use_raw_header_chronology_and_sequence_roles(
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
        "blank",
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


@pytest.mark.parametrize("preset", ["pt_pattern", "pt_pattern_cid_nl_dr"])
def test_delta_mass_preset_saves_unchanged_with_its_mass_policy(tmp_path, preset):
    from xic_extractor.presets.apply import apply_to_delta_mass_discovery

    assert preset in {item["key"] for item in list_methods()}
    details = method_details(preset)
    assert details["tuning_controls"] == []
    assert details["preview"]["delta_mass_companions"] == 3
    destination = tmp_path / "pt.toml"
    save_method(details["draft"], destination)
    saved, builtin = load_preset(destination), load_preset(preset)
    assert apply_to_delta_mass_discovery(saved) == apply_to_delta_mass_discovery(
        builtin
    )
    # Delta Mass omitting the key means none; the saved file keeps Easy-IC.
    assert saved.discovery_overrides["mass_recalibration"] == "easy_ic_restore"
    assert saved.discovery_overrides == builtin.discovery_overrides
    assert (saved.tags, saved.combine_mode) == (builtin.tags, builtin.combine_mode)


def test_new_cid_nl_and_delta_mass_method_round_trips_every_field(tmp_path):
    overrides = method_details("pt_pattern")["draft"]["overrides"]
    draft = {
        "name": "Iso + dR",
        "description": "Workbench 建立的方法",
        "combine_mode": "intersection",
        "tags": [
            {"strategy": "delta_mass", "name": "Delta Mass", "pattern_id": "dm"},
            {"strategy": "neutral_loss", "name": "DNA_dR", "neutral_loss_da": 116.0474},
        ],
        "overrides": {
            **overrides,
            "mass_recalibration": "easy_ic_restore",
            "positions": [
                {"label": "base", "delta_da": 0.0, "ratio_min": 1.0, "ratio_max": 1.0},
                {
                    "label": "+1.0034",
                    "delta_da": 1.003355,
                    "ratio_min": 0.05,
                    "ratio_max": 0.5,
                },
            ],
            "anchor_positions": ["base"],
            "discriminating_positions": ["+1.0034"],
            "lowest_position": "+1.0034",
            "base_support_positions": ["+1.0034"],
        },
    }
    destination = tmp_path / "new.toml"
    save_method(draft, destination)
    reloaded = method_details(destination)["draft"]
    assert reloaded["overrides"] == draft["overrides"]
    assert reloaded["combine_mode"] == "intersection"
    assert [(t["strategy"], t["name"]) for t in reloaded["tags"]] == [
        ("delta_mass", "Delta Mass"),
        ("neutral_loss", "DNA_dR"),
    ]


@pytest.mark.parametrize("preset", ["pt_pattern", "pt_pattern_cid_nl_dr"])
def test_workbench_runs_delta_mass_discovery_without_alignment(
    tmp_path, monkeypatch, preset
):
    import xic_workbench.untargeted as module
    from xic_extractor.discovery.delta_mass_csv import DeltaMassDiscoveryOutputs
    from xic_extractor.discovery.models import DiscoveryRunOutputs

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raws = []
    for stem in ("A", "B"):
        (raw_dir / f"{stem}.raw").touch()
        raws.append(raw_dir / f"{stem}.raw")
    (raw_dir / "excluded.raw").touch()
    monkeypatch.setattr(
        module,
        "read_analysis_set",
        lambda *_args: SimpleNamespace(
            raw_paths=tuple(raws),
            sample_metadata=tuple(SampleMetadata(r.stem, r.stem) for r in raws),
        ),
    )
    discovered = {}

    def discovery(paths, *, output_dir, settings, **_kwargs):
        discovered.update(paths=paths, loss=settings.neutral_loss_profiles)
        per_sample = []
        for path in paths:
            folder = output_dir / path.stem
            folder.mkdir(parents=True)
            peaks = folder / "discovery_peaks.csv"
            peaks.write_text(
                f"peak_id,sample_stem\n{path.stem}-1,{path.stem}\n", encoding="utf-8"
            )
            per_sample.append(DiscoveryRunOutputs(peaks, folder / "e", folder / "f"))
        return DiscoveryBatchOutputs(output_dir / "index.csv", tuple(per_sample))

    captured = {}

    def delta_mass(
        *, raw_dir, output_dir, strategy, cid_nl_peaks_csv, raw_paths, **_kwargs
    ):
        captured.update(
            label=strategy.label,
            peaks=cid_nl_peaks_csv.read_text(encoding="utf-8")
            if cid_nl_peaks_csv
            else None,
            raws=tuple(raw_paths),
        )
        candidates = output_dir / "delta_mass_candidates.csv"
        candidates.write_text(
            "status\ncandidate\nrejected\ncandidate\n", encoding="utf-8"
        )
        return DeltaMassDiscoveryOutputs(
            candidates, output_dir / "positions.csv", output_dir / "sidecar.json"
        )

    monkeypatch.setattr(module, "run_discovery_batch", discovery)
    monkeypatch.setattr(module, "run_delta_mass_discovery", delta_mass)
    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        lambda *_args, **_kwargs: pytest.fail("Delta Mass must not run Alignment"),
    )
    summary = UntargetedRun(
        parse_request(
            {
                "preset": preset,
                "preset_snapshot": method_details(preset)["draft"],
                "raw_dir": str(raw_dir),
                "dll_dir": str(tmp_path),
                "output_dir": str(tmp_path / "out"),
            }
        ),
        progress=lambda *_args: None,
        should_cancel=lambda: False,
    ).execute()

    assert captured["label"] == preset
    # The selected samples, not every RAW in the folder.
    assert captured["raws"] == tuple(raws)
    assert summary["modules"] == {"delta_mass": "completed"}
    assert summary["delta_mass"]["candidate_count"] == 2
    assert summary["matrix_tsv"] is None
    if preset == "pt_pattern":
        assert captured["peaks"] is None and not discovered
    else:
        assert [p.neutral_loss_da for p in discovered["loss"]] == [116.0474]
        # One dR peak table across samples, for the composite's base join.
        assert captured["peaks"] == "peak_id,sample_stem\nA-1,A\nB-1,B\n"


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
    request = _request_values(tmp_path)
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


def _request_values(tmp_path) -> dict:
    return {
        "preset": "dna_dr",
        "overrides": {},
        "raw_dir": str(tmp_path / "raws"),
        "dll_dir": str(tmp_path / "dll"),
        "output_dir": str(tmp_path / "output"),
        "targets_csv": "",
        "samples": None,
    }


def test_parse_request_preserves_sample_metadata_and_rejects_bad_method(
    tmp_path,
) -> None:
    values = _request_values(tmp_path)
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
    assert request.execution_profile.alignment_workers == 4

    values["overrides"] = {"invented_knob": 1}
    with pytest.raises(ValueError, match="unknown CID-NL recipe override"):
        parse_request(values)


def test_full_analysis_runs_backfill_and_reports_saved_matrix_overview(
    tmp_path, monkeypatch
) -> None:
    import xic_workbench.untargeted as module

    values = _request_values(tmp_path)
    values["preset_snapshot"] = method_details("dna_dr")["draft"]
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    (tmp_path / "dll").mkdir()
    output = tmp_path / "output"
    output.mkdir()
    batch = tmp_path / "batch.csv"
    batch.write_text(
        "sample_stem,peak_count,admitted_peak_count\nA,3,1\n",
        encoding="utf-8",
    )
    matrix = output / "alignment_matrix.tsv"
    review = output / "alignment_review.tsv"
    evidence = output / "alignment_explanation.sqlite"
    for path in (matrix, review):
        path.write_text("x", encoding="utf-8")
    from tests.inspector_fixtures import create_review_batch

    evidence, _fixture_matrix = create_review_batch(output)
    locator = SimpleNamespace(report_path=evidence)
    raw = raw_dir / "A.raw"
    raw.touch()
    monkeypatch.setattr(
        module,
        "read_analysis_set",
        lambda *_args: SimpleNamespace(
            raw_paths=(raw,),
            sample_metadata=(
                SampleMetadata("A", "A", sample_role="study_sample", group="case"),
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "run_discovery_batch",
        lambda *_args, **_kwargs: DiscoveryBatchOutputs(
            batch_index_csv=batch, per_sample=()
        ),
    )
    captured = {}

    def align(request, context=None):
        captured.update(request=request, context=context)
        context.progress_callback(
            {"stage": "final_grouping", "completed": 2, "total": 4}
        )
        return AlignmentRunOutputs(
            review_tsv=review,
            matrix_tsv=matrix,
            evidence=locator,
        )

    monkeypatch.setattr(module, "run_untargeted_application", align)
    progress = []
    summary = UntargetedRun(
        parse_request(values),
        progress=lambda *args: progress.append(args),
        should_cancel=lambda: False,
    ).execute()

    assert captured["request"].publication.emit_alignment_explanation is True
    assert captured["request"].alignment_config.backfill_enabled is True
    assert captured["request"].publication.explanation_full_audit is False
    assert summary["overview"]["matrix_count"] == 3
    assert summary["overview"]["complete_count"] == 1
    assert summary["overview"]["unresolved_count"] == 1
    assert summary["evidence"] is locator
    assert (0, 0, "跨樣本特徵分組 · 2/4") in progress
    saved = load_sample_metadata(output / "sample_metadata.tsv")
    assert [(s.raw_stem, s.sample_role, s.group) for s in saved] == [
        ("A", "study_sample", "case")
    ]
    assert summary["samples"][0]["group"] == "case"
    assert summary["published_paths"] == [
        str(output / "sample_metadata.tsv"),
        str(batch),
        str(review),
        str(matrix),
        str(evidence),
    ]


def test_full_mode_dispatches_discovery_then_alignment_and_honors_cancel(
    tmp_path, monkeypatch
) -> None:
    import xic_workbench.untargeted as module

    values = _request_values(tmp_path)
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    raw = raw_dir / "A.raw"
    raw.write_bytes(b"")
    output = tmp_path / "output"
    output.mkdir()
    index = output / "discovery_batch_index.csv"
    index.write_text(
        "sample_stem,peak_count,admitted_peak_count\nA,1,1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "read_analysis_set",
        lambda *_args: SimpleNamespace(
            raw_paths=(raw,), sample_metadata=(SampleMetadata("A", "A"),)
        ),
    )
    monkeypatch.setattr(
        module,
        "run_discovery_batch",
        lambda *_args, **_kwargs: DiscoveryBatchOutputs(
            batch_index_csv=index, per_sample=()
        ),
    )
    monkeypatch.setattr(
        module,
        "run_untargeted_application",
        lambda request, context=None, **_kwargs: AlignmentRunOutputs(),
    )
    progress = []
    summary = UntargetedRun(
        parse_request(values),
        progress=lambda *args: progress.append(args),
        should_cancel=lambda: False,
    ).execute()
    assert summary["published_paths"] == [
        str(output / "sample_metadata.tsv"),
        str(index),
    ]
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
    values = _request_values(tmp_path)
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
    values = _request_values(tmp_path)
    values.update(preset=str(preset_path), targets_csv=str(targets_path))
    submitted = []
    jobs = AnalysisJobs(config_dir)
    monkeypatch.setattr(jobs._executor, "submit", lambda *args: submitted.append(args))
    try:
        jobs.start("untargeted", values)
        runner = jobs._runner
        draft["overrides"]["nl_tolerance_ppm"] = 25
        save_method(draft, preset_path)
        targets_path.write_text("label,mz\nCHANGED,200\n")
        assert runner._build_settings().nl_tolerance_ppm == 12.5
        from xic_extractor.configuration.execution_profile import (
            default_parallel_workers,
        )

        assert (
            runner.request.execution_profile.parallel_workers
            == default_parallel_workers()
        )
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
    values = _request_values(tmp_path)
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


@pytest.mark.parametrize("policy", ("none", "easy_ic_probe", "easy_ic_restore"))
def test_alignment_istd_config_uses_recipe_mass_policy(tmp_path, policy):
    from dataclasses import replace

    from xic_extractor.configuration.mass_recalibration import (
        resolve_mass_recalibration,
    )
    from xic_extractor.presets import apply_to_discovery

    request = parse_request(
        dict(
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
