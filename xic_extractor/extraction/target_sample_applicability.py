from __future__ import annotations

from xic_extractor.configuration.models import Target

TARGET_SAMPLE_APPLICABILITY_RNA_CONTAINING = "rna_containing"


def target_sample_exclusion_reasons(
    target: Target,
    sample_name: str,
) -> tuple[str, ...]:
    applicability = getattr(target, "sample_applicability", "all") or "all"
    if applicability == "all":
        return ()
    if applicability == TARGET_SAMPLE_APPLICABILITY_RNA_CONTAINING:
        if _is_rna_containing_sample(sample_name):
            return ()
        return ("target_sample_applicability:rna_containing",)
    return (f"target_sample_applicability:{applicability}",)


def target_sample_is_applicable(target: Target, sample_name: str) -> bool:
    return not target_sample_exclusion_reasons(target, sample_name)


def _is_rna_containing_sample(sample_name: str) -> bool:
    normalized = "".join(ch for ch in sample_name.lower() if ch.isalnum())
    return "rna" in normalized
