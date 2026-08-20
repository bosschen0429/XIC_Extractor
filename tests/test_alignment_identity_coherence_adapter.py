from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_coherence.adapter import (
    IdentityCoherenceDiagnosticRun,
    IdentityCoherenceSeedSource,
    build_cell_candidate_evidence,
    build_identity_coherence_seed_sources,
    candidate_identity_family_id,
    candidate_is_non_seed_pool_member,
    retrieve_identity_coherence_trace,
    run_identity_coherence_diagnostic,
)
from xic_extractor.alignment.identity_coherence.models import (
    CandidateTrace,
    IdentityCoherenceTraceRequest,
    IdentityCoherenceTraceResult,
)
from xic_extractor.alignment.ownership import OwnershipBuildResult
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    OwnerAssignment,
    SampleLocalMS1Owner,
)
from xic_extractor.discovery.models import DiscoveryCandidate


def _candidate(
    candidate_id: str,
    *,
    sample_stem: str = "Sample_A",
    precursor_mz: float = 500.0,
    product_mz: float = 384.0,
    observed_loss: float = 116.0,
    best_seed_rt: float = 5.0,
    apex_rt: float | None = 5.0,
    start_rt: float | None = 4.95,
    end_rt: float | None = 5.05,
    area: float | None = 1000.0,
    height: float | None = 100.0,
    matched_tags: tuple[str, ...] = ("MeR", "dR"),
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        review_priority="HIGH",
        evidence_score=80,
        evidence_tier="A",
        ms2_support="seed",
        ms1_support="owner",
        rt_alignment="local",
        candidate_id=candidate_id,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=observed_loss,
        best_seed_rt=best_seed_rt,
        seed_event_count=2,
        ms1_peak_found=apex_rt is not None,
        ms1_apex_rt=apex_rt,
        ms1_area=area,
        ms2_product_max_intensity=10000.0,
        reason="test candidate",
        raw_file=Path(f"{sample_stem}.raw"),
        sample_stem=sample_stem,
        best_ms2_scan_id=101,
        seed_scan_ids=(101,),
        neutral_loss_tag="dR",
        configured_neutral_loss_da=116.0,
        neutral_loss_mass_error_ppm=0.0,
        rt_seed_min=best_seed_rt,
        rt_seed_max=best_seed_rt,
        ms1_search_rt_min=best_seed_rt - 0.2,
        ms1_search_rt_max=best_seed_rt + 0.2,
        ms1_seed_delta_min=0.0,
        ms1_peak_rt_start=start_rt,
        ms1_peak_rt_end=end_rt,
        ms1_height=height,
        ms1_trace_quality="clean",
        ms1_scan_support_score=0.9,
        matched_tag_count=len(matched_tags),
        matched_tag_names=matched_tags,
        primary_tag_name="dR",
    )


def _event(candidate: DiscoveryCandidate) -> IdentityEvent:
    return IdentityEvent(
        candidate_id=candidate.candidate_id,
        sample_stem=candidate.sample_stem,
        raw_file=str(candidate.raw_file),
        neutral_loss_tag=candidate.neutral_loss_tag,
        precursor_mz=candidate.precursor_mz,
        product_mz=candidate.product_mz,
        observed_neutral_loss_da=candidate.observed_neutral_loss_da,
        seed_rt=candidate.best_seed_rt,
        evidence_score=candidate.evidence_score,
        seed_event_count=candidate.seed_event_count,
    )


def _owner(candidate: DiscoveryCandidate) -> SampleLocalMS1Owner:
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{candidate.candidate_id}",
        sample_stem=candidate.sample_stem,
        raw_file=str(candidate.raw_file),
        precursor_mz=candidate.precursor_mz,
        owner_apex_rt=float(candidate.ms1_apex_rt),
        owner_peak_start_rt=float(candidate.ms1_peak_rt_start),
        owner_peak_end_rt=float(candidate.ms1_peak_rt_end),
        owner_area=float(candidate.ms1_area),
        owner_height=float(candidate.ms1_height),
        primary_identity_event=_event(candidate),
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="primary_identity_event",
    )


def _ownership(*owners: SampleLocalMS1Owner) -> OwnershipBuildResult:
    assignments = tuple(
        OwnerAssignment(
            candidate_id=owner.primary_identity_event.candidate_id,
            owner_id=owner.owner_id,
            assignment_status="primary",
            reason="primary_identity_event",
        )
        for owner in owners
    )
    return OwnershipBuildResult(
        owners=owners,
        assignments=assignments,
        ambiguous_records=(),
    )


def test_build_seed_sources_uses_primary_pre_backfill_owners() -> None:
    seed = _candidate("CAND-SEED")
    source = build_identity_coherence_seed_sources(
        candidates=(seed,),
        ownership=_ownership(_owner(seed)),
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )[0]

    assert isinstance(source, IdentityCoherenceSeedSource)
    assert source.request.request_id == "ICR000001"
    assert source.decision_id == "ICD000001"
    assert source.identity_family_id == "ICF000001"
    assert source.seed_candidate.candidate_id == "CAND-SEED"
    assert source.seed_evidence.candidate_id == "CAND-SEED"
    assert source.owner.owner_id == "OWN-CAND-SEED"
    assert source.owner_assignment_status == "primary"
    assert source.seed_gate.seed_gate_class.value == "coherent_seed"


def test_build_seed_sources_skips_owner_without_candidate_join() -> None:
    seed = _candidate("CAND-SEED")
    sources = build_identity_coherence_seed_sources(
        candidates=(),
        ownership=_ownership(_owner(seed)),
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )

    assert sources == ()


def test_build_seed_sources_skips_owner_without_assignment() -> None:
    seed = _candidate("CAND-SEED")
    sources = build_identity_coherence_seed_sources(
        candidates=(seed,),
        ownership=OwnershipBuildResult(
            owners=(_owner(seed),),
            assignments=(),
            ambiguous_records=(),
        ),
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )

    assert sources == ()


def test_candidate_pool_member_uses_metadata_before_trace_retrieval() -> None:
    seed = _candidate("CAND-SEED", sample_stem="Sample_A", best_seed_rt=5.0)
    request = build_identity_coherence_seed_sources(
        candidates=(seed,),
        ownership=_ownership(_owner(seed)),
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )[0].request
    nearby = _candidate("CAND-NEAR", sample_stem="Sample_B", best_seed_rt=5.5)
    far_rt = _candidate("CAND-FAR", sample_stem="Sample_B", best_seed_rt=9.0)
    same_sample = _candidate(
        "CAND-SAME",
        sample_stem="Sample_A",
        best_seed_rt=5.1,
    )
    bad_morphology = _candidate(
        "CAND-BAD",
        sample_stem="Sample_B",
        apex_rt=None,
        start_rt=None,
        end_rt=None,
        area=None,
        height=None,
    )

    assert candidate_is_non_seed_pool_member(
        request,
        nearby,
        seed_candidate=seed,
        config=AlignmentConfig(),
    )
    assert not candidate_is_non_seed_pool_member(
        request,
        far_rt,
        seed_candidate=seed,
        config=AlignmentConfig(),
    )
    assert not candidate_is_non_seed_pool_member(
        request,
        same_sample,
        seed_candidate=seed,
        config=AlignmentConfig(),
    )
    assert not candidate_is_non_seed_pool_member(
        request,
        bad_morphology,
        seed_candidate=seed,
        config=AlignmentConfig(),
    )


def test_candidate_identity_family_id_is_diagnostic_only() -> None:
    assert candidate_identity_family_id(3) == "ICF000003"


class FakeRawSource:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float, float, float]] = []

    def extract_xic(self, mz: float, rt_min: float, rt_max: float, ppm_tol: float):
        self.calls.append((mz, rt_min, rt_max, ppm_tol))
        return (
            [rt_min, (rt_min + rt_max) / 2.0, rt_max],
            [0.0, 10.0, 0.0],
        )


class FailingRawSource:
    def extract_xic(self, mz: float, rt_min: float, rt_max: float, ppm_tol: float):
        raise OSError("raw unavailable")


def test_retrieve_identity_trace_uses_candidate_peak_boundaries() -> None:
    request = IdentityCoherenceTraceRequest(
        decision_id="ICD000001",
        request_id="ICR000001",
        sample_id="Sample_B",
        candidate_id="CAND-2",
        precursor_mz=500.0,
        ppm_tolerance=20.0,
        rt_min=5.10,
        rt_max=5.20,
    )
    source = FakeRawSource()

    result = retrieve_identity_coherence_trace(request, {"Sample_B": source})

    assert result.status == "pass"
    assert result.request == request
    assert result.raw_xic_request_count == 1
    assert result.xic_point_count == 3
    assert source.calls == [(500.0, 5.10, 5.20, 20.0)]
    assert result.trace == CandidateTrace(
        rt_min=(5.10, 5.15, 5.20),
        intensity=(0.0, 10.0, 0.0),
    )


def test_retrieve_identity_trace_blocks_missing_raw_source() -> None:
    request = IdentityCoherenceTraceRequest(
        decision_id="ICD000001",
        request_id="ICR000001",
        sample_id="Missing",
        candidate_id="CAND-2",
        precursor_mz=500.0,
        ppm_tolerance=20.0,
        rt_min=5.10,
        rt_max=5.20,
    )

    result = retrieve_identity_coherence_trace(request, {})

    assert result.status == "blocked_infrastructure"
    assert result.blocked_reason == "missing_raw_source"
    assert result.raw_xic_request_count == 0
    assert result.xic_point_count == 0
    assert result.trace is None


def test_retrieve_identity_trace_blocks_extraction_error() -> None:
    request = IdentityCoherenceTraceRequest(
        decision_id="ICD000001",
        request_id="ICR000001",
        sample_id="Sample_B",
        candidate_id="CAND-2",
        precursor_mz=500.0,
        ppm_tolerance=20.0,
        rt_min=5.10,
        rt_max=5.20,
    )

    result = retrieve_identity_coherence_trace(
        request,
        {"Sample_B": FailingRawSource()},
    )

    assert result.status == "blocked_infrastructure"
    assert result.blocked_reason == "raw_xic_extraction_error"
    assert result.raw_xic_request_count == 1
    assert result.trace is None


def test_build_cell_candidate_evidence_attaches_pass_trace() -> None:
    candidate = _candidate("CAND-2", sample_stem="Sample_B")
    trace_result = retrieve_identity_coherence_trace(
        IdentityCoherenceTraceRequest(
            decision_id="ICD000001",
            request_id="ICR000001",
            sample_id="Sample_B",
            candidate_id="CAND-2",
            precursor_mz=500.0,
            ppm_tolerance=20.0,
            rt_min=4.95,
            rt_max=5.05,
        ),
        {"Sample_B": FakeRawSource()},
    )

    cell = build_cell_candidate_evidence(
        candidate,
        owner_assignment_status="supporting",
        trace_result=trace_result,
    )

    assert cell.sample_id == "Sample_B"
    assert cell.candidate_evidence.candidate_id == "CAND-2"
    assert cell.owner_assignment_status == "supporting"
    assert cell.trace is not None
    assert cell.point_count == 3
    assert cell.blocked_reason == ""


def test_build_cell_candidate_evidence_marks_blocked_trace() -> None:
    candidate = _candidate("CAND-2", sample_stem="Sample_B")
    trace_result = retrieve_identity_coherence_trace(
        IdentityCoherenceTraceRequest(
            decision_id="ICD000001",
            request_id="ICR000001",
            sample_id="Sample_B",
            candidate_id="CAND-2",
            precursor_mz=500.0,
            ppm_tolerance=20.0,
            rt_min=4.95,
            rt_max=5.05,
        ),
        {},
    )

    cell = build_cell_candidate_evidence(
        candidate,
        owner_assignment_status="primary",
        trace_result=trace_result,
    )

    assert cell.trace is None
    assert cell.blocked_reason == "missing_raw_source"


def test_build_cell_candidate_evidence_marks_data_quality_trace() -> None:
    candidate = _candidate("CAND-2", sample_stem="Sample_B")
    request = IdentityCoherenceTraceRequest(
        decision_id="ICD000001",
        request_id="ICR000001",
        sample_id="Sample_B",
        candidate_id="CAND-2",
        precursor_mz=500.0,
        ppm_tolerance=20.0,
        rt_min=4.95,
        rt_max=5.05,
    )
    trace_result = IdentityCoherenceTraceResult(
        request=request,
        trace=None,
        status="data_quality_reject",
        blocked_reason="invalid_trace_payload",
        raw_xic_request_count=1,
    )

    cell = build_cell_candidate_evidence(
        candidate,
        owner_assignment_status="primary",
        trace_result=trace_result,
    )

    assert cell.blocked_reason == ""
    assert cell.data_quality_reason == "invalid_trace_payload"


def test_run_diagnostic_writes_outputs_for_pre_backfill_state(tmp_path) -> None:
    seed = _candidate("CAND-SEED", sample_stem="Sample_A", best_seed_rt=5.0)
    non_seed_1 = _candidate("CAND-B", sample_stem="Sample_B", best_seed_rt=5.02)
    non_seed_2 = _candidate("CAND-C", sample_stem="Sample_C", best_seed_rt=5.03)
    output_dir = tmp_path / "identity"

    result = run_identity_coherence_diagnostic(
        candidates=(seed, non_seed_1, non_seed_2),
        ownership=_ownership(_owner(seed)),
        sample_order=("Sample_A", "Sample_B", "Sample_C"),
        raw_sources={
            "Sample_A": FakeRawSource(),
            "Sample_B": FakeRawSource(),
            "Sample_C": FakeRawSource(),
        },
        raw_paths={},
        dll_dir=tmp_path,
        raw_workers=1,
        raw_xic_batch_size=64,
        output_dir=output_dir,
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )

    assert isinstance(result, IdentityCoherenceDiagnosticRun)
    assert len(result.records) == 1
    assert result.paths.requests_tsv.is_file()
    assert result.paths.decisions_tsv.is_file()
    assert result.paths.cell_evidence_tsv.is_file()
    assert result.paths.controls_tsv.is_file()
    assert result.paths.summary_md.is_file()
    assert result.context.raw_xic_request_count == 3
    assert result.context.xic_point_count == 9
    assert "Backfill" in result.paths.summary_md.read_text(encoding="utf-8")
    assert result.records[0].row_result.decision.seed_candidate_id == "CAND-SEED"


def test_run_diagnostic_does_not_retrieve_non_seed_traces_when_seed_gate_fails(
    tmp_path,
) -> None:
    seed = _candidate(
        "CAND-SEED",
        sample_stem="Sample_A",
        best_seed_rt=5.0,
    )
    bad_owner = replace(
        _owner(seed),
        owner_peak_start_rt=5.10,
        owner_peak_end_rt=5.20,
    )
    non_seed = _candidate("CAND-B", sample_stem="Sample_B", best_seed_rt=5.02)
    source_b = FakeRawSource()

    result = run_identity_coherence_diagnostic(
        candidates=(seed, non_seed),
        ownership=_ownership(bad_owner),
        sample_order=("Sample_A", "Sample_B"),
        raw_sources={"Sample_A": FakeRawSource(), "Sample_B": source_b},
        raw_paths={},
        dll_dir=tmp_path,
        raw_workers=1,
        raw_xic_batch_size=64,
        output_dir=tmp_path / "identity",
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )

    assert result.records[0].row_result.cells == ()
    assert source_b.calls == []
    assert result.context.raw_xic_request_count == 0


def test_run_diagnostic_evaluates_controls_manifest(tmp_path) -> None:
    seed = _candidate("CAND-SEED", sample_stem="Sample_A", best_seed_rt=5.0)
    non_seed_1 = _candidate("CAND-B", sample_stem="Sample_B", best_seed_rt=5.02)
    non_seed_2 = _candidate("CAND-C", sample_stem="Sample_C", best_seed_rt=5.03)
    manifest = tmp_path / "controls.tsv"
    manifest.write_text(
        "\t".join(
            [
                "control_id",
                "control_type",
                "control_name",
                "expected_mapping_status",
                "control_expected_behavior",
                "fragment_observation_mode",
                "precursor_tolerance_ppm",
                "product_tolerance_ppm",
                "cid_observed_loss_tolerance_ppm",
                "rt_tolerance_sec",
                "required_failure_reason_when_missed",
                "seed_candidate_id",
                "positive_control_target_name",
                "positive_control_target_mz",
                "positive_control_target_rt_sec",
                "positive_control_mapping_error_ppm",
                "positive_control_mapping_delta_rt_sec",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "CTRL-1",
                "positive_targeted_istd",
                "seed positive",
                "mapped",
                "would_primary",
                "cid_neutral_loss",
                "20",
                "20",
                "20",
                "60",
                "review_only_insufficient_support",
                "CAND-SEED",
                "seed positive",
                "500.0",
                "300.0",
                "0.0",
                "0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_identity_coherence_diagnostic(
        candidates=(seed, non_seed_1, non_seed_2),
        ownership=_ownership(_owner(seed)),
        sample_order=("Sample_A", "Sample_B", "Sample_C"),
        raw_sources={
            "Sample_A": FakeRawSource(),
            "Sample_B": FakeRawSource(),
            "Sample_C": FakeRawSource(),
        },
        raw_paths={},
        dll_dir=tmp_path,
        raw_workers=1,
        raw_xic_batch_size=64,
        output_dir=tmp_path / "identity",
        alignment_config=AlignmentConfig(),
        controls_manifest_path=manifest,
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )

    assert len(result.control_rows) == 1
    assert result.context.control_manifest_path == str(manifest)
    assert result.control_rows[0]["control_status"] == "assessed"
    assert result.control_rows[0]["control_pass"] in {True, "true"}


def test_run_diagnostic_process_mode_matches_serial_ordering(
    monkeypatch,
    tmp_path,
) -> None:
    seed = _candidate("CAND-SEED", sample_stem="Sample_A", best_seed_rt=5.0)
    non_seed_1 = _candidate("CAND-B", sample_stem="Sample_B", best_seed_rt=5.02)
    non_seed_2 = _candidate("CAND-C", sample_stem="Sample_C", best_seed_rt=5.03)
    candidates = (seed, non_seed_1, non_seed_2)
    ownership = _ownership(_owner(seed))

    serial = run_identity_coherence_diagnostic(
        candidates=candidates,
        ownership=ownership,
        sample_order=("Sample_A", "Sample_B", "Sample_C"),
        raw_sources={
            "Sample_A": FakeRawSource(),
            "Sample_B": FakeRawSource(),
            "Sample_C": FakeRawSource(),
        },
        raw_paths={},
        dll_dir=tmp_path,
        raw_workers=1,
        raw_xic_batch_size=64,
        output_dir=tmp_path / "serial",
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )

    def fake_process(requests, **kwargs):
        return type(
            "ProcessOutput",
            (),
            {
                "results": tuple(
                    IdentityCoherenceTraceResult(
                        request=request,
                        trace=CandidateTrace(
                            rt_min=(
                                request.rt_min,
                                (request.rt_min + request.rt_max) / 2.0,
                                request.rt_max,
                            ),
                            intensity=(0.0, 10.0, 0.0),
                        ),
                        status="pass",
                        raw_xic_request_count=1,
                        xic_point_count=3,
                    )
                    for request in requests
                ),
                "timing_stats": (),
            },
        )()

    monkeypatch.setattr(
        "xic_extractor.alignment.identity_coherence.trace_retrieval."
        "run_identity_trace_process",
        fake_process,
    )
    process = run_identity_coherence_diagnostic(
        candidates=candidates,
        ownership=ownership,
        sample_order=("Sample_A", "Sample_B", "Sample_C"),
        raw_sources={},
        raw_paths={
            "Sample_A": tmp_path / "Sample_A.raw",
            "Sample_B": tmp_path / "Sample_B.raw",
            "Sample_C": tmp_path / "Sample_C.raw",
        },
        dll_dir=tmp_path,
        raw_workers=8,
        raw_xic_batch_size=64,
        output_dir=tmp_path / "process",
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )

    def row_signature(result):
        return [
            (
                record.seed_gate.resolved_request.request_id,
                record.row_result.decision.decision_id,
                record.row_result.decision.identity_family_id,
                record.row_result.decision.decision,
            )
            for record in result.records
        ]

    assert row_signature(process) == row_signature(serial)
    assert [
        [cell.candidate_id for cell in record.row_result.cells]
        for record in process.records
    ] == [
        [cell.candidate_id for cell in record.row_result.cells]
        for record in serial.records
    ]
    assert [result.request.candidate_id for result in process.trace_results] == [
        result.request.candidate_id for result in serial.trace_results
    ]


def test_run_diagnostic_process_mode_batches_all_seed_trace_requests_once(
    monkeypatch,
    tmp_path,
) -> None:
    seed_a = _candidate("CAND-SEED-A", sample_stem="Sample_A", best_seed_rt=5.0)
    seed_d = _candidate("CAND-SEED-D", sample_stem="Sample_D", best_seed_rt=5.01)
    non_seed_b = _candidate("CAND-B", sample_stem="Sample_B", best_seed_rt=5.02)
    non_seed_c = _candidate("CAND-C", sample_stem="Sample_C", best_seed_rt=5.03)
    captured_batches: list[tuple[IdentityCoherenceTraceRequest, ...]] = []

    def fake_process(requests, **kwargs):
        captured_batches.append(tuple(requests))
        return type(
            "ProcessOutput",
            (),
            {
                "results": tuple(
                    IdentityCoherenceTraceResult(
                        request=request,
                        trace=CandidateTrace(
                            rt_min=(
                                request.rt_min,
                                (request.rt_min + request.rt_max) / 2.0,
                                request.rt_max,
                            ),
                            intensity=(0.0, 10.0, 0.0),
                        ),
                        status="pass",
                        raw_xic_request_count=1,
                        xic_point_count=3,
                    )
                    for request in requests
                ),
                "timing_stats": (),
            },
        )()

    monkeypatch.setattr(
        "xic_extractor.alignment.identity_coherence.trace_retrieval."
        "run_identity_trace_process",
        fake_process,
    )

    result = run_identity_coherence_diagnostic(
        candidates=(seed_a, seed_d, non_seed_b, non_seed_c),
        ownership=_ownership(_owner(seed_a), _owner(seed_d)),
        sample_order=("Sample_A", "Sample_D", "Sample_B", "Sample_C"),
        raw_sources={},
        raw_paths={
            "Sample_A": tmp_path / "Sample_A.raw",
            "Sample_D": tmp_path / "Sample_D.raw",
            "Sample_B": tmp_path / "Sample_B.raw",
            "Sample_C": tmp_path / "Sample_C.raw",
        },
        dll_dir=tmp_path,
        raw_workers=8,
        raw_xic_batch_size=64,
        output_dir=tmp_path / "process",
        alignment_config=AlignmentConfig(),
        fragment_profile_id="dna-cid-v1",
        fragment_profile_hash="hash1",
    )

    assert len(captured_batches) == 1
    assert len(captured_batches[0]) == len(result.trace_results)
    assert result.context.raw_xic_request_count == len(captured_batches[0])
    assert [
        record.seed_gate.resolved_request.seed_candidate_id
        for record in result.records
    ] == ["CAND-SEED-A", "CAND-SEED-D"]
