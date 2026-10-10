from __future__ import annotations

import csv
from datetime import datetime
from types import SimpleNamespace

import pytest

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
            "preset": "dna_dr",
            "raw_dir": "raws",
            "custom_roles": {"sample-a": "qc"},
            "unknown": "must not persist",
        },
        config_dir=tmp_path,
    )

    restored = read_discovery_config(config_dir=tmp_path)

    assert restored["mode"] == "align_only"
    assert restored["custom_roles"] == {"sample-a": "qc"}
    assert "unknown" not in restored
    assert restored["output_dir"]


def test_samples_keep_shallowest_raw_and_complete_metadata(tmp_path) -> None:
    raw_dir = tmp_path / "raws"
    nested = raw_dir / "nested"
    nested.mkdir(parents=True)
    (raw_dir / "Sample-A.raw").write_bytes(b"")
    (nested / "sample-a.RAW").write_bytes(b"")
    (nested / "Sample-B.raw").write_bytes(b"")
    sequence = tmp_path / "sample_metadata.tsv"
    with sequence.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
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
        sequence_source=sequence,
        custom_roles={"Sample-A": "pooled_qc"},
    )

    assert [row["raw_stem"] for row in result["samples"]] == ["Sample-A", "Sample-B"]
    first = result["samples"][0]
    assert first == {
        "sample_name": "Alpha",
        "raw_stem": "Sample-A",
        "injection_order": 2,
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
    monkeypatch.setattr(
        "xic_extractor.raw_reader.read_raw_sample_header",
        lambda path, dll_dir: headers[path],
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
        "Sequence Beta",
        "Sequence Alpha",
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
    assert controls["rt_max"]["maximum"] == 60.0
    assert controls["resolver_mode"]["choices"]
    assert details["effective_values"]["rt_max"] == 60.0

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
    assert method_details(destination)["effective_values"]["nl_tolerance_ppm"] == 12.5


def test_method_adapter_rejects_unknown_override_without_writing(tmp_path) -> None:
    details = method_details("dna_dr")
    draft = details["draft"]
    draft["overrides"]["invented_knob"] = 1
    destination = tmp_path / "invalid.toml"

    with pytest.raises(ValueError, match="unsupported override"):
        save_method(draft, destination)

    assert not destination.exists()


def _request_values(tmp_path, mode: str) -> dict:
    return {
        "mode": mode,
        "preset": "dna_dr",
        "overrides": {"rt_max": 60.0},
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


def test_alignment_mode_emits_review_evidence_without_full_audit(
    tmp_path, monkeypatch
) -> None:
    import xic_workbench.untargeted as module

    values = _request_values(tmp_path, "align_only")
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
    identity = output / "alignment_matrix_identity.tsv"
    review = output / "alignment_review.tsv"
    evidence = output / "cid_nl_explanation.sqlite"
    for path in (matrix, identity, review, evidence):
        path.write_text("x", encoding="utf-8")
    locator = SimpleNamespace(report_path=evidence)
    captured = {}

    def align(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            review_tsv=review,
            matrix_tsv=matrix,
            matrix_identity_tsv=identity,
            cells_tsv=None,
            run_metadata_json=None,
            evidence=locator,
            owner_build_route_receipt=None,
        )

    monkeypatch.setattr(module, "run_untargeted_application", align)
    summary = UntargetedRun(
        parse_request(values), progress=lambda *_args: None, should_cancel=lambda: False
    ).execute()

    assert captured["emit_cid_nl_explanation"] is True
    assert captured["explanation_full_audit"] is False
    assert summary["evidence"] is locator
    assert summary["published_paths"] == [
        str(review),
        str(matrix),
        str(identity),
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
        lambda **_kwargs: SimpleNamespace(
            review_tsv=None,
            matrix_tsv=None,
            matrix_identity_tsv=None,
            cells_tsv=None,
            run_metadata_json=None,
            evidence=None,
            owner_build_route_receipt=None,
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

    monkeypatch.setattr("xic_extractor.raw_reader.read_raw_sample_header", header)
    rows = load_samples(raw_dir, dll_dir=tmp_path)["samples"]
    assert {r["raw_stem"]: r["sample_role"] for r in rows} == {
        "Blank_01": "blank",
        "Sample001": "solvent",
        "Sample002": "study_sample",
    }
