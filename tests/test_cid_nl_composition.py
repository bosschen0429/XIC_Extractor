import csv
from dataclasses import replace

import numpy as np
import pytest

from tests.alignment_pipeline_helpers import (
    render,
    written_cells,
    written_groups,
    written_rows,
)
from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _peak_config,
)
from tests.cid_nl_runtime_fixtures import install_runtime_raws
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
    execute_dense_partition,
    fixture_assessment,
)
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.edge_scoring import (
    evaluate_owner_edge as _evaluate_owner_edge,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.presets.apply import apply_to_discovery
from xic_extractor.presets.loader import load_preset
from xic_extractor.sample_metadata import SampleMetadata


def evaluate_owner_edge(left, right, *, config, **kwargs):
    return _evaluate_owner_edge(
        left,
        right,
        config=config,
        method_evidence=CID_NL_GROUPING_RULES.pair_evidence(left, right, config),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("mode", "scenario"),
    (
        ("union", "different_tags"),
        ("intersection", "complete"),
        ("intersection", "one_scan"),
        ("intersection", "missing"),
        ("intersection", "disjoint"),
        ("intersection", "pooled"),
        ("intersection", "recovery_complete"),
        ("intersection", "recovery_missing"),
    ),
)
def test_configured_composition_runs_discovery_to_owner_matrix(
    tmp_path, monkeypatch, mode, scenario
):
    preset_file = tmp_path / "method.toml"
    preset_file.write_text(
        'name = "two losses"\ndescription = "Composition fixture"\n'
        f'combine_mode = "{mode}"\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "A"\nvalue = 116.0474\n'
        '[[tag]]\nstrategy = "neutral_loss"\nname = "B"\nvalue = 132.0423\n'
        "[discovery]\nrt_min = 7.5\nrt_max = 8.6\n"
        'ms1_search_padding_min = 0.2\nresolver_mode = "local_minimum"\n',
        encoding="utf-8",
    )
    settings = apply_to_discovery(load_preset(str(preset_file)))
    peak_config = _peak_config(tmp_path)
    recovery = scenario.startswith("recovery_")
    paths = tuple(
        tmp_path / f"S{i}.synthetic"
        for i in (
            (1, 2, 3, 4) if recovery or scenario in {"missing", "disjoint"} else (1, 2)
        )
    )
    sources = {}
    candidates = {}
    for index, path in enumerate(paths):
        path.write_bytes(path.stem.encode())
        losses = (
            (116.0474, 132.0423)
            if mode == "intersection"
            else ((116.0474,) if index == 0 else (132.0423,))
        )
        if scenario == "pooled":
            losses = (116.0474,) if index == 0 else (132.0423,)
        if scenario == "missing" and index == 3:
            losses = (116.0474,)
        if scenario == "recovery_missing" and index == 3:
            losses = (116.0474,)
        rts = (7.98, 8.02) if len(losses) == 2 else (8.0,)
        if scenario == "disjoint" and index == 3:
            rts = (7.8, 8.3)
        source = _FakeRaw(rts, neutral_loss_das=losses)
        if scenario == "one_scan":
            source = _FakeRaw((8.0,))
            source.scans = (
                replace(
                    source.scans[0],
                    masses=np.asarray((500.0 - 116.0474, 500.0 - 132.0423)),
                    intensities=np.asarray((800.0, 700.0)),
                ),
            )
        sources[path] = source
        candidates[path.stem] = _current_candidates(
            source,
            raw_file=path,
            settings=settings,
        )
        if recovery and index == 3:
            candidates[path.stem] = ()
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=p.stem,
                raw_stem=p.stem,
                injection_order=i + 1,
                sample_role="sample",
            )
            for i, p in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        peaks_by_sample=candidates,
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
    )
    assert len(batch.subject_batch.subjects) == 1
    install_runtime_raws(monkeypatch, sources_by_path=sources, tmp_path=tmp_path)
    alignment_config = AlignmentConfig(anchor_min_seed_events=1)
    runtime = execute_dense_partition(
        batch,
        alignment_config=alignment_config,
    )
    by_sample = {
        result.job.sample.raw_stem: fixture_assessment(result)
        for result in runtime.sample_results
    }
    if recovery:
        recovered = next(
            result
            for result in runtime.sample_results
            if result.job.sample.raw_stem == "S4"
        )
        assert recovered.backfill_attempt is not None
        assert (
            fixture_assessment(recovered).method_subject
            == batch.subject_batch.subjects[0]
        )
        assert fixture_assessment(recovered).method_result == (
            "SUPPORT" if scenario == "recovery_complete" else "HOLD"
        )
        assert len(fixture_assessment(recovered).evidence_observations) == (
            2 if scenario == "recovery_complete" else 1
        )
    if scenario in {"missing", "disjoint"}:
        assert by_sample["S4"].method_result == "HOLD"
        assert "incomplete_composite_requirement" in by_sample["S4"].reason_codes
    if scenario == "one_scan":
        assert all(
            len(assessment.evidence_observations) == 2
            and len({ref.scan_number for ref in assessment.evidence_observations}) == 1
            for assessment in by_sample.values()
        )
    completed = complete_dense_partition(
        batch, runtime, alignment_config=alignment_config
    )
    writer = completed.application_result
    if scenario == "pooled":
        assert all(item.method_result == "HOLD" for item in by_sample.values())
        assert not written_groups(writer)
        _assert_unsupported_exclusion_closes_exact_coverage(completed)
        destination = tmp_path / "empty-matrix.tsv"
        render("matrix", destination, writer)
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            assert {"S1", "S2"}.issubset(reader.fieldnames)
            assert list(reader) == []
        return
    assert len(written_groups(writer)) == 1
    cells = tuple(written_cells(writer))
    assert len(cells) == len(paths)
    assert all(
        (cell.value is not None)
        == (
            not (
                sample_stem == "S4"
                and scenario in {"missing", "disjoint", "recovery_missing"}
            )
        )
        for _row, sample_stem, cell in cells
    )
    destination = tmp_path / "matrix.tsv"
    render("matrix", destination, writer)
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 1
    assert float(rows[0]["S1"]) > 0
    if scenario in {"missing", "disjoint"}:
        assert rows[0]["S4"] == ""
    if scenario == "recovery_missing":
        assert rows[0]["S4"] == ""
    if scenario == "recovery_complete":
        assert float(rows[0]["S4"]) > 0
        (child,) = completed.application_result.children
        core = child.scientific_core
        (before,) = core.grouping_result.initial_population
        (after,) = core.final_population
        assert len(before.member_subjects) == 3
        assert len(after.member_subjects) == 4
        assert set(before.member_subjects).issubset(after.member_subjects)
        assert len(core.partition_transition.admitted_members) == 1
        assert len(core.lineage) == 1
        recovered_cell = next(
            cell
            for cell in core.cell_observations
            if cell.canonical_sample.raw_stem == "S4"
        )
        assert recovered_cell.backfill_cell is not None
        assert recovered_cell.backfill_cell.method_assessment is fixture_assessment(
            recovered
        )
        assert recovered_cell.integrations == (
            fixture_assessment(recovered).candidate_peaks[0].integration,
        )
        assert recovered_cell.predecessor_cells == ()
        assert [
            association.position.group.group_hypothesis_id
            for association in recovered_cell.position_associations
        ] == [before.group_hypothesis_id]
        assert all(
            cell["source_candidate_id"] == ""
            for cell in written_rows(tmp_path / "written", writer)["cells"]
            if cell["sample_stem"] == "S4"
        )
    if scenario == "different_tags":
        _assert_composite_labels_cannot_grant_membership(batch, runtime)
    if scenario == "one_scan":
        _assert_duplicate_loss_evidence_rejected(batch, runtime)


def _assert_unsupported_exclusion_closes_exact_coverage(completed):

    result = completed.application_result
    assert not result.children
    exclusions = result.excluded_candidates
    assert exclusions
    assert all(
        item.reason_code == "composite_requirement_not_supported"
        and len(item.composite_assessments) == 2
        for item in exclusions
    )


def _assert_composite_labels_cannot_grant_membership(batch, runtime):
    from xic_extractor.alignment.feature_membership_inference import (
        infer_feature_memberships,
    )
    from xic_extractor.alignment.hypothesis_owner_adapter import (
        compose_cid_nl_peak_hypotheses_from_assessment,
        owner_from_cid_nl_assessment_support,
    )

    owners = []
    for result in runtime.sample_results:
        assessment = fixture_assessment(result)
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            sample=result.job.sample,
            assessment=assessment,
        )
        owners.append(
            owner_from_cid_nl_assessment_support(
                sample=result.job.sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
        )
    assert (
        evaluate_owner_edge(
            *owners, config=AlignmentConfig(), rt_frame=SampleRtFrame()
        ).failure_reason
        == ""
    )
    unbound = tuple(
        replace(owner, cid_nl_composite_assessment=None) for owner in owners
    )
    assert unbound[0].neutral_loss_tag == unbound[1].neutral_loss_tag
    assert (
        evaluate_owner_edge(
            *unbound, config=AlignmentConfig(), rt_frame=SampleRtFrame()
        ).failure_reason
        == "product_mz_out_of_tolerance"
    )
    crossed = replace(
        owners[1], cid_nl_composite_assessment=owners[0].cid_nl_composite_assessment
    )
    assert (
        evaluate_owner_edge(
            owners[0], crossed, config=AlignmentConfig(), rt_frame=SampleRtFrame()
        ).failure_reason
        == "product_mz_out_of_tolerance"
    )

    # Composition membership retains its typed provenance gate. With both
    # assessments removed these become ordinary owners, whose product scatter
    # is scored by the separate window-grouping rule.
    for population, expected_groups in (
        (tuple(owners), 1),
        ((owners[0], crossed), 2),
    ):
        inferred = infer_feature_memberships(
            population,
            config=AlignmentConfig(anchor_min_seed_events=1),
            rt_frame=SampleRtFrame(),
            rules=CID_NL_GROUPING_RULES,
        )
        assert len(inferred.groups) == expected_groups


def _assert_duplicate_loss_evidence_rejected(batch, runtime):
    from xic_extractor.peak_detection.cid_nl_evidence import cid_nl_evidence_observation
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlMethodAssessmentContractError,
        mint_cid_nl_method_assessment,
    )

    result = runtime.sample_results[0]
    assessment = fixture_assessment(result)
    measured = assessment.evidence_observations[0]
    duplicate = cid_nl_evidence_observation(
        sample=result.job.sample,
        acquisition_source=result.job.acquisition_source_expectation.source,
        scan_number=measured.scan_number,
        precursor_mz=measured.precursor_mz,
        product_mz=measured.product_mz,
        configured_neutral_loss_da=measured.configured_neutral_loss_da,
        observed_neutral_loss_da=measured.observed_neutral_loss_da,
        observed_loss_error_ppm=measured.observed_loss_error_ppm,
        scan_rt_min=measured.scan_rt_min,
        product_intensity=measured.product_intensity + 1,
    )
    with pytest.raises(
        CidNlMethodAssessmentContractError, match="duplicate acquisition scan"
    ):
        mint_cid_nl_method_assessment(
            sample=result.job.sample,
            acquisition_source=result.job.acquisition_source_expectation.source,
            method_subject=assessment.method_subject,
            detector_invocation_id=assessment.detector_invocation_id,
            evaluation_receipt=assessment.evaluation_receipt,
            candidate_peaks=assessment.candidate_peaks,
            evidence_refs=(*assessment.evidence_observations, duplicate),
            individual_support_refs=assessment.individual_support_refs,
            set_relation_refs=assessment.set_relation_refs,
        )


def test_composite_requirement_identity_distinguishes_each_composition():
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        CidNlComposition,
        mint_untargeted_cid_nl_method_subject,
    )

    first = CidNlComposition(
        tags=(("A", 116.0474), ("B", 132.0423)), combine_mode="union"
    )
    variants = (
        first,
        replace(first, tags=tuple(reversed(first.tags))),
        replace(first, combine_mode="intersection"),
        replace(first, tags=(("A", 116.0474), ("B", 130.0))),
    )
    subjects = tuple(
        mint_untargeted_cid_nl_method_subject(
            precursor_mz=500.0, precursor_tolerance_ppm=5.0, composition=composition
        )
        for composition in variants
    )
    assert len({subject.method_subject_id for subject in subjects}) == len(subjects)
    for subject in subjects:
        assert subject.neutral_loss_da is None
