from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import check_cid_nl_discovery_release_slice as release_slice


@pytest.mark.ledger_snapshot
def test_current_cid_nl_discovery_release_slice_is_stable() -> None:
    assert release_slice.check_cid_nl_discovery_release_slice() == []


def test_release_slice_control_plane_override_fails_closed(
    tmp_path, capsys
) -> None:
    control_plane = tmp_path / "control-plane.md"
    control_plane.write_text("# Invalid compatibility index\n", encoding="utf-8")

    exit_code = release_slice.main(["--control-plane", str(control_plane)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "productization_state:" in stderr
    assert "compatibility index must exactly match" in stderr


def test_release_slice_accepts_stable_compatibility_lane_registry(tmp_path) -> None:
    roadmap = tmp_path / "roadmap.md"
    handoff = tmp_path / "handoff.md"
    roadmap.write_text(
        "\n".join(
            [
                "CID-NL Discovery is a bounded accepted slice",
                "reopen broad Backfill",
            ]
        ),
        encoding="utf-8",
    )
    handoff.write_text(
        "\n".join(
            [
                "Stable lane keys:",
                "cid_nl_default_product_activation_v1",
                "broad_backfill_autowrite",
            ]
        ),
        encoding="utf-8",
    )
    problems: list[str] = []
    release_slice._check_docs(
        roadmap=roadmap,
        handoff=handoff,
        problems=problems,
    )

    assert problems == []


def test_release_slice_propagates_artifact_retention_problems(monkeypatch) -> None:
    monkeypatch.setattr(
        release_slice,
        "check_validation_artifact_retention",
        lambda: SimpleNamespace(problems=("inventory drift",)),
    )

    problems = release_slice.check_cid_nl_discovery_release_slice()

    assert "validation_artifact_retention: inventory drift" in problems


def test_release_slice_propagates_bounded_lane_problems(monkeypatch) -> None:
    monkeypatch.setattr(
        release_slice,
        "check_bounded_product_lanes",
        lambda: ["lane drift"],
    )

    problems = release_slice.check_cid_nl_discovery_release_slice()

    assert "bounded_product_lanes: lane drift" in problems


def test_release_slice_propagates_85raw_universe_closure_problems(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        release_slice,
        "check_cid_nl_85raw_universe_closure",
        lambda: ["closure drift"],
    )

    problems = release_slice.check_cid_nl_discovery_release_slice()

    assert "85raw_universe_closure: closure drift" in problems
