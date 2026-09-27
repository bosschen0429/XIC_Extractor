from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from xic_extractor.analysis_set import discover_raw_files, select_analysis_set
from xic_extractor.injection_timeline import (
    InjectionHeader,
    PerturbationPolicy,
    build_injection_timeline,
)
from xic_extractor.sample_metadata import SampleMetadata

BASE = datetime(2026, 1, 1, 12)


def header(path: str, minute: float, *, source: str | None = None) -> InjectionHeader:
    return InjectionHeader(
        raw_path=path,
        acquisition_source=source or f"D:/acquired/{Path(path).name}",
        creation_date=BASE + timedelta(minutes=minute),
        start_time_min=0,
        end_time_min=10,
        instrument_method="m.meth",
    )


def resolve(headers, roles, metadata=None):
    timeline = build_injection_timeline(headers, PerturbationPolicy())
    return select_analysis_set(timeline, roles, metadata)


def test_flat_and_nested_layouts_give_the_same_analysis_set() -> None:
    names = [
        ("Blank_1", "blank", 0),
        ("SDOLEK-1", "system_suitability", 11),
        ("QC1", "qc", 22),
        ("Tumor_1", "study_sample", 33),
    ]
    flat = [header(f"/b/{n}.raw", m) for n, _, m in names]
    nested = [
        header(f"/b/{'Blank/' if r == 'blank' else ''}{n}.raw", m) for n, r, m in names
    ]
    flat_roles = {h.raw_path: r for h, (_, r, _) in zip(flat, names, strict=True)}
    nested_roles = {h.raw_path: r for h, (_, r, _) in zip(nested, names, strict=True)}

    flat_set = resolve(flat, flat_roles)
    nested_set = resolve(nested, nested_roles)

    assert [e.raw_stem for e in flat_set.analyzed] == ["QC1", "Tumor_1"]
    assert [e.raw_stem for e in nested_set.analyzed] == ["QC1", "Tumor_1"]
    assert [e.injection_order for e in nested_set.entries] == [1, 2, 3, 4]
    assert [e.context_reason for e in nested_set.entries[:2]] == ["role", "role"]


def test_copies_resolve_to_one_shallow_member() -> None:
    top = header("/b/Tumor_1.raw", 0)
    copy = replace(top, raw_path="/b/validation/Tumor_1.raw")
    result = resolve(
        [copy, top], {top.raw_path: "study_sample", copy.raw_path: "study_sample"}
    )
    assert result.raw_paths == (Path("/b/Tumor_1.raw"),)


def test_metadata_overrides_header_role_and_excludes_injected_samples() -> None:
    headers = [
        header("/b/except sample/Normal_1.raw", 0),
        header("/b/Pool_A.raw", 11),
        header("/b/Tumor_1.raw", 22),
    ]
    roles = {h.raw_path: "study_sample" for h in headers}
    metadata = [
        SampleMetadata(
            "Normal_1", "normal_1", excluded=True, exclusion_reason="design defect"
        ),
        SampleMetadata("Pool A", "Pool_A", sample_role="blank"),
        SampleMetadata("Tumor", "Tumor_1", sample_role="unknown"),
    ]
    result = resolve(headers, roles, metadata)
    assert [
        (e.raw_stem, e.sample_role, e.role_source, e.context_reason)
        for e in result.entries
    ] == [
        ("Normal_1", "study_sample", "raw_header", "excluded"),
        ("Pool_A", "blank", "sample_metadata", "role"),
        ("Tumor_1", "study_sample", "raw_header", None),
    ]


def test_distinct_analyzed_injections_sharing_a_stem_are_rejected() -> None:
    first = header("/b/a/S.raw", 0, source="D:/run1/S.raw")
    second = header("/b/c/S.raw", 11, source="D:/run2/S.raw")
    roles = {first.raw_path: "study_sample", second.raw_path: "study_sample"}
    with pytest.raises(ValueError, match="share RAW stem: S"):
        resolve([first, second], roles)
    roles[second.raw_path] = "blank"
    assert resolve([first, second], roles).raw_paths == (Path("/b/a/S.raw"),)


def test_discover_raw_files_is_recursive(tmp_path: Path) -> None:
    (tmp_path / "Blank").mkdir()
    for rel in ("S.RAW", "Blank/B.raw", "notes.txt"):
        (tmp_path / rel).write_bytes(b"")
    assert [
        p.relative_to(tmp_path).as_posix() for p in discover_raw_files(tmp_path)
    ] == ["Blank/B.raw", "S.RAW"]


def test_unreadable_raw_stays_a_filename_role_member(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import read_analysis_set

    (tmp_path / "Blank").mkdir()
    for rel in ("S1.raw", "Blank/Blank_1.raw"):
        (tmp_path / rel).write_bytes(b"not a raw")
    result = read_analysis_set(tmp_path, tmp_path / "missing-dll")
    assert [
        (e.raw_stem, e.role_source, e.injection_order, e.analyzed)
        for e in result.entries
    ] == [
        ("Blank_1", "raw_file_name", None, False),
        ("S1", "raw_file_name", None, True),
    ]


def test_unchanged_raw_header_is_read_once(tmp_path: Path, monkeypatch) -> None:
    from contextlib import nullcontext
    from types import SimpleNamespace

    from xic_extractor import raw_reader

    raw = tmp_path / "S1.raw"
    raw.write_bytes(b"x")
    opens: list[Path] = []

    def fake_open(path: Path, dll_dir: Path):
        opens.append(path)
        return nullcontext(
            SimpleNamespace(
                sample_header=SimpleNamespace(
                    sample_type="Unknown", sample_name="free text", comment=""
                )
            )
        )

    monkeypatch.setattr(raw_reader, "open_raw", fake_open)
    monkeypatch.setattr(
        raw_reader,
        "_injection_header",
        lambda handle, path: header(str(path.resolve()), 0),
    )
    first = raw_reader.read_analysis_set(tmp_path, tmp_path)
    second = raw_reader.read_analysis_set(tmp_path, tmp_path)
    assert first == second
    assert first.analyzed[0].role_source == "raw_header"
    assert opens == [raw]
