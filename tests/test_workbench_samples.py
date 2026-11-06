from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from tests.raw_header_fakes import install_raw_sample_headers
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata
from xic_workbench import targeted
from xic_workbench.samples import (
    read_batch_samples,
    review_sample_groups,
    sample_annotations_path,
    sample_overview,
    save_batch_samples,
)


def _raw_batch(tmp_path: Path, monkeypatch, stems: tuple[str, ...]) -> Path:
    raw_dir = tmp_path / "raws"
    raw_dir.mkdir()
    for stem in stems:
        (raw_dir / f"{stem}.raw").write_bytes(b"")
    install_raw_sample_headers(
        monkeypatch,
        lambda path: SimpleNamespace(
            sample_name=path.stem,
            sample_type="Unknown",
            comment="",
            instrument_method="",
        ),
        lambda path: (stems + (path.stem,)).index(path.stem) * 10,
    )
    return raw_dir


def _sequence(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("File Name", "Sample Type", "Group"))
        writer.writerows(rows)
    return path


def test_annotation_file_is_one_per_raw_folder(tmp_path) -> None:
    first = sample_annotations_path(tmp_path / "config", tmp_path / "Batch A")
    again = sample_annotations_path(tmp_path / "config", tmp_path / "Batch A" / ".")
    other = sample_annotations_path(tmp_path / "config", tmp_path / "Batch B")

    assert first == again
    assert first != other
    assert first.parent == tmp_path / "config" / "sample_metadata"
    assert first.name.startswith("Batch_A-") and first.suffix == ".tsv"


def test_review_groups_use_confirmed_roles_by_raw_stem_without_raw_reads(tmp_path):
    config, raw_dir = tmp_path / "config", tmp_path / "raw"
    source = [
        {"sample_key": "canonical-study", "sample_stem": "Renamed_sample"},
        {"sample_key": "canonical-qc", "sample_stem": "Pool"},
        {"sample_key": "TumorBC12345_DNA"},
        {"sample_key": "unavailable-sample"},
    ]
    assert review_sample_groups(config, raw_dir, source) == {}
    # There are no RAW files or headers here; only confirmed folder annotations.
    save_batch_samples(
        config,
        raw_dir,
        [
            asdict(
                SampleMetadata(
                    "unrelated display alias",
                    "RENAMED_sample",
                    sample_role="study_sample",
                    group="Study",
                )
            ),
            asdict(SampleMetadata("Pool", "Pool", sample_role="pooled_qc")),
            asdict(SampleMetadata("TumorBC12345_DNA", "TumorBC12345_DNA")),
            asdict(SampleMetadata("not in report", "extra", group="Excluded")),
        ],
    )
    # Curves group by confirmed role; stored study groups are not shown.
    assert review_sample_groups(config, raw_dir, source) == {
        "canonical-study": "研究樣本",
        "canonical-qc": "Pooled QC",
        "TumorBC12345_DNA": "未指定",
    }
    assert review_sample_groups(config, tmp_path / "different-raw-folder", source) == {}


def test_sequence_preview_does_not_wait_for_raw_headers(tmp_path, monkeypatch):
    from tests.sample_metadata_sequence_parsing_fixtures import _create_synthetic_docx

    raw_dir = _raw_batch(tmp_path, monkeypatch, ("NormalBC001_DNA", "QC_Pool_1"))
    sequence = tmp_path / "sequence.docx"
    _create_synthetic_docx(sequence)
    monkeypatch.setattr(
        "xic_workbench.samples.read_analysis_set",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("RAW opened")),
    )
    result = read_batch_samples(
        tmp_path / "config",
        raw_dir,
        dll_dir=None,
        sequence_source=sequence,
        import_sequence=True,
        read_headers=False,
    )
    assert result["summary"]["matched_count"] == 2
    assert result["summary"]["chronology_pending"] is True
    assert result["summary"]["unmatched_sequence_names"]
    assert [row["sample_role"] for row in result["samples"]] == [
        "study_sample",
        "pooled_qc",
    ]
    assert all(row["injection_order"] is None for row in result["samples"])
    saved = load_sample_metadata(sample_annotations_path(tmp_path / "config", raw_dir))
    assert {row.raw_stem for row in saved} == {"NormalBC001_DNA", "QC_Pool_1"}


def test_confirmed_annotations_are_the_folder_source_until_reimported(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("Liver_A1", "Liver_B1", "QC_1"))
    first = _sequence(
        tmp_path / "first.csv",
        [("Liver_A1", "Unknown", ""), ("QC_1", "QC", "")],
    )

    read = read_batch_samples(config, raw_dir, dll_dir=tmp_path, sequence_source=first)
    assert read["summary"]["annotation_source"] == "sequence"
    assert not sample_annotations_path(config, raw_dir).exists()

    imported = read_batch_samples(
        config, raw_dir, dll_dir=tmp_path, sequence_source=first, import_sequence=True
    )
    rows = imported["samples"]
    rows[0].update(group="Liver", excluded=False)
    rows[1].update(group="", excluded=True, exclusion_reason="spray failed")
    save_batch_samples(config, raw_dir, rows)

    # A different 上機資訊 path does not silently replace confirmed annotations.
    other = _sequence(tmp_path / "other.csv", [("Liver_A1", "Blank", "Other")])
    refreshed = read_batch_samples(
        config, raw_dir, dll_dir=tmp_path, sequence_source=other
    )
    by_stem = {row["raw_stem"]: row for row in refreshed["samples"]}
    assert refreshed["summary"]["annotation_source"] == "confirmed"
    assert by_stem["Liver_A1"]["group"] == "Liver"
    assert by_stem["Liver_B1"]["excluded"] is True
    assert by_stem["QC_1"]["sample_role"] == "qc"

    # Re-importing takes roles from the list but keeps confirmed groups and
    # exclusions the list does not supply.
    reimported = read_batch_samples(
        config, raw_dir, dll_dir=tmp_path, sequence_source=other, import_sequence=True
    )
    by_stem = {row["raw_stem"]: row for row in reimported["samples"]}
    assert by_stem["Liver_A1"]["sample_role"] == "blank"
    assert by_stem["Liver_A1"]["group"] == "Liver"
    assert by_stem["Liver_B1"]["exclusion_reason"] == "spray failed"
    saved = {
        row.raw_stem: row
        for row in load_sample_metadata(sample_annotations_path(config, raw_dir))
    }
    assert saved["Liver_A1"].sample_role == "blank"


def test_confirmed_sample_metadata_preserves_qc_and_blank_roles(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("S1", "S2"))
    rows = read_batch_samples(config, raw_dir, dll_dir=tmp_path)["samples"]
    rows[0]["sample_role"] = "pooled_qc"
    rows[1]["sample_role"] = "blank"
    save_batch_samples(config, raw_dir, rows)
    restored = read_batch_samples(config, raw_dir, dll_dir=tmp_path)["samples"]
    assert [(row["raw_stem"], row["sample_role"]) for row in restored] == [
        ("S1", "pooled_qc"),
        ("S2", "blank"),
    ]


def test_overview_lists_excluded_samples_with_their_reasons() -> None:
    rows = (
        SampleMetadata("a", "a", sample_role="study_sample"),
        SampleMetadata(
            "g",
            "g",
            sample_role="study_sample",
            excluded=True,
            exclusion_reason="clogged",
        ),
        SampleMetadata("h", "h", sample_role="blank", excluded=True),
    )

    assert sample_overview(rows) == {
        "excluded": [
            {"raw_stem": "g", "reason": "clogged"},
            {"raw_stem": "h", "reason": ""},
        ]
    }


def test_targeted_run_reads_confirmed_annotations_for_this_run_only(
    tmp_path, monkeypatch
) -> None:
    calls = []

    class Stop(Exception):
        pass

    def prepare(config_dir, **kwargs):
        calls.append(kwargs)
        raise Stop

    monkeypatch.setattr(targeted.config_loader, "prepare_targeted_config", prepare)
    annotations = tmp_path / "confirmed.tsv"
    for value in (annotations, None):
        try:
            targeted.TargetedRun(
                tmp_path,
                sample_annotations=value,
                progress=lambda *_: None,
                should_cancel=lambda: False,
            )
        except Stop:
            pass

    assert calls[0]["settings_overrides"] == {
        "injection_order_source": str(annotations)
    }
    assert calls[1]["settings_overrides"] is None


def test_review_exclusion_keeps_the_batch_annotations(tmp_path, monkeypatch) -> None:
    import pytest

    from xic_workbench.samples import exclude_sample

    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("S1", "QC_1"))
    sequence = _sequence(tmp_path / "list.csv", [("S1", "Blank", "Liver")])

    # An unconfirmed folder is read with its batch's 上機資訊 first.
    result = exclude_sample(
        config, raw_dir, "s1", " clogged ", dll_dir=tmp_path, sequence_source=sequence
    )
    saved = {
        row.raw_stem: row
        for row in load_sample_metadata(sample_annotations_path(config, raw_dir))
    }
    assert (saved["S1"].excluded, saved["S1"].exclusion_reason) == (True, "clogged")
    assert (saved["S1"].sample_role, saved["S1"].group) == ("blank", "Liver")
    assert saved["QC_1"].excluded is False
    assert result["overview"]["excluded"] == [{"raw_stem": "S1", "reason": "clogged"}]

    # A confirmed folder keeps its edits; no reason is required.
    exclude_sample(config, raw_dir, "QC_1", dll_dir=None)
    assert {
        row.raw_stem
        for row in load_sample_metadata(sample_annotations_path(config, raw_dir))
        if row.excluded
    } == {"S1", "QC_1"}
    with pytest.raises(ValueError, match="不在這個 RAW 資料夾"):
        exclude_sample(config, raw_dir, "Missing", "", dll_dir=None)


def test_an_exclusion_without_reason_is_saved_and_reused(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("S1", "S2"))
    rows = read_batch_samples(config, raw_dir, dll_dir=tmp_path)["samples"]
    rows[1]["excluded"] = True
    save_batch_samples(config, raw_dir, rows)
    reread = read_batch_samples(config, raw_dir, dll_dir=tmp_path)["samples"]
    assert reread[1]["excluded"] is True
    assert reread[1]["exclusion_reason"] == ""


def test_filename_preview_preserves_unknown_role_when_groups_are_saved(
    tmp_path, monkeypatch
):
    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("S1", "QC_1"))
    result = read_batch_samples(config, raw_dir, dll_dir=None, read_headers=False)
    assert all(row["sample_role"] == "unknown" for row in result["samples"])
    result["samples"][0]["group"] = "Exposure"
    save_batch_samples(config, raw_dir, result["samples"])
    saved = load_sample_metadata(sample_annotations_path(config, raw_dir))
    assert all(row.sample_role == "unknown" for row in saved)
    assert saved[0].group == "Exposure"
