"""CID-NL batch Alignment through its formal entry, asserted on published files.

Each test persists Discovery results, replaces only the Thermo RAW boundary
with synthetic acquisitions, runs ``run_untargeted_application`` and reads the
artifacts it publishes. Expected values follow from the synthetic RAW shape:
apex RTs are the Gaussian centres and areas scale with the intensity factor.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_raw_fixture import _FakeRaw
from tests.discovery_peak_fixtures import representative_event
from tests.untargeted_alignment_entry_fixtures import (
    SETTINGS,
    cell_sources,
    discover,
    publish_alignment,
    published_cells,
    published_matrix,
    raw_paths,
    read_tsv,
    tsv_header,
)
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.sample_metadata import SampleMetadata


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_completed_empty_recovery_preserves_each_compatible_source_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backfill_enabled: bool,
) -> None:
    """An empty initial search leaves the cell blank with or without Backfill.
    The 8 ppm-apart source subjects retain their own ions and support one row.
    """
    stems = ("SupportA1", "SupportA2", "SupportB1", "SupportB2", "Missing")
    settings = replace(SETTINGS, precursor_mz_tolerance_ppm=20.0)
    paths = raw_paths(tmp_path, stems)
    raws: dict[str, _FakeRaw] = {}
    peaks: dict[str, tuple] = {}
    precursors = (500.0, 500.0, 500.004, 500.004)
    for stem, precursor_mz in zip(stems[:4], precursors, strict=True):
        raws[stem] = _FakeRaw(
            (7.98,), scan_precursor_mz=precursor_mz, neutral_loss_das=(116.0474,)
        )
        raws[stem].additional_ms1_ions = {}
        peaks[stem] = discover(raws[stem], paths[stem], settings)[:1]
    raws["Missing"] = _FakeRaw((7.98,))
    raws["Missing"].additional_ms1_ions = {}
    raws["Missing"].intensity[:] = 0.0
    peaks["Missing"] = ()
    opened: set[str] = set()

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=AlignmentConfig(
            precursor_mz_tolerance_ppm=30.0,
            product_mz_tolerance_ppm=30.0,
            backfill_enabled=backfill_enabled,
        ),
        opened_samples=opened,
    )

    assert "Missing" in opened

    (row,) = read_tsv(output / "alignment_matrix.tsv")
    assert row["Missing"] == ""
    support_areas = [float(row[stem]) for stem in stems[:4]]
    assert min(support_areas) > 0
    assert support_areas == pytest.approx([support_areas[0]] * 4, rel=1e-6)
    (feature,) = published_matrix(output)
    assert float(row["Mz"]) == pytest.approx(sum(precursors) / 4, abs=1e-3)
    cells = published_cells(output)
    assert {
        stem: cells[(feature, stem)]["source_candidate_id"] for stem in stems[:4]
    } == {stem: peaks[stem][0].peak_id for stem in stems[:4]}
    missing = cells[(feature, "Missing")]
    assert missing["area"] == ""
    assert missing["status"] == ("unavailable" if backfill_enabled else "not_searched")
    metadata = json.loads(
        (output / "alignment_run_metadata.json").read_text(encoding="utf-8")
    )
    assert (metadata.get("backfill_execution") == "not_run") is not backfill_enabled

    if not backfill_enabled:
        from xic_extractor.alignment_explanation.reader import (
            read_alignment_explanation,
            read_review_curves,
        )

        report = output / "alignment_explanation.sqlite"
        detail = read_alignment_explanation(
            report, candidate_id=peaks[stems[0]][0].peak_id
        )
        missing_queries = [
            location
            for group in detail["groups"]
            if "feature_id" in group
            for location in read_review_curves(
                report, feature_id=group["feature_id"]
            ).locations
            if location.sample_key == "Missing"
        ]
        assert len(missing_queries) == 1
        assert missing_queries[0].physical_peak_key is None
        assert missing_queries[0].rt_apex_min == pytest.approx(7.98, abs=0.02)
        assert missing_queries[0].ppm == settings.precursor_mz_tolerance_ppm


def _nearby_subjects(tmp_path: Path):
    settings = replace(SETTINGS, precursor_mz_tolerance_ppm=1.0)
    paths = raw_paths(tmp_path, ("S1",))
    source_raw, candidates = _nearby_subject_candidates(paths["S1"], settings=settings)
    return settings, paths, source_raw, candidates


def test_one_raw_serves_two_nearby_subjects_as_separate_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two 8 ppm-apart ions have separate XICs and a known 0.7 area ratio;
    each keeps its own mass and CID, and singletons stay out of the Matrix.
    """
    settings, paths, source_raw, candidates = _nearby_subjects(tmp_path)
    assert len(candidates) == 2

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws={"S1": source_raw},
        peaks={"S1": candidates},
        settings=settings,
    )

    assert {path.name for path in output.iterdir() if path.is_file()} == {
        "alignment_matrix.tsv",
        "alignment_cells.tsv",
        "alignment_rt_anchors.tsv",
        "alignment_run_metadata.json",
        "alignment_explanation.sqlite",
    }
    assert read_tsv(output / "alignment_matrix.tsv") == []
    cells = published_cells(output)
    expected = sorted(candidates, key=lambda candidate: candidate.peak_mz)
    by_candidate = {cell["source_candidate_id"]: cell for cell in cells.values()}
    assert len({feature for feature, _ in cells}) == 2
    assert set(by_candidate) == {candidate.peak_id for candidate in expected}
    lower, higher = (float(by_candidate[c.peak_id]["area"]) for c in expected)
    assert lower > 0
    assert higher == pytest.approx(0.7 * lower, rel=1e-5)


def test_sample_metadata_sets_matrix_order_and_backfill_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request sample metadata reaches Alignment planning unchanged.

    Matrix columns follow injection order. An MS1-only peak is measured in
    every case, but a blank or excluded Support2 cannot vote to publish it.
    """

    stems = ("Support1", "Support2", "Hold")
    paths = raw_paths(tmp_path, stems)
    raws = {
        stem: _FakeRaw((rt,)) for stem, rt in zip(stems, (7.98, 8.0, 8.02), strict=True)
    }
    peaks = {stem: discover(raws[stem], paths[stem])[:1] for stem in stems[:2]}
    peaks["Hold"] = ()
    outcomes = {}
    for role, excluded in (("sample", False), ("blank", False), ("sample", True)):
        name = f"{role}-{excluded}"
        opened: set[str] = set()
        output = publish_alignment(
            tmp_path,
            monkeypatch,
            paths=paths,
            raws=raws,
            peaks=peaks,
            sample_metadata=(
                SampleMetadata(
                    sample_name="Support1",
                    raw_stem="Support1",
                    injection_order=3,
                    sample_role="sample",
                ),
                SampleMetadata(
                    sample_name="Support2",
                    raw_stem="Support2",
                    injection_order=1,
                    sample_role=role,
                    excluded=excluded,
                    exclusion_reason="operator exclusion" if excluded else "",
                ),
                SampleMetadata(
                    sample_name="Hold",
                    raw_stem="Hold",
                    injection_order=2,
                    sample_role="sample",
                ),
            ),
            name=name,
            opened_samples=opened,
        )
        assert tsv_header(output / "alignment_matrix.tsv")[2:] == [
            "Support2",
            "Hold",
            "Support1",
        ]
        (row,) = read_tsv(output / "alignment_matrix.tsv")
        (hold,) = (
            cell
            for (_f, stem), cell in published_cells(output).items()
            if stem == "Hold"
        )
        assert row["Support1"] != "" and row["Support2"] != ""
        assert hold["source_candidate_id"] == ""
        outcomes[(role, excluded)] = (
            row["Hold"] != "",
            hold["status"],
            "Hold" in opened,
        )

    assert outcomes == {
        ("sample", False): (True, "backfilled", True),
        ("blank", False): (False, "unresolved", True),
        ("sample", True): (False, "unresolved", True),
    }


@pytest.mark.parametrize(
    "scenario",
    (
        "mixed",
        "all_hold",
        "hold_no_input",
        "support_no_input",
        "all_reject",
        "hold_reject",
        "other_support",
        "ambiguous_hold_below_threshold",
        "ambiguous_hold_above_threshold",
    ),
)
def test_discovery_review_rows_cannot_become_alignment_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    """Only admitted peaks seed Alignment; the batch seed reaches other injections
    without turning their review-only Discovery candidates into row roots.
    """
    above_threshold = scenario == "ambiguous_hold_above_threshold"
    ambiguous = scenario.startswith("ambiguous_hold_")
    stems = ("Support", "Hold", "Support2") if above_threshold else ("Support", "Hold")
    paths = raw_paths(tmp_path, stems)
    settings = replace(SETTINGS, precursor_mz_tolerance_ppm=1.0)

    def observation_only_raw(peak_rts: tuple[float, ...]) -> _FakeRaw:
        raw = _FakeRaw(tuple(rt - 0.01 for rt in peak_rts))
        raw.scans = tuple(replace(scan, precursor_mz=500.5) for scan in raw.scans)
        raw.intensity[:] = [
            sum(
                1000.0 * math.exp(-0.5 * ((rt - center) / 0.015) ** 2)
                for center in peak_rts
            )
            for rt in raw.rt
        ]
        return raw

    raws = {stem: _FakeRaw((8.0,)) for stem in stems}
    if scenario not in {"all_reject", "hold_reject", "other_support"}:
        raws["Hold"] = observation_only_raw((7.8, 8.2) if ambiguous else (8.0,))
    if scenario in {"all_hold", "hold_no_input", "hold_reject"}:
        raws["Support"] = observation_only_raw((8.0,))
    rejected_samples = (
        ("Support", "Hold")
        if scenario == "all_reject"
        else ("Hold",)
        if scenario == "hold_reject"
        else ()
    )
    for stem in rejected_samples:
        raws[stem].intensity[:] = 0
    support_candidates = discover(raws["Support"], paths["Support"], settings)
    hold_candidates = discover(raws["Hold"], paths["Hold"], settings)
    peaks: dict[str, tuple] = {
        "Support": support_candidates[:1],
        "Hold": hold_candidates[:1],
    }
    if scenario in {"hold_no_input", "support_no_input"}:
        peaks["Hold"] = ()
    if ambiguous:
        peaks["Hold"] = hold_candidates
    if scenario == "other_support":
        for stem, path in paths.items():
            first_raw = observation_only_raw((7.98,))
            second_raw = _FakeRaw(
                (7.98,), scan_precursor_mz=500.004, neutral_loss_das=(116.0474,)
            )
            second_raw.intensity = first_raw.intensity.copy()
            nearby = tuple(
                discover(raw, path, settings)[0] for raw in (first_raw, second_raw)
            )
            second_raw.scans = (first_raw.scans[0], second_raw.scans[0])
            raws[stem] = second_raw
            peaks[stem] = tuple(
                sorted(
                    nearby,
                    key=lambda item: representative_event(item).scan.precursor_mz,
                )
            )
    if above_threshold:
        peaks["Support2"] = discover(raws["Support2"], paths["Support2"], settings)

    admitted = {c.peak_id for values in peaks.values() for c in values if c.admitted}
    rejected = {c.peak_id for values in peaks.values() for c in values} - admitted
    assert (
        len(admitted)
        == {
            "mixed": 1,
            "all_hold": 0,
            "hold_no_input": 0,
            "support_no_input": 1,
            "all_reject": 0,
            "hold_reject": 0,
            "other_support": 2,
            "ambiguous_hold_below_threshold": 1,
            "ambiguous_hold_above_threshold": 2,
        }[scenario]
    )

    opened: set[str] = set()
    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        opened_samples=opened,
    )

    assert opened == (set(stems) if admitted else set())
    assert tsv_header(output / "alignment_matrix.tsv")[2:] == list(stems)
    written = cell_sources(output)
    assert written <= admitted
    assert not written & rejected
    assert bool(written) == bool(admitted)
    if not admitted:
        assert not read_tsv(output / "alignment_matrix.tsv")


@pytest.mark.parametrize("supported_samples", [2, 3])
def test_owners_from_every_supported_sample_join_one_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supported_samples: int,
) -> None:
    """Owners from every supported sample join one row, centred on their mean."""
    stems = ("Early", "Middle", "Late")
    apexes = (8.0, 8.1, 8.2)
    paths = raw_paths(tmp_path, stems)
    raws = {stem: _FakeRaw((rt,)) for stem, rt in zip(stems, apexes, strict=True)}
    for stem in stems[supported_samples:]:
        raws[stem].intensity[:] = 0
    peaks = {stem: discover(raws[stem], paths[stem]) for stem in stems}

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        alignment_config=AlignmentConfig(),
    )

    (row,) = read_tsv(output / "alignment_matrix.tsv")
    (feature,) = published_matrix(output)
    assert float(row["RT"]) == pytest.approx(
        sum(apexes[:supported_samples]) / supported_samples, abs=1e-3
    )
    cells = published_cells(output)
    for stem in stems[:supported_samples]:
        assert float(row[stem]) > 0
        cell = cells[(feature, stem)]
        assert cell["status"] == "detected"
        assert cell["source_candidate_id"] == peaks[stem][0].peak_id
    for stem in stems[supported_samples:]:
        assert row[stem] == ""


def test_known_istd_reference_joins_drifting_samples_in_one_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the reference, 1.067 min apart stay two one-sample groups,
    which are reviewed but not published to the Matrix. The known ISTD reference
    binds both samples and closes one row whose cells keep each sample's own drifted
    apex and area.
    """
    from openpyxl import Workbook

    stems = ("S0", "S1")
    drift_min = 1.067
    settings = replace(SETTINGS, precursor_mz_tolerance_ppm=20.0, rt_max=10)
    paths = raw_paths(tmp_path, stems)
    raws = {}
    for i, (stem, precursor_mz, observed_loss) in enumerate(
        zip(stems, (500.0, 500.004), (116.0474, 116.0474005), strict=True)
    ):
        raw = _FakeRaw(
            (8.0,), scan_precursor_mz=precursor_mz, neutral_loss_das=(observed_loss,)
        )
        raw.rt += i * drift_min
        raw.intensity *= 1.0 + i * 0.4
        raw.scans = tuple(
            replace(scan, rt=scan.rt + i * drift_min) for scan in raw.scans
        )
        raws[stem] = raw
    peaks = {stem: discover(raws[stem], paths[stem], settings) for stem in stems}

    workbook = Workbook()
    targets = workbook.active
    targets.title = "Targets"
    targets.append(
        [
            "Label",
            "Role",
            "m/z",
            "RT min",
            "RT max",
            "ppm tol",
            "NL (Da)",
            "NL ppm warn",
            "NL ppm max",
            "ISTD Pair",
        ]
    )
    targets.append(["known", "ISTD", 500.002, 7, 10, 40, 116.0474, 20, 50, ""])
    results = workbook.create_sheet("XIC Results")
    results.append(["SampleName", "Target", "Role", "RT", "Area"])
    for i, stem in enumerate(stems):
        results.append([stem, "known", "ISTD", 8.0 + i * drift_min, -999])
    reference = tmp_path / "reference.xlsx"
    workbook.save(reference)

    common = dict(
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=AlignmentConfig(),
    )
    plain = publish_alignment(tmp_path, monkeypatch, name="plain", **common)
    corrected = publish_alignment(
        tmp_path,
        monkeypatch,
        name="corrected",
        known_istd_reference_workbook=reference,
        **common,
    )

    assert len({feature for feature, _ in published_cells(plain)}) == 2
    assert read_tsv(plain / "alignment_matrix.tsv") == []
    plain_values = {
        stem: float(cell["area"])
        for (_f, stem), cell in published_cells(plain).items()
        if cell["area"]
    }
    assert set(plain_values) == set(stems)
    (row,) = read_tsv(corrected / "alignment_matrix.tsv")
    assert {stem: float(row[stem]) for stem in stems} == pytest.approx(plain_values)
    assert plain_values["S1"] / plain_values["S0"] == pytest.approx(1.4, rel=1e-3)
    cells = published_cells(corrected)
    assert {
        stem: float(cell["apex_rt"]) for (_f, stem), cell in cells.items()
    } == pytest.approx({"S0": 8.0, "S1": 8.0 + drift_min}, abs=0.01)
    metadata = json.loads(
        (corrected / "alignment_run_metadata.json").read_text(encoding="utf-8")
    )
    assert any(key.startswith("known_istd_reference_") for key in metadata)


@pytest.mark.parametrize("recovery_case", ("positive", "threshold", "completed_empty"))
def test_backfill_recovers_sample_without_discovery_peak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery_case: str,
) -> None:
    """Unequal heights separate the owner-mean RT from an area-weighted RT. One direct
    support is below K = 2: no row and no residual Backfill for Hold.
    """
    stems = (
        ("Support1", "Hold")
        if recovery_case == "threshold"
        else ("Support1", "Support2", "Support3", "Hold")
    )
    apexes = (7.97, 8.03) if recovery_case == "threshold" else (7.97, 7.99, 8.01, 8.03)
    scales = (1.0, 9.0) if recovery_case == "threshold" else (1.0, 9.0, 2.0, 9.0)
    paths = raw_paths(tmp_path, stems)
    raws = {}
    for stem, apex, scale in zip(stems, apexes, scales, strict=True):
        raws[stem] = _FakeRaw((apex,))
        raws[stem].intensity *= scale
    if recovery_case != "positive":
        raws["Hold"].intensity[:] = 0.0
    peaks = {stem: discover(raws[stem], paths[stem])[:1] for stem in stems[:-1]}
    peaks["Hold"] = ()

    output = publish_alignment(
        tmp_path, monkeypatch, paths=paths, raws=raws, peaks=peaks
    )

    matrix = read_tsv(output / "alignment_matrix.tsv")
    cells = published_cells(output)
    (feature,) = {feature for feature, _ in cells}
    hold = cells[(feature, "Hold")]
    supports = stems[:-1]
    assert {
        stem: cells[(feature, stem)]["source_candidate_id"] for stem in supports
    } == {stem: peaks[stem][0].peak_id for stem in supports}
    if recovery_case == "threshold":
        assert matrix == []
        assert hold["status"] == "not_searched"
        return
    (row,) = matrix
    assert tsv_header(output / "alignment_matrix.tsv")[2:] == list(stems)
    if recovery_case == "completed_empty":
        assert row["Hold"] == "" and hold["status"] == "unavailable"
        assert all(float(row[stem]) > 0 for stem in supports)
        return

    assert hold["status"] == "backfilled"
    assert hold["source_candidate_id"] == ""
    assert float(row["Hold"]) > 0
    areas = {stem: float(row[stem]) for stem in stems}
    assert areas == pytest.approx(
        {
            stem: areas["Support1"] * scale
            for stem, scale in zip(stems, scales, strict=True)
        },
        rel=1e-3,
    )
    owner_mean_rt = sum(apexes[:-1]) / len(supports)
    weighted_rt = sum(
        float(cells[(feature, stem)]["apex_rt"]) * areas[stem] for stem in stems
    ) / sum(areas.values())
    assert abs(weighted_rt - owner_mean_rt) > 0.01
    assert float(row["RT"]) == pytest.approx(owner_mean_rt, abs=1e-3)
    assert float(row["Mz"]) == pytest.approx(500.0, abs=1e-3)
    assert {stem: float(cells[(feature, stem)]["apex_rt"]) for stem in stems} == (
        pytest.approx(dict(zip(stems, apexes, strict=True)), abs=0.005)
    )


@pytest.mark.parametrize("output_level", ("machine", "validation-minimal", "debug"))
def test_atomic_publication_preserves_ms1_peak_and_no_peak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_level: str,
) -> None:
    """Areas scale 1:2:3:4 with the synthetic intensity factor."""
    from xic_extractor.alignment_explanation.published_cells import (
        read_published_cells,
    )
    from xic_extractor.alignment_explanation.reader import read_review_evidence
    from xic_extractor.diagnostics.duplicate_quantification import (
        summarize_duplicate_quantification,
    )

    stems = ("Support1", "Support2", "Support3", "MS1Only", "NoPeak")
    apexes = (7.97, 7.99, 8.01, 8.03, 8.05)
    paths = raw_paths(tmp_path, stems)
    raws = {}
    for scale, (stem, apex) in enumerate(zip(stems, apexes, strict=True), start=1):
        raw = _FakeRaw((apex,))
        raw.intensity *= scale
        if stem in {"MS1Only", "NoPeak"}:
            raw.scans = ()
        if stem == "NoPeak":
            raw.intensity[:] = 0.0
        raws[stem] = raw
    peaks = {stem: discover(raws[stem], paths[stem]) for stem in stems}
    assert all(len(peaks[stem]) == 1 for stem in stems[:3])
    assert peaks["MS1Only"] == peaks["NoPeak"] == ()

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        output_level=output_level,
    )

    (matrix_row,) = read_tsv(output / "alignment_matrix.tsv")
    cells = {stem: cell for (_f, stem), cell in published_cells(output).items()}
    assert set(cells) == set(stems)
    areas = [float(matrix_row[stem]) for stem in stems[:4]]
    assert areas == pytest.approx([areas[0] * k for k in (1, 2, 3, 4)], rel=1e-3)
    for stem in stems[:4]:
        assert float(cells[stem]["area"]) == pytest.approx(
            float(matrix_row[stem]), rel=5e-6
        )
    assert matrix_row["NoPeak"] == cells["NoPeak"]["area"] == ""
    for stem in stems[:3]:
        assert cells[stem]["status"] == "detected"
        assert cells[stem]["source_candidate_id"] == peaks[stem][0].peak_id
    assert cells["MS1Only"]["status"] == "backfilled"
    assert cells["MS1Only"]["source_candidate_id"] == ""
    assert cells["NoPeak"]["status"] == "unavailable"
    assert cells["NoPeak"]["source_candidate_id"] == ""

    published = read_published_cells(output)
    assert {(cell.sample, cell.origin, cell.apex_rt_min) for cell in published} == {
        ("Support1", "detected", 7.97),
        ("Support2", "detected", 7.99),
        ("Support3", "detected", 8.01),
        ("MS1Only", "backfilled", 8.03),
    }
    assert {cell.sample: cell.value for cell in published} == pytest.approx(
        dict(zip(stems, areas, strict=False)), rel=5e-6
    )
    assert summarize_duplicate_quantification(published)["valued_cells"] == 4
    evidence = read_review_evidence(output / "alignment_explanation.sqlite")
    stems_by_key = {
        sample["sample_key"]: sample["sample_stem"]
        for sample in evidence.summary["samples"]
    }
    assert {
        stems_by_key[sample_key]: fact["apex_rt_min"]
        for group in evidence.groups.values()
        for sample_key, fact in group.cells.items()
    } == {
        "Support1": 7.97,
        "Support2": 7.99,
        "Support3": 8.01,
        "MS1Only": 8.03,
        "NoPeak": None,
    }


def test_paired_batch_publication_binds_matrix_review_and_sqlite(tmp_path, monkeypatch):
    """The real publisher consumes calibrated pairing without splitting values."""
    from xic_extractor.alignment.batch_correspondence import (
        BatchIstd,
        pair_grouped_batches,
    )
    from xic_extractor.alignment_explanation.published_cells import read_published_cells
    from xic_extractor.alignment_explanation.reader import read_review_evidence

    stems = ("Old1", "Old2", "New1", "New2", "New3")
    paths = raw_paths(tmp_path, stems)
    raws = {stem: _FakeRaw((8.0,)) for stem in stems}
    for scale, raw in enumerate(raws.values(), start=1):
        raw.intensity *= scale
    peaks = {stem: discover(raws[stem], paths[stem]) for stem in stems}
    metadata = tuple(
        SampleMetadata(
            stem, stem, injection_order=index, batch_id=stem[:-1], sample_role="sample"
        )
        for index, stem in enumerate(stems, start=1)
    )

    def pair_with_fixture_istds(batches, **kwargs):
        return pair_grouped_batches(
            [
                replace(
                    batch,
                    istds=(
                        BatchIstd(batch.batch_id, "early", 7.6),
                        BatchIstd(batch.batch_id, "late", 8.5),
                    ),
                )
                for batch in batches
            ],
            **kwargs,
        )

    monkeypatch.setattr(
        "xic_extractor.applications.untargeted.application.pair_grouped_batches",
        pair_with_fixture_istds,
    )
    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        sample_metadata=metadata,
    )
    (matrix,) = read_tsv(output / "alignment_matrix.tsv")
    (feature,) = published_matrix(output)
    areas = [float(matrix[stem]) for stem in stems]
    assert areas == pytest.approx(
        [areas[0] * i for i in range(1, len(stems) + 1)], rel=5e-6
    )
    cells = read_published_cells(output)
    assert {(cell.row, cell.feature_id, cell.origin) for cell in cells} == {
        (1, feature, "detected")
    }
    assert {cell.sample: cell.value for cell in cells} == pytest.approx(
        dict(zip(stems, areas, strict=True)), rel=5e-6
    )
    stored = read_review_evidence(output / "alignment_explanation.sqlite")
    row = stored.groups[feature]
    assert row.disposition == "established"
    assert {member["sample_key"] for member in row.members} == set(stems)
