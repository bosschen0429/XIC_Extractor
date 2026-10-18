import csv
import json
from dataclasses import replace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    _current_candidates,
    _FakeRaw,
    _peak_config,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raws,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
    execute_dense_partition,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv
from xic_extractor.presets.apply import apply_to_discovery
from xic_extractor.presets.loader import load_preset
from xic_extractor.sample_metadata import SampleMetadata


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
        "[discovery]\nrt_min = 7.5\nrt_max = 8.6\nseed_rt_gap_min = 0.2\n"
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
            source, raw_file=path, settings=settings, peak_config=peak_config
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
                sample_role="study_sample",
            )
            for i, p in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:composition",
    )
    assert len(batch.subject_batch.subjects) == 1
    _install_runtime_raws(monkeypatch, sources_by_path=sources, tmp_path=tmp_path)
    alignment_config = AlignmentConfig(anchor_min_seed_events=1)
    runtime = execute_dense_partition(
        batch,
        alignment_config=alignment_config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "composition"
        ),
    )
    by_sample = {
        result.job.sample.metadata.raw_stem: result.effective_assessment
        for result in runtime.sample_results
    }
    assert {
        assessment.artifact.semantic_contract.schema_version
        for assessment in by_sample.values()
    } == {"cid_nl_method_assessment_physical_query"}
    if recovery:
        recovered = next(
            result
            for result in runtime.sample_results
            if result.job.sample.metadata.raw_stem == "S4"
        )
        assert recovered.backfill_attempt is not None
        assert (
            recovered.effective_assessment.content.method_subject
            == batch.subject_batch.subjects[0]
        )
        assert recovered.effective_assessment.content.method_result == (
            "SUPPORT" if scenario == "recovery_complete" else "HOLD"
        )
        assert len(recovered.effective_assessment.evidence_refs) == (
            2 if scenario == "recovery_complete" else 1
        )
    if scenario in {"missing", "disjoint"}:
        assert by_sample["S4"].content.method_result == "HOLD"
        assert (
            "incomplete_composite_requirement" in by_sample["S4"].content.reason_codes
        )
    if scenario == "one_scan":
        assert all(
            len(assessment.evidence_refs) == 2
            and assessment.content.evaluation_receipt.scanned_event_count == 1
            for assessment in by_sample.values()
        )
    completed = complete_dense_partition(
        batch, runtime, alignment_config=alignment_config
    )
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    if scenario == "pooled":
        assert all(item.content.method_result == "HOLD" for item in by_sample.values())
        assert not writer.matrix.clusters
        assert not writer.matrix.cells
        _assert_unsupported_exclusion_closes_exact_coverage(completed)
        destination = tmp_path / "empty-matrix.tsv"
        write_alignment_matrix_tsv(
            destination,
            writer.matrix,
            alignment_config=AlignmentConfig(),
            production_decisions=writer.production_decisions,
        )
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            assert {"S1", "S2"}.issubset(reader.fieldnames)
            assert list(reader) == []
        return
    assert len(writer.matrix.clusters) == 1
    assert len(writer.matrix.cells) == len(paths)
    assert all(
        writer.production_decisions.cell(
            cell.cluster_id, cell.sample_stem
        ).write_matrix_value
        == (
            not (
                cell.sample_stem == "S4"
                and scenario in {"missing", "disjoint", "recovery_missing"}
            )
        )
        for cell in writer.matrix.cells
    )
    destination = tmp_path / "matrix.tsv"
    write_alignment_matrix_tsv(
        destination,
        writer.matrix,
        alignment_config=AlignmentConfig(),
        production_decisions=writer.production_decisions,
    )
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
            if cell.canonical_sample.metadata.raw_stem == "S4"
        )
        assert recovered_cell.backfill_cell is not None
        assert (
            recovered_cell.backfill_cell.method_assessment
            is recovered.effective_assessment
        )
        assert recovered_cell.integrations == (
            recovered.effective_assessment.candidate_peaks[0].content.integration,
        )
        assert recovered_cell.predecessor_cells == ()
        assert [
            association.position.group_identity.group_hypothesis_id
            for association in recovered_cell.position_associations
        ] == [before.group_hypothesis_id]
        assert (
            next(
                cell for cell in writer.matrix.cells if cell.sample_stem == "S4"
            ).source_candidate_id
            is None
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
    from xic_extractor.alignment.edge_scoring import evaluate_owner_edge
    from xic_extractor.alignment.feature_membership_inference import (
        infer_feature_memberships,
    )
    from xic_extractor.alignment.hypothesis_owner_adapter import (
        compose_cid_nl_peak_hypotheses_from_assessment,
        owner_from_cid_nl_assessment_support,
    )

    owners = []
    for result in runtime.sample_results:
        assessment = result.effective_assessment
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=batch.context.processing_owners,
            sample=result.job.sample,
            assessment=assessment,
        )
        owners.append(
            owner_from_cid_nl_assessment_support(
                sample=result.job.sample, assessment=assessment, hypothesis=hypothesis
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
        )
        assert len(inferred.groups) == expected_groups


def _assert_duplicate_loss_evidence_rejected(batch, runtime):
    from xic_extractor.peak_detection.cid_nl_evidence import mint_cid_nl_evidence_ref
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlMethodAssessmentContractError,
        mint_cid_nl_method_assessment_ref,
    )

    result = runtime.sample_results[0]
    assessment = result.effective_assessment
    measured = assessment.evidence_refs[0].observation
    duplicate = mint_cid_nl_evidence_ref(
        processing_owners=batch.context.processing_owners,
        sample=result.job.sample,
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
        mint_cid_nl_method_assessment_ref(
            processing_owners=batch.context.processing_owners,
            sample=result.job.sample,
            method_subject=assessment.content.method_subject,
            detector_invocation_id=assessment.content.detector_invocation_id,
            evaluation_receipt=assessment.content.evaluation_receipt,
            candidate_peaks=assessment.candidate_peaks,
            evidence_refs=(*assessment.evidence_refs, duplicate),
            individual_support_refs=assessment.individual_support_refs,
            set_relation_refs=assessment.set_relation_refs,
        )


def test_composite_requirement_identity_and_strict_payload_replay():
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        CidNlComposition,
        CidNlMethodSubjectContractError,
        _subject_from_payload,
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
        payload = json.loads(json.dumps(subject.request_payload()))
        assert payload["schema_version"] == "cid_nl_method_subject"
        assert _subject_from_payload(payload) == subject
        payload["composition"]["undeclared"] = True
        with pytest.raises(CidNlMethodSubjectContractError, match="wrong fields"):
            _subject_from_payload(payload)
    single = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0, precursor_tolerance_ppm=5.0, neutral_loss_da=116.0474
    )
    payload = single.request_payload()
    assert payload["schema_version"] == "cid_nl_method_subject"
    assert payload["composition"] is None
    assert _subject_from_payload(payload) == single
    payload["schema_version"] = "unsupported_cid_nl_method_subject"
    with pytest.raises(CidNlMethodSubjectContractError, match="schema must be"):
        _subject_from_payload(payload)
    payload["schema_version"] = "cid_nl_method_subject"
    payload.pop("composition")
    with pytest.raises(CidNlMethodSubjectContractError, match="wrong fields"):
        _subject_from_payload(payload)
