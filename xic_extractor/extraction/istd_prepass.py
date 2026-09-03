from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction import target_extraction
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.raw_reader import open_raw

if TYPE_CHECKING:
    from xic_extractor.extractor import ExtractionResult


def extract_istd_anchors_only(
    config: ExtractionConfig,
    istd_targets: list[Target],
    raw_path: Path,
    *,
    expected_acquisition_source: AcquisitionSourceIdentity | None = None,
) -> (
    tuple[
        dict[str, float],
        dict[str, ExtractionResult],
        list[DiagnosticRecord],
        dict[str, tuple[float, float | None]],
    ]
    | None
):
    if not istd_targets:
        return {}, {}, [], {}
    try:
        raw_context = (
            open_raw(raw_path, config.dll_dir)
            if expected_acquisition_source is None
            else open_raw(
                raw_path,
                config.dll_dir,
                expected_acquisition_source=expected_acquisition_source,
            )
        )
        with raw_context as raw:
            results: dict[str, ExtractionResult] = {}
            diagnostics: list[DiagnosticRecord] = []
            anchors: dict[str, float] = {}
            shape_metrics_by_label: dict[str, tuple[float, float | None]] = {}
            for target in istd_targets:
                target_extraction.extract_one_target(
                    raw,
                    config,
                    raw_path.stem,
                    target,
                    reference_rt=None,
                    strict_preferred_rt=False,
                    results=results,
                    diagnostics=diagnostics,
                    shape_metrics_by_label=shape_metrics_by_label,
                )
                result = results.get(target.label)
                anchor_rt = target_extraction.credible_istd_anchor_rt(result)
                if anchor_rt is not None:
                    anchors[target.label] = anchor_rt
            return anchors, results, diagnostics, shape_metrics_by_label
    except Exception:
        return None
