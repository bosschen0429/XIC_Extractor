"""Versioned pairwise conflict graph for Pt pattern hypothesis adapters.

The graph records structural incompatibility only.  It does not select a
hypothesis, instantiate a physical event, or grant product authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Literal

from xic_extractor.discovery.delta_mass_patterns import (
    PT_PATTERN_CANDIDATE_SCHEMA_VERSION,
)
from xic_extractor.discovery.detected_features import (
    DETECTED_FEATURES_SCHEMA_VERSION,
)

PtPatternPosition = Literal["base", "pt_plus_1", "pt_plus_2", "pt_plus_4"]

PT_PATTERN_POSITIONS: tuple[PtPatternPosition, ...] = (
    "base",
    "pt_plus_1",
    "pt_plus_2",
    "pt_plus_4",
)
SHARED_CLAIM_CONFLICT_REASON = "shared_normalized_feature_claim"

_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True, order=True, slots=True)
class NormalizedFeatureClaim:
    sample_stem: str
    feature_source_kind: str
    feature_source_artifact_sha256: str
    source_schema_version: str
    feature_id: str

    @property
    def canonical_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.sample_stem,
            self.feature_source_kind,
            self.feature_source_artifact_sha256,
            self.source_schema_version,
            self.feature_id,
        )


@dataclass(frozen=True, slots=True)
class PtConflictGraphInput:
    """Adapter input for one proposed Pt position assignment."""

    source_hypothesis_id: str
    hypothesis_source_artifact_sha256: str
    hypothesis_source_schema_version: str
    claims_by_position: Mapping[str, NormalizedFeatureClaim]


@dataclass(frozen=True, slots=True)
class PtConflictGraphVertex:
    vertex_id: str
    source_hypothesis_id: str
    hypothesis_source_artifact_sha256: str
    hypothesis_source_schema_version: str
    sample_stem: str
    position_claims: tuple[tuple[PtPatternPosition, NormalizedFeatureClaim], ...]


@dataclass(frozen=True, slots=True)
class PtConflictGraphEdge:
    edge_id: str
    left_vertex_id: str
    right_vertex_id: str
    reason: str
    shared_claims: tuple[NormalizedFeatureClaim, ...]


@dataclass(frozen=True, slots=True)
class PtConflictGraphComponent:
    component_instance_id: str
    vertex_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    is_clique: bool


@dataclass(frozen=True, slots=True)
class PtHypothesisConflictGraph:
    graph_rule_version: str
    graph_instance_id: str
    source_artifact_sha256s: tuple[str, ...]
    vertices: tuple[PtConflictGraphVertex, ...]
    edges: tuple[PtConflictGraphEdge, ...]
    components: tuple[PtConflictGraphComponent, ...]


def build_pt_hypothesis_conflict_graph(
    inputs: Sequence[PtConflictGraphInput],
    *,
    graph_rule_version: str,
) -> PtHypothesisConflictGraph:
    """Build a deterministic graph without selecting winners or events."""

    rule_version = graph_rule_version.strip()
    if not rule_version:
        raise ValueError("graph_rule_version must be non-empty")
    if not inputs:
        raise ValueError("Pt conflict graph requires at least one hypothesis")

    vertices = _build_vertices(inputs)
    sample_stems = {vertex.sample_stem for vertex in vertices}
    if len(sample_stems) != 1:
        raise ValueError("Pt conflict graph inputs must use one sample_stem")

    source_hashes = tuple(
        sorted(
            {
                claim.feature_source_artifact_sha256
                for vertex in vertices
                for _, claim in vertex.position_claims
            }
            | {
                vertex.hypothesis_source_artifact_sha256 for vertex in vertices
            }
        )
    )
    edges = _build_edges(vertices)
    components = _build_components(
        vertices,
        edges,
        graph_rule_version=rule_version,
        source_artifact_sha256s=source_hashes,
    )
    graph_instance_id = _stable_id(
        "pt_graph",
        {
            "graph_rule_version": rule_version,
            "source_artifact_sha256s": source_hashes,
            "vertex_ids": tuple(vertex.vertex_id for vertex in vertices),
            "edge_ids": tuple(edge.edge_id for edge in edges),
            "component_instance_ids": tuple(
                component.component_instance_id for component in components
            ),
        },
    )
    return PtHypothesisConflictGraph(
        graph_rule_version=rule_version,
        graph_instance_id=graph_instance_id,
        source_artifact_sha256s=source_hashes,
        vertices=vertices,
        edges=edges,
        components=components,
    )


def _build_vertices(
    inputs: Sequence[PtConflictGraphInput],
) -> tuple[PtConflictGraphVertex, ...]:
    source_ids: set[str] = set()
    assignment_keys: set[
        tuple[tuple[PtPatternPosition, tuple[str, str, str, str, str]], ...]
    ] = set()
    vertices: list[PtConflictGraphVertex] = []
    for item in inputs:
        source_id = item.source_hypothesis_id.strip()
        if not source_id:
            raise ValueError("source_hypothesis_id must be non-empty")
        if source_id in source_ids:
            raise ValueError(f"duplicate source hypothesis id: {source_id}")
        source_ids.add(source_id)
        hypothesis_source_artifact_sha256 = _normalized_sha256(
            item.hypothesis_source_artifact_sha256,
            label="hypothesis_source_artifact_sha256",
        )
        hypothesis_source_schema_version = (
            item.hypothesis_source_schema_version.strip()
        )
        if not hypothesis_source_schema_version:
            raise ValueError(
                "hypothesis_source_schema_version must be non-empty"
            )
        if hypothesis_source_schema_version != PT_PATTERN_CANDIDATE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported hypothesis_source_schema_version: "
                f"{hypothesis_source_schema_version!r}"
            )

        actual_positions = set(item.claims_by_position)
        expected_positions = set(PT_PATTERN_POSITIONS)
        if actual_positions != expected_positions:
            raise ValueError(
                "Pt hypothesis must assign exactly base, pt_plus_1, "
                "pt_plus_2, pt_plus_4"
            )
        position_claims = tuple(
            (position, _normalize_claim(item.claims_by_position[position]))
            for position in PT_PATTERN_POSITIONS
        )
        claims = tuple(claim for _, claim in position_claims)
        if len(set(claims)) != len(PT_PATTERN_POSITIONS):
            raise ValueError(
                "Pt hypothesis must assign four distinct normalized feature claims"
            )
        claim_samples = {claim.sample_stem for claim in claims}
        if len(claim_samples) != 1:
            raise ValueError("Pt hypothesis claims must use one sample_stem")

        assignment_key = tuple(
            (position, claim.canonical_key) for position, claim in position_claims
        )
        if assignment_key in assignment_keys:
            raise ValueError("duplicate normalized hypothesis assignment")
        assignment_keys.add(assignment_key)
        vertex_id = _stable_id(
            "pt_vertex",
            {
                "hypothesis_source_artifact_sha256": (
                    hypothesis_source_artifact_sha256
                ),
                "hypothesis_source_schema_version": (
                    hypothesis_source_schema_version
                ),
                "position_claims": assignment_key,
            },
        )
        vertices.append(
            PtConflictGraphVertex(
                vertex_id=vertex_id,
                source_hypothesis_id=source_id,
                hypothesis_source_artifact_sha256=(
                    hypothesis_source_artifact_sha256
                ),
                hypothesis_source_schema_version=(
                    hypothesis_source_schema_version
                ),
                sample_stem=claims[0].sample_stem,
                position_claims=position_claims,
            )
        )
    return tuple(sorted(vertices, key=lambda item: item.vertex_id))


def _normalize_claim(claim: NormalizedFeatureClaim) -> NormalizedFeatureClaim:
    values = {
        "sample_stem": claim.sample_stem.strip(),
        "feature_source_kind": claim.feature_source_kind.strip(),
        "source_schema_version": claim.source_schema_version.strip(),
        "feature_id": claim.feature_id.strip(),
    }
    for label, value in values.items():
        if not value:
            raise ValueError(f"{label} must be non-empty")
    if values["source_schema_version"] != DETECTED_FEATURES_SCHEMA_VERSION:
        raise ValueError(
            "unsupported feature source_schema_version: "
            f"{values['source_schema_version']!r}"
        )
    artifact_sha256 = _normalized_sha256(
        claim.feature_source_artifact_sha256,
        label="feature_source_artifact_sha256",
    )
    return NormalizedFeatureClaim(
        sample_stem=values["sample_stem"],
        feature_source_kind=values["feature_source_kind"],
        feature_source_artifact_sha256=artifact_sha256,
        source_schema_version=values["source_schema_version"],
        feature_id=values["feature_id"],
    )


def _normalized_sha256(value: str, *, label: str) -> str:
    normalized = value.strip().lower()
    if _SHA256_HEX.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be a 64-character hex digest")
    return normalized


def _build_edges(
    vertices: Sequence[PtConflictGraphVertex],
) -> tuple[PtConflictGraphEdge, ...]:
    vertex_ids_by_claim: dict[NormalizedFeatureClaim, set[str]] = {}
    for vertex in vertices:
        for _, claim in vertex.position_claims:
            vertex_ids_by_claim.setdefault(claim, set()).add(vertex.vertex_id)

    shared_claims_by_pair: dict[
        tuple[str, str], set[NormalizedFeatureClaim]
    ] = {}
    for claim, owner_ids in vertex_ids_by_claim.items():
        for left, right in combinations(sorted(owner_ids), 2):
            shared_claims_by_pair.setdefault((left, right), set()).add(claim)

    edges = []
    for (left, right), shared_claims in sorted(shared_claims_by_pair.items()):
        edge_id = _stable_id(
            "pt_edge",
            {
                "left_vertex_id": left,
                "right_vertex_id": right,
                "reason": SHARED_CLAIM_CONFLICT_REASON,
            },
        )
        edges.append(
            PtConflictGraphEdge(
                edge_id=edge_id,
                left_vertex_id=left,
                right_vertex_id=right,
                reason=SHARED_CLAIM_CONFLICT_REASON,
                shared_claims=tuple(sorted(shared_claims)),
            )
        )
    return tuple(edges)


def _build_components(
    vertices: Sequence[PtConflictGraphVertex],
    edges: Sequence[PtConflictGraphEdge],
    *,
    graph_rule_version: str,
    source_artifact_sha256s: tuple[str, ...],
) -> tuple[PtConflictGraphComponent, ...]:
    adjacency: dict[str, set[str]] = {
        vertex.vertex_id: set() for vertex in vertices
    }
    edge_by_pair: dict[frozenset[str], PtConflictGraphEdge] = {}
    for edge in edges:
        adjacency[edge.left_vertex_id].add(edge.right_vertex_id)
        adjacency[edge.right_vertex_id].add(edge.left_vertex_id)
        edge_by_pair[frozenset((edge.left_vertex_id, edge.right_vertex_id))] = edge

    unseen = set(adjacency)
    raw_components: list[tuple[str, ...]] = []
    while unseen:
        stack = [min(unseen)]
        connected: set[str] = set()
        while stack:
            vertex_id = stack.pop()
            if vertex_id in connected:
                continue
            connected.add(vertex_id)
            stack.extend(sorted(adjacency[vertex_id] - connected, reverse=True))
        unseen -= connected
        raw_components.append(tuple(sorted(connected)))

    components = []
    for vertex_ids in sorted(raw_components):
        component_edges = tuple(
            sorted(
                edge_by_pair[frozenset(pair)].edge_id
                for pair in combinations(vertex_ids, 2)
                if frozenset(pair) in edge_by_pair
            )
        )
        possible_edge_count = len(vertex_ids) * (len(vertex_ids) - 1) // 2
        is_clique = len(component_edges) == possible_edge_count
        component_instance_id = _stable_id(
            "pt_component",
            {
                "graph_rule_version": graph_rule_version,
                "source_artifact_sha256s": source_artifact_sha256s,
                "vertex_ids": vertex_ids,
                "edge_ids": component_edges,
            },
        )
        components.append(
            PtConflictGraphComponent(
                component_instance_id=component_instance_id,
                vertex_ids=vertex_ids,
                edge_ids=component_edges,
                is_clique=is_clique,
            )
        )
    return tuple(components)


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"
