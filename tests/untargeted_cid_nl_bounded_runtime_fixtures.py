"""Helpers shared by test_untargeted_cid_nl_bounded_runtime and other test modules."""

from __future__ import annotations

from dataclasses import replace

from openpyxl import Workbook

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.cid_nl_runtime_fixtures import build_discovery_receipts, install_runtime_raws
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from xic_extractor.applications.untargeted.istd_reference import (
    read_known_istd_reference,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.input_planning import (
    prepare_eager_untargeted_plan,
)


def _plan(
    tmp_path, monkeypatch, *, scenario="positive", opened_sink=None, sample_count=4
):
    physical_records = {}
    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(sample_count))
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        precursor_mz_tolerance_ppm=(
            5.0 if scenario == "cross_subject_recovery" else 1.0
        ),
    )
    peak_config = _peak_config(tmp_path)
    raws = {}
    candidates = {}
    for path in paths:
        if not path.exists():
            path.write_bytes(path.stem.encode())
        raw, sample_candidates = _nearby_subject_candidates(
            path,
            settings=settings,
            peak_config=peak_config,
            physical_records=physical_records,
        )
        third_raw = _FakeRaw(
            (7.98,), scan_precursor_mz=500.008, neutral_loss_das=(116.0474,)
        )
        third_scan_number = raw.scans[-1].scan_number + 1
        third_raw.scans = (
            replace(
                third_raw.scans[0],
                scan_number=third_scan_number,
                rt=float(raw.rt[third_scan_number - 1]),
            ),
        )
        if scenario == "mixed_eligibility":
            third_raw.intensity[:] = 0.0
        third_candidates = _current_candidates(
            third_raw,
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
        )
        sample_candidates = tuple(
            sorted(
                (*sample_candidates, *third_candidates),
                key=lambda item: item.candidate_id,
            )
        )
        raw.scans = (*raw.scans, *third_raw.scans)
        if scenario == "ineligible":
            raw.intensity[:] = 0.0
            sample_candidates = _current_candidates(
                raw,
                raw_file=path,
                settings=settings,
                physical_records=physical_records,
            )
        raws[path] = raw
        if scenario == "recovery" and path == paths[-1]:
            candidates[path.stem] = ()
        elif scenario == "below_threshold" and path != paths[0]:
            candidates[path.stem] = ()
        elif scenario == "blank_missing" and path == paths[-1]:
            candidates[path.stem] = ()
        else:
            candidates[path.stem] = sample_candidates
        if scenario == "partial_recovery" and path == paths[-1]:
            candidates[path.stem] = sample_candidates[:1]
        if scenario == "cross_subject_recovery":
            precursor = 500.0 if path == paths[0] else 500.004
            candidates[path.stem] = (
                tuple(c for c in sample_candidates if c.precursor_mz == precursor)
                if path in paths[:2]
                else ()
            )
    plan = prepare_eager_untargeted_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=index + 1,
                sample_role=(
                    "blank"
                    if scenario == "blank_missing" and path == paths[-1]
                    else "study_sample"
                ),
            )
            for index, path in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:bounded-runtime",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    opened = install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    if opened_sink is not None:
        opened_sink.append(opened)
    import xic_extractor.untargeted_execution.partition.execution as runtime_owner

    monkeypatch.setattr(
        runtime_owner,
        "ensure_assemblies_loaded",
        lambda *_args: None,
    )
    reference = None
    if scenario == "istd":
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
        targets.append(["known", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50, ""])
        results = workbook.create_sheet("XIC Results")
        results.append(["SampleName", "Target", "Role", "RT", "Area"])
        for path in paths:
            results.append([path.stem, "known", "ISTD", 7.98, -999])
        reference_path = tmp_path / "reference.xlsx"
        workbook.save(reference_path)
        reference = read_known_istd_reference(reference_path)
    assert len(plan.subject_batch.subjects) == (
        0
        if scenario == "ineligible"
        else 2
        if scenario in {"cross_subject_recovery", "mixed_eligibility"}
        else 3
    )
    return plan, reference
