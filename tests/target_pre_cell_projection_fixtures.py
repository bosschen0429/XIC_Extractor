"""Helpers shared by test_target_pre_cell_projection and other test modules."""

from __future__ import annotations

from copy import copy
from pathlib import Path

import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.cell_observations_fixtures import (
    _coverage_for_groups,
    _receipt_fixture,
    _support_assessment,
)
from tests.cross_sample_association_lineage_fixtures import (
    _final_receipt as _lineage_final_receipt,
)
from tests.group_fixtures import group_from_members
from tests.processing_identity_no_raw_fixture import (
    ProcessingIdentityNoRawFixture,
    build_processing_identity_no_raw_fixture,
)
from tests.search_scope_capture_fixtures import _capture_binding, _peak_config
from xic_extractor.alignment.cell_observation.observations import (
    compose_final_group_cell_observation_population,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    TargetSearchScope,
)


def _finalized_case(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...],
    observed_stems: tuple[str, ...],
    group_specs: tuple[tuple[tuple[str, ...], str], ...],
    reverse_scope_samples: bool = False,
) -> tuple[
    ProcessingIdentityNoRawFixture,
    TargetSearchScope,
    FinalizedScientificAlignment,
    dict[str, object],
]:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=sample_stems
    )
    scope_samples = (
        tuple(reversed(fixture.samples)) if reverse_scope_samples else fixture.samples
    )
    target_value = fixture.target.definition.target
    method_subject = cid_nl_method_subject_from_target(fixture.target)
    bindings = tuple(
        _capture_binding(
            candidate_id=f"candidate-{sample.raw_stem}",
            method_subject_id=method_subject.method_subject_id,
            sample=sample,
            acquisition_source_id=fixture.source(sample).acquisition_source_id,
            mz=target_value.mz,
            ppm_tol=target_value.ppm_tol,
            rt_min=target_value.rt_min,
            rt_max=target_value.rt_max,
        )
        for sample in scope_samples
    )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        samples=scope_samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=tuple(binding.candidate_id for binding in bindings),
        capture_bindings=bindings,
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
    )
    assessments = tuple(
        _support_assessment(
            fixture,
            sample.raw_stem,
            suffix="target-projection",
            apex_rt=8.0,
            scan_number=80 + index,
            seed_count=2,
        )
        for index, sample in enumerate(fixture.samples)
    )
    spine = build_cid_nl_reference_spine(
        target=fixture.target,
        sample_assessments=tuple(zip(fixture.samples, assessments, strict=True)),
    )
    predecessor = spine.group
    member_by_stem = {
        sample.raw_stem: next(
            member
            for member in predecessor.member_subjects
            if member.sample_key == sample.sample_name
        )
        for sample in fixture.samples
    }
    hypothesis_by_stem = {
        stem: copy(
            next(
                hypothesis
                for hypothesis in spine.support_hypotheses
                if hypothesis.hypothesis_id == member.subject_id
            )
        )
        for stem, member in member_by_stem.items()
    }
    for stem, hypothesis in hypothesis_by_stem.items():
        object.__setattr__(hypothesis, "trace_group_id", f"trace-{stem}")
    groups = []
    for group_stems, disposition in group_specs:
        members = [member_by_stem[stem] for stem in group_stems]
        if not members:
            members.extend(predecessor.member_subjects)
        groups.append(
            group_from_members(
                member_subjects=tuple(members),
                disposition=disposition,
            )
        )
    receipt = _lineage_final_receipt(
        _receipt_fixture(fixture, fixture.samples),
        predecessors=(predecessor,),
        successors=tuple(groups),
    )
    coverage = _coverage_for_groups(
        fixture,
        (predecessor,),
        fixture.samples,
        predecessor_cells=spine.cells,
    )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )
    observed_set = set(observed_stems)
    hypotheses: dict[str, object] = {}
    terminals = []
    for _, sample in enumerate(fixture.samples):
        candidate_id = f"candidate-{sample.raw_stem}"
        if sample.raw_stem in observed_set:
            hypothesis = hypothesis_by_stem[sample.raw_stem]
            hypotheses[sample.raw_stem] = hypothesis
            terminals.append(
                ObservedSearchSubject(
                    candidate_id=candidate_id,
                    sample_key=(sample.sample_name),
                    trace_group_id=hypothesis.trace_group_id,
                    hypothesis=hypothesis,
                )
            )
        else:
            terminals.append(
                SearchedNoObservationSubject(
                    candidate_id=candidate_id,
                    sample_key=(sample.sample_name),
                    trace_group_id=f"trace-{sample.raw_stem}",
                    detection_status="not_detected",
                )
            )
    cell_observations = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=spine.cells,
    )
    result = FinalizedScientificAlignment(
        scope=scope,
        canonical_samples=tuple(
            sorted(
                scope.samples,
                key=lambda sample: sample.sample_name,
            )
        ),
        terminal_subjects=tuple(
            sorted(terminals, key=finalizer_owner._terminal_sort_key)
        ),
        cell_observation_handoff=handoff,
        cell_observations=cell_observations,
        grouping_result=receipt,
    )
    return fixture, scope, result, hypotheses
