"""CID-NL batch Alignment through its formal entry, asserted on published files.

Each test persists Discovery results, replaces only the Thermo RAW boundary
with synthetic acquisitions, runs ``run_untargeted_application`` and reads the
artifacts it publishes. Expected values follow from the synthetic RAW shape:
apex RTs are the Gaussian centres and areas scale with the intensity factor.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_raw_fixture import _current_candidates, _FakeRaw, _settings
from tests.cid_nl_runtime_fixtures import install_runtime_raws, persist_discovery_batch
from tests.discovery_csv_fixtures import representative_event
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.applications.untargeted.application import (
    run_untargeted_application,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.applications.untargeted.request import (
    UntargetedRequest,
    untargeted_peak_config,
)
from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
    UntargetedExecutionPolicy,
)
from xic_extractor.configuration.mass_recalibration import MassRecalibration
from xic_extractor.discovery.csv_reader import DiscoveryBatchIndex
from xic_extractor.sample_metadata import SampleMetadata

# Synthetic acquisitions carry no lock masses, so Discovery recorded none.
SETTINGS = replace(_settings(), mass_recalibration=MassRecalibration.NONE)


def _raw_paths(tmp_path: Path, stems: tuple[str, ...]) -> dict[str, Path]:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    paths = {}
    for stem in stems:
        path = raw_dir / f"{stem}.raw"
        path.write_bytes(f"acquisition:{stem}".encode())
        paths[stem] = path
    return paths


def _discover(raw: _FakeRaw, path: Path, settings=SETTINGS):
    return tuple(_current_candidates(raw, raw_file=path, settings=settings))


def _samples(stems, **overrides: SampleMetadata) -> tuple[SampleMetadata, ...]:
    return tuple(
        overrides.get(
            stem,
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="sample",
            ),
        )
        for index, stem in enumerate(stems, start=1)
    )


def _publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    paths: Mapping[str, Path],
    raws: Mapping[str, _FakeRaw],
    peaks: Mapping[str, tuple],
    settings=SETTINGS,
    alignment_config: AlignmentConfig | None = None,
    sample_metadata: tuple[SampleMetadata, ...] | None = None,
    output_level: str = "machine",
    known_istd_reference_workbook: Path | None = None,
    name: str = "alignment",
) -> Path:
    """Run the formal Untargeted Alignment entry and return its output folder."""
    stems = tuple(paths)
    discovery = persist_discovery_batch(
        raw_paths=tuple(paths.values()),
        settings=settings,
        peaks_by_sample=peaks,
        root=tmp_path / f"{name}-discovery",
    )
    install_runtime_raws(
        monkeypatch,
        sources_by_path={paths[stem]: raws[stem] for stem in stems},
        tmp_path=tmp_path,
    )
    output_dir = tmp_path / name
    raw_dir = tmp_path / "raw"
    run_untargeted_application(
        UntargetedRequest(
            discovery_batch_index=DiscoveryBatchIndex(
                path=tmp_path / f"{name}-discovery" / "discovery_batch_index.csv",
                sample_order=discovery.sample_order,
                peak_csvs=discovery.peak_csvs,
                raw_files=discovery.raw_files,
                event_csvs=discovery.event_csvs,
                peak_receipts=discovery.peak_receipts,
            ),
            raw_dir=raw_dir,
            dll_dir=tmp_path,
            output_dir=output_dir,
            alignment_config=alignment_config
            or AlignmentConfig(anchor_min_seed_events=1),
            peak_config=untargeted_peak_config(
                raw_dir=raw_dir,
                dll_dir=tmp_path,
                output_dir=output_dir,
                discovery_settings=settings,
            ),
            discovery_settings=settings,
            sample_metadata=sample_metadata or _samples(stems),
            known_istd_reference_workbook=known_istd_reference_workbook,
            execution=UntargetedExecutionPolicy(
                profile=ExecutionProfile(
                    alignment_workers=1, backend="thread", overlap_partitions=False
                ),
                acquisition_cache_dir=tmp_path / f"{name}-facts",
            ),
            publication=UntargetedPublicationPolicy(
                output_level=output_level,
                emit_alignment_cells=True,
                emit_alignment_explanation=True,
            ),
        )
    )
    return output_dir


def _tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle, delimiter="\t"))


def _cells(output_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    return {
        (row["feature_id"], row["sample_stem"]): row
        for row in _tsv(output_dir / "alignment_cells.tsv")
    }


def _sources(output_dir: Path) -> set[str]:
    return {
        row["source_candidate_id"]
        for row in _cells(output_dir).values()
        if row["source_candidate_id"]
    }


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_completed_empty_recovery_preserves_each_compatible_source_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backfill_enabled: bool,
) -> None:
    stems = ("SupportA1", "SupportA2", "SupportB1", "SupportB2", "Missing")
    settings = replace(SETTINGS, precursor_mz_tolerance_ppm=20.0)
    paths = _raw_paths(tmp_path, stems)
    raws: dict[str, _FakeRaw] = {}
    peaks: dict[str, tuple] = {}
    precursors = (500.0, 500.0, 500.0105, 500.0105)
    for stem, precursor_mz in zip(stems[:4], precursors, strict=True):
        raws[stem] = _FakeRaw(
            (7.98,), scan_precursor_mz=precursor_mz, neutral_loss_das=(116.0474,)
        )
        peaks[stem] = _discover(raws[stem], paths[stem], settings)[:1]
    raws["Missing"] = _FakeRaw((7.98,))
    raws["Missing"].intensity[:] = 0.0
    peaks["Missing"] = ()

    output = _publish(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=AlignmentConfig(
            anchor_min_seed_events=1,
            product_mz_tolerance_ppm=30.0,
            backfill_enabled=backfill_enabled,
        ),
    )

    # Both 21 ppm-apart source subjects support one row.
    (row,) = _tsv(output / "alignment_matrix.tsv")
    assert row["Missing"] == ""
    support_areas = [float(row[stem]) for stem in stems[:4]]
    assert min(support_areas) > 0
    assert support_areas == pytest.approx([support_areas[0]] * 4, rel=1e-6)
    (review,) = _tsv(output / "alignment_review.tsv")
    assert float(review["group_center_mz"]) == pytest.approx(
        sum(precursors) / 4, abs=1e-3
    )
    cells = _cells(output)
    feature = review["feature_id"]
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
        )

        detail = read_alignment_explanation(
            output / "alignment_explanation.sqlite",
            candidate_id=peaks[stems[0]][0].peak_id,
        )
        missing_queries = [
            query
            for group in detail["groups"]
            for query in group["xic_extractions"]
            if query["sample_key"] == "Missing"
        ]
        assert len(missing_queries) == 1
        assert missing_queries[0]["group_position"] is True
        assert missing_queries[0]["rt_apex_min"] == pytest.approx(7.98, abs=0.02)
        assert missing_queries[0]["ppm"] == settings.precursor_mz_tolerance_ppm


def _nearby_subjects(tmp_path: Path):
    settings = replace(SETTINGS, precursor_mz_tolerance_ppm=1.0)
    paths = _raw_paths(tmp_path, ("S1",))
    source_raw, candidates = _nearby_subject_candidates(
        paths["S1"], settings=settings, peak_config=None
    )
    return settings, paths, source_raw, candidates


def test_sample_job_builder_binds_shared_inputs_to_each_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, paths, source_raw, candidates = _nearby_subjects(tmp_path)
    assert len(candidates) == 2

    output = _publish(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws={"S1": source_raw},
        peaks={"S1": candidates},
        settings=settings,
    )

    # One RAW and one sample's settings serve two 8 ppm-apart subjects: each
    # group keeps its own precursor and is integrated from that shared trace.
    # A one-sample group is reviewed, not published to the Matrix.
    assert _tsv(output / "alignment_matrix.tsv") == []
    rows = _tsv(output / "alignment_review.tsv")
    assert sorted(float(row["group_center_mz"]) for row in rows) == pytest.approx(
        sorted(candidate.peak_mz for candidate in candidates), abs=1e-4
    )
    areas = [float(cell["area"]) for cell in _cells(output).values()]
    assert len(areas) == 2
    assert areas[0] > 0
    assert areas == pytest.approx([areas[0]] * 2, rel=1e-6)


def test_alignment_adapter_prepares_and_executes_bounded_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request sample metadata reaches Alignment planning unchanged."""

    stems = ("Support1", "Support2", "Hold")
    paths = _raw_paths(tmp_path, stems)
    raws = {
        stem: _FakeRaw((rt,)) for stem, rt in zip(stems, (7.98, 8.0, 8.02), strict=True)
    }
    peaks = {stem: _discover(raws[stem], paths[stem])[:1] for stem in stems[:2]}
    peaks["Hold"] = ()
    outcomes = {}
    for role, excluded in (("sample", False), ("blank", False), ("sample", True)):
        name = f"{role}-{excluded}"
        output = _publish(
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
        )
        # Matrix columns follow the requested injection order.
        assert _header(output / "alignment_matrix.tsv")[2:] == [
            "Support2",
            "Hold",
            "Support1",
        ]
        (row,) = _tsv(output / "alignment_matrix.tsv")
        (hold,) = (
            cell for (_f, stem), cell in _cells(output).items() if stem == "Hold"
        )
        outcomes[(role, excluded)] = (row["Hold"] != "", hold["status"])

    # Backfill needs two eligible direct supports; a blank or excluded
    # Support2 leaves only Support1.
    assert outcomes == {
        ("sample", False): (True, "backfilled"),
        ("blank", False): (False, "not_searched"),
        ("sample", True): (False, "not_searched"),
    }


def test_production_batch_executes_one_atomic_sample_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, paths, source_raw, candidates = _nearby_subjects(tmp_path)

    output = _publish(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws={"S1": source_raw},
        peaks={"S1": candidates},
        settings=settings,
    )

    assert {path.name for path in output.iterdir() if path.is_file()} == {
        "alignment_matrix.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        "alignment_rt_anchors.tsv",
        "alignment_run_metadata.json",
        "alignment_explanation.sqlite",
    }
    review = {row["feature_id"]: row for row in _tsv(output / "alignment_review.tsv")}
    assert len(review) == 2
    candidate_by_mz = {round(c.peak_mz, 4): c.peak_id for c in candidates}
    assert {
        feature: cell["source_candidate_id"]
        for (feature, _stem), cell in _cells(output).items()
    } == {
        feature: candidate_by_mz[round(float(row["group_center_mz"]), 4)]
        for feature, row in review.items()
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
    above_threshold = scenario == "ambiguous_hold_above_threshold"
    ambiguous = scenario.startswith("ambiguous_hold_")
    stems = ("Support", "Hold", "Support2") if above_threshold else ("Support", "Hold")
    paths = _raw_paths(tmp_path, stems)
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
    support_candidates = _discover(raws["Support"], paths["Support"], settings)
    hold_candidates = _discover(raws["Hold"], paths["Hold"], settings)
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
                _discover(raw, path, settings)[0] for raw in (first_raw, second_raw)
            )
            second_raw.scans = (first_raw.scans[0], second_raw.scans[0])
            raws[stem] = second_raw
            peaks[stem] = tuple(
                sorted(nearby, key=lambda item: representative_event(item).precursor_mz)
            )
    if above_threshold:
        peaks["Support2"] = _discover(raws["Support2"], paths["Support2"], settings)

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

    output = _publish(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
    )

    assert _header(output / "alignment_matrix.tsv")[2:] == list(stems)
    written = _sources(output)
    assert written <= admitted
    assert not written & rejected
    assert bool(written) == bool(admitted)
    if not admitted:
        assert not _tsv(output / "alignment_matrix.tsv")
        assert not _tsv(output / "alignment_review.tsv")


@pytest.mark.parametrize("supported_samples", [2, 3])
def test_production_initial_groups_use_complete_feature_inference_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supported_samples: int,
) -> None:
    stems = ("Early", "Middle", "Late")
    apexes = (8.0, 8.1, 8.2)
    paths = _raw_paths(tmp_path, stems)
    raws = {stem: _FakeRaw((rt,)) for stem, rt in zip(stems, apexes, strict=True)}
    for stem in stems[supported_samples:]:
        raws[stem].intensity[:] = 0
    peaks = {stem: _discover(raws[stem], paths[stem]) for stem in stems}

    output = _publish(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        alignment_config=AlignmentConfig(),
    )

    # Owners from every supported sample join one row, centred on their mean.
    (row,) = _tsv(output / "alignment_matrix.tsv")
    (review,) = _tsv(output / "alignment_review.tsv")
    assert review["identity_reason"] == "established"
    assert review["detected_count"] == str(supported_samples)
    assert float(review["group_center_rt"]) == pytest.approx(
        sum(apexes[:supported_samples]) / supported_samples, abs=1e-3
    )
    cells = _cells(output)
    for stem in stems[:supported_samples]:
        assert float(row[stem]) > 0
        cell = cells[(review["feature_id"], stem)]
        assert cell["status"] == "detected"
        assert cell["source_candidate_id"] == peaks[stem][0].peak_id
    for stem in stems[supported_samples:]:
        assert row[stem] == ""


def test_known_istd_reference_closes_drifting_group_and_cell_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpyxl import Workbook

    stems = ("S0", "S1")
    drift_min = 1.067
    settings = replace(SETTINGS, precursor_mz_tolerance_ppm=20.0, rt_max=10)
    paths = _raw_paths(tmp_path, stems)
    raws = {}
    for i, (stem, precursor_mz, observed_loss) in enumerate(
        zip(stems, (500.0, 500.03), (116.0474, 116.0474005), strict=True)
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
    peaks = {stem: _discover(raws[stem], paths[stem], settings) for stem in stems}

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
    targets.append(["known", "ISTD", 500.015, 7, 10, 40, 116.0474, 20, 50, ""])
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
    plain = _publish(tmp_path, monkeypatch, name="plain", **common)
    corrected = _publish(
        tmp_path,
        monkeypatch,
        name="corrected",
        known_istd_reference_workbook=reference,
        **common,
    )

    # Without the reference, 60 ppm and 1.067 min apart stay two one-sample
    # groups, which are reviewed but not published to the Matrix.
    assert len(_tsv(plain / "alignment_review.tsv")) == 2
    assert _tsv(plain / "alignment_matrix.tsv") == []
    plain_values = {
        stem: float(cell["area"])
        for (_f, stem), cell in _cells(plain).items()
        if cell["area"]
    }
    assert set(plain_values) == set(stems)
    assert len(_tsv(corrected / "alignment_review.tsv")) == 1
    # The known ISTD reference binds both samples and closes one row whose
    # cells keep each sample's own drifted apex and area.
    (row,) = _tsv(corrected / "alignment_matrix.tsv")
    assert {stem: float(row[stem]) for stem in stems} == pytest.approx(plain_values)
    assert plain_values["S1"] / plain_values["S0"] == pytest.approx(1.4, rel=1e-3)
    cells = _cells(corrected)
    assert {
        stem: float(cell["apex_rt"]) for (_f, stem), cell in cells.items()
    } == pytest.approx({"S0": 8.0, "S1": 8.0 + drift_min}, abs=0.01)
    metadata = json.loads(
        (corrected / "alignment_run_metadata.json").read_text(encoding="utf-8")
    )
    assert any(key.startswith("known_istd_reference_") for key in metadata)


@pytest.mark.parametrize("recovery_case", ("positive", "threshold", "completed_empty"))
def test_production_composition_closes_recovered_observation_without_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery_case: str,
) -> None:
    stems = (
        ("Support1", "Hold")
        if recovery_case == "threshold"
        else ("Support1", "Support2", "Support3", "Hold")
    )
    apexes = (7.97, 8.03) if recovery_case == "threshold" else (7.97, 7.99, 8.01, 8.03)
    # Unequal heights separate the owner-mean RT from an area-weighted RT.
    scales = (1.0, 9.0) if recovery_case == "threshold" else (1.0, 9.0, 2.0, 9.0)
    paths = _raw_paths(tmp_path, stems)
    raws = {}
    for stem, apex, scale in zip(stems, apexes, scales, strict=True):
        raws[stem] = _FakeRaw((apex,))
        raws[stem].intensity *= scale
    if recovery_case != "positive":
        raws["Hold"].intensity[:] = 0.0
    peaks = {stem: _discover(raws[stem], paths[stem])[:1] for stem in stems[:-1]}
    peaks["Hold"] = ()

    output = _publish(tmp_path, monkeypatch, paths=paths, raws=raws, peaks=peaks)

    matrix = _tsv(output / "alignment_matrix.tsv")
    (review,) = _tsv(output / "alignment_review.tsv")
    cells = _cells(output)
    feature = review["feature_id"]
    hold = cells[(feature, "Hold")]
    supports = stems[:-1]
    assert {
        stem: cells[(feature, stem)]["source_candidate_id"] for stem in supports
    } == {stem: peaks[stem][0].peak_id for stem in supports}
    if recovery_case == "threshold":
        # One direct support is below K = 2: no row, Hold is not searched.
        assert matrix == []
        assert hold["status"] == "not_searched"
        return
    (row,) = matrix
    assert _header(output / "alignment_matrix.tsv")[2:] == list(stems)
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
    assert float(review["group_center_rt"]) == pytest.approx(owner_mean_rt, abs=1e-3)
    assert float(row["RT"]) == pytest.approx(owner_mean_rt, abs=0.005)
    assert float(review["group_center_mz"]) == pytest.approx(500.0, abs=1e-3)
    assert {stem: float(cells[(feature, stem)]["apex_rt"]) for stem in stems} == (
        pytest.approx(dict(zip(stems, apexes, strict=True)), abs=0.005)
    )


@pytest.mark.parametrize("output_level", ("machine", "validation-minimal", "debug"))
def test_atomic_publication_preserves_ms1_only_backfill_and_no_peak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_level: str,
) -> None:
    from xic_extractor.alignment_explanation.published_cells import (
        read_published_cells,
    )
    from xic_extractor.alignment_explanation.reader import read_review_evidence
    from xic_extractor.diagnostics.duplicate_quantification import (
        summarize_duplicate_quantification,
    )

    stems = ("Support1", "Support2", "Support3", "MS1Only", "NoPeak")
    apexes = (7.97, 7.99, 8.01, 8.03, 8.05)
    paths = _raw_paths(tmp_path, stems)
    raws = {}
    for scale, (stem, apex) in enumerate(zip(stems, apexes, strict=True), start=1):
        raw = _FakeRaw((apex,))
        raw.intensity *= scale
        if stem in {"MS1Only", "NoPeak"}:
            raw.scans = ()
        if stem == "NoPeak":
            raw.intensity[:] = 0.0
        raws[stem] = raw
    peaks = {stem: _discover(raws[stem], paths[stem]) for stem in stems}
    assert all(len(peaks[stem]) == 1 for stem in stems[:3])
    assert peaks["MS1Only"] == peaks["NoPeak"] == ()

    output = _publish(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        output_level=output_level,
    )

    (matrix_row,) = _tsv(output / "alignment_matrix.tsv")
    (review_row,) = _tsv(output / "alignment_review.tsv")
    cells = {stem: cell for (_f, stem), cell in _cells(output).items()}
    assert set(cells) == set(stems)
    # Areas scale 1:2:3:4 with the synthetic intensity factor.
    areas = [float(matrix_row[stem]) for stem in stems[:4]]
    assert areas == pytest.approx([areas[0] * k for k in (1, 2, 3, 4)], rel=1e-3)
    for stem in stems[:4]:
        assert float(cells[stem]["area"]) == pytest.approx(
            float(matrix_row[stem]), rel=5e-6
        )
    assert matrix_row["NoPeak"] == cells["NoPeak"]["area"] == ""
    assert review_row["detected_count"] == "3"
    assert review_row["accepted_backfill_count"] == "1"
    assert review_row["accepted_cell_count"] == "4"
    for stem in stems[:3]:
        assert cells[stem]["status"] == "detected"
        assert cells[stem]["source_candidate_id"] == peaks[stem][0].peak_id
    assert cells["MS1Only"]["status"] == "backfilled"
    assert cells["MS1Only"]["source_candidate_id"] == ""
    assert cells["NoPeak"]["status"] == "unavailable"
    assert cells["NoPeak"]["source_candidate_id"] == ""

    published = read_published_cells(output)
    assert {(cell.sample, cell.direct, cell.apex_rt_min) for cell in published} == {
        ("Support1", True, 7.97),
        ("Support2", True, 7.99),
        ("Support3", True, 8.01),
        ("MS1Only", False, 8.03),
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
