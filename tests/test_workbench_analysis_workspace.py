"""Workspace method actions exercise the same interface used by the HTML client."""

import pytest

from tests.targeted_fixtures import targeted_rows
from tests.workbench_http import workbench_client
from xic_workbench.workspace import ReviewWorkspace


def test_http_method_save_load_replaces_intent_preserves_batch_and_metadata(tmp_path):
    config_dir = tmp_path / "config"
    workspace = ReviewWorkspace(config_dir=config_dir)
    with workbench_client(workspace) as request:
        _, original, _ = request("/api/config")
        settings = original["settings"]
        targets = targeted_rows()
        targets[1]["future_annotation"] = "keep this exact metadata"
        status, saved, _ = request(
            "/api/targeted/method/save",
            {"name": "method A", "settings": settings, "targets": targets},
        )
        assert status == 200
        path = saved["methods"][0]["path"]
        assert saved["methods"] == [{"name": "method A", "path": path}]
        assert request("/api/config")[1]["targeted_methods"] == saved["methods"]
        local = {
            "data_dir": "new batch",
            "dll_dir": "machine dll",
            "injection_order_source": "batch order.csv",
        }
        current = {**settings, **local}
        status, loaded, _ = request(
            "/api/targeted/method/load", {"path": path, "settings": current}
        )
        assert status == 200
        assert loaded["summary"]["label"] == "共用 Targeted 策略"
        assert {key: loaded["settings"][key] for key in local} == local
        assert loaded["targets"][1]["istd_pair"] == "standard"
        assert loaded["targets"][0]["role"] == "istd"
        assert (
            loaded["targets"][1]["future_annotation"] == targets[1]["future_annotation"]
        )
        status, saved_b, _ = request(
            "/api/targeted/method/save",
            {
                "name": "method B",
                "settings": original["settings"],
                "targets": targeted_rows()[:1],
            },
        )
        assert status == 200
        b_path = next(m["path"] for m in saved_b["methods"] if m["name"] == "method B")
        status, loaded_b, _ = request(
            "/api/targeted/method/load",
            {"path": b_path, "settings": loaded["settings"]},
        )
        assert status == 200
        assert loaded_b["summary"]["label"] == "共用 Targeted 策略"
        assert {key: loaded_b["settings"][key] for key in local} == local
        assert len(loaded_b["targets"]) == 1
        # Both actions edit/save methods, not active batch state.
        _, unchanged, _ = request("/api/config")
        assert unchanged["settings"] == original["settings"]
        assert unchanged["targets"] == original["targets"]
        assert not (config_dir / "workspace.json").exists()


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../escape",
        "C:/outside",
        "bad\\\\name",
        "CON",
        "nul.txt",
        "COM1",
        "LPT9.csv",
        "bad.",
    ],
)
def test_method_save_rejects_invalid_name_without_writing(tmp_path, name):
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
    with workbench_client(workspace) as request:
        _, config, _ = request("/api/config")
        status, _, _ = request(
            "/api/targeted/method/save",
            {"name": name, "settings": config["settings"], "targets": targeted_rows()},
        )
        assert status == 400
        assert request("/api/config")[1]["targeted_methods"] == []
        assert not (tmp_path / "config" / "methods").exists()


def test_existing_method_rejected_and_bootstrap_omits_incomplete_folders(tmp_path):
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
    with workbench_client(workspace) as request:
        _, config, _ = request("/api/config")
        values = {
            "name": "original",
            "settings": config["settings"],
            "targets": targeted_rows(),
        }
        assert request("/api/targeted/method/save", values)[0] == 200
        root = tmp_path / "config" / "methods"
        before = (root / "original.xlsx").read_bytes()
        (root / "incomplete").mkdir()
        (root / "incomplete" / "settings.csv").write_text("broken")
        (root / "not a method.txt").write_text("unrelated")
        values["settings"] = {**config["settings"], "dirty_matrix_mode": "true"}
        assert request("/api/targeted/method/save", values)[0] == 400
        assert before == (root / "original.xlsx").read_bytes()
        assert [m["name"] for m in request("/api/config")[1]["targeted_methods"]] == [
            "original"
        ]


@pytest.mark.parametrize(
    "name", ["targets.csv", "targets.xlsx", "targeted-method.xlsx"]
)
def test_istd_upload_uses_shared_target_files_without_changing_targeted(tmp_path, name):
    import base64
    from pathlib import Path

    from xic_extractor.configuration.paths import targets_template_path
    from xic_extractor.configuration.targets import read_istd_targets

    source = targets_template_path().parent / name
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
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

    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
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
    "method,tag,count",
    [("dna_dr", "dR", 6), ("rna_ribose", "R", 1), ("rna_mer", "MeR", 0)],
)
def test_istd_http_preview_and_upload_share_method_applicability(
    tmp_path, method, tag, count
):
    import base64

    from xic_extractor.configuration.paths import targets_template_path
    from xic_workbench.methods import method_details

    source = targets_template_path().with_suffix(".xlsx")
    snapshot = method_details(method)["draft"]
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
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
            assert all(t["cid_nl_tag"] == tag for t in selection["targets"])
            assert len(selection["method_skipped"]) == 7 - count
            assert selection["skipped_count"] == 7
