from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.edge_scoring import (
    evaluate_owner_edge as _evaluate_owner_edge,
)
from xic_extractor.alignment.grouping_rules import MethodPairEvidence
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.integration_result import IntegrationResult


def evaluate_owner_edge(left, right, *, config, **kwargs):
    return _evaluate_owner_edge(
        left,
        right,
        config=config,
        method_evidence=CID_NL_GROUPING_RULES.pair_evidence(left, right, config),
        **kwargs,
    )


@dataclass(frozen=True)
class _DriftLookup:
    deltas: dict[str, float]
    orders: dict[str, int]
    source: str = "batch_istd_trend"

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self.deltas.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        return self.orders.get(sample_stem)


def _owner(
    sample_stem: str,
    *,
    neutral_loss_tag: str = "DNA_dR",
    precursor_mz: float = 250.0,
    product_mz: float = 150.0,
    observed_neutral_loss_da: float = 100.0,
    owner_apex_rt: float = 10.00,
    owner_area: float = 1000.0,
    evidence_score: int = 80,
    seed_event_count: int = 2,
    assignment_reason: str = "same_apex_window",
    identity_conflict: bool = False,
    support_seed_rt: float | None = None,
) -> SampleLocalMS1Owner:
    primary = IdentityEvent(
        candidate_id=f"{sample_stem}#primary",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=observed_neutral_loss_da,
        seed_rt=owner_apex_rt,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
    )
    support = ()
    if support_seed_rt is not None:
        support = (
            IdentityEvent(
                candidate_id=f"{sample_stem}#support",
                sample_stem=sample_stem,
                raw_file=f"{sample_stem}.raw",
                neutral_loss_tag=neutral_loss_tag,
                precursor_mz=precursor_mz,
                product_mz=product_mz,
                observed_neutral_loss_da=observed_neutral_loss_da,
                seed_rt=support_seed_rt,
                evidence_score=evidence_score,
                seed_event_count=1,
            ),
        )
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample_stem}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        precursor_mz=precursor_mz,
        owner_apex_rt=owner_apex_rt,
        owner_peak_start_rt=owner_apex_rt - 0.05,
        owner_peak_end_rt=owner_apex_rt + 0.05,
        owner_area=owner_area,
        owner_height=100.0,
        primary_identity_event=primary,
        supporting_events=support,
        identity_conflict=identity_conflict,
        assignment_reason=assignment_reason,
    )


def test_blocked_edge_reports_neutral_loss_reason() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", neutral_loss_tag="DNA_dR"),
        _owner("s2", neutral_loss_tag="DNA_base_loss"),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "blocked_edge"
    assert edge.failure_reason == "neutral_loss_tag_mismatch"
    assert edge.reason == "blocked: neutral_loss_tag_mismatch"
    assert edge.score == 0


@pytest.mark.parametrize("reason", ("product_mz_out_of_tolerance", "method_mass_gate"))
@pytest.mark.parametrize("allows_continuity", (False, True))
def test_method_answer_not_reason_text_controls_rt_continuity(
    reason: str, allows_continuity: bool
) -> None:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        classify_cross_sample_initial_edge,
    )

    left = replace(_owner("s1"), primary_identity_event=None)
    right = replace(_owner("s2", owner_apex_rt=10.2), primary_identity_event=None)
    config = AlignmentConfig()
    edge = _evaluate_owner_edge(
        left,
        right,
        config=config,
        rt_frame=SampleRtFrame().with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s1": 0.0, "s2": 0.1}, orders={"s1": 1, "s2": 2}
            )
        ),
        method_evidence=MethodPairEvidence(
            identity_failure=None,
            measurement_failure=reason,
            seed_support_level="strong",
            duplicate_context="none",
            measurement_failure_allows_rt_continuity=allows_continuity,
        ),
    )
    assert edge.decision == "blocked_edge" and edge.failure_reason == reason
    assert edge.rt_drift_corrected_delta_sec == (
        pytest.approx(6.0) if allows_continuity else None
    )
    assert classify_cross_sample_initial_edge(
        edge, context="targeted_istd_continuity", config=config
    ) == ("eligible" if allows_continuity else "excluded")


@pytest.mark.parametrize(
    ("left", "right", "expected_reason"),
    [
        (_owner("s1"), _owner("s1"), "same_sample"),
        (
            _owner("s1", precursor_mz=250.0000),
            _owner("s2", precursor_mz=250.0200),
            "precursor_mz_out_of_tolerance",
        ),
        (
            _owner("s1", product_mz=150.0000),
            _owner("s2", product_mz=150.0100),
            "product_mz_out_of_tolerance",
        ),
        (
            _owner("s1", observed_neutral_loss_da=100.0000),
            _owner("s2", observed_neutral_loss_da=100.0100),
            "observed_loss_out_of_tolerance",
        ),
    ],
)
def test_numeric_and_sample_hard_gates(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    expected_reason: str,
) -> None:
    edge = evaluate_owner_edge(
        left, right, config=AlignmentConfig(), rt_frame=SampleRtFrame()
    )

    assert edge.decision == "blocked_edge"
    assert edge.failure_reason == expected_reason
    assert edge.reason == f"blocked: {expected_reason}"


def test_non_detected_owner_blocks_edge() -> None:
    edge = evaluate_owner_edge(
        _owner("s1"),
        _owner("s2"),
        config=AlignmentConfig(),
        right_detected_owner=False,
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "blocked_edge"
    assert edge.failure_reason == "non_detected_owner"


def test_ambiguous_owner_blocks_edge() -> None:
    edge = evaluate_owner_edge(
        _owner("s1"),
        _owner("s2"),
        config=AlignmentConfig(),
        left_ambiguous_owner=True,
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "blocked_edge"
    assert edge.failure_reason == "ambiguous_owner"


def test_missing_drift_and_raw_over_strict_window_is_weak() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", owner_apex_rt=10.00),
        _owner("s2", owner_apex_rt=10.75),
        config=AlignmentConfig(preferred_rt_sec=30.0, max_rt_sec=120.0),
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "weak_edge"
    assert edge.failure_reason == ""
    assert edge.rt_raw_delta_sec == pytest.approx(45.0)
    assert edge.rt_drift_corrected_delta_sec is None
    assert edge.drift_prior_source == "none"


def test_missing_drift_and_raw_close_edge_is_strong() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", owner_apex_rt=10.00),
        _owner("s2", owner_apex_rt=10.25),
        config=AlignmentConfig(preferred_rt_sec=30.0, max_rt_sec=120.0),
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "strong_edge"
    assert edge.rt_raw_delta_sec == pytest.approx(15.0)
    assert edge.rt_raw_delta_sec <= 30.0
    assert edge.rt_drift_corrected_delta_sec is None
    assert edge.drift_prior_source == "none"
    assert edge.seed_support_level == "strong"
    assert edge.owner_quality == "clean"
    assert edge.score >= 55
    assert edge.failure_reason == ""


def test_drift_corrected_close_edge_is_strong_even_when_raw_exceeds_strict() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", owner_apex_rt=10.00),
        _owner("s2", owner_apex_rt=10.75),
        config=AlignmentConfig(preferred_rt_sec=30.0, max_rt_sec=120.0),
        rt_frame=SampleRtFrame().with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s1": 0.00, "s2": 0.50},
                orders={"s1": 10, "s2": 13},
            )
        ),
    )

    assert edge.decision == "strong_edge"
    assert edge.left_sample_stem == "s1"
    assert edge.right_sample_stem == "s2"
    assert edge.neutral_loss_tag == "DNA_dR"
    assert edge.left_precursor_mz == pytest.approx(250.0)
    assert edge.right_precursor_mz == pytest.approx(250.0)
    assert edge.left_rt_min == pytest.approx(10.00)
    assert edge.right_rt_min == pytest.approx(10.75)
    assert edge.rt_raw_delta_sec == pytest.approx(45.0)
    assert edge.rt_drift_corrected_delta_sec == pytest.approx(15.0)
    assert edge.drift_prior_source == "batch_istd_trend"
    assert edge.injection_order_gap == 3
    assert edge.seed_support_level == "strong"
    assert edge.owner_quality == "clean"
    assert edge.score >= 60


def test_invalid_drift_prior_source_raises_before_evidence_is_produced() -> None:
    with pytest.raises(ValueError, match="Unsupported drift prior source: typo_source"):
        evaluate_owner_edge(
            _owner("s1", owner_apex_rt=10.00),
            _owner("s2", owner_apex_rt=10.75),
            config=AlignmentConfig(preferred_rt_sec=30.0, max_rt_sec=120.0),
            rt_frame=SampleRtFrame().with_sources(
                drift_lookup=_DriftLookup(
                    deltas={"s1": 0.00, "s2": 0.50},
                    orders={"s1": 10, "s2": 13},
                    source="typo_source",
                )
            ),
        )


def test_sufficient_raw_edge_is_preserved_before_applying_drift() -> None:
    class CountingDrift(_DriftLookup):
        calls = 0

        def sample_delta_min(self, sample_stem: str) -> float | None:
            type(self).calls += 1
            return super().sample_delta_min(sample_stem)

    edge = evaluate_owner_edge(
        _owner("s1", owner_apex_rt=10.00),
        _owner("s2", owner_apex_rt=10.10),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=120.0),
        rt_frame=SampleRtFrame().with_sources(
            drift_lookup=CountingDrift(
                deltas={"s1": 0.00, "s2": -0.30},
                orders={"s1": 1, "s2": 2},
            )
        ),
    )

    assert edge.decision == "strong_edge"
    assert CountingDrift.calls == 0
    assert edge.drift_prior_source != "none"
    assert edge.rt_raw_delta_sec == pytest.approx(6.0)
    assert edge.rt_drift_corrected_delta_sec is None
    assert edge.score == 55
    assert edge.rt_basis == "raw"
    assert "raw_sufficient" in edge.reason


@pytest.mark.parametrize("corrected_sec", [24.0, 65.0])
def test_degraded_drift_falls_back_without_keeping_prior_bonus(
    corrected_sec: float,
) -> None:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        classify_cross_sample_initial_edge,
    )

    raw_sec = 6.0 if corrected_sec == 24.0 else 56.0
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=120.0)
    left = _owner("s1", evidence_score=45, seed_event_count=1)
    right = _owner(
        "s2",
        owner_apex_rt=10.0 + raw_sec / 60.0,
        evidence_score=45,
        seed_event_count=1,
    )
    raw = evaluate_owner_edge(left, right, config=config, rt_frame=SampleRtFrame())
    edge = evaluate_owner_edge(
        left,
        right,
        config=config,
        rt_frame=SampleRtFrame().with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s1": 0.0, "s2": (raw_sec - corrected_sec) / 60.0},
                orders={"s1": 1, "s2": 2},
            )
        ),
    )

    assert edge.rt_basis == "raw"
    assert "raw_fallback" in edge.reason
    assert edge.drift_prior_source == "batch_istd_trend"
    assert edge.rt_drift_corrected_delta_sec == pytest.approx(corrected_sec)
    assert edge.score == raw.score == 45
    assert edge.decision == raw.decision == "weak_edge"
    assert (
        classify_cross_sample_initial_edge(
            edge,
            context="full_batch_continuity",
            config=config,
        )
        == "eligible"
    )
    assert (
        classify_cross_sample_initial_edge(
            edge,
            context="complete_link",
            config=config,
        )
        == "eligible"
    )


def test_insufficient_raw_keeps_helpful_drift() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", evidence_score=45, seed_event_count=1),
        _owner("s2", owner_apex_rt=11.5, evidence_score=45, seed_event_count=1),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=120.0),
        rt_frame=SampleRtFrame().with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s1": 0.0, "s2": 1.0},
                orders={"s1": 1, "s2": 2},
            )
        ),
    )

    assert edge.decision == "strong_edge"
    assert edge.rt_basis == "drift_corrected"
    assert edge.rt_raw_delta_sec == pytest.approx(90.0)
    assert edge.rt_drift_corrected_delta_sec == pytest.approx(30.0)


@pytest.mark.parametrize(
    ("right", "expected_decision"),
    [
        (_owner("s1"), "blocked_edge"),
        (_owner("s2", identity_conflict=True), "blocked_edge"),
        (_owner("s2", precursor_mz=400.0), "blocked_edge"),
        (_owner("s2", evidence_score=20, seed_event_count=0), "weak_edge"),
        (_owner("s2", owner_area=0), "weak_edge"),
        (_owner("s2", assignment_reason="ambiguous_nearby"), "weak_edge"),
    ],
)
def test_drift_is_not_queried_when_it_cannot_admit_the_pair(
    right: SampleLocalMS1Owner,
    expected_decision: str,
) -> None:
    class UnneededDrift(_DriftLookup):
        def sample_delta_min(self, sample_stem: str) -> float | None:
            pytest.fail("RT correction cannot overcome this non-RT failure")

    edge = evaluate_owner_edge(
        _owner("s1"),
        right,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame().with_sources(
            drift_lookup=UnneededDrift(deltas={}, orders={})
        ),
    )
    assert edge.decision == expected_decision
    assert edge.rt_basis == "raw"
    assert edge.rt_drift_corrected_delta_sec is None


@pytest.mark.parametrize("failure", ["product", "loss"])
def test_targeted_istd_hard_gate_exception_retains_its_rt_query(failure: str) -> None:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        classify_cross_sample_initial_edge,
    )

    edge = evaluate_owner_edge(
        _owner("s1"),
        _owner(
            "s2",
            owner_apex_rt=11.5,
            product_mz=155.0 if failure == "product" else 150.0,
            observed_neutral_loss_da=105.0 if failure == "loss" else 100.0,
        ),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame().with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s1": 0.0, "s2": 1.0},
                orders={},
            )
        ),
    )
    assert edge.decision == "blocked_edge"
    assert edge.rt_basis == "drift_corrected"
    assert edge.rt_drift_corrected_delta_sec == pytest.approx(30.0)
    assert (
        classify_cross_sample_initial_edge(
            edge,
            context="targeted_istd_continuity",
            config=AlignmentConfig(),
        )
        == "eligible"
    )
    assert (
        classify_cross_sample_initial_edge(
            edge,
            context="complete_link",
            config=AlignmentConfig(),
        )
        == "excluded"
    )


def test_weak_seed_support_keeps_hard_pass_edge_weak() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", evidence_score=35, seed_event_count=1),
        _owner("s2", evidence_score=35, seed_event_count=1),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "weak_edge"
    assert edge.seed_support_level == "weak"


@pytest.mark.parametrize(
    ("evidence_score", "expected_support", "expected_decision"),
    [
        (65, "strong", "strong_edge"),
        (60, "strong", "strong_edge"),
        (59, "moderate", "weak_edge"),
        (55, "moderate", "weak_edge"),
    ],
)
def test_seed_support_uses_anchor_min_evidence_score_threshold(
    evidence_score: int,
    expected_support: str,
    expected_decision: str,
) -> None:
    edge = evaluate_owner_edge(
        _owner("s1", evidence_score=evidence_score, seed_event_count=2),
        _owner("s2", evidence_score=evidence_score, seed_event_count=2),
        config=AlignmentConfig(anchor_min_evidence_score=60),
        rt_frame=SampleRtFrame(),
    )

    assert edge.seed_support_level == expected_support
    assert edge.decision == expected_decision


def test_identity_conflict_blocks_edge() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", identity_conflict=True),
        _owner("s2"),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "blocked_edge"
    assert edge.failure_reason == "identity_conflict"


def test_backfill_bridge_precedes_non_detected_owner() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", identity_conflict=True),
        _owner("s1"),
        config=AlignmentConfig(),
        edge_depends_on_backfill=True,
        right_detected_owner=False,
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "blocked_edge"
    assert edge.failure_reason == "backfill_bridge"


def test_non_detected_owner_precedes_ambiguous_and_identity_conflict() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", identity_conflict=True),
        _owner("s2"),
        config=AlignmentConfig(),
        left_detected_owner=False,
        left_ambiguous_owner=True,
        rt_frame=SampleRtFrame(),
    )

    assert edge.decision == "blocked_edge"
    assert edge.failure_reason == "non_detected_owner"


@pytest.mark.parametrize(
    (
        "left",
        "right",
        "expected_reason",
        "call_kwargs",
    ),
    [
        (
            _owner("s1", identity_conflict=True),
            _owner("s2"),
            "ambiguous_owner",
            {"left_ambiguous_owner": True},
        ),
        (
            _owner("s1", identity_conflict=True),
            _owner("s1"),
            "identity_conflict",
            {},
        ),
        (
            _owner("s1"),
            _owner("s1", neutral_loss_tag="DNA_base_loss"),
            "same_sample",
            {},
        ),
        (
            _owner("s1", neutral_loss_tag="DNA_dR", precursor_mz=250.0000),
            _owner("s2", neutral_loss_tag="DNA_base_loss", precursor_mz=250.0200),
            "neutral_loss_tag_mismatch",
            {},
        ),
        (
            _owner("s1", precursor_mz=250.0000, product_mz=150.0000),
            _owner("s2", precursor_mz=250.0200, product_mz=150.0100),
            "precursor_mz_out_of_tolerance",
            {},
        ),
        (
            _owner(
                "s1",
                product_mz=150.0000,
                observed_neutral_loss_da=100.0000,
            ),
            _owner(
                "s2",
                product_mz=150.0100,
                observed_neutral_loss_da=100.0100,
            ),
            "product_mz_out_of_tolerance",
            {},
        ),
    ],
)
def test_adjacent_hard_gate_precedence_edges(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    expected_reason: str,
    call_kwargs: dict[str, bool],
) -> None:
    edge = evaluate_owner_edge(
        left,
        right,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
        **call_kwargs,
    )

    assert edge.decision == "blocked_edge"
    assert edge.failure_reason == expected_reason


def _resolved_owner(
    sample_stem: str,
    *,
    snr: float,
    evidence_score: int = 18,
    seed_event_count: int = 2,
    owner_apex_rt: float = 10.00,
) -> SampleLocalMS1Owner:
    """An owner carrying the peak Alignment actually resolved for it."""
    owner = _owner(
        sample_stem,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
        owner_apex_rt=owner_apex_rt,
    )
    return replace(
        owner,
        selected_integration=IntegrationResult(
            rt_left_min=owner_apex_rt - 0.05,
            rt_apex_min=owner_apex_rt,
            rt_right_min=owner_apex_rt + 0.05,
            raw_apex_rt_min=owner_apex_rt,
            rt_width_min=0.1,
            height_raw=snr * 10.0,
            height_smoothed=snr * 10.0,
            area_raw_counts_seconds=1000.0,
            baseline_residual_mad=10.0,
        ),
    )


@pytest.mark.parametrize("snr", [0.0, 1.5, 5.0, 50.0])
@pytest.mark.parametrize(
    "score,expected", [(18, "weak"), (55, "moderate"), (90, "strong")]
)
def test_peak_noise_cannot_override_discovery_identity_support(snr, score, expected):
    edge = evaluate_owner_edge(
        _resolved_owner("s1", snr=snr, evidence_score=score),
        _resolved_owner("s2", snr=snr, evidence_score=score, owner_apex_rt=10.10),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
    )
    assert edge.seed_support_level == expected


def test_owner_without_baseline_residual_keeps_discovery_rating() -> None:
    edge = evaluate_owner_edge(
        _owner("s1", evidence_score=18),
        _owner("s2", evidence_score=18, owner_apex_rt=10.10),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
    )

    assert edge.seed_support_level == "weak"
    assert edge.decision == "weak_edge"
