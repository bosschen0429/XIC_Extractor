from dataclasses import replace
from pathlib import Path

import pytest

from tests.analysis_set_fixtures import (
    header,
)
from xic_extractor import raw_reader_errors
from xic_extractor.analysis_set import discover_raw_files, select_analysis_set
from xic_extractor.injection_timeline import (
    PerturbationPolicy,
    build_injection_timeline,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_batch_header_reads_overlap_without_changing_acquisition_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Barrier

    from xic_extractor import raw_sequence

    paths = [tmp_path / "A.raw", tmp_path / "B.raw"]
    for path in paths:
        path.touch()
    rendezvous = Barrier(2, timeout=2)

    def read_header(path, _dll):
        # Both independent file reads must start before either finishes.
        rendezvous.wait()
        return (
            header(str(path.resolve()), 11 if path.stem == "A" else 0),
            "sample",
            path.stem,
        )

    monkeypatch.setattr(raw_sequence, "_analysis_header", read_header)
    result = raw_sequence.read_analysis_set(
        tmp_path, tmp_path, raw_paths=paths, analysis="untargeted"
    )
    assert [(entry.raw_stem, entry.injection_order) for entry in result.analyzed] == [
        ("B", 1),
        ("A", 2),
    ]


def header_rows(headers, roles):
    return {
        h.raw_path: SampleMetadata(
            Path(h.raw_path).stem, Path(h.raw_path).stem, sample_role=roles[h.raw_path]
        )
        for h in headers
    }


def resolve(headers, roles, metadata=None):
    timeline = build_injection_timeline(headers, PerturbationPolicy())
    return select_analysis_set(
        timeline, header_rows(headers, roles), metadata or (), analysis="untargeted"
    )


def test_flat_and_nested_layouts_give_the_same_analysis_set() -> None:
    names = [
        ("Blank_1", "blank", 0),
        ("SDOLEK-1", "other", 11),
        ("QC1", "qc", 22),
        ("Tumor_1", "sample", 33),
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
    headers = [header("/b/A.raw", 0), header("/b/B.raw", 11)]
    rows = {
        h.raw_path: SampleMetadata(name, Path(h.raw_path).stem, sample_role="sample")
        for h, name in zip(headers, header_names, strict=True)
    }
    result = select_analysis_set(
        build_injection_timeline(headers, PerturbationPolicy()),
        rows,
        analysis="untargeted",
    )
    for token in ("A", "B"):
        matches = [
            row
            for row in result.sample_metadata
            if token in {row.sample_name, row.raw_stem}
        ]
        assert [row.raw_stem for row in matches] == [token]


@pytest.mark.parametrize("same_stem_context", [False, True])
@pytest.mark.parametrize("retained_copy", [False, True])
def test_retained_alignment_keeps_full_root_injection_order(
    tmp_path, monkeypatch, same_stem_context, retained_copy
):
    from tests.cid_nl_raw_fixture import (
        TEST_PROFILE,
        _current_candidates,
        _FakeRaw,
        _peak_config,
        _settings,
    )
    from tests.cid_nl_runtime_fixtures import persist_discovery_batch
    from xic_extractor.discovery.input_source import DiscoveryInputSource
    from xic_extractor.raw_sequence import read_analysis_set
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
            "blank" if i == 0 else "sample",
            path.stem,
        )
        for i, path in enumerate(paths)
    }
    monkeypatch.setattr(
        "xic_extractor.raw_sequence._analysis_header", lambda p, d: facts[p]
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
        )
        for p in selected_paths
    }
    batch = persist_discovery_batch(
        raw_paths=selected_paths,
        settings=settings,
        peaks_by_sample=candidates,
        root=tmp_path / "discovery",
    )
    plan = prepare_untargeted_alignment_inputs(
        signal_processing_profile=TEST_PROFILE,
        discovery_batch=batch,
        peaks_by_sample=DiscoveryInputSource(batch),
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=config,
    )
    orders = {s.raw_stem: s.injection_order for s in plan.context.samples}
    assert orders == {"A": 1 if same_stem_context else 2, "B": 3}
    assert [
        (s.raw_stem, s.sample_name, s.sample_role) for s in plan.context.samples
    ] == [("A", "A", "sample"), ("B", "B", "sample")]
    assert orders == {
        e.raw_stem: e.injection_order
        for e in read_analysis_set(tmp_path, tmp_path, analysis="untargeted").analyzed
    }


def test_supplied_metadata_with_an_excluded_sample_aligns_the_analyzed_samples(
    tmp_path, monkeypatch
):
    """Workbench metadata keeps excluded rows; Discovery skipped those RAW."""
    from tests.cid_nl_raw_fixture import (
        TEST_PROFILE,
        _current_candidates,
        _FakeRaw,
        _peak_config,
        _settings,
    )
    from tests.cid_nl_runtime_fixtures import persist_discovery_batch
    from xic_extractor.discovery.input_source import DiscoveryInputSource
    from xic_extractor.untargeted_execution.alignment import (
        prepare_untargeted_alignment_inputs,
    )

    paths = (tmp_path / "A.raw", tmp_path / "B.raw")
    for path in paths:
        path.write_bytes(path.name.encode())
    settings, config = _settings(), _peak_config(tmp_path)
    candidates = {
        p.stem: _current_candidates(
            _FakeRaw((7.98,)),
            raw_file=p,
            settings=settings,
        )
        for p in paths
    }
    batch = persist_discovery_batch(
        raw_paths=paths,
        settings=settings,
        peaks_by_sample=candidates,
        root=tmp_path / "discovery",
    )
    metadata = (
        SampleMetadata("A", "A", injection_order=1, sample_role="sample"),
        SampleMetadata(
            "C",
            "C",
            injection_order=2,
            sample_role="sample",
            excluded=True,
            exclusion_reason="operator exclusion",
        ),
        SampleMetadata("B", "B", injection_order=3, sample_role="qc"),
    )
    plan = prepare_untargeted_alignment_inputs(
        signal_processing_profile=TEST_PROFILE,
        discovery_batch=batch,
        peaks_by_sample=DiscoveryInputSource(batch),
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=config,
        sample_metadata=metadata,
    )
    assert [
        (s.raw_stem, s.injection_order, s.sample_role) for s in plan.context.samples
    ] == [("A", 1, "sample"), ("B", 3, "qc")]


def test_copies_resolve_to_one_shallow_member() -> None:
    top = header("/b/Tumor_1.raw", 0)
    copy = replace(top, raw_path="/b/validation/Tumor_1.raw")
    result = resolve([copy, top], {top.raw_path: "sample", copy.raw_path: "sample"})
    assert result.raw_paths == (Path("/b/Tumor_1.raw"),)


def test_metadata_overrides_header_role_and_excludes_injected_samples() -> None:
    headers = [
        header("/b/except sample/Normal_1.raw", 0),
        header("/b/Pool_A.raw", 11),
        header("/b/Tumor_1.raw", 22),
    ]
    roles = {h.raw_path: "sample" for h in headers}
    metadata = [
        SampleMetadata(
            "Normal_1", "normal_1", excluded=True, exclusion_reason="design defect"
        ),
        SampleMetadata("Pool A", "Pool_A", sample_role="blank"),
        SampleMetadata("Tumor", "Tumor_1", sample_role=None),
    ]
    result = resolve(headers, roles, metadata)
    assert [
        (e.raw_stem, e.sample_role, e.role_source, e.context_reason)
        for e in result.entries
    ] == [
        ("Normal_1", "sample", "raw_header", "excluded"),
        ("Pool_A", "blank", "sample_metadata", "role"),
        ("Tumor_1", "sample", "raw_header", None),
    ]


def test_distinct_analyzed_injections_sharing_a_stem_are_rejected() -> None:
    first = header("/b/a/S.raw", 0, source="D:/run1/S.raw")
    second = header("/b/c/S.raw", 11, source="D:/run2/S.raw")
    roles = {first.raw_path: "sample", second.raw_path: "sample"}
    with pytest.raises(ValueError, match="share RAW stem: S"):
        resolve([first, second], roles)
    roles[second.raw_path] = "blank"
    assert resolve([first, second], roles).raw_paths == (Path("/b/a/S.raw"),)
    third = header("/b/c/S__2.raw", 22)
    roles[third.raw_path] = "sample"
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
    rows = header_rows([readable], {readable.raw_path: "sample"})

    def unread(stem, role):
        return {f"/b/{stem}.raw": SampleMetadata(stem, stem, sample_role=role)}

    result = select_analysis_set(
        timeline, rows, unreadable=unread("Blank_9", "blank"), analysis="untargeted"
    )
    assert result.raw_paths == (Path("/b/S1.raw"),)
    assert result.unreadable == (Path("/b/Blank_9.raw"),)
    excluded = SampleMetadata("S9", "S9", excluded=True)
    assert select_analysis_set(
        timeline,
        rows,
        [excluded],
        unreadable=unread("S9", "sample"),
        analysis="untargeted",
    ).unreadable == (Path("/b/S9.raw"),)
    with pytest.raises(ValueError, match="要分析的 RAW 檔頭.*S9.raw"):
        select_analysis_set(
            timeline, rows, unreadable=unread("S9", "sample"), analysis="untargeted"
        )


def test_targeted_analyzes_every_role_and_untargeted_only_samples_and_qc() -> None:
    names = [
        ("Blank_1", "blank", 0),
        ("SDOLEK-1", "other", 11),
        ("STD_1", "std", 22),
        ("QC1", "qc", 33),
        ("Tumor_1", "sample", 44),
        ("Tumor_2", "sample", 55),
    ]
    headers = [header(f"/b/{n}.raw", m) for n, _, m in names]
    rows = header_rows(
        headers, {h.raw_path: r for h, (_, r, _) in zip(headers, names, strict=True)}
    )
    timeline = build_injection_timeline(headers, PerturbationPolicy())
    excluded = [SampleMetadata("Tumor_2", "Tumor_2", excluded=True)]

    targeted = select_analysis_set(timeline, rows, excluded, analysis="targeted")
    untargeted = select_analysis_set(timeline, rows, excluded, analysis="untargeted")

    assert [e.raw_stem for e in targeted.analyzed] == [
        "Blank_1",
        "SDOLEK-1",
        "STD_1",
        "QC1",
        "Tumor_1",
    ]
    assert [e.raw_stem for e in untargeted.analyzed] == ["QC1", "Tumor_1"]
    # Exclusion is the only way out of Targeted, and it keeps the role.
    assert (targeted.entries[-1].sample_role, targeted.entries[-1].context_reason) == (
        "sample",
        "excluded",
    )


def test_listing_keeps_unreadable_raw_without_deciding_membership() -> None:
    readable = header("/b/S1.raw", 0)
    timeline = build_injection_timeline([readable], PerturbationPolicy())
    rows = header_rows([readable], {readable.raw_path: "sample"})
    unread = {
        "/b/Blank_9.raw": SampleMetadata("Blank_9", "Blank_9", sample_role="blank")
    }

    listed = select_analysis_set(timeline, rows, unreadable=unread, analysis=None)

    assert listed.analyzed == ()
    assert [
        (row.raw_stem, row.sample_role, row.injection_order)
        for row in listed.sample_metadata
    ] == [("S1", "sample", 1), ("Blank_9", "blank", None)]
    assert listed.unreadable == (Path("/b/Blank_9.raw"),)
    # Targeted analyzes a blank, so its unreadable header now stops the run.
    with pytest.raises(ValueError, match="Blank_9.raw"):
        select_analysis_set(timeline, rows, unreadable=unread, analysis="targeted")


def test_reader_stops_on_an_unreadable_analysis_raw(tmp_path: Path) -> None:
    from xic_extractor.raw_sequence import read_analysis_set

    (tmp_path / "S1.raw").write_bytes(b"not a raw")
    with pytest.raises(ValueError, match="S1.raw"):
        read_analysis_set(tmp_path, tmp_path / "missing-dll", analysis="untargeted")


def test_selected_analysis_does_not_open_explicit_context(tmp_path, monkeypatch):
    from xic_extractor import raw_sequence

    paths = {name: tmp_path / f"{name}.raw" for name in ("S1", "S2", "Blank", "old")}
    for path in paths.values():
        path.write_bytes(b"")
    opened = []

    def read_header(path, dll_dir):
        opened.append(path.name)
        assert path.stem in {"S1", "S2"}, "excluded/context RAW must not be opened"
        return (
            header(str(path.resolve()), 20 if path.stem == "S1" else 10),
            "sample",
            path.stem,
        )

    monkeypatch.setattr(raw_sequence, "_analysis_header", read_header)
    metadata = (
        SampleMetadata("S1", "S1", group="Exposure"),
        SampleMetadata("S2", "S2", sample_role="qc"),
        SampleMetadata("Blank", "Blank", sample_role="blank"),
        SampleMetadata("old", "old", excluded=True, exclusion_reason="replaced"),
    )
    analysis = raw_sequence.read_analysis_set(
        tmp_path, tmp_path, metadata, include_context=False, analysis="untargeted"
    )
    assert set(opened) == {"S1.raw", "S2.raw"}
    assert [entry.raw_stem for entry in analysis.analyzed] == ["S2", "S1"]
    assert [entry.injection_order for entry in analysis.analyzed] == [1, 2]
    assert analysis.analyzed[1].metadata.group == "Exposure"
    assert set(analysis.unopened_context) == {paths["Blank"], paths["old"]}
    contexts = [entry for entry in analysis.entries if not entry.analyzed]
    assert all(entry.metadata.injection_order is None for entry in contexts)
    assert contexts[-1].metadata.exclusion_reason == "replaced"


@pytest.mark.parametrize("last", ["readable", "unreadable", "unopened"])
def test_header_progress_ends_at_every_listed_raw(tmp_path, monkeypatch, last):
    from xic_extractor import raw_sequence

    paths = [tmp_path / f"{stem}.raw" for stem in ("S1", "S2", "Z")]
    for path in paths:
        path.write_bytes(b"")

    def read_header(path, dll_dir):
        if path.stem == "Z" and last == "unreadable":
            raise raw_reader_errors.RawReaderError("no MS controller")
        order = {"S1": 0, "S2": 1, "Z": 2}[path.stem]
        return header(str(path.resolve()), order), "sample", path.stem

    monkeypatch.setattr(raw_sequence, "_analysis_header", read_header)
    reports = []
    raw_sequence.read_analysis_set(
        tmp_path,
        tmp_path,
        (SampleMetadata("Z", "Z", sample_role="blank"),),
        include_context=last != "unopened",
        analysis="untargeted",
        progress=lambda done, total, _message: reports.append((done, total)),
    )
    assert reports[-1] == (3, 3)


def test_selected_unreadable_header_reports_the_file_and_original_reason(
    tmp_path, monkeypatch
):
    from xic_extractor import raw_sequence

    (tmp_path / "S1.raw").write_bytes(b"")

    def fail_header(path, dll_dir):
        raise raw_reader_errors.RawReaderError("no MS controller")

    monkeypatch.setattr(raw_sequence, "_analysis_header", fail_header)
    with pytest.raises(ValueError, match="S1.raw.*no MS controller"):
        raw_sequence.read_analysis_set(tmp_path, tmp_path, analysis="untargeted")


def test_raw_owns_names_and_order_while_a_partial_sequence_annotates(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.sample_metadata_sequence_parsing_fixtures import _create_synthetic_docx
    from xic_extractor import raw_sequence

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
        return h, "sample", f"header {path.stem}"

    monkeypatch.setattr(raw_sequence, "_analysis_header", analysis_header)
    sequence = tmp_path / "sequence.docx"
    _create_synthetic_docx(sequence)

    result = raw_sequence.read_analysis_set(
        tmp_path, tmp_path, sequence_source=sequence, analysis="untargeted"
    )

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
        "qc",
        "blank",
    )
    assert rows["TumorBC001_DNA"].group == ""
    assert rows["Old_S1"].sample_role == "sample"
    assert result.sequence is not None and result.sequence.matched_count == 4


def test_repeated_sequence_names_match_injections_not_nested_copies(
    tmp_path: Path, monkeypatch
) -> None:
    from xic_extractor import raw_sequence

    # A nested copy of A_1 is the same injection; it must not add a third
    # candidate for the two repeated "A" rows.
    clock = {"A_1": 0, "A_2": 11, "validation/A_1": 0}
    for rel in clock:
        (tmp_path / f"{rel}.raw").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"{rel}.raw").write_bytes(b"")

    def analysis_header(path: Path, _dll: Path):
        rel = path.relative_to(tmp_path).with_suffix("").as_posix()
        return header(str(path.resolve()), clock[rel]), "sample", path.stem

    monkeypatch.setattr(raw_sequence, "_analysis_header", analysis_header)
    sequence = tmp_path / "sequence.csv"
    sequence.write_text(
        "Order,Raw File,Sample Name,Role\n1,A,A,QC\n2,A,A,QC\n", encoding="utf-8"
    )

    result = raw_sequence.read_analysis_set(
        tmp_path, tmp_path, sequence_source=sequence, analysis="untargeted"
    )

    assert [(row.raw_stem, row.sample_role) for row in result.sample_metadata] == [
        ("A_1", "qc"),
        ("A_2", "qc"),
    ]
    assert result.sequence is not None and result.sequence.matched_count == 2


def test_unchanged_raw_header_is_read_once(tmp_path: Path, monkeypatch) -> None:
    import os
    from contextlib import nullcontext
    from types import SimpleNamespace

    from xic_extractor import raw_reader as native_reader
    from xic_extractor import raw_sequence

    raw = tmp_path / "S1.raw"
    raw.write_bytes(b"x")
    opens: list[Path] = []

    def fake_open(path: Path, dll_dir: Path):
        opens.append(path)
        return nullcontext(
            SimpleNamespace(
                sample_header=SimpleNamespace(
                    sample_type="Unknown",
                    sample_name=f"header {path.read_text()}",
                    comment="",
                    instrument_method="",
                )
            )
        )

    monkeypatch.setattr(native_reader, "open_raw", fake_open)
    monkeypatch.setattr(
        native_reader,
        "_injection_header",
        lambda handle, path: header(str(path.resolve()), 0),
    )
    first = raw_sequence.read_analysis_set(tmp_path, tmp_path, analysis="untargeted")
    second = raw_sequence.read_analysis_set(tmp_path, tmp_path, analysis="untargeted")
    assert first == second
    assert first.analyzed[0].role_source == "raw_header"
    assert opens == [raw]

    old_stat = raw.stat()
    replacement = tmp_path / "replacement.tmp"
    replacement.write_bytes(b"y")
    os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    replacement.replace(raw)
    replaced = raw_sequence.read_analysis_set(tmp_path, tmp_path, analysis="untargeted")
    assert replaced.analyzed[0].metadata.sample_name == "header y"
    assert opens == [raw, raw]


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
