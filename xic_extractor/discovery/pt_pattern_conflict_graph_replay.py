"""No-RAW replay and sidecar writer for Pt hypothesis conflict graphs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.delta_mass_patterns import (
    PT_PATTERN_CANDIDATE_SCHEMA_VERSION,
)
from xic_extractor.discovery.detected_features import (
    DETECTED_FEATURES_SCHEMA_VERSION,
    detected_feature_identity,
    load_detected_features_csv,
)
from xic_extractor.discovery.pt_pattern_conflict_graph import (
    PT_PATTERN_POSITIONS,
    NormalizedFeatureClaim,
    PtConflictGraphComponent,
    PtConflictGraphInput,
    PtConflictGraphVertex,
    PtHypothesisConflictGraph,
    build_pt_hypothesis_conflict_graph,
)
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    text_value,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_conflict_graph_replay"
READINESS_TIER = "diagnostic_only"
GRAPH_RULE_VERSION = "pt_conflict_graph"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_conflict_graph"

_LEGACY_RESOLUTION_STATUSES = frozenset(
    {
        "unique_strict_support",
        "unique_core_support",
        "ambiguous_multiple_strict_support",
        "ambiguous_multiple_core_support",
        "no_same_scan_support",
    }
)

_CANDIDATE_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "base_feature_id",
    "pt_plus_1_feature_id",
    "pt_plus_2_feature_id",
    "pt_plus_4_feature_id",
)
_FEATURE_ID_COLUMN_BY_POSITION = {
    "base": "base_feature_id",
    "pt_plus_1": "pt_plus_1_feature_id",
    "pt_plus_2": "pt_plus_2_feature_id",
    "pt_plus_4": "pt_plus_4_feature_id",
}
_LEGACY_CONFLICT_COLUMNS = (
    "component_id",
    "candidate_ids",
    "resolution_status",
    "preferred_candidate_id",
)

VERTEX_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "report_id",
    "graph_rule_version",
    "graph_instance_id",
    "component_instance_id",
    "component_vertex_count",
    "component_is_clique",
    "vertex_id",
    "source_hypothesis_id",
    "sample_stem",
    "hypothesis_source_artifact_sha256",
    "hypothesis_source_schema_version",
    "feature_source_artifact_sha256",
    "feature_source_schema_version",
    "feature_source_kinds_by_position_json",
    "base_feature_id",
    "pt_plus_1_feature_id",
    "pt_plus_2_feature_id",
    "pt_plus_4_feature_id",
)
EDGE_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "report_id",
    "graph_rule_version",
    "graph_instance_id",
    "component_instance_id",
    "edge_id",
    "left_vertex_id",
    "right_vertex_id",
    "left_source_hypothesis_id",
    "right_source_hypothesis_id",
    "reason",
    "shared_claim_count",
    "shared_feature_ids",
    "shared_claim_keys_json",
)
COMPONENT_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "report_id",
    "graph_rule_version",
    "graph_instance_id",
    "component_instance_id",
    "vertex_count",
    "edge_count",
    "possible_pair_count",
    "pairwise_compatible_pair_count",
    "is_clique",
    "component_wide_at_most_one_would_overconstrain",
    "source_hypothesis_ids",
    "vertex_ids",
)
LEGACY_COMPARISON_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "report_id",
    "legacy_component_id",
    "legacy_resolution_status",
    "legacy_preferred_candidate_id",
    "legacy_candidate_count",
    "successor_component_instance_id",
    "successor_component_vertex_count",
    "induced_edge_count",
    "induced_possible_pair_count",
    "induced_pairwise_compatible_pair_count",
    "induced_is_clique",
    "component_wide_at_most_one_would_overconstrain",
    "candidate_ids",
)


def run_conflict_graph_replay(
    *,
    candidates_csv: Path,
    detected_features_csv: Path,
    output_dir: Path,
    report_id: str,
    legacy_conflicts_csv: Path | None = None,
    graph_rule_version: str = GRAPH_RULE_VERSION,
) -> dict[str, Path]:
    """Build and write a structural graph without selecting hypotheses."""

    normalized_report_id = report_id.strip()
    if not normalized_report_id:
        raise ValueError("report_id must be non-empty")

    candidate_rows = read_delimited_rows(
        candidates_csv,
        required_columns=_CANDIDATE_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    if not candidate_rows:
        raise ValueError(f"{candidates_csv}: no candidate rows")
    candidate_hash = file_sha256(candidates_csv, uppercase=False)
    feature_hash = file_sha256(detected_features_csv, uppercase=False)
    legacy_hash = (
        file_sha256(legacy_conflicts_csv, uppercase=False)
        if legacy_conflicts_csv is not None
        else None
    )
    graph_inputs = _graph_inputs(
        candidate_rows,
        detected_features_csv=detected_features_csv,
        candidate_hash=candidate_hash,
        feature_hash=feature_hash,
    )
    graph = build_pt_hypothesis_conflict_graph(
        graph_inputs,
        graph_rule_version=graph_rule_version,
    )
    vertex_rows, edge_rows, component_rows = _graph_rows(
        graph,
        report_id=normalized_report_id,
    )
    legacy_rows = (
        _legacy_comparison_rows(
            graph,
            read_delimited_rows(
                legacy_conflicts_csv,
                required_columns=_LEGACY_CONFLICT_COLUMNS,
                delimiter=",",
                encoding="utf-8-sig",
            ),
            report_id=normalized_report_id,
        )
        if legacy_conflicts_csv is not None
        else []
    )
    summary = _summary(
        graph,
        component_rows=component_rows,
        legacy_rows=legacy_rows,
        candidate_hash=candidate_hash,
        feature_hash=feature_hash,
        legacy_hash=legacy_hash,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "vertices_csv": output_dir / "pt_pattern_conflict_graph_vertices.csv",
        "edges_csv": output_dir / "pt_pattern_conflict_graph_edges.csv",
        "components_csv": output_dir / "pt_pattern_conflict_graph_components.csv",
        "legacy_comparison_csv": (
            output_dir / "pt_pattern_conflict_graph_legacy_comparison.csv"
        ),
        "summary_json": output_dir / "pt_pattern_conflict_graph_summary.json",
        "run_metadata_json": (
            output_dir / "pt_pattern_conflict_graph_run_metadata.json"
        ),
    }
    write_delimited_rows(outputs["vertices_csv"], vertex_rows, VERTEX_COLUMNS)
    write_delimited_rows(outputs["edges_csv"], edge_rows, EDGE_COLUMNS)
    write_delimited_rows(outputs["components_csv"], component_rows, COMPONENT_COLUMNS)
    write_delimited_rows(
        outputs["legacy_comparison_csv"],
        legacy_rows,
        LEGACY_COMPARISON_COLUMNS,
    )
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    outputs["run_metadata_json"].write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "readiness_tier": READINESS_TIER,
                "report_id": normalized_report_id,
                "graph_instance_id": graph.graph_instance_id,
                "inputs": {
                    "candidates_csv": str(candidates_csv),
                    "detected_features_csv": str(detected_features_csv),
                    "legacy_conflicts_csv": (
                        str(legacy_conflicts_csv)
                        if legacy_conflicts_csv is not None
                        else None
                    ),
                },
                "output_dir": str(output_dir),
                "diagnostic_only": True,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return outputs


def _graph_inputs(
    candidate_rows: Sequence[Mapping[str, str]],
    *,
    detected_features_csv: Path,
    candidate_hash: str,
    feature_hash: str,
) -> tuple[PtConflictGraphInput, ...]:
    features = load_detected_features_csv(detected_features_csv)
    feature_by_identity = {
        detected_feature_identity(feature): feature for feature in features
    }

    inputs = []
    for row_index, row in enumerate(candidate_rows, start=2):
        candidate_id = _required_text(row, "candidate_id", row_index=row_index)
        sample_stem = _required_text(row, "sample_stem", row_index=row_index)
        hypothesis_schema = _required_text(
            row,
            "schema_version",
            row_index=row_index,
        )
        if hypothesis_schema != PT_PATTERN_CANDIDATE_SCHEMA_VERSION:
            raise ValueError(
                f"candidate row {row_index}: unsupported schema_version "
                f"{hypothesis_schema!r}"
            )
        claims: dict[str, NormalizedFeatureClaim] = {}
        for position in PT_PATTERN_POSITIONS:
            column = _FEATURE_ID_COLUMN_BY_POSITION[position]
            feature_id = _required_text(row, column, row_index=row_index)
            feature = feature_by_identity.get((sample_stem, feature_id))
            if feature is None:
                raise ValueError(
                    f"candidate row {row_index} {candidate_id}: {column} "
                    f"not found in detected features: {feature_id}"
                )
            if feature.sample_stem != sample_stem:
                raise ValueError(
                    f"candidate row {row_index} {candidate_id}: {column} sample "
                    f"{feature.sample_stem!r} does not match {sample_stem!r}"
                )
            claims[position] = NormalizedFeatureClaim(
                sample_stem=sample_stem,
                feature_source_kind=feature.source,
                feature_source_artifact_sha256=feature_hash,
                source_schema_version=DETECTED_FEATURES_SCHEMA_VERSION,
                feature_id=feature_id,
            )
        inputs.append(
            PtConflictGraphInput(
                source_hypothesis_id=candidate_id,
                hypothesis_source_artifact_sha256=candidate_hash,
                hypothesis_source_schema_version=hypothesis_schema,
                claims_by_position=claims,
            )
        )
    return tuple(inputs)


def _graph_rows(
    graph: PtHypothesisConflictGraph,
    *,
    report_id: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    vertex_by_id = {vertex.vertex_id: vertex for vertex in graph.vertices}
    component_by_vertex = {
        vertex_id: component
        for component in graph.components
        for vertex_id in component.vertex_ids
    }
    edge_by_id = {edge.edge_id: edge for edge in graph.edges}

    vertex_rows = [
        _vertex_row(
            vertex,
            component=component_by_vertex[vertex.vertex_id],
            graph=graph,
            report_id=report_id,
        )
        for vertex in graph.vertices
    ]
    edge_rows = []
    component_rows = []
    for component in graph.components:
        source_ids = tuple(
            vertex_by_id[vertex_id].source_hypothesis_id
            for vertex_id in component.vertex_ids
        )
        possible_pairs = (
            len(component.vertex_ids) * (len(component.vertex_ids) - 1) // 2
        )
        compatible_pairs = possible_pairs - len(component.edge_ids)
        component_rows.append(
            {
                **_common_row(graph, report_id=report_id),
                "component_instance_id": component.component_instance_id,
                "vertex_count": len(component.vertex_ids),
                "edge_count": len(component.edge_ids),
                "possible_pair_count": possible_pairs,
                "pairwise_compatible_pair_count": compatible_pairs,
                "is_clique": _flag(component.is_clique),
                "component_wide_at_most_one_would_overconstrain": _flag(
                    compatible_pairs > 0 and len(component.vertex_ids) > 1
                ),
                "source_hypothesis_ids": ";".join(source_ids),
                "vertex_ids": ";".join(component.vertex_ids),
            }
        )
        for edge_id in component.edge_ids:
            edge = edge_by_id[edge_id]
            edge_rows.append(
                {
                    **_common_row(graph, report_id=report_id),
                    "component_instance_id": component.component_instance_id,
                    "edge_id": edge.edge_id,
                    "left_vertex_id": edge.left_vertex_id,
                    "right_vertex_id": edge.right_vertex_id,
                    "left_source_hypothesis_id": vertex_by_id[
                        edge.left_vertex_id
                    ].source_hypothesis_id,
                    "right_source_hypothesis_id": vertex_by_id[
                        edge.right_vertex_id
                    ].source_hypothesis_id,
                    "reason": edge.reason,
                    "shared_claim_count": len(edge.shared_claims),
                    "shared_feature_ids": ";".join(
                        claim.feature_id for claim in edge.shared_claims
                    ),
                    "shared_claim_keys_json": json.dumps(
                        [claim.canonical_key for claim in edge.shared_claims],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
    return vertex_rows, edge_rows, component_rows


def _vertex_row(
    vertex: PtConflictGraphVertex,
    *,
    component: PtConflictGraphComponent,
    graph: PtHypothesisConflictGraph,
    report_id: str,
) -> dict[str, object]:
    position_claims = dict(vertex.position_claims)
    return {
        **_common_row(graph, report_id=report_id),
        "component_instance_id": component.component_instance_id,
        "component_vertex_count": len(component.vertex_ids),
        "component_is_clique": _flag(component.is_clique),
        "vertex_id": vertex.vertex_id,
        "source_hypothesis_id": vertex.source_hypothesis_id,
        "sample_stem": vertex.sample_stem,
        "hypothesis_source_artifact_sha256": (vertex.hypothesis_source_artifact_sha256),
        "hypothesis_source_schema_version": (vertex.hypothesis_source_schema_version),
        "feature_source_artifact_sha256": next(
            iter(position_claims.values())
        ).feature_source_artifact_sha256,
        "feature_source_schema_version": next(
            iter(position_claims.values())
        ).source_schema_version,
        "feature_source_kinds_by_position_json": json.dumps(
            {
                position: position_claims[position].feature_source_kind
                for position in PT_PATTERN_POSITIONS
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        **{
            _FEATURE_ID_COLUMN_BY_POSITION[position]: (
                position_claims[position].feature_id
            )
            for position in PT_PATTERN_POSITIONS
        },
    }


def _legacy_comparison_rows(
    graph: PtHypothesisConflictGraph,
    legacy_rows: Sequence[Mapping[str, str]],
    *,
    report_id: str,
) -> list[dict[str, object]]:
    vertex_by_source = {
        vertex.source_hypothesis_id: vertex for vertex in graph.vertices
    }
    component_by_vertex = {
        vertex_id: component
        for component in graph.components
        for vertex_id in component.vertex_ids
    }
    edge_pairs = {
        frozenset((edge.left_vertex_id, edge.right_vertex_id)) for edge in graph.edges
    }
    output = []
    seen_legacy_ids: set[str] = set()
    for row_index, row in enumerate(legacy_rows, start=2):
        legacy_id = _required_text(row, "component_id", row_index=row_index)
        if legacy_id in seen_legacy_ids:
            raise ValueError(f"duplicate legacy component_id: {legacy_id}")
        seen_legacy_ids.add(legacy_id)
        candidate_ids = tuple(
            value.strip()
            for value in text_value(row.get("candidate_ids")).split(";")
            if value.strip()
        )
        if len(candidate_ids) < 2 or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError(
                f"legacy component {legacy_id} must contain distinct candidate ids"
            )
        resolution_status = _required_text(
            row,
            "resolution_status",
            row_index=row_index,
        )
        if resolution_status not in _LEGACY_RESOLUTION_STATUSES:
            raise ValueError(
                f"legacy component {legacy_id} has unsupported resolution_status: "
                f"{resolution_status!r}"
            )
        preferred_candidate_id = text_value(row.get("preferred_candidate_id"))
        if preferred_candidate_id and preferred_candidate_id not in candidate_ids:
            raise ValueError(
                f"legacy component {legacy_id} preferred candidate is not a member: "
                f"{preferred_candidate_id}"
            )
        if resolution_status == "unique_strict_support" and not preferred_candidate_id:
            raise ValueError(
                f"legacy component {legacy_id} unique_strict_support requires a "
                "preferred candidate"
            )
        if (
            resolution_status
            in {
                "unique_core_support",
                "ambiguous_multiple_strict_support",
                "ambiguous_multiple_core_support",
                "no_same_scan_support",
            }
            and preferred_candidate_id
        ):
            raise ValueError(
                f"legacy component {legacy_id} {resolution_status} must not name a "
                "preferred candidate"
            )
        unknown = sorted(set(candidate_ids) - vertex_by_source.keys())
        if unknown:
            raise ValueError(
                f"legacy component {legacy_id} has unknown candidate ids: "
                + ", ".join(unknown)
            )
        vertices = tuple(
            vertex_by_source[candidate_id] for candidate_id in candidate_ids
        )
        components = {component_by_vertex[vertex.vertex_id] for vertex in vertices}
        if len(components) != 1:
            raise ValueError(
                f"legacy component {legacy_id} spans successor graph components"
            )
        component = next(iter(components))
        induced_possible = len(vertices) * (len(vertices) - 1) // 2
        induced_edges = sum(
            frozenset((left.vertex_id, right.vertex_id)) in edge_pairs
            for left_index, left in enumerate(vertices)
            for right in vertices[left_index + 1 :]
        )
        compatible_pairs = induced_possible - induced_edges
        output.append(
            {
                "schema_version": SCHEMA_VERSION,
                "readiness_tier": READINESS_TIER,
                "report_id": report_id,
                "legacy_component_id": legacy_id,
                "legacy_resolution_status": resolution_status,
                "legacy_preferred_candidate_id": preferred_candidate_id,
                "legacy_candidate_count": len(candidate_ids),
                "successor_component_instance_id": component.component_instance_id,
                "successor_component_vertex_count": len(component.vertex_ids),
                "induced_edge_count": induced_edges,
                "induced_possible_pair_count": induced_possible,
                "induced_pairwise_compatible_pair_count": compatible_pairs,
                "induced_is_clique": _flag(compatible_pairs == 0),
                "component_wide_at_most_one_would_overconstrain": _flag(
                    compatible_pairs > 0
                ),
                "candidate_ids": ";".join(candidate_ids),
            }
        )
    return output


def _summary(
    graph: PtHypothesisConflictGraph,
    *,
    component_rows: Sequence[Mapping[str, object]],
    legacy_rows: Sequence[Mapping[str, object]],
    candidate_hash: str,
    feature_hash: str,
    legacy_hash: str | None,
) -> dict[str, object]:
    multi_rows = [row for row in component_rows if _int_value(row["vertex_count"]) > 1]
    overconstrained = [
        row
        for row in component_rows
        if row["component_wide_at_most_one_would_overconstrain"] == "TRUE"
    ]
    legacy_overconstrained = [
        row
        for row in legacy_rows
        if row["component_wide_at_most_one_would_overconstrain"] == "TRUE"
    ]
    legacy_ambiguous = [
        row
        for row in legacy_rows
        if text_value(row["legacy_resolution_status"]).startswith("ambiguous_")
    ]
    legacy_preferred = [
        row
        for row in legacy_rows
        if row["legacy_resolution_status"] == "unique_strict_support"
        and text_value(row["legacy_preferred_candidate_id"])
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "graph_rule_version": graph.graph_rule_version,
        "graph_instance_id": graph.graph_instance_id,
        "changes_current_machine_call": False,
        "changes_counted_detection": False,
        "changes_matrix": False,
        "selects_hypothesis": False,
        "creates_event": False,
        "inputs": {
            "candidates_sha256": candidate_hash,
            "detected_features_sha256": feature_hash,
            "detected_features_schema_version": DETECTED_FEATURES_SCHEMA_VERSION,
            "legacy_conflicts_sha256": legacy_hash,
        },
        "vertex_count": len(graph.vertices),
        "edge_count": len(graph.edges),
        "component_count": len(graph.components),
        "multi_row_component_count": len(multi_rows),
        "non_clique_multi_row_component_count": len(overconstrained),
        "component_wide_at_most_one_overconstraint_component_count": len(
            overconstrained
        ),
        "pairwise_compatible_pair_count": sum(
            _int_value(row["pairwise_compatible_pair_count"]) for row in component_rows
        ),
        "max_component_size": max(
            _int_value(row["vertex_count"]) for row in component_rows
        ),
        "legacy_component_count": len(legacy_rows),
        "legacy_component_wide_at_most_one_overconstraint_count": len(
            legacy_overconstrained
        ),
        "legacy_pairwise_compatible_pair_count": sum(
            _int_value(row["induced_pairwise_compatible_pair_count"])
            for row in legacy_rows
        ),
        "legacy_ambiguous_component_count": len(legacy_ambiguous),
        "legacy_preferred_candidate_count": len(legacy_preferred),
        "verdict": (
            "pairwise_graph_required"
            if overconstrained
            else "no_non_clique_component_observed"
        ),
    }


def _common_row(
    graph: PtHypothesisConflictGraph,
    *,
    report_id: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "report_id": report_id,
        "graph_rule_version": graph.graph_rule_version,
        "graph_instance_id": graph.graph_instance_id,
    }


def _required_text(
    row: Mapping[str, object],
    column: str,
    *,
    row_index: int,
) -> str:
    value = text_value(row.get(column))
    if not value:
        raise ValueError(f"candidate row {row_index}: {column} must be non-empty")
    return value


def _flag(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _int_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"expected integer-compatible value, got {value!r}")
    return int(value)
