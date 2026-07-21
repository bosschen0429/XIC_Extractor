from __future__ import annotations

import pytest

from xic_extractor.discovery.pt_pattern_conflict_graph import (
    NormalizedFeatureClaim,
    PtConflictGraphInput,
    build_pt_hypothesis_conflict_graph,
)


def _claim(
    feature_id: str,
    *,
    artifact_sha256: str = "a" * 64,
    sample_stem: str = "SampleA",
    source_kind: str = "local_ms1_feature",
) -> NormalizedFeatureClaim:
    return NormalizedFeatureClaim(
        sample_stem=sample_stem,
        feature_source_kind=source_kind,
        feature_source_artifact_sha256=artifact_sha256,
        source_schema_version="detected_features.v1",
        feature_id=feature_id,
    )


def _hypothesis(
    source_hypothesis_id: str,
    feature_ids: tuple[str, str, str, str],
    *,
    artifact_sha256: str = "a" * 64,
    sample_stem: str = "SampleA",
    source_kind: str = "local_ms1_feature",
) -> PtConflictGraphInput:
    return PtConflictGraphInput(
        source_hypothesis_id=source_hypothesis_id,
        hypothesis_source_artifact_sha256="c" * 64,
        hypothesis_source_schema_version="pt_pattern_candidates.v1",
        claims_by_position={
            position: _claim(
                feature_id,
                artifact_sha256=artifact_sha256,
                sample_stem=sample_stem,
                source_kind=source_kind,
            )
            for position, feature_id in zip(
                ("base", "pt_plus_1", "pt_plus_2", "pt_plus_4"),
                feature_ids,
                strict=True,
            )
        },
    )


def test_conflict_graph_keeps_pairwise_edges_inside_non_clique_component() -> None:
    graph = build_pt_hypothesis_conflict_graph(
        (
            _hypothesis("A", ("a0", "shared-ab", "a2", "a4")),
            _hypothesis("B", ("b0", "shared-ab", "shared-bc", "b4")),
            _hypothesis("C", ("c0", "c1", "shared-bc", "c4")),
        ),
        graph_rule_version="pt_conflict_graph.v1",
    )

    vertex_by_source = {
        vertex.source_hypothesis_id: vertex.vertex_id for vertex in graph.vertices
    }
    edge_pairs = {
        frozenset((edge.left_vertex_id, edge.right_vertex_id))
        for edge in graph.edges
    }

    assert len(graph.components) == 1
    assert len(graph.edges) == 2
    assert graph.components[0].is_clique is False
    assert frozenset((vertex_by_source["A"], vertex_by_source["B"])) in edge_pairs
    assert frozenset((vertex_by_source["B"], vertex_by_source["C"])) in edge_pairs
    assert frozenset((vertex_by_source["A"], vertex_by_source["C"])) not in edge_pairs


def test_conflict_graph_preserves_singletons_as_graph_partitions() -> None:
    graph = build_pt_hypothesis_conflict_graph(
        (
            _hypothesis("shared-a", ("f1", "f2", "f3", "f4")),
            _hypothesis("shared-b", ("f4", "f5", "f6", "f7")),
            _hypothesis("singleton", ("f8", "f9", "f10", "f11")),
        ),
        graph_rule_version="pt_conflict_graph.v1",
    )

    component_sizes = sorted(
        len(component.vertex_ids) for component in graph.components
    )

    assert component_sizes == [1, 2]
    assert sum(component_sizes) == len(graph.vertices) == 3


def test_conflict_graph_is_deterministic_across_input_and_mapping_order() -> None:
    left = _hypothesis("left", ("f1", "f2", "f3", "shared"))
    right = _hypothesis("right", ("shared", "f5", "f6", "f7"))
    reordered_left = PtConflictGraphInput(
        source_hypothesis_id="left",
        hypothesis_source_artifact_sha256=(
            left.hypothesis_source_artifact_sha256
        ),
        hypothesis_source_schema_version=left.hypothesis_source_schema_version,
        claims_by_position=dict(reversed(tuple(left.claims_by_position.items()))),
    )

    first = build_pt_hypothesis_conflict_graph(
        (left, right),
        graph_rule_version="pt_conflict_graph.v1",
    )
    second = build_pt_hypothesis_conflict_graph(
        (right, reordered_left),
        graph_rule_version="pt_conflict_graph.v1",
    )

    assert first == second


def test_same_bare_feature_id_from_different_artifacts_does_not_conflict() -> None:
    graph = build_pt_hypothesis_conflict_graph(
        (
            _hypothesis("source-a", ("f1", "f2", "f3", "same-id")),
            _hypothesis(
                "source-b",
                ("same-id", "f5", "f6", "f7"),
                artifact_sha256="b" * 64,
            ),
        ),
        graph_rule_version="pt_conflict_graph.v1",
    )

    assert len(graph.edges) == 0
    assert len(graph.components) == 2


@pytest.mark.parametrize(
    ("inputs", "message"),
    (
        (
            (
                _hypothesis("duplicate", ("f1", "f2", "f3", "f4")),
                _hypothesis("duplicate", ("f5", "f6", "f7", "f8")),
            ),
            "duplicate source hypothesis id",
        ),
        (
            (
                PtConflictGraphInput(
                    source_hypothesis_id="missing-position",
                    hypothesis_source_artifact_sha256="c" * 64,
                    hypothesis_source_schema_version="pt_pattern_candidates.v1",
                    claims_by_position={
                        "base": _claim("f1"),
                        "pt_plus_1": _claim("f2"),
                        "pt_plus_2": _claim("f3"),
                    },
                ),
            ),
            "exactly base, pt_plus_1, pt_plus_2, pt_plus_4",
        ),
        (
            (_hypothesis("duplicate-claim", ("f1", "f1", "f3", "f4")),),
            "four distinct normalized feature claims",
        ),
        (
            (
                _hypothesis("sample-a", ("f1", "f2", "f3", "f4")),
                _hypothesis(
                    "sample-b",
                    ("f5", "f6", "f7", "f8"),
                    sample_stem="SampleB",
                ),
            ),
            "one sample_stem",
        ),
    ),
)
def test_conflict_graph_fails_closed_on_invalid_inputs(
    inputs: tuple[PtConflictGraphInput, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_pt_hypothesis_conflict_graph(
            inputs,
            graph_rule_version="pt_conflict_graph.v1",
        )


def test_conflict_graph_rejects_duplicate_normalized_assignments() -> None:
    with pytest.raises(ValueError, match="duplicate normalized hypothesis assignment"):
        build_pt_hypothesis_conflict_graph(
            (
                _hypothesis("source-a", ("f1", "f2", "f3", "f4")),
                _hypothesis("source-b", ("f1", "f2", "f3", "f4")),
            ),
            graph_rule_version="pt_conflict_graph.v1",
        )


def test_conflict_graph_rejects_invalid_source_hash() -> None:
    invalid = _hypothesis("invalid-hash", ("f1", "f2", "f3", "f4"))
    invalid_claims = dict(invalid.claims_by_position)
    invalid_claims["base"] = _claim("f1", artifact_sha256="not-a-sha256")

    with pytest.raises(ValueError, match="feature_source_artifact_sha256"):
        build_pt_hypothesis_conflict_graph(
            (
                PtConflictGraphInput(
                    source_hypothesis_id=invalid.source_hypothesis_id,
                    hypothesis_source_artifact_sha256=(
                        invalid.hypothesis_source_artifact_sha256
                    ),
                    hypothesis_source_schema_version=(
                        invalid.hypothesis_source_schema_version
                    ),
                    claims_by_position=invalid_claims,
                ),
            ),
            graph_rule_version="pt_conflict_graph.v1",
        )


def test_conflict_graph_rejects_unknown_hypothesis_source_schema() -> None:
    valid = _hypothesis("unknown-schema", ("f1", "f2", "f3", "f4"))

    with pytest.raises(
        ValueError,
        match="unsupported hypothesis_source_schema_version",
    ):
        build_pt_hypothesis_conflict_graph(
            (
                PtConflictGraphInput(
                    source_hypothesis_id=valid.source_hypothesis_id,
                    hypothesis_source_artifact_sha256=(
                        valid.hypothesis_source_artifact_sha256
                    ),
                    hypothesis_source_schema_version="pt_pattern_candidates.v999",
                    claims_by_position=valid.claims_by_position,
                ),
            ),
            graph_rule_version="pt_conflict_graph.v1",
        )


def test_conflict_graph_rejects_unknown_feature_source_schema() -> None:
    valid = _hypothesis("unknown-feature-schema", ("f1", "f2", "f3", "f4"))
    claims = dict(valid.claims_by_position)
    base = claims["base"]
    claims["base"] = NormalizedFeatureClaim(
        sample_stem=base.sample_stem,
        feature_source_kind=base.feature_source_kind,
        feature_source_artifact_sha256=base.feature_source_artifact_sha256,
        source_schema_version="detected_features.v999",
        feature_id=base.feature_id,
    )

    with pytest.raises(ValueError, match="unsupported feature source_schema_version"):
        build_pt_hypothesis_conflict_graph(
            (
                PtConflictGraphInput(
                    source_hypothesis_id=valid.source_hypothesis_id,
                    hypothesis_source_artifact_sha256=(
                        valid.hypothesis_source_artifact_sha256
                    ),
                    hypothesis_source_schema_version=(
                        valid.hypothesis_source_schema_version
                    ),
                    claims_by_position=claims,
                ),
            ),
            graph_rule_version="pt_conflict_graph.v1",
        )
