"""Project sample-local Delta Mass review evidence without RAW or reclassification."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.diagnostics.delta_mass_review_data import (
    REVIEW_SCHEMA,
    decode_delta_mass_review,
)
from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)


@dataclass(frozen=True, slots=True)
class DeltaMassReviewLocator:
    review_path: Path
    source_sha256: str


class DeltaMassReviewSource:
    def __init__(self, path: Path, loaded: tuple[object, str]) -> None:
        try:
            self.review = decode_delta_mass_review(loaded[0])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExplanationError(f"Invalid Delta Mass review: {exc}") from exc
        self.locator = DeltaMassReviewLocator(path, loaded[1])
        self.summary: dict[str, Any] = {
            "schema_version": REVIEW_SCHEMA,
            "source_kind": "delta_mass_review",
            "diagnostic_only": True,
            "plot_evidence_capability": "retained_curves",
            "processing_attempt_id": str(path.resolve()),
            "samples": [
                {"sample_key": sample, "sample_stem": sample}
                for sample in sorted({c.sample_stem for c in self.review.candidates})
            ],
        }

    def load_items(self, sample_ids: set[str]) -> list[CanonicalItem]:
        items = []
        for candidate in self.review.candidates:
            if candidate.sample_stem not in sample_ids:
                continue
            _, ref = build_canonical_ref(
                "candidate",
                self.summary["processing_attempt_id"],
                candidate.candidate_id,
                candidate.sample_stem,
            )
            items.append(
                CanonicalItem(
                    kind="candidate",
                    attempt_id=self.summary["processing_attempt_id"],
                    identifier=candidate.candidate_id,
                    serialized_ref=ref,
                    sample_ids=(candidate.sample_stem,),
                    witnesses=tuple(
                        Witness(
                            sample_key=candidate.sample_stem,
                            candidate_id=candidate.candidate_id,
                            precursor_mz=p.query_mz,
                            rt_apex_min=candidate.base_rt_apex_min,
                            sample_stem=candidate.sample_stem,
                        )
                        for p in candidate.positions
                    ),
                    stratum="rest",
                    details={"candidate": candidate},
                )
            )
        return items

    def load_render_items(
        self, items: list[CanonicalItem], *, capability: str, check_cancel: Any
    ) -> list[dict[str, Any]]:
        rendered = []
        for item in items:
            check_cancel()
            candidate = item.details["candidate"]
            saved = asdict(candidate)
            for position in saved["positions"]:
                position.pop("raw_rt")
                position.pop("raw_intensity")
            rendered.append(
                {
                    "id": item.serialized_ref,
                    "kind": "candidate",
                    "stratum": "rest",
                    "title": candidate.sample_stem,
                    "badge_label": "Delta Mass 候選",
                    "method_label": candidate.method_label,
                    "reason_label": candidate.candidate_id,
                    "precursor_mz": candidate.base_mz,
                    "rt_apex_min": candidate.base_rt_apex_min,
                    "review_rt_window": candidate.rt_window,
                    "review_summary": {
                        "observed_sample_count": int(
                            any(p.raw_rt for p in candidate.positions)
                        ),
                        "scope_sample_count": 1,
                        "origin_kinds": [],
                    },
                    "samples": [{"sample_id": candidate.sample_stem}],
                    "witnesses": [asdict(w) for w in item.witnesses],
                    "curves": [
                        {
                            "sample_id": candidate.sample_stem,
                            "candidate_id": candidate.candidate_id,
                            "evidence_ref": position.label,
                            "position_label": position.label,
                            "position_index": index,
                            "stage_label": position.label,
                            "raw_rts": position.raw_rt,
                            "raw_intensities": position.raw_intensity,
                            "processed_curves": [],
                        }
                        for index, position in enumerate(candidate.positions)
                    ],
                    "delta_mass": {
                        **saved,
                        "discovery_directory": self.review.discovery_directory,
                    },
                }
            )
        return rendered
