from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

from tests.raw_header_fakes import install_raw_sample_headers
from xic_extractor.sample_metadata import SampleMetadata, load_sample_metadata
from xic_workbench import targeted
from xic_workbench.samples import (
    read_batch_samples,
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


def test_legacy_roles_apply_only_before_annotations_are_confirmed(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("S1", "S2"))

    legacy = read_batch_samples(
        config, raw_dir, dll_dir=tmp_path, legacy_roles={"S1": "pooled_qc"}
    )
    assert legacy["samples"][0]["sample_role"] == "pooled_qc"

    save_batch_samples(config, raw_dir, legacy["samples"])
    rows = read_batch_samples(
        config, raw_dir, dll_dir=tmp_path, legacy_roles={"S1": "blank"}
    )["samples"]
    assert rows[0]["sample_role"] == "pooled_qc"


def test_read_offers_naming_suggestions_without_applying_them(
    tmp_path, monkeypatch
) -> None:
    raw_dir = _raw_batch(
        tmp_path, monkeypatch, ("TumorBC2312_DNA", "TumorBC2313_DNA", "QC_1")
    )

    result = read_batch_samples(tmp_path / "config", raw_dir, dll_dir=tmp_path)

    assert result["group_suggestions"] == {
        "TumorBC2312_DNA": "Tumor DNA",
        "TumorBC2313_DNA": "Tumor DNA",
        "QC_1": "QC",
    }
    assert all(row["group"] == "" for row in result["samples"])


def test_overview_counts_analyzed_samples_by_group_key() -> None:
    rows = (
        SampleMetadata("a", "a", sample_role="study_sample", group="Tumor"),
        SampleMetadata("b", "b", sample_role="study_sample", group=" tumor "),
        SampleMetadata("c", "c", sample_role="study_sample", group="Normal"),
        SampleMetadata("d", "d", sample_role="study_sample"),
        SampleMetadata("e", "e", sample_role="pooled_qc"),
        SampleMetadata("f", "f", sample_role="blank"),
        SampleMetadata(
            "g",
            "g",
            sample_role="study_sample",
            group="Tumor",
            excluded=True,
            exclusion_reason="clogged",
        ),
    )

    overview = sample_overview(rows)

    assert [(g["group"], g["count"]) for g in overview["groups"]] == [
        ("Tumor", 2),
        ("Normal", 1),
        ("Other", 1),
        ("QC", 1),
    ]
    colors = {g["group"]: g["color"] for g in overview["groups"]}
    assert colors["Other"] == "var(--group-other)"
    assert colors["QC"] == "var(--group-qc)"
    assert colors["Tumor"] != colors["Normal"]
    # Every analyzed row carries its group's colour, whatever its spelling.
    assert overview["row_groups"]["b"] == {"group": "tumor", "color": colors["Tumor"]}
    assert "f" not in overview["row_groups"] and "g" not in overview["row_groups"]
    assert overview["unassigned_study_samples"] == 1
    assert overview["single_sample_groups"] == ["Normal"]
    assert overview["excluded"] == [{"raw_stem": "g", "reason": "clogged"}]


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


def test_moved_folder_points_to_its_earlier_annotations(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config"
    stems = ("S1", "S2", "S3", "S4", "S5")
    raw_dir = _raw_batch(tmp_path, monkeypatch, stems)
    first = read_batch_samples(config, raw_dir, dll_dir=tmp_path)
    assert first["summary"]["earlier_annotations"] == []
    save_batch_samples(config, raw_dir, first["samples"])

    moved = tmp_path / "moved"
    raw_dir.rename(moved)
    (moved / "S5.raw").unlink()
    (moved / "New.raw").write_bytes(b"")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "Other.raw").write_bytes(b"")

    after_move = read_batch_samples(config, moved, dll_dir=tmp_path)
    assert after_move["summary"]["annotation_source"] == "raw"
    assert after_move["summary"]["earlier_annotations"] == [
        {
            "path": str(sample_annotations_path(config, raw_dir)),
            "matched": 4,
            "total": 5,
        }
    ]
    elsewhere = read_batch_samples(config, unrelated, dll_dir=tmp_path)
    assert elsewhere["summary"]["earlier_annotations"] == []


def test_collaborator_table_previews_matched_unmatched_and_ambiguous(
    tmp_path,
) -> None:
    from openpyxl import Workbook

    from xic_workbench.samples import preview_annotations

    raw_stems = ["LiverA1", "LiverB1", "QC_1", "QC_2", "Dup_X", "DupX"]
    pasted = (
        "Sample\tCondition\tExclude?\tWhy\n"
        "Liver A1\tFibrosis\t\t\n"
        "Liver B1\tHealthy\tyes\tclot\n"
        "Dup X\tFibrosis\t\t\n"
        "Kidney C1\tHealthy\t\t\n"
    )
    columns = preview_annotations(raw_stems, text=pasted)
    assert columns["columns"] == ["Sample", "Condition", "Exclude?", "Why"]
    assert columns["guess"] == {"name": 0, "group": 1, "excluded": 2, "reason": 3}

    preview = preview_annotations(raw_stems, text=pasted, columns=columns["guess"])
    assert preview["matched"] == [
        {
            "raw_stem": "LiverA1",
            "name": "Liver A1",
            "group": "Fibrosis",
            "excluded": False,
            "exclusion_reason": "",
        },
        {
            "raw_stem": "LiverB1",
            "name": "Liver B1",
            "group": "Healthy",
            "excluded": True,
            "exclusion_reason": "clot",
        },
    ]
    assert preview["unmatched"] == ["Kidney C1"]
    assert preview["ambiguous"] == ["Dup X"]

    book = Workbook()
    book.active.append(["File Name", "Group"])
    book.active.append(["Liver A1", "Fibrosis"])
    workbook = tmp_path / "collaborator.xlsx"
    book.save(workbook)
    from_file = preview_annotations(raw_stems, path=workbook)
    assert from_file["guess"]["name"] == 0 and from_file["guess"]["group"] == 1
    cohort = preview_annotations(raw_stems, text="Sample ID\tCohort\nLiver A1\t1\n")
    assert cohort["guess"]["group"] == 1
    matched = preview_annotations(
        raw_stems, path=workbook, columns={"name": 0, "group": 1}
    )["matched"]
    assert [row["raw_stem"] for row in matched] == ["LiverA1"]


def test_earlier_batch_metadata_carries_groups_by_sample_name(
    tmp_path, monkeypatch
) -> None:
    from xic_workbench.samples import list_confirmed_annotations, preview_annotations

    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("Tumor_A", "Normal_A"))
    rows = read_batch_samples(config, raw_dir, dll_dir=tmp_path)["samples"]
    rows[0].update(group="Tumor")
    rows[1].update(excluded=True, exclusion_reason="hemolysed")
    saved = Path(save_batch_samples(config, raw_dir, rows)["path"])

    preview = preview_annotations(["Tumor-A", "Normal-A", "New_B"], path=saved)
    assert preview["kind"] == "metadata"
    assert [(r["raw_stem"], r["group"], r["excluded"]) for r in preview["matched"]] == [
        ("Tumor-A", "Tumor", False),
        ("Normal-A", "", True),
    ]
    assert preview["unmatched_raw"] == ["New_B"]
    assert list_confirmed_annotations(config) == [{"path": str(saved), "name": "raws"}]


def test_earlier_metadata_binds_its_own_raw_exactly(tmp_path, monkeypatch) -> None:
    from xic_workbench.samples import preview_annotations

    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("Dup_X", "DupX", "Other"))
    rows = read_batch_samples(config, raw_dir, dll_dir=tmp_path)["samples"]
    rows[0].update(group="first")
    rows[1].update(group="second")
    saved = Path(save_batch_samples(config, raw_dir, rows)["path"])

    preview = preview_annotations(["DupX", "Dup_X", "Other"], path=saved)

    assert [(r["raw_stem"], r["group"]) for r in preview["matched"]] == [
        ("DupX", "second"),
        ("Dup_X", "first"),
        ("Other", ""),
    ]
    assert preview["ambiguous"] == []


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

    # A confirmed folder keeps its edits; an exclusion needs its reason.
    with pytest.raises(ValueError, match="請填寫排除原因"):
        exclude_sample(config, raw_dir, "QC_1", " ", dll_dir=None)
    exclude_sample(config, raw_dir, "QC_1", "carry-over", dll_dir=None)
    assert {
        row.raw_stem
        for row in load_sample_metadata(sample_annotations_path(config, raw_dir))
        if row.excluded
    } == {"S1", "QC_1"}
    with pytest.raises(ValueError, match="不在這個 RAW 資料夾"):
        exclude_sample(config, raw_dir, "Missing", "", dll_dir=None)


def test_an_exclusion_without_reason_is_never_written(tmp_path, monkeypatch) -> None:
    import pytest

    config = tmp_path / "config"
    raw_dir = _raw_batch(tmp_path, monkeypatch, ("S1", "S2"))
    rows = read_batch_samples(config, raw_dir, dll_dir=tmp_path)["samples"]
    save_batch_samples(config, raw_dir, rows)

    rows[1].update(excluded=True, exclusion_reason=" ")
    with pytest.raises(ValueError, match="排除的樣本需要填排除原因：S2"):
        save_batch_samples(config, raw_dir, rows)
    # The folder's file stays readable and unchanged.
    reread = read_batch_samples(config, raw_dir, dll_dir=tmp_path)["samples"]
    assert reread[1]["excluded"] is False
