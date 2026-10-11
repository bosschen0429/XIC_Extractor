from dataclasses import replace

import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import (
    TargetIdentityError,
    mint_target_list_snapshot,
    resolve_target_definition,
)


def test_target_owner_mints_stable_key_and_exact_definition() -> None:
    snapshot = _snapshot(_target())
    resolved = resolve_target_definition(snapshot, "Analyte")

    assert resolved.definition.target_key.target_label == "Analyte"
    assert resolved.definition.target_key.target_key_id == (
        "xic.target-key:"
        "733bfc01bd24b4869ae67b2e765669931b3a917c33001e879b341ad0fd2b2ce3"
    )
    assert resolved.definition.target == _target()
    assert resolved.snapshot is snapshot
    assert resolved.lookup_label == "Analyte"


def test_definition_change_preserves_key_but_invalidates_definition_and_snapshot() -> (
    None
):
    original = _snapshot(_target())
    changed = _snapshot(replace(_target(), mz=251.0))

    original_definition = original.definitions[0]
    changed_definition = changed.definitions[0]

    assert changed_definition.target_key == original_definition.target_key
    assert changed_definition.target_definition_id != (
        original_definition.target_definition_id
    )
    assert changed.snapshot_id != original.snapshot_id
    with pytest.raises(TargetIdentityError, match="stale target definition"):
        resolve_target_definition(
            changed,
            "Analyte",
            expected_definition=original_definition,
        )


def test_target_reorder_invalidates_ordered_snapshot_not_definition_identity() -> None:
    analyte = replace(
        _target(),
        istd_pair="ISTD",
        paired_rt_relation="none",
    )
    istd = replace(
        analyte,
        label="ISTD",
        mz=255.0,
        is_istd=True,
        istd_pair="",
        paired_rt_relation="none",
    )

    forward = _snapshot(analyte, istd)
    reverse = _snapshot(istd, analyte)

    assert forward.snapshot_id != reverse.snapshot_id
    assert {item.target_definition_id for item in forward.definitions} == {
        item.target_definition_id for item in reverse.definitions
    }


def test_snapshot_is_detached_from_caller_list_mutation() -> None:
    targets = [_target()]
    snapshot = mint_target_list_snapshot(
        targets,
        identity_namespace="target-list:package-0-test",
    )
    original_id = snapshot.snapshot_id

    targets[0] = replace(targets[0], rt_max=4.0)
    targets.append(replace(_target(), label="Other", mz=300.0))

    assert snapshot.snapshot_id == original_id
    assert snapshot.definitions == _snapshot(_target()).definitions
    assert _snapshot(*targets).snapshot_id != original_id


def test_target_resolution_fails_closed_for_missing_duplicate_or_stale_key() -> None:
    snapshot = _snapshot(_target())
    resolved = resolve_target_definition(snapshot, "Analyte")

    with pytest.raises(TargetIdentityError, match="not owned"):
        resolve_target_definition(snapshot, "Missing")
    with pytest.raises(TargetIdentityError, match="duplicate target label"):
        _snapshot(_target(), _target())

    other_namespace = mint_target_list_snapshot(
        (_target(),),
        identity_namespace="target-list:other-owner",
    )
    with pytest.raises(TargetIdentityError, match="stale target key"):
        resolve_target_definition(
            other_namespace,
            "Analyte",
            expected_key=resolved.definition.target_key,
        )


def test_target_identity_rejects_forged_rebinding() -> None:
    snapshot = _snapshot(_target())
    definition = snapshot.definitions[0]

    with pytest.raises(TargetIdentityError, match="mismatches"):
        replace(definition, target=replace(definition.target, rt_min=0.5))
    with pytest.raises(TargetIdentityError, match="mismatches"):
        replace(snapshot, content_sha256="f" * 64)


def _snapshot(*targets: Target):
    return mint_target_list_snapshot(
        targets,
        identity_namespace="target-list:package-0-test",
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=3.0,
        ppm_tol=10.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
        isotope_label_type="unknown",
        paired_rt_relation="none",
        sample_applicability="all",
    )
