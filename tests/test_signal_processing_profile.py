from __future__ import annotations

from dataclasses import fields, replace

import pytest

from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    DEFAULT_SIGNAL_PROCESSING_PROFILE_ID,
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
    deserialize_signal_processing_profile,
    resolve_signal_processing_profile,
    serialize_signal_processing_profile,
)

EXPECTED_DEFAULT_JSON = (
    b'{"schema_version":"signal_processing_profile","method":"gaussian",'
    b'"window_points":15,"gaussian_sigma_rule":"window_points_div_6",'
    b'"gaussian_kernel_normalization":"unit_sum",'
    b'"gaussian_edge_policy":"zero_pad_same","baseline_method":"asls",'
    b'"baseline_lam":100000,"baseline_p":0.01,"baseline_n_iter":10,'
    b'"physical_signal_formula":"gaussian_raw_minus_nonnegative_asls_raw_positive",'
    b'"boundary_basis":"chrom_peak_segment_closed_subject",'
    b'"segment_universe_limit":64,'
    b'"segment_selection_behavior":'
    b'"closed_segment_contains_query_complete_context",'
    b'"area_basis":"trapezoid_counts_seconds_on_canonical_signal",'
    b'"relative_shape_basis":"own_max_canonical_signal"}'
)

_PROFILE_FIELDS = (
    "method",
    "window_points",
    "schema_version",
    "gaussian_sigma_rule",
    "gaussian_kernel_normalization",
    "gaussian_edge_policy",
    "baseline_method",
    "baseline_lam",
    "baseline_p",
    "baseline_n_iter",
    "physical_signal_formula",
    "boundary_basis",
    "segment_universe_limit",
    "segment_selection_behavior",
    "area_basis",
    "relative_shape_basis",
)
_FIXED_PROFILE_FIELDS = tuple(
    name for name in _PROFILE_FIELDS if name != "window_points"
)


def test_default_profile_has_exact_canonical_bytes_hash_and_id() -> None:
    profile = DEFAULT_SIGNAL_PROCESSING_PROFILE

    assert profile == SignalProcessingProfile()
    assert canonical_signal_processing_profile_bytes(profile) == EXPECTED_DEFAULT_JSON
    assert serialize_signal_processing_profile(profile) == EXPECTED_DEFAULT_JSON
    expected_hash = "ce1cdcae6e4186e012dde1535fa412634f92e2b22377aeb83fdc00b5077b5818"
    assert profile.profile_hash == expected_hash
    assert profile.profile_id == f"signal-profile:{expected_hash}"
    assert DEFAULT_SIGNAL_PROCESSING_PROFILE_ID == profile.profile_id


def test_profile_reader_rejects_unconstrained_and_clipped_peak_semantics() -> None:
    old_profile = EXPECTED_DEFAULT_JSON.replace(
        b"gaussian_raw_minus_nonnegative_asls_raw_positive",
        b"gaussian_raw_minus_asls_raw_positive",
    ).replace(
        b"closed_segment_contains_query_complete_context",
        b"closed_segment_contains_query_unique_only",
    )

    with pytest.raises(ValueError, match="physical_signal_formula"):
        deserialize_signal_processing_profile(old_profile)


@pytest.mark.parametrize("window_points", [True, False, 2, 4, 1000, 3.0, "15"])
def test_profile_rejects_non_bool_odd_integer_contract(window_points: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        SignalProcessingProfile(window_points=window_points)  # type: ignore[arg-type]


def test_resolver_supports_only_gaussian_and_15_is_default() -> None:
    assert resolve_signal_processing_profile() == DEFAULT_SIGNAL_PROCESSING_PROFILE
    assert resolve_signal_processing_profile(
        signal_processing_method="gaussian",
        signal_processing_window_points=21,
    ) == SignalProcessingProfile(window_points=21)
    with pytest.raises(ValueError, match="gaussian"):
        resolve_signal_processing_profile(signal_processing_method="savgol")


def test_profile_schema_is_closed_and_point15_point21_have_distinct_identity() -> None:
    profile_15 = DEFAULT_SIGNAL_PROCESSING_PROFILE
    profile_21 = SignalProcessingProfile(window_points=21)

    assert tuple(field.name for field in fields(SignalProcessingProfile)) == (
        _PROFILE_FIELDS
    )
    assert canonical_signal_processing_profile_bytes(profile_15) != (
        canonical_signal_processing_profile_bytes(profile_21)
    )
    assert profile_15.profile_hash != profile_21.profile_hash
    assert profile_15.profile_id != profile_21.profile_id


@pytest.mark.parametrize("field_name", _FIXED_PROFILE_FIELDS)
def test_every_fixed_profile_field_rejects_mutation(field_name: str) -> None:
    profile = DEFAULT_SIGNAL_PROCESSING_PROFILE
    value = getattr(profile, field_name)
    mutated = (
        f"{value}-mutated"
        if isinstance(value, str)
        else value + 1
        if isinstance(value, int)
        else value + 0.1
    )

    with pytest.raises(ValueError):
        replace(profile, **{field_name: mutated})


def test_strict_deserializer_rejects_noncanonical_unknown_duplicate_and_mismatch() -> (
    None
):
    profile = deserialize_signal_processing_profile(
        EXPECTED_DEFAULT_JSON,
        expected_profile_hash=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_hash,
        expected_profile_id=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id,
    )
    assert profile == DEFAULT_SIGNAL_PROCESSING_PROFILE

    with pytest.raises(ValueError, match="canonical"):
        deserialize_signal_processing_profile(EXPECTED_DEFAULT_JSON + b"\n")
    with pytest.raises(ValueError, match="unknown"):
        deserialize_signal_processing_profile(
            EXPECTED_DEFAULT_JSON[:-1] + b',"extra":1}'
        )
    with pytest.raises(ValueError, match="duplicate"):
        deserialize_signal_processing_profile(
            EXPECTED_DEFAULT_JSON[:-1] + b',"method":"gaussian"}'
        )
    with pytest.raises(ValueError, match="profile_hash"):
        deserialize_signal_processing_profile(
            EXPECTED_DEFAULT_JSON,
            expected_profile_hash="0" * 64,
        )
    with pytest.raises(ValueError, match="profile_id"):
        deserialize_signal_processing_profile(
            EXPECTED_DEFAULT_JSON,
            expected_profile_id="signal-profile:" + "0" * 64,
        )
    with pytest.raises(ValueError, match="baseline_lam"):
        deserialize_signal_processing_profile(
            EXPECTED_DEFAULT_JSON.replace(
                b'"baseline_lam":100000',
                b'"baseline_lam":100000.0',
            )
        )
