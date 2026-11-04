"""Acquire/evaluate flat method clauses against the same physical peak population."""

from dataclasses import replace
from typing import Literal

from xic_extractor.configuration.targeted_identification import CidNlCondition
from xic_extractor.configuration.targeted_strategy import targeted_tag_profile
from xic_extractor.evidence.targeted_identification import (
    IdentificationConditionEvidence,
    MethodCandidateEvidence,
)
from xic_extractor.extraction.targeted_delta_mass import assess_targeted_delta_mass
from xic_extractor.extraction.targeted_peak_selection import (
    assess_targeted_candidate_population,
)
from xic_extractor.peak_detection.models import PeakCandidate


def assess_targeted_method_conditions(raw, assessment, window, seeds_by_tag):
    method = assessment.target.identification_method
    assert method is not None
    by_candidate: dict[PeakCandidate, list[IdentificationConditionEvidence]] = {
        candidate: [] for candidate in assessment.candidate_evaluations
    }
    for index, condition in enumerate(method.conditions):
        if isinstance(condition, CidNlCondition):
            profile = targeted_tag_profile(condition.tag)
            cid_target = replace(
                assessment.target,
                identification_method=None,
                method_name="",
                cid_nl_tag=condition.tag,
                neutral_loss_da=profile.neutral_loss_profiles[0].neutral_loss_da,
                nl_ppm_warn=profile.nl_tolerance_ppm,
                nl_ppm_max=profile.nl_tolerance_ppm,
            )
            cid_assessment = assess_targeted_candidate_population(
                target=cid_target,
                config=assessment.config,
                trace_group=assessment.trace_group,
                prepared_result=assessment.prepared_result,
                universe=assessment.universe,
                selection_window=assessment.selection_window,
                preferred_rt=assessment.preferred_rt,
                strict_preferred_rt=assessment.strict_preferred_rt,
                method_window=window,
                method_seeds=seeds_by_tag[condition.tag],
            )
            for candidate, answers in by_candidate.items():
                evidence = cid_assessment.candidate_ms2_evidence[candidate]
                status: Literal["matched", "not_matched", "not_acquired"] = (
                    "matched"
                    if evidence.nl_match
                    else "not_matched"
                    if evidence.ms2_present
                    else "not_acquired"
                )
                answers.append(
                    IdentificationConditionEvidence(
                        index, condition.kind, f"CID-NL · {condition.tag}", status
                    )
                )
        else:
            settings = condition.pattern_settings(
                method.mz_tolerance_ppm, method.name, index
            )
            hypotheses = assess_targeted_delta_mass(
                raw, assessment, settings, condition.quant_position
            )
            for candidate, answers in by_candidate.items():
                matches = hypotheses[candidate]
                status = (
                    "matched"
                    if any(match.status == "candidate" for match in matches)
                    else "not_matched"
                )
                answers.append(
                    IdentificationConditionEvidence(
                        index, condition.kind, condition.label, status, matches
                    )
                )
    return {
        candidate: MethodCandidateEvidence(
            index,
            candidate.selection_apex_rt,
            candidate.peak.peak_start,
            candidate.peak.peak_end,
            method.accepts(answer.status for answer in answers),
            tuple(answers),
        )
        for index, (candidate, answers) in enumerate(by_candidate.items())
    }
