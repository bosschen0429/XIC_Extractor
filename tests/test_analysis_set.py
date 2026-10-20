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


def header_rows(headers, roles):
    return {
        h.raw_path: SampleMetadata(
            Path(h.raw_path).stem, Path(h.raw_path).stem, sample_role=roles[h.raw_path]
        )
        for h in headers
    }


def resolve(headers, roles, metadata=None):
    timeline = build_injection_timeline(headers, PerturbationPolicy())
    return select_analysis_set(timeline, header_rows(headers, roles), metadata or ())


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


@pytest.mark.parametrize("header_names", [("B", "B"), ("B", "different"), ("B", "A")])
def test_header_names_cannot_collide_with_another_raw_alias(header_names):
    from xic_extractor.sample_metadata import (
        mint_sample_metadata_snapshot,
        resolve_canonical_sample_identity,
    )

    headers = [header("/b/A.raw", 0), header("/b/B.raw", 11)]
    rows = {
        h.raw_path: SampleMetadata(
            name, Path(h.raw_path).stem, sample_role="study_sample"
        )
        for h, name in zip(headers, header_names, strict=True)
    }
    result = select_analysis_set(
        build_injection_timeline(headers, PerturbationPolicy()), rows
    )
    snapshot = mint_sample_metadata_snapshot(
        result.sample_metadata, identity_namespace="test"
    )
    assert resolve_canonical_sample_identity(snapshot, "A").metadata.raw_stem == "A"
    assert resolve_canonical_sample_identity(snapshot, "B").metadata.raw_stem == "B"


@pytest.mark.parametrize("same_stem_context", [False, True])
@pytest.mark.parametrize("retained_copy", [False, True])
def test_retained_alignment_keeps_full_root_injection_order(
    tmp_path, monkeypatch, same_stem_context, retained_copy
):
    physical_records = {}
    from tests.cid_nl_raw_fixture import (
        TEST_PROFILE,
        _current_candidates,
        _FakeRaw,
        _peak_config,
        _settings,
    )
    from tests.test_untargeted_cid_nl_real_data_runtime import _discovery_receipts
    from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
    from xic_extractor.raw_reader import read_analysis_set
    from xic_extractor.untargeted_execution.alignment import (
        prepare_untargeted_alignment_inputs,
    )

    paths = (
        tmp_path / ("context/A.raw" if same_stem_context else "Blank.raw"),
        tmp_path / "A.raw",
        tmp_path / "B.raw",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    minutes = (11, 0, 22) if same_stem_context else (0, 11, 22)
    facts = {
        path: (
            header(str(path), minutes[i]),
            "blank" if i == 0 else "study_sample",
            path.stem,
        )
        for i, path in enumerate(paths)
    }
    monkeypatch.setattr(
        "xic_extractor.raw_reader._analysis_header", lambda p, d: facts[p]
    )
    selected_paths = paths[1:]
    if retained_copy:
        copy = tmp_path / "retained" / "A.raw"
        copy.parent.mkdir()
        copy.write_bytes(paths[1].read_bytes())
        original_header, role, name = facts[paths[1]]
        facts[copy] = (replace(original_header, raw_path=str(copy)), role, name)
        selected_paths = (copy, paths[2])
    settings, config = _settings(), _peak_config(tmp_path)
    candidates = {
        p.stem: _current_candidates(
            _FakeRaw((7.98,)),
            raw_file=p,
            settings=settings,
            peak_config=config,
            physical_records=physical_records,
        )
        for p in selected_paths
    }
    receipts = _discovery_receipts(tmp_path, selected_paths, candidates)
    batch = DiscoveryBatchInput(
        sample_order=("A", "B"),
        candidate_csvs={},
        raw_files={p.stem: p for p in selected_paths},
        review_csvs={},
        candidate_receipts=receipts,
    )
    monkeypatch.setattr(
        "xic_extractor.untargeted_execution.alignment._load_physical_replay_inputs",
        lambda **options: (options["candidates_by_sample"], physical_records, None),
    )
    prepared = prepare_untargeted_alignment_inputs(
        signal_processing_profile=TEST_PROFILE,
        discovery_batch=batch,
        candidates_by_sample=candidates,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=config,
    )
    try:
        orders = {
            s.metadata.raw_stem: s.metadata.injection_order
            for s in prepared.plan.context.samples
        }
        assert orders == {"A": 1 if same_stem_context else 2, "B": 3}
        assert [
            (s.metadata.raw_stem, s.metadata.sample_name, s.metadata.sample_role)
            for s in prepared.plan.context.samples
        ] == [("A", "A", "study_sample"), ("B", "B", "study_sample")]
        assert orders == {
            e.raw_stem: e.injection_order
            for e in read_analysis_set(tmp_path, tmp_path).analyzed
        }
    finally:
        prepared.close()


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
    third = header("/b/c/S__2.raw", 22)
    roles[third.raw_path] = "study_sample"
    result = resolve([first, second, third], roles)
    assert [row.sample_name for row in result.sample_metadata] == ["S", "S__3", "S__2"]


def test_discover_raw_files_is_recursive(tmp_path: Path) -> None:
    (tmp_path / "Blank").mkdir()
    for rel in ("S.RAW", "Blank/B.raw", "notes.txt"):
        (tmp_path / rel).write_bytes(b"")
    assert [
        p.relative_to(tmp_path).as_posix() for p in discover_raw_files(tmp_path)
    ] == ["Blank/B.raw", "S.RAW"]


def test_unreadable_raw_stops_only_when_it_would_be_analyzed() -> None:
    readable = header("/b/S1.raw", 0)
    timeline = build_injection_timeline([readable], PerturbationPolicy())
    rows = header_rows([readable], {readable.raw_path: "study_sample"})

    def unread(stem, role):
        return {f"/b/{stem}.raw": SampleMetadata(stem, stem, sample_role=role)}

    result = select_analysis_set(timeline, rows, unreadable=unread("Blank_9", "blank"))
    assert result.raw_paths == (Path("/b/S1.raw"),)
    assert result.unreadable == (Path("/b/Blank_9.raw"),)
    excluded = SampleMetadata("S9", "S9", excluded=True)
    assert select_analysis_set(
        timeline, rows, [excluded], unreadable=unread("S9", "study_sample")
    ).unreadable == (Path("/b/S9.raw"),)
    with pytest.raises(ValueError, match="要分析的 RAW 檔頭.*S9.raw"):
        select_analysis_set(timeline, rows, unreadable=unread("S9", "study_sample"))


def test_reader_stops_on_an_unreadable_analysis_raw(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import read_analysis_set

    (tmp_path / "S1.raw").write_bytes(b"not a raw")
    with pytest.raises(ValueError, match="S1.raw"):
        read_analysis_set(tmp_path, tmp_path / "missing-dll")


def test_raw_owns_names_and_order_while_a_partial_sequence_annotates(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.test_sample_metadata_sequence_parsing import _create_synthetic_docx
    from xic_extractor import raw_reader

    # Two batches in one root; the Word sequence covers the newer one only and
    # numbers it Blank 1 ... Normal 4, against the RAW clock below.
    clock = {
        "older/Old_S1": 0,
        "newer/NormalBC001_DNA": 10,
        "newer/TumorBC001_DNA": 20,
        "newer/QC_Pool_1": 30,
        "newer/Blank_1": 40,
    }
    for rel in clock:
        (tmp_path / f"{rel}.raw").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"{rel}.raw").write_bytes(b"")

    def analysis_header(path: Path, _dll: Path):
        rel = path.relative_to(tmp_path).with_suffix("").as_posix()
        h = header(str(path.resolve()), clock[rel])
        return h, "study_sample", f"header {path.stem}"

    monkeypatch.setattr(raw_reader, "_analysis_header", analysis_header)
    sequence = tmp_path / "sequence.docx"
    _create_synthetic_docx(sequence)

    result = raw_reader.read_analysis_set(tmp_path, tmp_path, sequence_source=sequence)

    rows = {row.raw_stem: row for row in result.sample_metadata}
    assert [row.raw_stem for row in result.sample_metadata] == [
        "Old_S1",
        "NormalBC001_DNA",
        "TumorBC001_DNA",
        "QC_Pool_1",
        "Blank_1",
    ]
    assert [row.injection_order for row in result.sample_metadata] == [1, 2, 3, 4, 5]
    assert {row.sample_name for row in result.sample_metadata} == {
        f"header {stem}" for stem in rows
    }
    assert (rows["QC_Pool_1"].sample_role, rows["Blank_1"].sample_role) == (
        "pooled_qc",
        "blank",
    )
    assert rows["TumorBC001_DNA"].group == "Tumor"
    assert rows["Old_S1"].sample_role == "study_sample"
    assert result.sequence is not None and result.sequence.matched_count == 4


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
                    sample_type="Unknown",
                    sample_name="free text",
                    comment="",
                    instrument_method="",
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


def test_discovery_cli_excludes_metadata_flagged_nested_samples(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.run_discovery import _raw_paths_from_dir
    from tests.raw_header_fakes import install_fake_raw_headers
    from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS

    install_fake_raw_headers(monkeypatch)
    raws = tmp_path / "raws"
    for rel in ("S1.raw", "Blank/Blank_1.raw", "except sample/S2.raw", "sub/S3.raw"):
        (raws / rel).parent.mkdir(parents=True, exist_ok=True)
        (raws / rel).write_bytes(b"not a raw")
    metadata = tmp_path / "samples.tsv"
    row = dict.fromkeys(SAMPLE_METADATA_COLUMNS, "")
    row |= {
        "schema_version": "sample_metadata",
        "sample_name": "S2",
        "raw_stem": "S2",
        "excluded": "true",
        "exclusion_reason": "injected but outside the design",
    }
    metadata.write_text(
        "\t".join(SAMPLE_METADATA_COLUMNS)
        + "\n"
        + "\t".join(row[c] for c in SAMPLE_METADATA_COLUMNS)
        + "\n",
        encoding="utf-8",
    )
    paths = _raw_paths_from_dir(raws, tmp_path / "dll", metadata)
    assert [p.relative_to(raws.resolve()).as_posix() for p in paths] == [
        "S1.raw",
        "sub/S3.raw",
    ]


def test_alignment_locates_nested_analysis_members(tmp_path: Path) -> None:
    from xic_extractor.acquisition.raw_paths import existing_raw_paths

    nested = tmp_path / "sub" / "S3.raw"
    nested.parent.mkdir()
    nested.write_bytes(b"")
    outside = tmp_path.parent / "elsewhere" / "S4.raw"
    paths = existing_raw_paths(
        sample_order=("S3", "S4"),
        raw_files={"S3": nested, "S4": outside},
        raw_dir=tmp_path,
    )
    assert paths == {"S3": nested}
