"""Render completed delta-mass answers without re-running method decisions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from xic_extractor.configuration.delta_mass import DeltaMassPatternSettings
from xic_extractor.discovery.delta_mass_patterns import DeltaMassCandidate
from xic_extractor.tabular_io import format_machine_float, write_delimited_rows

CANDIDATE_COLUMNS = (
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    "status",
    "rejected_at_layer",
    "reject_reason",
    "anchor_position",
    "anchor_feature_id",
    "base_mz",
    "base_rt_apex_min",
    "base_height",
    "position_count",
    "positions_from_table",
    "positions_from_raw",
    "discriminating_position",
    "discriminating_shape_r",
    "cid_nl_base_status",
    "cid_nl_base_candidate_id",
    "cid_nl_base_join_mode",
    "base_expected_net_height",
    "base_support_score",
    "base_support_status",
)
POSITION_COLUMNS = (
    "candidate_id",
    "sample_stem",
    "position_label",
    "delta_da",
    "partner_source",
    "feature_id",
    "expected_mz",
    "observed_mz",
    "mz_error_ppm",
    "apex_rt_min",
    "apex_delta_sec",
    "apex_threshold_sec",
    "height",
    "height_ratio_to_base",
    "ratio_status",
    "ratio_min",
    "ratio_max",
    "shape_r",
    "shape_r_threshold",
    "position_status",
    "raw_peak_height",
    "local_background",
    "background_noise",
    "height_above_background",
    "required_height_above_background",
    "signal_status",
    "lowest_status",
)


@dataclass(frozen=True)
class DeltaMassDiscoveryOutputs:
    candidates_csv: Path
    positions_csv: Path
    sidecar_json: Path


def write_delta_mass_outputs(
    output_dir: Path,
    candidates: Sequence[DeltaMassCandidate],
    sidecar: Mapping[str, object],
    settings: DeltaMassPatternSettings,
) -> DeltaMassDiscoveryOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = DeltaMassDiscoveryOutputs(
        output_dir / "delta_mass_candidates.csv",
        output_dir / "delta_mass_positions.csv",
        output_dir / "delta_mass_sidecar.json",
    )
    rows = []
    positions: list[dict[str, object]] = []
    for candidate in candidates:
        row = {k: v for k, v in asdict(candidate).items() if k != "positions"}
        discriminating = [
            p
            for p in candidate.positions
            if p.position_label in settings.discriminating_positions
        ]
        row.update(
            position_count=len(candidate.positions),
            positions_from_table=sum(
                p.partner_source in {"anchor", "table"} for p in candidate.positions
            ),
            positions_from_raw=sum(
                p.partner_source == "raw" for p in candidate.positions
            ),
            discriminating_position=";".join(p.position_label for p in discriminating),
            discriminating_shape_r=";".join(
                str(p.shape_r) if p.shape_r is not None else "" for p in discriminating
            ),
        )
        rows.append(row)
        for position in candidate.positions:
            position_fields = asdict(position)
            # Review reuses the actual Discovery XIC center, not a display-rounded mass.
            position_fields["expected_mz"] = format_machine_float(position.expected_mz)
            position_fields["height_ratio_to_base"] = position_fields.pop(
                "height_ratio"
            )
            positions.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "sample_stem": candidate.sample_stem,
                    **position_fields,
                }
            )
    write_delimited_rows(outputs.candidates_csv, rows, CANDIDATE_COLUMNS)
    write_delimited_rows(outputs.positions_csv, positions, POSITION_COLUMNS)
    outputs.sidecar_json.write_text(
        json.dumps(dict(sidecar), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return outputs
