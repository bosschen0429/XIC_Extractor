"""Workspace method actions exercise the same interface used by the HTML client."""

import pytest

from tests.targeted_fixtures import targeted_rows
from tests.workbench_config_fixtures import ConfigurationWorkspace
from tests.workbench_http import workbench_client


def test_untargeted_method_preview_does_not_replace_the_saved_batch(tmp_path):
    from xic_extractor.presets.loader import load_preset
    from xic_workbench.methods import method_details, save_method
    from xic_workbench.untargeted_config import read_untargeted_config

    current = tmp_path / "current_discovery_method.toml"
    draft = method_details("dna_dr")["draft"]
    draft["name"] = "Saved method"
    save_method(draft, current)
    saved_bytes = current.read_bytes()
    draft["name"] = "Edited method"
    draft["overrides"]["ms2_precursor_tol_da"] = 0.37
    with workbench_client(ConfigurationWorkspace(config_dir=tmp_path)) as request:
        status, preview, _ = request("/api/method", {"draft": draft})
        assert status == 200
        assert preview["path"] == str(current)
        assert preview["preset"]["name"] == "Edited method"
        assert current.read_bytes() == saved_bytes
        assert not (tmp_path / "discovery_gui.json").exists()

        status, _, _ = request(
            "/api/untargeted/save",
            {"preset": preview["path"], "preset_snapshot": preview["draft"]},
        )
        assert status == 200
        saved = load_preset(current)
        assert saved.name == "Edited method"
        assert saved.discovery_overrides["ms2_precursor_tol_da"] == 0.37
        assert "preset_snapshot" not in read_untargeted_config(config_dir=tmp_path)
        assert (
            request("/api/method", {"preset": str(current)})[1]["preset"]["name"]
            == "Edited method"
        )


def test_invalid_untargeted_method_preview_writes_nothing(tmp_path):
    from xic_workbench.methods import method_details

    draft = method_details("dna_dr")["draft"]
    draft["tags"] = []
    with workbench_client(ConfigurationWorkspace(config_dir=tmp_path)) as request:
        assert request("/api/method", {"draft": draft})[0] == 400
    assert not (tmp_path / "current_discovery_method.toml").exists()
    assert not (tmp_path / "discovery_gui.json").exists()


def test_http_list_export_and_upload_keep_metadata_without_touching_batch(tmp_path):
    import base64

    config_dir = tmp_path / "config"
    workspace = ConfigurationWorkspace(config_dir=config_dir)
    with workbench_client(workspace) as request:
        _, original, _ = request("/api/config")
        targets = targeted_rows()
        targets[1]["future_annotation"] = "keep this exact metadata"
        path = tmp_path / "list A.xlsx"
        status, exported, _ = request(
            "/api/targets/export", {"path": str(path), "targets": targets}
        )
        assert status == 200
        assert exported["path"] == str(path)
        status, uploaded, _ = request(
            "/api/targets/upload",
            {
                "name": path.name,
                "content": base64.b64encode(path.read_bytes()).decode(),
            },
        )
        assert status == 200
        assert uploaded["targets"][1]["istd_pair"] == "standard"
        assert uploaded["targets"][0]["role"] == "istd"
        assert (
            uploaded["targets"][1]["future_annotation"]
            == targets[1]["future_annotation"]
        )
        # Exchanging list files never changes the active batch.
        _, unchanged, _ = request("/api/config")
        assert unchanged["settings"] == original["settings"]
        assert unchanged["targets"] == original["targets"]
        assert not (config_dir / "workspace.json").exists()


def test_istd_upload_uses_shared_target_files_without_changing_targeted(tmp_path):
    import base64
    from pathlib import Path

    from xic_extractor.configuration.paths import targets_template_path
    from xic_extractor.configuration.targets import read_istd_targets

    source = targets_template_path()
    name = source.name
    workspace = ConfigurationWorkspace(config_dir=tmp_path / "config")
    with workbench_client(workspace) as request:
        original = request("/api/config")[1]
        status, result, _ = request(
            "/api/istd/upload",
            {
                "name": name,
                "content": base64.b64encode(source.read_bytes()).decode(),
            },
        )
        assert status == 200
        saved = Path(result["path"])
        assert saved.is_relative_to(tmp_path / "config")
        assert saved.read_bytes() == source.read_bytes()
        selection = read_istd_targets(saved)
        assert result["selection"]["skipped_count"] == selection.skipped_count
        assert [r["label"] for r in result["selection"]["targets"]] == [
            t.label for t in selection.targets
        ]
        assert all(t.is_istd for t in selection.targets)
        current = request("/api/config")[1]
        assert current["targets"] == original["targets"]
        assert current["settings"] == original["settings"]
        assert current["untargeted"] == original["untargeted"]


def test_istd_upload_rejects_invalid_content_without_writing(tmp_path):
    import base64

    workspace = ConfigurationWorkspace(config_dir=tmp_path / "config")
    with workbench_client(workspace) as request:
        for name, payload in [
            ("bad.xls", b"bad"),
            ("bad.xlsx", b"bad"),
            (
                "empty.csv",
                b"label,mz,expected_rt_min,cid_nl_tag,role,istd_pair\nA,250,2,dR,analyte,\n",
            ),
        ]:
            status, _, _ = request(
                "/api/istd/upload",
                {
                    "name": name,
                    "content": base64.b64encode(payload).decode(),
                },
            )
            assert status == 400
        assert not (tmp_path / "config" / "imports").exists()


@pytest.mark.parametrize(
    "method,loss,count",
    [("dna_dr", 116.0474, 6), ("rna_ribose", 132.0423, 1), ("rna_mer", 146.0579, 0)],
)
def test_istd_http_preview_and_upload_share_method_applicability(
    tmp_path, method, loss, count
):
    import base64

    from xic_extractor.configuration.paths import targets_template_path
    from xic_workbench.methods import method_details

    source = targets_template_path()
    snapshot = method_details(method)["draft"]
    workspace = ConfigurationWorkspace(config_dir=tmp_path / "config")
    with workbench_client(workspace) as request:
        for action, values in [
            ("istd", {"path": str(source)}),
            (
                "istd/upload",
                {
                    "name": source.name,
                    "content": base64.b64encode(source.read_bytes()).decode(),
                },
            ),
        ]:
            status, result, _ = request(
                f"/api/{action}", {**values, "preset_snapshot": snapshot}
            )
            assert status == 200
            selection = result["selection"]
            assert len(selection["targets"]) == count
            assert all(t["neutral_loss_da"] == loss for t in selection["targets"])
            assert len(selection["method_skipped"]) == 7 - count
            assert selection["skipped_count"] == 7
