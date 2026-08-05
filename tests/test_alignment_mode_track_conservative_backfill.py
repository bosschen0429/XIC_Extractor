from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.check_production_acceptance_manifest import (
    REQUIRED_COLUMNS,
    check_production_acceptance_manifest,
    production_acceptance_manifest_sha256,
)
from tests import issue180_m9_raw_validation_fixture as gate_c_fixture
from tests.issue161_no_raw_fixture import (
    issue161_evidence,
    issue161_peak,
    issue161_support_policy,
    issue161_unique_support,
)
from tests.package2_no_raw_fixture import (
    Package2NoRawFixture,
    build_package2_no_raw_fixture,
)
from xic_extractor.alignment import conservative_backfill as backfill
from xic_extractor.alignment import untargeted_cid_nl_golden_path as golden_path
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_coherence.mode_track_assignment import (
    ModeTrackTrajectoryPolicy,
)
from xic_extractor.alignment.identity_coherence.mode_track_producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentRef,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.observations import (
    canonical_peak_observation_ref_bytes,
)
from xic_extractor.tabular_io import render_delimited_rows


def test_mode_track_facts_split_mixed_assessments_without_m1_cells(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )

    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )

    assert len(facts) == 2
    by_center = {item.group.group_center_rt: item for item in facts}
    assert len(by_center) == 2
    assert {
        (
            item.sample_count_n,
            item.minimum_support_k,
            item.direct_support_count_s,
            item.group_frozen,
            item.may_attempt_recovery,
        )
        for item in facts
    } == {(3, 2, 2, True, True)}
    assert {len(item.direct_support_assignments) for item in facts} == {2}
    assert {len(item.exact_owner_candidate_assignments) for item in facts} == {1}
    assert all(item.product_authority == "none" for item in facts)
    assert all(not hasattr(item, "source_cells") for item in facts)


def test_mode_track_support_votes_come_from_assessment_closures_only(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )

    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )

    for item in facts:
        assert len(item.group.owner_ids) == 3
        assert len(item.direct_support_sample_ids) == 2
        assert item.direct_support_count_s == 2
        assert {
            assignment.canonical_sample_identity_id
            for assignment in item.direct_support_assignments
        } == set(item.direct_support_sample_ids)
        assert all(
            assignment.candidate_observation_class == "direct_support"
            for assignment in item.direct_support_assignments
        )
        assert all(
            assignment.candidate_observation_class == "non_training"
            and assignment.reason_codes == ("existing_group_owner_observation",)
            and assignment.peak_hypothesis_id in item.group.owner_ids
            for assignment in item.exact_owner_candidate_assignments
        )


def test_mode_track_facts_are_order_invariant_and_retain_exact_assignments(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )

    forward = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )
    reversed_input = _assess(
        fixture=fixture,
        sample_assessments=tuple(reversed(sample_assessments)),
        model_set=model_set,
        batches=tuple(reversed(batches)),
    )

    assert reversed_input == forward
    assert all(
        candidate.group_hypothesis_id == item.group.group_hypothesis_id
        for item in forward
        for candidate in item.exact_owner_candidate_assignments
    )


def test_mode_track_facts_reject_partial_sample_batch_population(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )

    with pytest.raises(
        backfill.ConservativeBackfillContractError,
        match="complete sample population",
    ):
        _assess(
            fixture=fixture,
            sample_assessments=sample_assessments,
            model_set=model_set,
            batches=batches[:-1],
        )


def test_mode_track_groups_activate_independently_of_exact_owner_candidates(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path,
        support_pattern=(
            ("s1", ("early",), 101),
            ("s2", ("early", "late"), 201),
            ("s3", ("early",), 301),
        ),
    )

    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )
    early, late = sorted(facts, key=lambda item: item.group.group_center_rt)

    assert (early.direct_support_count_s, early.minimum_support_k) == (3, 2)
    assert early.may_attempt_recovery is True
    assert early.exact_owner_candidate_assignments == ()
    assert (late.direct_support_count_s, late.minimum_support_k) == (1, 2)
    assert late.may_attempt_recovery is False
    assert len(late.exact_owner_candidate_assignments) == 2
    assert late.reason_codes == ("direct_support_threshold_not_met",)


def test_mode_track_projection_prepares_only_m2_eligible_exact_owner_cells(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )
    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )

    build_projection = getattr(
        golden_path,
        "build_untargeted_cid_nl_mode_track_projection",
    )
    projection = build_projection(
        processing_owners=fixture.owners,
        mode_track_facts=facts,
        source_artifact_relpath="output/m9/mode_track_source.json",
    )

    assert projection.backfill_decisions == ()
    assert len(projection.input_quant_matrix_rows) == 2
    assert {
        row["peak_hypothesis_id"] for row in projection.input_matrix_identity_rows
    } == {item.group.group_hypothesis_id for item in facts}
    assert len(projection.pending_acceptance_rows) == 2
    assert len(projection.expected_diff_rows) == 2
    assert all(
        row["peak_hypothesis_id"] in {item.group.group_hypothesis_id for item in facts}
        and row["acceptance_decision"] == "accept_basic_backfill"
        and row["write_authority"] == "TRUE"
        for row in projection.pending_acceptance_rows
    )
    source_rows = json.loads(projection.source_artifact_bytes)["rows"]
    assert len(source_rows) == 2
    assert all(
        set(row)
        >= {
            "owner_ids",
            "acquisition_ids",
            "activation",
            "direct_support_assignment_ids",
            "physical_ids",
        }
        for row in source_rows
    )
    assert projection.product_authority == "none_until_checked_manifest"


def test_mode_track_projection_does_not_publish_below_threshold_candidates(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path,
        support_pattern=(
            ("s1", ("early",), 101),
            ("s2", ("early", "late"), 201),
            ("s3", ("early",), 301),
        ),
    )
    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )

    projection = getattr(
        golden_path,
        "build_untargeted_cid_nl_mode_track_projection",
    )(
        processing_owners=fixture.owners,
        mode_track_facts=facts,
        source_artifact_relpath="output/m9/mode_track_source.json",
    )

    assert projection.pending_acceptance_rows == ()
    assert projection.expected_diff_rows == ()


def test_mode_track_projection_enters_existing_checked_qmv_owner(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )
    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )
    projection = golden_path.build_untargeted_cid_nl_mode_track_projection(
        processing_owners=fixture.owners,
        mode_track_facts=facts,
        source_artifact_relpath="output/m9/mode_track_source.json",
    )
    source = json.loads(projection.source_artifact_bytes)
    expected_assignments = {
        (fact.group.group_hypothesis_id, assignment.sample.metadata.raw_stem): (
            assignment
        )
        for fact in facts
        if fact.may_attempt_recovery
        for assignment in fact.exact_owner_candidate_assignments
    }

    assert source["schema_version"] == "m9_mode_track_source_v2"
    assert len(source["rows"]) == len(expected_assignments)
    for row in source["rows"]:
        assignment = expected_assignments[
            (row["group_hypothesis_id"], row["sample_stem"])
        ]
        observation = assignment.peak_hypothesis.peak_observation_refs[0]
        assert row["peak_observation_artifact_id"] == (
            observation.artifact.evidence_artifact_id
        )
        assert row["peak_observation_artifact_content_id"] == (
            observation.artifact.content_identity.artifact_content_id
        )
        assert row["peak_observation"] == json.loads(
            canonical_peak_observation_ref_bytes(
                observation.content,
                observation.generation_provenance,
            )
        )
        assert row["peak_observation"]["content"][
            "peak_observation_content_id"
        ] == row["physical_ids"][2]
    manifest_path = _checked_manifest(tmp_path, projection)

    terminal = golden_path.build_untargeted_cid_nl_quant_matrix_version(
        projection,
        production_acceptance_manifest_path=manifest_path,
        manifest_root=tmp_path,
    )

    assert len(terminal.quant_matrix_rows) == 2
    assert all(
        sum(bool(row.get(stem, "")) for stem in projection.matrix_header[2:]) == 3
        for row in terminal.quant_matrix_rows
    )
    assert (
        sum(
            row["cell_status"] == "accepted_backfill"
            for row in terminal.cell_provenance_rows
        )
        == 2
    )
    assert all("0" not in row.values() for row in terminal.quant_matrix_rows)


def test_gate_c_two_stage_finalization_is_reproducible_and_rollback_exact(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    manifest_path = _checked_manifest(tmp_path, projection)

    first = gate_c_fixture.finalize_gate_c_proposal(
        proposal_dir=proposal_dir,
        manual_review_path=review_path,
        manifest_path=manifest_path,
        manifest_root=tmp_path,
        output_dir=tmp_path / "final-1",
    )
    second = gate_c_fixture.finalize_gate_c_proposal(
        proposal_dir=proposal_dir,
        manual_review_path=review_path,
        manifest_path=manifest_path,
        manifest_root=tmp_path,
        output_dir=tmp_path / "final-2",
    )

    assert first.read_bytes() == second.read_bytes()
    receipt = json.loads(first.read_bytes())
    assert receipt["adopted_cell_count"] == 2
    assert receipt["rollback_status"] == "baseline_exact"


def test_gate_c_proposal_binds_complete_mode_track_decision_census(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path,
        support_pattern=(
            ("s1", ("early",), 101),
            ("s2", ("early",), 201),
            ("s3", ("late",), 301),
        ),
    )
    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )
    projection = golden_path.build_untargeted_cid_nl_mode_track_projection(
        processing_owners=fixture.owners,
        mode_track_facts=facts,
        source_artifact_relpath="proposal/mode_track_source.json",
    )
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=tmp_path / "proposal",
        run_context=_gate_c_run_context(projection),
    )

    proposal = json.loads((tmp_path / "proposal/proposal.json").read_bytes())
    receipt = json.loads(
        (tmp_path / "proposal/proposal_receipt.json").read_bytes()
    )
    decisions = proposal["candidate_decision_rows"]
    support_counts = [
        row["direct_support_count_s"] for row in proposal["mode_track_fact_rows"]
    ]
    assert support_counts == [
        2,
        1,
    ]
    assert sum(row["decision"] == "proposed" for row in decisions) == 1
    assert sum(row["decision"] == "rejected" for row in decisions) == 2
    assert receipt["run_context"]["candidate_census"] == {
        "candidate_decisions_sha256": gate_c_fixture._json_sha256(decisions),
        "exact_owner_candidate_count": 3,
        "mode_track_facts_sha256": gate_c_fixture._json_sha256(
            proposal["mode_track_fact_rows"]
        ),
        "pending": 1,
        "rejected": 2,
        "track_count": 2,
    }


def test_gate_c_finalizer_rejects_tampered_proposal(tmp_path: Path) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    proposal_path = proposal_dir / "proposal.json"
    proposal_path.write_text(
        proposal_path.read_text(encoding="utf-8").replace(
            '"matrix_header"', '"changed_matrix_header"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="proposal hash changed"):
        gate_c_fixture.finalize_gate_c_proposal(
            proposal_dir=proposal_dir,
            manual_review_path=tmp_path / "missing-review.json",
            manifest_path=tmp_path / "missing-manifest.tsv",
            manifest_root=tmp_path,
            output_dir=tmp_path / "blocked-final",
        )


def test_gate_c_finalizer_rejects_failed_review_and_manifest_drift(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection, verdict="fail")
    manifest_path = _checked_manifest(tmp_path, projection)
    with pytest.raises(ValueError, match="not passing"):
        gate_c_fixture.finalize_gate_c_proposal(
            proposal_dir=proposal_dir,
            manual_review_path=review_path,
            manifest_path=manifest_path,
            manifest_root=tmp_path,
            output_dir=tmp_path / "failed-review",
        )

    _gate_c_review(tmp_path, projection, verdict="pass")
    rows = [dict(row) for row in projection.pending_acceptance_rows]
    rows[0]["decision_reason"] = "owner_valid_but_changed_after_c1"
    manifest_sha = production_acceptance_manifest_sha256(rows)
    changed = tuple({**row, "manifest_sha256": manifest_sha} for row in rows)
    manifest_path.write_text(
        render_delimited_rows(
            changed,
            REQUIRED_COLUMNS,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="differs from C1 proposal"):
        gate_c_fixture.finalize_gate_c_proposal(
            proposal_dir=proposal_dir,
            manual_review_path=review_path,
            manifest_path=manifest_path,
            manifest_root=tmp_path,
            output_dir=tmp_path / "changed-manifest",
        )


def test_gate_c_terminal_oracle_rejects_owner_output_with_missing_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, proposal_dir = _gate_c_proposal(tmp_path)
    gate_c_fixture.freeze_gate_c_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_gate_c_run_context(projection),
    )
    review_path = _gate_c_review(tmp_path, projection)
    manifest_path = _checked_manifest(tmp_path, projection)
    owner = gate_c_fixture.build_quant_matrix_version_from_checked_manifest

    def missing_value(**kwargs):
        result = owner(**kwargs)
        rows = [dict(row) for row in result.quant_matrix_rows]
        rows[0][projection.matrix_header[-1]] = ""
        return type(result)(
            quant_matrix_rows=tuple(rows),
            cell_provenance_rows=result.cell_provenance_rows,
            row_summary_rows=result.row_summary_rows,
            expected_diff_summary_rows=result.expected_diff_summary_rows,
        )

    monkeypatch.setattr(
        gate_c_fixture,
        "build_quant_matrix_version_from_checked_manifest",
        missing_value,
    )
    with pytest.raises(ValueError, match="unauthorized value"):
        gate_c_fixture.finalize_gate_c_proposal(
            proposal_dir=proposal_dir,
            manual_review_path=review_path,
            manifest_path=manifest_path,
            manifest_root=tmp_path,
            output_dir=tmp_path / "unauthorized-final",
        )


def _assess(
    *,
    fixture: Package2NoRawFixture,
    sample_assessments: tuple[
        tuple[object, CidNlMethodAssessmentRef],
        ...,
    ],
    model_set,
    batches,
):
    operation = getattr(
        backfill,
        "assess_mode_track_conservative_backfill_groups",
    )
    return operation(
        processing_owners=fixture.owners,
        model_set=model_set,
        assignment_batches=batches,
        sample_assessments=sample_assessments,
    )


def _mixed_mode_track_fixture(
    tmp_path: Path,
    *,
    support_pattern: tuple[
        tuple[str, tuple[str, ...], int],
        ...,
    ] = (
        ("s1", ("early",), 101),
        ("s2", ("early", "late"), 201),
        ("s3", ("late",), 301),
    ),
):
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2", "s3"),
        sample_roles={
            "s1": "study_sample",
            "s2": "study_sample",
            "s3": "study_sample",
        },
    )
    sample_assessments = tuple(
        (
            fixture.sample(sample_stem),
            _two_peak_assessment(
                fixture,
                sample_stem=sample_stem,
                supported=supported,
                scan_number=scan_number,
            ),
        )
        for sample_stem, supported, scan_number in support_pattern
    )
    _, model_set, batches = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=sample_assessments,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    assert len(model_set.tracks) == 2
    assert tuple(len(batch.assignments) for batch in batches) == (2, 2, 2)
    return fixture, sample_assessments, model_set, batches


def _two_peak_assessment(
    fixture: Package2NoRawFixture,
    *,
    sample_stem: str,
    supported: tuple[str, ...],
    scan_number: int,
) -> CidNlMethodAssessmentRef:
    sample = fixture.sample(sample_stem)
    peaks = {
        "early": issue161_peak(
            fixture,
            sample_stem,
            apex_rt=7.8,
            rt_left_min=7.7,
            rt_right_min=7.9,
        ),
        "late": issue161_peak(
            fixture,
            sample_stem,
            apex_rt=8.4,
            rt_left_min=8.3,
            rt_right_min=8.5,
        ),
    }
    candidate_peaks = (peaks["early"], peaks["late"])
    evidence_refs = tuple(
        issue161_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number + index,
            scan_rt_min=peaks[name].content.integration.rt_apex_min,
        )
        for index, name in enumerate(supported)
    )
    support_refs = tuple(
        issue161_unique_support(
            fixture,
            sample_stem,
            peak=peaks[name],
            evidence=evidence,
            candidate_peaks=candidate_peaks,
        )
        for name, evidence in zip(supported, evidence_refs, strict=True)
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}:m9-red",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.7,
            support_policy=issue161_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=len(supported),
            reason_code=None,
        ),
        candidate_peaks=candidate_peaks,
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )


def _gate_c_proposal(tmp_path: Path):
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )
    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )
    output_dir = tmp_path / "proposal"
    projection = golden_path.build_untargeted_cid_nl_mode_track_projection(
        processing_owners=fixture.owners,
        mode_track_facts=facts,
        source_artifact_relpath="proposal/mode_track_source.json",
    )
    return projection, output_dir


def _gate_c_run_context(projection) -> dict[str, object]:
    owner_ids = json.loads(projection.source_artifact_bytes)["rows"][0]["owner_ids"]
    return {
        "code_sha": "synthetic-no-raw",
        "processing_request_id": owner_ids[0],
        "processing_attempt_id": owner_ids[1],
        "call_cost": {"raw_open_count": 0, "xic_batch_count": 0},
        "candidate_census": {"pending": len(projection.pending_acceptance_rows)},
    }


def _gate_c_review(
    tmp_path: Path,
    projection,
    *,
    verdict: str = "pass",
) -> Path:
    receipt = json.loads((tmp_path / "proposal/proposal_receipt.json").read_bytes())
    source_rows = json.loads(projection.source_artifact_bytes)["rows"]
    source_by_key = {
        (row["group_hypothesis_id"], row["sample_stem"]): row for row in source_rows
    }
    context = receipt["run_context"]
    rows = []
    for pending in projection.pending_acceptance_rows:
        key = (pending["peak_hypothesis_id"], pending["sample_stem"])
        candidate = source_by_key[key]
        rows.append(
            {
                "peak_hypothesis_id": key[0],
                "sample_stem": key[1],
                "proposal_sha256": receipt["proposal_sha256"],
                "source_artifact_sha256": projection.source_artifact_sha256,
                "processing_request_id": context["processing_request_id"],
                "processing_attempt_id": context["processing_attempt_id"],
                "candidate_key_sha256": hashlib.sha256(
                    json.dumps(
                        candidate,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "reviewer_id": "synthetic-reviewer",
                "reviewed_at": "2026-08-05T00:00:00+08:00",
                "evidence_locator": f"synthetic-eic-ms2:{key[0]}:{key[1]}",
                "verdict": verdict,
                "notes": "synthetic no-RAW rehearsal",
            }
        )
    path = tmp_path / "manual_review.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "issue180_m9_gate_c_manual_review_v1",
                "rows": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _checked_manifest(tmp_path: Path, projection) -> Path:
    source_path = tmp_path / projection.source_artifact_relpath
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(projection.source_artifact_bytes)
    rows = tuple(dict(row) for row in projection.pending_acceptance_rows)
    manifest_sha = production_acceptance_manifest_sha256(rows)
    checked = tuple({**row, "manifest_sha256": manifest_sha} for row in rows)
    manifest_path = tmp_path / "production_acceptance_manifest.tsv"
    manifest_path.write_text(
        render_delimited_rows(
            checked,
            REQUIRED_COLUMNS,
            delimiter="\t",
            extrasaction="raise",
            lineterminator="\n",
        ),
        encoding="utf-8",
    )
    assert (
        check_production_acceptance_manifest(
            manifest_path=manifest_path,
            repo_root=tmp_path,
        )
        == []
    )
    return manifest_path
