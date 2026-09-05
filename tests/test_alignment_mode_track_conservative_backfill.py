from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.check_production_acceptance_manifest import (
    REQUIRED_COLUMNS,
    check_production_acceptance_manifest,
    production_acceptance_manifest_sha256,
)
from tests import mode_track_raw_validation_fixture as closed_writer_fixture
from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.processing_identity_no_raw_fixture import (
    ProcessingIdentityNoRawFixture,
    build_processing_identity_no_raw_fixture,
)
from xic_extractor.alignment import conservative_backfill as backfill
from xic_extractor.alignment import untargeted_cid_nl_golden_path as golden_path
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackTrajectoryPolicy,
)
from xic_extractor.alignment.mode_track.producer import (
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
from xic_extractor.processing_identity import compose_processing_owner_references
from xic_extractor.sample_metadata import (
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)
from xic_extractor.tabular_io import render_delimited_rows


def test_mode_track_facts_split_mixed_assessments_without_cell_observation_refs(
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


@pytest.mark.parametrize("ineligible_kind", ("role", "excluded"))
def test_mode_track_candidates_exclude_activation_ineligible_samples(
    tmp_path: Path,
    ineligible_kind: str,
) -> None:
    roles = {
        "s1": "study_sample",
        "s2": "study_sample",
        "s3": "unknown" if ineligible_kind == "role" else "study_sample",
    }
    excluded = frozenset({"s3"}) if ineligible_kind == "excluded" else frozenset()
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path,
        sample_roles=roles,
        excluded_samples=excluded,
    )

    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )
    early, late = sorted(facts, key=lambda item: item.group.group_center_rt)

    assert early.may_attempt_recovery is True
    assert early.exact_owner_candidate_assignments == ()
    assert {
        fixture.sample("s1").identity.sample_identity_id,
        fixture.sample("s2").identity.sample_identity_id,
    } == set(early.activation_eligible_sample_ids)
    assert all(
        assignment.sample.metadata.raw_stem != "s3"
        for fact in (early, late)
        for assignment in fact.exact_owner_candidate_assignments
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


def test_mode_track_projection_prepares_only_backfill_eligible_exact_owner_cells(
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
        source_artifact_relpath="output/mode_track/mode_track_source.json",
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
        source_artifact_relpath="output/mode_track/mode_track_source.json",
    )

    assert projection.pending_acceptance_rows == ()
    assert projection.expected_diff_rows == ()


def test_mode_track_projection_rejects_same_ids_with_changed_sample_snapshot(
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
    changed_snapshot = mint_sample_metadata_snapshot(
        tuple(
            replace(sample.metadata, group="substituted-snapshot")
            for sample in fixture.owners.sample_resolutions
        ),
        identity_namespace=fixture.owners.sample_snapshot.identity_namespace,
    )
    changed_samples = tuple(
        resolve_canonical_sample_identity(changed_snapshot, sample.lookup_token)
        for sample in fixture.owners.sample_resolutions
    )
    substituted = compose_processing_owner_references(
        request=fixture.owners.request,
        attempt=fixture.owners.attempt,
        sample_snapshot=changed_snapshot,
        sample_resolutions=changed_samples,
        target_snapshot=fixture.owners.target_snapshot,
        evidence_artifacts=fixture.owners.evidence_artifacts,
        review_target=fixture.owners.review_target,
        review_snapshot=fixture.owners.review_snapshot,
        review_record=fixture.owners.review_record,
    )
    with pytest.raises(
        golden_path.UntargetedCidNlGoldenPathError,
        match="sample population",
    ):
        golden_path.build_untargeted_cid_nl_mode_track_projection(
            processing_owners=substituted,
            mode_track_facts=facts,
            source_artifact_relpath="proposal/mode_track_source.json",
        )


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
        source_artifact_relpath="output/mode_track/mode_track_source.json",
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

    assert source["schema_version"] == "mode_track_projection_source_v2"
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
        assert (
            row["peak_observation"]["content"]["peak_observation_content_id"]
            == row["physical_ids"][2]
        )
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


def test_closed_writer_two_stage_finalization_is_reproducible_and_rollback_exact(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    _checked_manifest(tmp_path, projection)

    first = _finalize_closed_writer_reviewed_projection(
        projection=projection,
        proposal_dir=proposal_dir,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        manifest_root=tmp_path,
        output_dir=tmp_path / "final-1",
    )
    second = _finalize_closed_writer_reviewed_projection(
        projection=projection,
        proposal_dir=proposal_dir,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        manifest_root=tmp_path,
        output_dir=tmp_path / "final-2",
    )

    assert first.read_bytes() == second.read_bytes()
    receipt = json.loads(first.read_bytes())
    assert receipt["adopted_cell_count"] == 2
    assert receipt["rollback_status"] == "baseline_exact"


def test_closed_writer_fresh_projection_ignores_resealed_c1_writer_values(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    proposal_path = proposal_dir / "proposal.json"
    receipt_path = proposal_dir / "proposal_receipt.json"
    proposal = json.loads(proposal_path.read_bytes())
    baseline_row = next(
        row
        for row in proposal["input_quant_matrix_rows"]
        if any(row.get(sample, "") for sample in projection.matrix_header[2:])
    )
    changed_sample = next(
        sample
        for sample in projection.matrix_header[2:]
        if baseline_row.get(sample, "")
    )
    baseline_row[changed_sample] = "999999999"
    proposal_path.write_text(
        json.dumps(proposal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = json.loads(receipt_path.read_bytes())
    receipt["proposal_sha256"] = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    replay = json.loads(replay_path.read_bytes())
    replay["proposal_sha256"] = receipt["proposal_sha256"]
    replay_path.write_text(
        json.dumps(replay, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    review = json.loads(review_path.read_bytes())
    review["observation_replay_sha256"] = hashlib.sha256(
        replay_path.read_bytes()
    ).hexdigest()
    for row in review["rows"]:
        row["proposal_sha256"] = receipt["proposal_sha256"]
    review_path.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validated_review = closed_writer_fixture._rehydrate_closed_writer_review_set(
        proposal_dir=proposal_dir,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        manifest_root=tmp_path,
        fresh_projection=projection,
    )
    final = closed_writer_fixture.finalize_closed_writer_fresh_projection(
        projection=projection,
        validated_review=validated_review,
        manifest_root=tmp_path,
        output_dir=tmp_path / "fresh-final",
    )
    result = json.loads((final.parent / "result_qmv.json").read_bytes())

    fresh_baseline = next(
        row for row in projection.input_quant_matrix_rows if row.get(changed_sample, "")
    )
    final_row = next(
        row
        for row in result["quant_matrix_rows"]
        if row["Mz"] == fresh_baseline["Mz"] and row["RT"] == fresh_baseline["RT"]
    )
    assert final_row[changed_sample] == fresh_baseline[changed_sample]
    assert final_row[changed_sample] != "999999999"


def test_closed_writer_writer_boundary_accepts_only_fresh_typed_authority() -> None:
    assert not hasattr(closed_writer_fixture, "finalize_closed_writer_proposal")
    assert set(
        inspect.signature(
            closed_writer_fixture.finalize_closed_writer_fresh_projection
        ).parameters
    ) == {"projection", "validated_review", "manifest_root", "output_dir"}


def test_closed_writer_transaction_receipt_publishes_atomically_or_not_at_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    validated_review = closed_writer_fixture._rehydrate_closed_writer_review_set(
        proposal_dir=proposal_dir,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        manifest_root=tmp_path,
        fresh_projection=projection,
    )
    fresh_owner_receipt = tmp_path / "fresh_owner_receipt.json"
    review_transfer_receipt = tmp_path / "review_transfer_receipt.json"
    fresh_owner_receipt.write_text("{}\n", encoding="utf-8")
    review_transfer_receipt.write_text("{}\n", encoding="utf-8")
    published_output = tmp_path / "published-c2-final"
    published_receipt = closed_writer_fixture._finalize_closed_writer_transaction(
        projection=projection,
        validated_review=validated_review,
        manifest_root=tmp_path,
        final_output_dir=published_output,
        code_sha="synthetic-no-raw",
        metadata_sha256="synthetic-metadata",
        fresh_owner_receipt_path=fresh_owner_receipt,
        review_transfer_receipt_path=review_transfer_receipt,
    )
    assert published_receipt == published_output / "transaction_receipt.json"
    assert (published_output / "result_qmv.json").is_file()
    assert (published_output / "final_receipt.json").is_file()
    assert published_receipt.is_file()

    final_output = tmp_path / "c2-final"
    original_write_json = closed_writer_fixture._write_json

    def fail_transaction_receipt(path: Path, payload: object) -> None:
        if path.name == "transaction_receipt.json":
            raise OSError("injected transaction receipt failure")
        original_write_json(path, payload)

    monkeypatch.setattr(closed_writer_fixture, "_write_json", fail_transaction_receipt)

    with pytest.raises(OSError, match="injected transaction receipt failure"):
        closed_writer_fixture._finalize_closed_writer_transaction(
            projection=projection,
            validated_review=validated_review,
            manifest_root=tmp_path,
            final_output_dir=final_output,
            code_sha="synthetic-no-raw",
            metadata_sha256="synthetic-metadata",
            fresh_owner_receipt_path=fresh_owner_receipt,
            review_transfer_receipt_path=review_transfer_receipt,
        )

    assert not final_output.exists()
    staging = closed_writer_fixture._closed_writer_transaction_staging_dir(final_output)
    assert (staging / "final_receipt.json").is_file()
    assert not (staging / "transaction_receipt.json").exists()


def test_closed_writer_review_transfer_rebinds_exact_subjects_and_finalizes(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    manifest_path = _checked_manifest(tmp_path, projection)
    retained_review = review_path.replace(tmp_path / "manual_review_v1.json")
    retained_replay = replay_path.replace(
        tmp_path / "manual_review_observation_replay_v1.json"
    )
    retained_manifest = manifest_path.replace(tmp_path / "checked_manifest_v1.tsv")
    _bind_review_replay(retained_review, retained_replay, tmp_path)

    receipt_path = closed_writer_fixture.transfer_closed_writer_review(
        reviewed_packet_dir=tmp_path,
        proposal_dir=proposal_dir,
        manifest_root=tmp_path,
        output_dir=tmp_path / "transferred",
        code_sha="synthetic-transfer",
    )
    receipt = json.loads(receipt_path.read_bytes())

    assert receipt["review_subject_count"] == 2
    assert receipt["product_authority"] == "none_review_input_only"
    closed_writer_fixture._validate_review_transfer_receipt(
        receipt_path=receipt_path,
        code_sha="synthetic-transfer",
        proposal_dir=proposal_dir,
        manual_review_path=tmp_path / "transferred/manual_review_v1.json",
        manual_review_replay_path=(
            tmp_path / "transferred/manual_review_observation_replay_v1.json"
        ),
        manifest_path=tmp_path / "transferred/checked_manifest_v1.tsv",
        manifest_root=tmp_path,
    )
    final = _finalize_closed_writer_reviewed_projection(
        projection=projection,
        proposal_dir=proposal_dir,
        manual_review_path=tmp_path / "transferred/manual_review_v1.json",
        manual_review_replay_path=(
            tmp_path / "transferred/manual_review_observation_replay_v1.json"
        ),
        manifest_root=tmp_path,
        output_dir=tmp_path / "final-after-transfer",
    )
    assert json.loads(final.read_bytes())["adopted_cell_count"] == 2
    assert retained_manifest.is_file()


def test_closed_writer_review_transfer_receipt_rejects_resealed_input_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    manifest_path = _checked_manifest(tmp_path, projection)
    retained_review = review_path.replace(tmp_path / "manual_review_v1.json")
    retained_replay = replay_path.replace(
        tmp_path / "manual_review_observation_replay_v1.json"
    )
    manifest_path.replace(tmp_path / "checked_manifest_v1.tsv")
    _bind_review_replay(retained_review, retained_replay, tmp_path)
    transferred = tmp_path / "transferred"
    receipt_path = closed_writer_fixture.transfer_closed_writer_review(
        reviewed_packet_dir=tmp_path,
        proposal_dir=proposal_dir,
        manifest_root=tmp_path,
        output_dir=transferred,
        code_sha="synthetic-transfer",
    )
    receipt = json.loads(receipt_path.read_bytes())
    receipt["checked_manifest_sha256"] = "0" * 64
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    raw_started = False

    def unexpected_raw_run(**_kwargs) -> None:
        nonlocal raw_started
        raw_started = True
        raise AssertionError("RAW owner must not start before transfer validation")

    monkeypatch.setattr(
        closed_writer_fixture, "_run_mode_track_batch", unexpected_raw_run
    )
    sample_metadata = projection.mode_track_facts[0].sample_population[0].metadata
    with pytest.raises(ValueError, match="review-transfer receipt bindings changed"):
        closed_writer_fixture.run_closed_writer_finalization(
            raw_files=tuple(tmp_path / f"sample-{index}.raw" for index in range(85)),
            sample_metadata=(sample_metadata,) * 85,
            dll_dir=tmp_path,
            manifest_root=tmp_path,
            replay_output_dir=tmp_path / "c2-replay",
            final_output_dir=tmp_path / "c2-final",
            proposal_dir=proposal_dir,
            manual_review_path=transferred / "manual_review_v1.json",
            manual_review_replay_path=(
                transferred / "manual_review_observation_replay_v1.json"
            ),
            review_transfer_receipt_path=receipt_path,
            manifest_path=transferred / "checked_manifest_v1.tsv",
            code_sha="synthetic-transfer",
            metadata_sha256="synthetic-metadata",
        )
    assert raw_started is False


def test_closed_writer_proposal_binds_complete_mode_track_decision_census(
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
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=tmp_path / "proposal",
        run_context=_closed_writer_run_context(projection),
    )

    proposal = json.loads((tmp_path / "proposal/proposal.json").read_bytes())
    receipt = json.loads((tmp_path / "proposal/proposal_receipt.json").read_bytes())
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
        "candidate_decisions_sha256": closed_writer_fixture._json_sha256(decisions),
        "exact_owner_candidate_count": 3,
        "mode_track_facts_sha256": closed_writer_fixture._json_sha256(
            proposal["mode_track_fact_rows"]
        ),
        "pending": 1,
        "rejected": 2,
        "track_count": 2,
    }


def test_closed_writer_scientific_projection_is_stable_across_fresh_attempts(
    tmp_path: Path,
) -> None:
    projections = []
    attempt_ids = []
    for stage in ("c1", "c2"):
        stage_root = tmp_path / stage
        stage_root.mkdir()
        fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
            stage_root
        )
        facts = _assess(
            fixture=fixture,
            sample_assessments=sample_assessments,
            model_set=model_set,
            batches=batches,
        )
        projections.append(
            closed_writer_fixture._mode_track_scientific_projection_rows(facts)
        )
        attempt_ids.append(fixture.owners.attempt.processing_attempt_id)

    assert attempt_ids[0] != attempt_ids[1]
    assert projections[0] == projections[1]


def test_closed_writer_fresh_owner_rejects_resealed_rejected_to_proposed_claim(
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
    rows = closed_writer_fixture._mode_track_scientific_projection_rows(facts)
    forged = [dict(row) for row in rows]
    below_threshold = next(
        row for row in forged if row["may_attempt_recovery"] is False
    )
    below_threshold["may_attempt_recovery"] = True
    below_threshold["reason_codes"] = ["direct_support_threshold_met"]

    with pytest.raises(
        ValueError,
        match="fresh Backfill scientific projection differs",
    ):
        closed_writer_fixture._validate_fresh_mode_track_projection(
            persisted_rows=forged,
            fresh_facts=facts,
        )


def test_closed_writer_fresh_owner_rejects_resealed_sample_stem_claim(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    proposal = json.loads((proposal_dir / "proposal.json").read_bytes())
    proposed = next(
        row
        for row in proposal["candidate_decision_rows"]
        if row["decision"] == "proposed"
    )
    proposed["sample_stem"] = "cross-sample-substitution"

    with pytest.raises(
        ValueError,
        match="candidate decisions differ from fresh Backfill",
    ):
        closed_writer_fixture._validate_fresh_proposed_keys(
            proposal,
            projection.mode_track_facts,
        )


def test_closed_writer_finalizer_rejects_resealed_backfill_eligibility_projection(
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
    proposal_dir = tmp_path / "proposal"
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    proposal_path = proposal_dir / "proposal.json"
    receipt_path = proposal_dir / "proposal_receipt.json"
    proposal = json.loads(proposal_path.read_bytes())
    row = next(
        item
        for item in proposal["mode_track_scientific_projection_rows"]
        if item["may_attempt_recovery"] is False
    )
    row["may_attempt_recovery"] = True
    row["reason_codes"] = ["direct_support_threshold_met"]
    proposal_path.write_text(
        json.dumps(proposal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = json.loads(receipt_path.read_bytes())
    receipt["proposal_sha256"] = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="fresh Backfill scientific projection differs",
    ):
        _finalize_closed_writer_reviewed_projection(
            projection=projection,
            proposal_dir=proposal_dir,
            manual_review_path=tmp_path / "missing-review.json",
            manual_review_replay_path=tmp_path / "missing-replay.json",
            manifest_root=tmp_path,
            output_dir=tmp_path / "forged-final",
        )


def test_closed_writer_finalizer_rejects_tampered_proposal(tmp_path: Path) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    proposal_path = proposal_dir / "proposal.json"
    proposal_path.write_text(
        proposal_path.read_text(encoding="utf-8").replace(
            '"matrix_header"', '"changed_matrix_header"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="proposal hash changed"):
        _finalize_closed_writer_reviewed_projection(
            projection=projection,
            proposal_dir=proposal_dir,
            manual_review_path=tmp_path / "missing-review.json",
            manual_review_replay_path=tmp_path / "missing-replay.json",
            manifest_root=tmp_path,
            output_dir=tmp_path / "blocked-final",
        )


def test_closed_writer_rejects_failed_review_and_ignores_external_manifest(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection, verdict="fail")
    replay_path = tmp_path / "manual_review_replay.json"
    manifest_path = _checked_manifest(tmp_path, projection)
    with pytest.raises(ValueError, match="not passing"):
        _finalize_closed_writer_reviewed_projection(
            projection=projection,
            proposal_dir=proposal_dir,
            manual_review_path=review_path,
            manual_review_replay_path=replay_path,
            manifest_root=tmp_path,
            output_dir=tmp_path / "failed-review",
        )

    _closed_writer_review(tmp_path, projection, verdict="pass")
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
    final = _finalize_closed_writer_reviewed_projection(
        projection=projection,
        proposal_dir=proposal_dir,
        manual_review_path=review_path,
        manual_review_replay_path=replay_path,
        manifest_root=tmp_path,
        output_dir=tmp_path / "changed-manifest",
    )
    internal_manifest = final.parent / "checked_manifest_v1.tsv"
    assert internal_manifest.is_file()
    assert "owner_valid_but_changed_after_c1" not in internal_manifest.read_text(
        encoding="utf-8"
    )


def test_closed_writer_terminal_oracle_rejects_owner_output_with_missing_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    owner = closed_writer_fixture.build_quant_matrix_version_from_checked_manifest

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
        closed_writer_fixture,
        "build_quant_matrix_version_from_checked_manifest",
        missing_value,
    )
    with pytest.raises(ValueError, match="unauthorized value"):
        _finalize_closed_writer_reviewed_projection(
            projection=projection,
            proposal_dir=proposal_dir,
            manual_review_path=review_path,
            manual_review_replay_path=replay_path,
            manifest_root=tmp_path,
            output_dir=tmp_path / "unauthorized-final",
        )
    assert not (tmp_path / "unauthorized-final").exists()


def test_closed_writer_finalizer_rejects_stale_or_incomplete_physical_replay(
    tmp_path: Path,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    replay = json.loads(replay_path.read_bytes())
    replay["proposal_sha256"] = "stale-proposal"
    replay_path.write_text(
        json.dumps(replay, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _bind_review_replay(review_path, replay_path, tmp_path)
    with pytest.raises(ValueError, match="authority binding changed"):
        _finalize_closed_writer_reviewed_projection(
            projection=projection,
            proposal_dir=proposal_dir,
            manual_review_path=review_path,
            manual_review_replay_path=replay_path,
            manifest_root=tmp_path,
            output_dir=tmp_path / "stale-replay-final",
        )

    receipt = json.loads((proposal_dir / "proposal_receipt.json").read_bytes())
    replay["proposal_sha256"] = receipt["proposal_sha256"]
    replay["rows"].pop()
    replay["row_count"] = len(replay["rows"])
    replay_path.write_text(
        json.dumps(replay, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _bind_review_replay(review_path, replay_path, tmp_path)

    with pytest.raises(ValueError, match="does not cover every pending row"):
        _finalize_closed_writer_reviewed_projection(
            projection=projection,
            proposal_dir=proposal_dir,
            manual_review_path=review_path,
            manual_review_replay_path=replay_path,
            manifest_root=tmp_path,
            output_dir=tmp_path / "incomplete-replay-final",
        )


@pytest.mark.parametrize(
    ("field_name", "index", "delta"),
    [("rt", 1, 1e-6), ("intensity", 0, 1.0)],
)
def test_closed_writer_v1_replay_rehydrates_owner_content_before_review(
    tmp_path: Path,
    field_name: str,
    index: int,
    delta: float,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    _closed_writer_review(tmp_path, projection)
    proposal = json.loads((proposal_dir / "proposal.json").read_bytes())
    receipt = json.loads((proposal_dir / "proposal_receipt.json").read_bytes())
    replay = json.loads((tmp_path / "manual_review_replay.json").read_bytes())
    source = json.loads(projection.source_artifact_bytes)
    for row in source["rows"]:
        row.pop("peak_observation", None)
    source_path = tmp_path / "retained-v1-mode-track-source.json"
    source_path.write_text(
        json.dumps(source, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    closed_writer_fixture._validate_manual_review_replay(
        replay,
        proposal,
        receipt,
        projection.pending_acceptance_rows,
        source_path,
    )
    content = replay["rows"][0]["observation_content"]
    content[field_name][index] += delta

    with pytest.raises(ValueError, match="observation content is owner-invalid"):
        closed_writer_fixture._validate_manual_review_replay(
            replay,
            proposal,
            receipt,
            projection.pending_acceptance_rows,
            source_path,
        )


def test_closed_writer_json_ingress_rejects_duplicate_nested_keys(
    tmp_path: Path,
) -> None:
    replay_path = tmp_path / "duplicate-content-key.json"
    replay_path.write_text(
        '{"observation_content":{"mz":349.215,"mz":350.0}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key: mz"):
        closed_writer_fixture._read_json_object(replay_path)


def test_closed_writer_rollback_is_derived_from_result_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection, proposal_dir = _closed_writer_proposal(tmp_path)
    closed_writer_fixture.freeze_closed_writer_proposal(
        projection=projection,
        manifest_root=tmp_path,
        output_dir=proposal_dir,
        run_context=_closed_writer_run_context(projection),
    )
    review_path = _closed_writer_review(tmp_path, projection)
    replay_path = tmp_path / "manual_review_replay.json"
    owner = closed_writer_fixture.build_quant_matrix_version_from_checked_manifest

    def mislabel_accepted_cell(**kwargs):
        result = owner(**kwargs)
        provenance = [dict(row) for row in result.cell_provenance_rows]
        accepted = next(
            row for row in provenance if row["cell_status"] == "accepted_backfill"
        )
        accepted["cell_status"] = "detected"
        return type(result)(
            quant_matrix_rows=result.quant_matrix_rows,
            cell_provenance_rows=tuple(provenance),
            row_summary_rows=result.row_summary_rows,
            expected_diff_summary_rows=result.expected_diff_summary_rows,
        )

    monkeypatch.setattr(
        closed_writer_fixture,
        "build_quant_matrix_version_from_checked_manifest",
        mislabel_accepted_cell,
    )
    with pytest.raises(ValueError, match="result-derived rollback differs"):
        _finalize_closed_writer_reviewed_projection(
            projection=projection,
            proposal_dir=proposal_dir,
            manual_review_path=review_path,
            manual_review_replay_path=replay_path,
            manifest_root=tmp_path,
            output_dir=tmp_path / "false-rollback-final",
        )


def _assess(
    *,
    fixture: ProcessingIdentityNoRawFixture,
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
    sample_roles: dict[str, str] | None = None,
    excluded_samples: frozenset[str] = frozenset(),
    support_pattern: tuple[
        tuple[str, tuple[str, ...], int],
        ...,
    ] = (
        ("s1", ("early",), 101),
        ("s2", ("early", "late"), 201),
        ("s3", ("late",), 301),
    ),
):
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2", "s3"),
        sample_roles=sample_roles
        or {"s1": "study_sample", "s2": "study_sample", "s3": "study_sample"},
        excluded_samples=excluded_samples,
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
    _, model_set, batches, _ = bootstrap_mode_tracks_from_cid_nl_support(
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
    fixture: ProcessingIdentityNoRawFixture,
    *,
    sample_stem: str,
    supported: tuple[str, ...],
    scan_number: int,
) -> CidNlMethodAssessmentRef:
    sample = fixture.sample(sample_stem)
    peaks = {
        "early": cid_nl_peak(
            fixture,
            sample_stem,
            apex_rt=7.8,
            rt_left_min=7.7,
            rt_right_min=7.9,
        ),
        "late": cid_nl_peak(
            fixture,
            sample_stem,
            apex_rt=8.4,
            rt_left_min=8.3,
            rt_right_min=8.5,
        ),
    }
    candidate_peaks = (peaks["early"], peaks["late"])
    evidence_refs = tuple(
        cid_nl_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number + index,
            scan_rt_min=peaks[name].content.integration.rt_apex_min,
        )
        for index, name in enumerate(supported)
    )
    support_refs = tuple(
        cid_nl_unique_support(
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
        detector_invocation_id=f"gaussian15:{sample_stem}:mode-track-red",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.7,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=len(supported),
            reason_code=None,
        ),
        candidate_peaks=candidate_peaks,
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )


def _closed_writer_proposal(tmp_path: Path):
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


def _finalize_closed_writer_reviewed_projection(
    *,
    projection,
    proposal_dir: Path,
    manual_review_path: Path,
    manual_review_replay_path: Path,
    manifest_root: Path,
    output_dir: Path,
) -> Path:
    validated_review = closed_writer_fixture._rehydrate_closed_writer_review_set(
        proposal_dir=proposal_dir,
        manual_review_path=manual_review_path,
        manual_review_replay_path=manual_review_replay_path,
        manifest_root=manifest_root,
        fresh_projection=projection,
    )
    return closed_writer_fixture.finalize_closed_writer_fresh_projection(
        projection=projection,
        validated_review=validated_review,
        manifest_root=manifest_root,
        output_dir=output_dir,
    )


def _closed_writer_run_context(projection) -> dict[str, object]:
    owner_ids = json.loads(projection.source_artifact_bytes)["rows"][0]["owner_ids"]
    return {
        "code_sha": "synthetic-no-raw",
        "processing_request_id": owner_ids[0],
        "processing_attempt_id": owner_ids[1],
        "call_cost": {
            "discovery_raw_open_count": 0,
            "runtime_raw_open_count": 0,
            "total_raw_open_count": 0,
            "runtime_xic_batch_count": 0,
        },
        "candidate_census": {"pending": len(projection.pending_acceptance_rows)},
    }


def _closed_writer_review(
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
    replay_path = _closed_writer_replay(tmp_path, projection)
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
                "schema_version": ("mode_track_closed_writer_manual_review_v1"),
                "observation_replay_relpath": replay_path.relative_to(
                    tmp_path
                ).as_posix(),
                "observation_replay_sha256": hashlib.sha256(
                    replay_path.read_bytes()
                ).hexdigest(),
                "rows": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _closed_writer_replay(tmp_path: Path, projection) -> Path:
    receipt = json.loads((tmp_path / "proposal/proposal_receipt.json").read_bytes())
    source_rows = json.loads(projection.source_artifact_bytes)["rows"]
    pending_keys = {
        (row["peak_hypothesis_id"], row["sample_stem"])
        for row in projection.pending_acceptance_rows
    }
    rows = []
    for source in source_rows:
        key = (source["group_hypothesis_id"], source["sample_stem"])
        if key not in pending_keys:
            continue
        rows.append(
            {
                "area_parity": "pass",
                "feature_family_id": source["group_hypothesis_id"],
                "observation_content": source["peak_observation"]["content"],
                "peak_hypothesis_id": source["group_hypothesis_id"],
                "peak_observation_content_id": source["physical_ids"][2],
                "proposal_quant_value": source["quant_value"],
                "sample_stem": source["sample_stem"],
            }
        )
    path = tmp_path / "manual_review_replay.json"
    path.write_text(
        json.dumps(
            {
                "proposal_sha256": receipt["proposal_sha256"],
                "row_count": len(rows),
                "rows": rows,
                "schema_version": ("mode_track_closed_writer_observation_replay_v1"),
                "source_artifact_sha256": projection.source_artifact_sha256,
                "validation_status": "diagnostic_only",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _bind_review_replay(review_path: Path, replay_path: Path, root: Path) -> None:
    review = json.loads(review_path.read_bytes())
    review["observation_replay_relpath"] = replay_path.relative_to(root).as_posix()
    review["observation_replay_sha256"] = hashlib.sha256(
        replay_path.read_bytes()
    ).hexdigest()
    review_path.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
