"""Physical proof, worksets and bounded method-subject membership."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.cid_nl_runtime_fixtures import (
    build_discovery_receipts,
)
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from tests.untargeted_cid_nl_production_batch_fixtures import (
    _assert_jobs_match_direct_construction,
    _physical_subject_facts,
    _subject_proof_record,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    group_cid_nl_physical_subject_units,
)
from xic_extractor.sample_metadata import SampleMetadata


@pytest.mark.parametrize(
    ("scenario", "masses", "expected_units"),
    (
        (
            "far",
            (500.0, 750.0),
            ((0,), (1,)),
        ),
        (
            "different-signal",
            (500.0, 500.006),
            ((0,), (1,)),
        ),
        (
            "different-apex",
            (500.0, 500.006),
            ((0,), (1,)),
        ),
        (
            "bridge",
            (500.0, 500.015, 500.03),
            ((0, 1), (2,)),
        ),
    ),
    ids=(
        "far-mass-identical-signal",
        "different-signal",
        "different-apex-unproven",
        "common-center-bridge",
    ),
)
def test_physical_subject_units_require_complete_common_proof(
    tmp_path: Path,
    scenario: str,
    masses: tuple[float, ...],
    expected_units: tuple[tuple[int, ...], ...],
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"physical-subject-units:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    peak_config = _peak_config(tmp_path)
    _raw, base_candidates = _nearby_subject_candidates(
        raw_path, settings=settings, peak_config=peak_config
    )
    candidates = tuple(
        replace(
            base_candidates[0],
            peak_id=f"subject-proof-{index}",
            best_ms2_scan_id=100 + index,
            precursor_mz=mz,
            channel_mz=mz,
            product_mz=mz - 116.0474,
            configured_neutral_loss_da=116.0474,
        )
        for index, mz in enumerate(masses)
    )
    records = {
        candidate.peak_id: _subject_proof_record(
            candidate,
            xic_mz=500.015 if scenario == "bridge" else None,
        )
        for candidate in candidates
    }
    if scenario == "different-signal":
        records[candidates[1].peak_id] = _subject_proof_record(
            candidates[1], raw=(1.0, 11.0, 1.0)
        )
    elif scenario == "different-apex":
        records[candidates[1].peak_id] = _subject_proof_record(candidates[1], apex=7.51)
    input_facts = _physical_subject_facts(candidates, records)
    if scenario == "far":

        class TraceMustStayLazy:
            def __iter__(self):
                raise AssertionError("far-mass candidates must not read trace arrays")

        assert all(fact.proof is not None for fact in input_facts)
        input_facts = tuple(
            replace(
                fact,
                proof=replace(
                    fact.proof,
                    rt=TraceMustStayLazy(),
                    raw=TraceMustStayLazy(),
                ),
            )
            for fact in input_facts
        )

    units = group_cid_nl_physical_subject_units(
        input_facts,
        composition=None,
        precursor_tolerance_ppm=20.0,
    )

    candidate_index = {
        candidate.peak_id: index for index, candidate in enumerate(candidates)
    }
    assert (
        tuple(
            tuple(candidate_index[candidate_id] for candidate_id in unit)
            for _requirement, unit in units
        )
        == expected_units
    )


def test_subject_planning_uses_physical_mass_without_rewriting_seed_receipts(
    tmp_path: Path,
) -> None:
    from xic_extractor.untargeted_execution.input_planning import (
        prepare_untargeted_subject_membership,
    )

    physical_records = {}
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"physical-ion-planning:S1")
    settings = replace(_settings(), neutral_loss_profile=None)
    _, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=_peak_config(tmp_path),
        physical_records=physical_records,
    )
    inferred = replace(
        candidates[0],
        precursor_mz=445.2140,
        channel_mz=445.2140,
        product_mz=445.2140 - 116.0474,
        ms1_apex_mz=445.2071,
        precursor_mz_basis="product_plus_neutral_loss",
    )
    subjects = prepare_untargeted_subject_membership(
        candidates_by_sample={"S1": (inferred,)},
        settings=settings,
        physical_records_by_candidate=physical_records,
    )
    assert len(subjects.subjects) == 1
    assert subjects.subjects[0].precursor_mz == 445.2071
    assert inferred.precursor_mz == 445.2140
    assert inferred.product_mz == 445.2140 - 116.0474

    selected = replace(inferred, ms1_apex_mz=None, precursor_mz_basis="scan_precursor")
    fallback = prepare_untargeted_subject_membership(
        candidates_by_sample={"S1": (selected,)},
        settings=settings,
        physical_records_by_candidate=physical_records,
    )
    assert fallback.subjects[0].precursor_mz == 445.2140


def test_physical_subject_units_keep_unproven_candidates_singleton(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"unproven-subject-units:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    _raw, base_candidates = _nearby_subject_candidates(
        raw_path, settings=settings, peak_config=_peak_config(tmp_path)
    )
    candidates = tuple(
        replace(
            base_candidates[0],
            peak_id=f"unproven-{index}",
            best_ms2_scan_id=100 + index,
        )
        for index in range(2)
    )

    units = group_cid_nl_physical_subject_units(
        _physical_subject_facts(
            candidates,
            {candidates[0].peak_id: _subject_proof_record(candidates[0])},
        ),
        composition=None,
        precursor_tolerance_ppm=20.0,
    )

    assert sorted(len(unit) for _requirement, unit in units) == [1, 1]


def test_proven_subject_unit_is_not_split_by_lower_fixed_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_records = {}
    from xic_extractor.untargeted_execution import input_planning as planning_owner

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"lower-anchor-subject-unit:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    _raw, base_candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=_peak_config(tmp_path),
        physical_records=physical_records,
    )
    masses = (499.995, 500.0, 500.015)
    candidates = tuple(
        replace(
            base_candidates[0],
            peak_id=f"lower-anchor-{index}",
            best_ms2_scan_id=100 + index,
            precursor_mz=mz,
            channel_mz=mz,
            product_mz=mz - 116.0474,
            configured_neutral_loss_da=116.0474,
        )
        for index, mz in enumerate(masses)
    )
    records = {
        candidates[0].peak_id: _subject_proof_record(
            candidates[0], apex=7.25, raw=(1.0, 5.0, 1.0), xic_mz=500.0
        ),
        **{
            candidate.peak_id: _subject_proof_record(candidate, xic_mz=500.0)
            for candidate in candidates[1:]
        },
    }
    captured = {}

    class ExpectedStop(Exception):
        pass

    def capture_subject_batch(**kwargs):
        captured["subject_batch"] = kwargs["method_subject"]
        raise ExpectedStop

    monkeypatch.setattr(
        planning_owner,
        "build_partition_context",
        capture_subject_batch,
    )
    with pytest.raises(ExpectedStop):
        planning_owner.prepare_eager_untargeted_plan(
            raw_paths=(raw_path,),
            sample_metadata=(),
            settings=settings,
            peak_config=_peak_config(tmp_path),
            candidates_by_sample={"S1": candidates},
            discovery_receipts_by_sample={},
            dll_dir=tmp_path,
            sample_identity_namespace="test:lower-anchor",
            physical_records_by_candidate=records,
            signal_processing_profile=TEST_PROFILE,
        )

    subject_batch = captured["subject_batch"]
    memberships = dict(subject_batch.candidate_memberships)
    assert memberships[candidates[0].peak_id] != memberships[candidates[1].peak_id]
    assert memberships[candidates[1].peak_id] == memberships[candidates[2].peak_id]
    assert len(subject_batch.subjects) == 2


def test_alignment_subject_worksets_preserve_only_compatible_overlap() -> None:
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        CidNlComposition,
        CidNlMethodSubjectContractError,
        cid_nl_alignment_subject_worksets,
        mint_untargeted_cid_nl_method_subject,
    )

    first = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    overlapping = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.015,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    disconnected = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.05,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    different_loss = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.005,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=132.0423,
    )
    composite = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.01,
        precursor_tolerance_ppm=20.0,
        composition=CidNlComposition(
            tags=(("DNA_dR", 116.0474), ("RNA_R", 132.0423)),
            combine_mode="union",
        ),
    )

    assert cid_nl_alignment_subject_worksets(
        (first, overlapping, disconnected, different_loss, composite)
    ) == (
        (first, overlapping),
        (disconnected,),
        (different_loss,),
        (composite,),
    )
    # A confirmed identity's search envelope must be compared together before
    # partition dispatch, even if two resolver windows do not directly overlap.
    assert cid_nl_alignment_subject_worksets(
        (first, overlapping, disconnected, different_loss, composite),
        comparison_mz_intervals=((499.99, 500.06),),
    ) == (
        (first, overlapping, disconnected),
        (different_loss,),
        (composite,),
    )
    with pytest.raises(
        CidNlMethodSubjectContractError,
        match="duplicate method subjects",
    ):
        cid_nl_alignment_subject_worksets((first, first))


@pytest.mark.parametrize(
    ("masses", "losses", "ppm", "centers"),
    (
        ((500.0, 500.004), (116.0474, 116.0474), 20.0, (500.0, 500.0)),
        ((500.0, 500.02), (116.0474, 116.0474), 20.0, (500.0, 500.02)),
        (
            (500.0, 500.008, 500.016),
            (116.0474,) * 3,
            20.0,
            (500.0, 500.0, 500.016),
        ),
        (
            (512.0, 512.0078125),
            (116.0474, 116.0474),
            15.2587890625,
            (512.0, 512.0),
        ),
        ((500.0, 500.0), (116.0474, 132.0423), 20.0, (500.0, 500.0)),
    ),
    ids=("nearby", "outside", "fixed-anchor", "inclusive-bound", "scalar-losses"),
)
def test_production_batch_preserves_bounded_subject_membership(
    tmp_path: Path, masses, losses, ppm, centers
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=ppm
    )
    peak_config = _peak_config(tmp_path)
    from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile

    physical_records = {}
    candidates = tuple(
        _current_candidates(
            _FakeRaw(
                (8.0 + index * 0.1,), scan_precursor_mz=mz, neutral_loss_das=(loss,)
            ),
            raw_file=raw_path,
            settings=replace(
                settings, neutral_loss_profiles=(NeutralLossProfile("DNA_dR", loss),)
            ),
            physical_records=physical_records,
        )[0]
        for index, (mz, loss) in enumerate(zip(masses, losses, strict=True))
    )
    arguments = dict(
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate=physical_records,
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
    )

    batch = build_dense_partition(**arguments, candidates_by_sample={"S1": candidates})
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
    )

    expected_memberships = tuple(
        sorted(
            (
                candidate.peak_id,
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=center,
                    precursor_tolerance_ppm=ppm,
                    neutral_loss_da=loss,
                ).method_subject_id,
            )
            for candidate, center, loss in zip(candidates, centers, losses, strict=True)
        )
    )
    assert batch.subject_batch.candidate_memberships == expected_memberships
    assert len(batch.subject_batch.subjects) == len(
        set(zip(centers, losses, strict=True))
    )
    permuted = build_dense_partition(
        **arguments, candidates_by_sample={"S1": tuple(reversed(candidates))}
    )
    assert (
        permuted.subject_batch.request_payload()
        == batch.subject_batch.request_payload()
    )
    assert tuple(candidate.precursor_mz for candidate in candidates) == masses
    assert tuple(job.method_subject for job in batch.jobs) == (
        batch.subject_batch.subjects
    )
    assert all(
        job.candidates is batch.context.candidates_by_sample["S1"] for job in batch.jobs
    )
    _assert_jobs_match_direct_construction(batch)
