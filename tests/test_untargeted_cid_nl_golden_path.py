from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.check_production_acceptance_manifest import (
    REQUIRED_COLUMNS,
    check_production_acceptance_manifest,
    production_acceptance_manifest_sha256,
)
from tests.issue161_no_raw_fixture import (
    issue161_evidence,
    issue161_peak,
    issue161_support_policy,
    issue161_unique_support,
)
from tests.issue175_no_raw_fixture import (
    NEUTRAL_LOSS_DA,
    PRECURSOR_MZ,
    PRODUCT_MZ,
    Issue175NoRawFixture,
    build_issue175_no_raw_fixture,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlAssessmentInput,
    UntargetedCidNlGoldenPathError,
    UntargetedCidNlRecoveryInput,
    build_untargeted_cid_nl_golden_path,
    build_untargeted_cid_nl_quant_matrix_version,
)
from xic_extractor.discovery.cid_nl_evidence_projection import (
    UntargetedCidNlProjectionRef,
    mint_untargeted_cid_nl_projection_ref,
)
from xic_extractor.discovery.models import DiscoverySeed
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    resolve_dda_cid_nl_candidate_set_relation_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentContractError,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubjectContractError,
    mint_untargeted_cid_nl_method_subject,
    validate_cid_nl_method_subject_binding,
)
from xic_extractor.processing_identity import (
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
    resolve_request_acquisition_source,
)
from xic_extractor.tabular_io import render_delimited_rows

SAMPLE_ROLES = {
    "StudySupport": "study_sample",
    "QCSupport": "qc",
    "Missing": "study_sample",
    "Split": "study_sample",
    "Reject": "study_sample",
    "Technical": "study_sample",
    "Blank": "blank",
    "MixSTD": "calibrator",
}
SOURCE_RELPATH = "validation/untargeted_cid_nl_golden_path_source.json"


def test_no_raw_golden_path_preserves_evidence_and_terminal_matrix_contract(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    sample_inputs, recovery = _golden_inputs(fixture)

    projection = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=sample_inputs,
        recovery_inputs=(recovery,),
        source_artifact_relpath=SOURCE_RELPATH,
    )
    replay = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=tuple(reversed(sample_inputs)),
        recovery_inputs=(recovery,),
        source_artifact_relpath=SOURCE_RELPATH,
    )

    assert fixture.owners.target_snapshot is None
    assert fixture.method_subject.subject_kind == "untargeted_precursor"
    assert fixture.method_subject.target_snapshot_id is None
    assert projection.validation_status == "production_candidate"
    assert projection.product_authority == "none_until_checked_manifest"
    assert projection.source_artifact_bytes == replay.source_artifact_bytes
    assert projection.source_artifact_sha256 == replay.source_artifact_sha256
    assert projection.backfill_facts.sample_count_n == 6
    assert projection.backfill_facts.minimum_support_k == 2
    assert projection.backfill_facts.direct_support_count_s == 2
    assert len(projection.backfill_facts.support_trace_refs) == 2
    assert len(projection.discovery_projection_refs) == 4
    assert len(projection.spine.support_hypotheses) == 2
    assert len(projection.spine.cells) == len(SAMPLE_ROLES)
    assert all(
        cell.content.group_target_key_id is None
        and cell.content.group_target_definition_id is None
        for cell in projection.spine.cells
    )
    assert len(projection.pending_acceptance_rows) == 1
    assert projection.pending_acceptance_rows[0]["sample_stem"] == "Missing"

    decision_by_stem = {
        _sample_stem(decision): decision for decision in projection.backfill_decisions
    }
    assert decision_by_stem["Missing"].disposition == "candidate_mapped"
    assert decision_by_stem["Split"].disposition == "hold"
    assert decision_by_stem["Reject"].disposition == "not_applicable"
    assert decision_by_stem["Technical"].disposition == "hold"
    assert decision_by_stem["Blank"].disposition == "hold"
    assert decision_by_stem["MixSTD"].disposition == "hold"

    source_payload = json.loads(projection.source_artifact_bytes)
    source_by_stem = {row["sample_stem"]: row for row in source_payload["rows"]}
    assert source_payload["method_subject"]["method_subject_id"] == (
        fixture.method_subject.method_subject_id
    )
    assert source_payload["alignment_config"]["anchor_min_seed_events"] == 1
    assert source_payload["backfill_policy"]["sample_count_n"] == 6
    assert source_payload["backfill_policy"]["minimum_support_k"] == 2
    assert source_payload["backfill_policy"]["direct_support_count_s"] == 2
    assert len(source_payload["backfill_policy"]["support_trace_artifact_ids"]) == 2
    assert source_by_stem["StudySupport"]["numeric_value_kind"] == "direct_support"
    assert source_by_stem["StudySupport"]["discovery_projection_artifact_ids"]
    assert source_by_stem["Missing"]["numeric_value_kind"] == (
        "accepted_backfill_input"
    )
    assert source_by_stem["Missing"]["recovery_universe_artifact_id"]
    assert source_by_stem["Missing"]["recovery_assessment_artifact_id"]
    assert source_by_stem["Split"]["numeric_value_kind"] == "blank"
    assert source_by_stem["Reject"]["numeric_value_kind"] == "blank"

    manifest_path, _ = _check_acceptance_manifest(tmp_path, projection)
    terminal = build_untargeted_cid_nl_quant_matrix_version(
        projection,
        production_acceptance_manifest_path=manifest_path,
        manifest_root=tmp_path,
    )
    assert len(terminal.quant_matrix_rows) == 1
    matrix_row = terminal.quant_matrix_rows[0]
    assert matrix_row["StudySupport"] == "660"
    assert matrix_row["QCSupport"] == "1320"
    assert matrix_row["Missing"] == "1980"
    for stem in ("Split", "Reject", "Technical", "Blank", "MixSTD"):
        assert matrix_row.get(stem, "") == ""
    assert "0" not in {matrix_row.get(stem, "") for stem in SAMPLE_ROLES}

    provenance = {row["sample_stem"]: row for row in terminal.cell_provenance_rows}
    assert provenance["StudySupport"]["cell_status"] == "detected"
    assert provenance["QCSupport"]["cell_status"] == "detected"
    assert provenance["Missing"]["cell_status"] == "accepted_backfill"
    assert provenance["Missing"]["matrix_area_source"] == (
        "peak_observation.integration.area_raw_counts_seconds"
    )
    assert "smoothed" not in provenance["Missing"]["matrix_area_source"]
    with pytest.raises(TypeError):
        terminal.quant_matrix_rows[0]["Missing"] = "999"


def test_golden_path_source_is_invariant_to_per_sample_projection_order(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    sample_inputs, recovery = _golden_inputs(fixture)
    multi_projection = _multi_projection_support_input(fixture, "StudySupport")
    reordered = replace(
        multi_projection,
        discovery_projections=tuple(reversed(multi_projection.discovery_projections)),
    )
    canonical_inputs = tuple(
        multi_projection
        if item.sample.metadata.raw_stem == "StudySupport"
        else item
        for item in sample_inputs
    )
    reordered_inputs = tuple(
        reordered if item.sample.metadata.raw_stem == "StudySupport" else item
        for item in sample_inputs
    )

    canonical = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=canonical_inputs,
        recovery_inputs=(recovery,),
        source_artifact_relpath=SOURCE_RELPATH,
    )
    replay = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=reordered_inputs,
        recovery_inputs=(recovery,),
        source_artifact_relpath=SOURCE_RELPATH,
    )

    assert canonical.source_artifact_bytes == replay.source_artifact_bytes
    assert canonical.source_artifact_sha256 == replay.source_artifact_sha256
    assert canonical.pending_acceptance_rows == replay.pending_acceptance_rows


def test_golden_path_rejects_missing_discovery_lineage_and_manifest_rewrite(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    sample_inputs, recovery = _golden_inputs(fixture)
    support_input = next(
        item
        for item in sample_inputs
        if item.sample.metadata.raw_stem == "StudySupport"
    )
    with pytest.raises(UntargetedCidNlGoldenPathError, match="cover every"):
        replace(support_input, discovery_projections=())

    projection = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=sample_inputs,
        recovery_inputs=(recovery,),
        source_artifact_relpath=SOURCE_RELPATH,
    )
    with pytest.raises(UntargetedCidNlGoldenPathError, match="must be minted"):
        replace(
            projection,
            input_quant_matrix_rows=({"Mz": "999", "RT": "999"},),
            _mint_token=object(),
        )
    manifest_path, checked_rows = _check_acceptance_manifest(tmp_path, projection)
    rewritten_without_sha = ({**checked_rows[0], "quant_value": "999"},)
    rewritten_sha = production_acceptance_manifest_sha256(rewritten_without_sha)
    rewritten = (
        {**rewritten_without_sha[0], "manifest_sha256": rewritten_sha},
    )
    _write_acceptance_manifest(manifest_path, rewritten)
    with pytest.raises(UntargetedCidNlGoldenPathError, match="rewrites"):
        build_untargeted_cid_nl_quant_matrix_version(
            projection,
            production_acceptance_manifest_path=manifest_path,
            manifest_root=tmp_path,
        )


def test_terminal_rechecks_manifest_digest_and_source_artifact(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    sample_inputs, recovery = _golden_inputs(fixture)
    projection = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=sample_inputs,
        recovery_inputs=(recovery,),
        source_artifact_relpath=SOURCE_RELPATH,
    )
    manifest_path, checked_rows = _check_acceptance_manifest(tmp_path, projection)

    forged_digest_rows = (
        {**checked_rows[0], "manifest_sha256": "F" * 64},
    )
    _write_acceptance_manifest(manifest_path, forged_digest_rows)
    with pytest.raises(UntargetedCidNlGoldenPathError, match="manifest_sha256"):
        build_untargeted_cid_nl_quant_matrix_version(
            projection,
            production_acceptance_manifest_path=manifest_path,
            manifest_root=tmp_path,
        )

    manifest_path, _ = _check_acceptance_manifest(tmp_path, projection)
    source_path = tmp_path / projection.source_artifact_relpath
    source_path.write_bytes(b"tampered after manifest validation")
    with pytest.raises(UntargetedCidNlGoldenPathError, match="source_artifact"):
        build_untargeted_cid_nl_quant_matrix_version(
            projection,
            production_acceptance_manifest_path=manifest_path,
            manifest_root=tmp_path,
        )


def test_projection_deep_freezes_every_matrix_authority_row(tmp_path: Path) -> None:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    sample_inputs, recovery = _golden_inputs(fixture)
    projection = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=sample_inputs,
        recovery_inputs=(recovery,),
        source_artifact_relpath=SOURCE_RELPATH,
    )

    mutation_attempts = (
        (projection.input_quant_matrix_rows[0], "Mz"),
        (projection.input_matrix_identity_rows[0], "peak_hypothesis_id"),
        (projection.pending_acceptance_rows[0], "quant_value"),
        (projection.expected_diff_rows[0], "activated_value"),
    )
    for row, field_name in mutation_attempts:
        with pytest.raises(TypeError):
            row[field_name] = "999"  # type: ignore[index]


def test_terminal_applies_complete_canonical_manifest_authority_rules(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    sample_inputs, recovery = _golden_inputs(fixture)
    forbidden_source = "validation/LOCKBOX_SHADOW_AUTOMATION/source.json"
    projection = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=sample_inputs,
        recovery_inputs=(recovery,),
        source_artifact_relpath=forbidden_source,
    )
    source_path = tmp_path / forbidden_source
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(projection.source_artifact_bytes)
    rows = tuple(dict(row) for row in projection.pending_acceptance_rows)
    manifest_sha = production_acceptance_manifest_sha256(rows)
    checked_rows = tuple({**row, "manifest_sha256": manifest_sha} for row in rows)
    manifest_path = tmp_path / "production_acceptance_manifest.tsv"
    _write_acceptance_manifest(manifest_path, checked_rows)

    assert any(
        "direct shadow/report/gallery/candidate source" in problem
        for problem in check_production_acceptance_manifest(
            manifest_path=manifest_path,
            repo_root=tmp_path,
        )
    )
    with pytest.raises(
        UntargetedCidNlGoldenPathError,
        match="canonical authority validation",
    ):
        build_untargeted_cid_nl_quant_matrix_version(
            projection,
            production_acceptance_manifest_path=manifest_path,
            manifest_root=tmp_path,
        )


def test_untargeted_method_subject_must_be_pinned_by_the_processing_request(
    tmp_path: Path,
) -> None:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    crossed_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=PRECURSOR_MZ,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=115.0,
    )

    with pytest.raises(CidNlMethodAssessmentContractError, match="not pinned"):
        mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("Missing"),
            method_subject=crossed_subject,
            detector_invocation_id="gaussian15:crossed-subject:v1",
            evaluation_receipt=_completed_receipt(scanned_event_count=0),
        )


@pytest.mark.parametrize(
    "numeric_field",
    ("precursor_mz", "precursor_tolerance_ppm", "neutral_loss_da"),
)
def test_untargeted_method_subject_binding_rejects_json_boolean_numeric_alias(
    tmp_path: Path,
    numeric_field: str,
) -> None:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=1.0,
        precursor_tolerance_ppm=1.0,
        neutral_loss_da=1.0,
    )
    request_payload = subject.request_payload()
    request_payload[numeric_field] = True
    original_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=original_request.acquisition_sources,
        acquisition_bindings=original_request.acquisition_bindings,
        scientific_input_schema_version=(
            original_request.scientific_input_schema_version
        ),
        processing_algorithm_version=original_request.processing_algorithm_version,
        processing_contract_version=original_request.processing_contract_version,
        scientific_inputs={
            "fixture": "issue175-no-raw",
            "cid_nl_method_subject": request_payload,
        },
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.owners.sample_resolutions,
    )

    with pytest.raises(CidNlMethodSubjectContractError, match="not pinned"):
        validate_cid_nl_method_subject_binding(subject, processing_owners=owners)


def _golden_inputs(
    fixture: Issue175NoRawFixture,
) -> tuple[
    tuple[UntargetedCidNlAssessmentInput, ...],
    UntargetedCidNlRecoveryInput,
]:
    study = _support_input(fixture, "StudySupport", scan_number=41, height=100.0)
    qc = _support_input(fixture, "QCSupport", scan_number=42, height=200.0)
    missing = _assessment_input(fixture, "Missing", scanned_event_count=0)

    split_left = issue161_peak(
        fixture,
        "Split",
        apex_rt=7.9,
        rt_left_min=7.8,
        rt_right_min=8.0,
    )
    split_right = issue161_peak(
        fixture,
        "Split",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    split_evidence = issue161_evidence(
        fixture,
        "Split",
        scan_number=43,
        scan_rt_min=8.0,
    )
    split_relation = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("Split"),
        canonical_evidence=split_evidence,
        candidate_peaks=(split_left, split_right),
        policy=issue161_support_policy(),
    )
    split_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("Split"),
        method_subject=fixture.method_subject,
        detector_invocation_id="gaussian15:Split:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(split_left, split_right),
        evidence_refs=(split_evidence,),
        set_relation_refs=(split_relation,),
    )
    split = UntargetedCidNlAssessmentInput(
        sample=fixture.sample("Split"),
        assessment=split_assessment,
        discovery_projections=(_projection(fixture, "Split", split_evidence),),
    )

    reject_evidence = issue161_evidence(
        fixture,
        "Reject",
        scan_number=44,
        scan_rt_min=8.0,
    )
    reject_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("Reject"),
        method_subject=fixture.method_subject,
        detector_invocation_id="gaussian15:Reject:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        evidence_refs=(reject_evidence,),
    )
    reject = UntargetedCidNlAssessmentInput(
        sample=fixture.sample("Reject"),
        assessment=reject_assessment,
        discovery_projections=(_projection(fixture, "Reject", reject_evidence),),
    )
    technical = _technical_input(fixture, "Technical")
    blank = _technical_input(fixture, "Blank")
    mix_std = _technical_input(fixture, "MixSTD")

    recovery_peak = issue161_peak(fixture, "Missing", height=300.0)
    recovery_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("Missing"),
        method_subject=fixture.method_subject,
        detector_invocation_id="gaussian15:Missing:backfill-v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
        candidate_peaks=(recovery_peak,),
    )
    recovery = UntargetedCidNlRecoveryInput(
        sample=fixture.sample("Missing"),
        candidate_assessment=recovery_assessment,
    )
    return (
        (study, qc, missing, split, reject, technical, blank, mix_std),
        recovery,
    )


def _support_input(
    fixture: Issue175NoRawFixture,
    sample_stem: str,
    *,
    scan_number: int,
    height: float,
) -> UntargetedCidNlAssessmentInput:
    peak = issue161_peak(fixture, sample_stem, height=height)
    evidence = issue161_evidence(
        fixture,
        sample_stem,
        scan_number=scan_number,
        scan_rt_min=8.0,
    )
    support = issue161_unique_support(
        fixture,
        sample_stem,
        peak=peak,
        evidence=evidence,
    )
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        method_subject=fixture.method_subject,
        detector_invocation_id=f"gaussian15:{sample_stem}:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(peak,),
        evidence_refs=(evidence,),
        individual_support_refs=(support,),
    )
    return UntargetedCidNlAssessmentInput(
        sample=fixture.sample(sample_stem),
        assessment=assessment,
        discovery_projections=(_projection(fixture, sample_stem, evidence),),
    )


def _multi_projection_support_input(
    fixture: Issue175NoRawFixture,
    sample_stem: str,
) -> UntargetedCidNlAssessmentInput:
    peak = issue161_peak(fixture, sample_stem, height=100.0)
    evidence_refs = tuple(
        issue161_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number,
            scan_rt_min=8.0,
        )
        for scan_number in (141, 142)
    )
    support_refs = tuple(
        issue161_unique_support(
            fixture,
            sample_stem,
            peak=peak,
            evidence=evidence,
        )
        for evidence in evidence_refs
    )
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        method_subject=fixture.method_subject,
        detector_invocation_id=f"gaussian15:{sample_stem}:multi-projection:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=2),
        candidate_peaks=(peak,),
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )
    return UntargetedCidNlAssessmentInput(
        sample=fixture.sample(sample_stem),
        assessment=assessment,
        discovery_projections=tuple(
            _projection(fixture, sample_stem, evidence)
            for evidence in evidence_refs
        ),
    )


def _assessment_input(
    fixture: Issue175NoRawFixture,
    sample_stem: str,
    *,
    scanned_event_count: int,
) -> UntargetedCidNlAssessmentInput:
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        method_subject=fixture.method_subject,
        detector_invocation_id=f"gaussian15:{sample_stem}:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=scanned_event_count),
    )
    return UntargetedCidNlAssessmentInput(
        sample=fixture.sample(sample_stem),
        assessment=assessment,
    )


def _technical_input(
    fixture: Issue175NoRawFixture,
    sample_stem: str,
) -> UntargetedCidNlAssessmentInput:
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        method_subject=fixture.method_subject,
        detector_invocation_id=f"gaussian15:{sample_stem}:unsupported-v1",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=issue161_support_policy(),
            acquisition_supported=False,
            search_completed=False,
            scanned_event_count=0,
            reason_code="dda_not_available",
        ),
        technical_state="unsupported",
    )
    return UntargetedCidNlAssessmentInput(
        sample=fixture.sample(sample_stem),
        assessment=assessment,
    )


def _completed_receipt(*, scanned_event_count: int) -> CidNlEvaluationReceipt:
    return CidNlEvaluationReceipt(
        query_rt_min=7.5,
        query_rt_max=8.6,
        support_policy=issue161_support_policy(),
        acquisition_supported=True,
        search_completed=True,
        scanned_event_count=scanned_event_count,
        reason_code=None,
    )


def _projection(
    fixture: Issue175NoRawFixture,
    sample_stem: str,
    evidence,
) -> UntargetedCidNlProjectionRef:
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
    )
    observation = evidence.observation
    seed = DiscoverySeed(
        raw_file=fixture.source_paths[sample_stem],
        sample_stem=sample_stem,
        scan_number=observation.scan_number,
        rt=observation.scan_rt_min,
        precursor_mz=PRECURSOR_MZ,
        product_mz=PRODUCT_MZ,
        product_intensity=observation.product_intensity,
        neutral_loss_tag="NL116",
        configured_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_loss_error_ppm=0.0,
        matched_tag_names=("NL116",),
        scan_precursor_mz=PRECURSOR_MZ,
        acquisition_source_id=source.acquisition_source_id,
    )
    return mint_untargeted_cid_nl_projection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        canonical_evidence=evidence,
        discovery_seed=seed,
    )


def _check_acceptance_manifest(
    tmp_path: Path,
    projection,
) -> tuple[Path, tuple[dict[str, str], ...]]:
    source_path = tmp_path / projection.source_artifact_relpath
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(projection.source_artifact_bytes)
    rows = tuple(dict(row) for row in projection.pending_acceptance_rows)
    manifest_sha = production_acceptance_manifest_sha256(rows)
    checked_rows = tuple({**row, "manifest_sha256": manifest_sha} for row in rows)
    manifest_path = tmp_path / "production_acceptance_manifest.tsv"
    _write_acceptance_manifest(manifest_path, checked_rows)
    assert (
        check_production_acceptance_manifest(
            manifest_path=manifest_path,
            repo_root=tmp_path,
        )
        == []
    )
    return manifest_path, checked_rows


def _write_acceptance_manifest(
    manifest_path: Path,
    rows: tuple[dict[str, str], ...],
) -> None:
    manifest_path.write_text(
        render_delimited_rows(
            rows,
            REQUIRED_COLUMNS,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )


def _sample_stem(decision) -> str:
    sample_id = decision.target_cell.content.canonical_sample_identity_id
    return next(
        sample.metadata.raw_stem
        for sample in decision.group_facts.sample_population
        if sample.identity.sample_identity_id == sample_id
    )
