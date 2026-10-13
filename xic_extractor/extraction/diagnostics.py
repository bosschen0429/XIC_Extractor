"""Targeted acquisition diagnostics unrelated to chemical evidence assessment."""

from xic_extractor.configuration.models import Target
from xic_extractor.output.messages import DiagnosticRecord


def istd_anchor_missing_diagnostic(
    sample_name: str, target: Target
) -> DiagnosticRecord:
    return DiagnosticRecord(
        sample_name=sample_name,
        target_label=target.label,
        issue="ISTD_ANCHOR_MISSING",
        reason=(
            f"ISTD '{target.istd_pair}' yielded no credible current-sample anchor; "
            "use the target expected RT with available sample drift as identity "
            "reference while retaining complete acquisition coverage"
        ),
    )
