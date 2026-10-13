"""Workspace method actions exercise the same interface used by the HTML client."""

import pytest

from tests.test_targeted_method_files import rows
from tests.test_xic_workbench import _client
from xic_workbench.workspace import ReviewWorkspace


def test_http_method_save_load_replaces_intent_preserves_batch_and_metadata(tmp_path):
    config_dir = tmp_path / "config"
    workspace = ReviewWorkspace(config_dir=config_dir)
    with _client(workspace) as request:
        _, original, _ = request("/api/config")
        settings = original["settings"]
        targets = rows()
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
        assert loaded["summary"]["label"] == "共用 CID-NL 策略"
        assert {key: loaded["settings"][key] for key in local} == local
        assert loaded["targets"][1]["istd_pair"] == "standard"
        assert loaded["targets"][0]["role"] == "istd"
        assert (
            loaded["targets"][1]["future_annotation"] == targets[1]["future_annotation"]
        )
        assert loaded["targets"][1]["sample_applicability"] == "rna_containing"
        status, saved_b, _ = request(
            "/api/targeted/method/save",
            {
                "name": "method B",
                "settings": original["settings"],
                "targets": rows()[:1],
            },
        )
        assert status == 200
        b_path = next(m["path"] for m in saved_b["methods"] if m["name"] == "method B")
        status, loaded_b, _ = request(
            "/api/targeted/method/load",
            {"path": b_path, "settings": loaded["settings"]},
        )
        assert status == 200
        assert loaded_b["summary"]["label"] == "共用 CID-NL 策略"
        assert {key: loaded_b["settings"][key] for key in local} == local
        assert len(loaded_b["targets"]) == 1
        # Both actions edit/save methods, not active batch state.
        _, unchanged, _ = request("/api/config")
        assert unchanged["settings"] == original["settings"]
        assert unchanged["targets"] == original["targets"]
        assert not (config_dir / "settings.csv").exists()


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
    with _client(workspace) as request:
        _, config, _ = request("/api/config")
        status, _, _ = request(
            "/api/targeted/method/save",
            {"name": name, "settings": config["settings"], "targets": rows()},
        )
        assert status == 400
        assert request("/api/config")[1]["targeted_methods"] == []
        assert not (tmp_path / "config" / "methods").exists()


def test_existing_method_rejected_and_bootstrap_omits_incomplete_folders(tmp_path):
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
    with _client(workspace) as request:
        _, config, _ = request("/api/config")
        values = {"name": "original", "settings": config["settings"], "targets": rows()}
        assert request("/api/targeted/method/save", values)[0] == 200
        root = tmp_path / "config" / "methods"
        before = {p.name: p.read_bytes() for p in (root / "original").iterdir()}
        (root / "incomplete").mkdir()
        (root / "incomplete" / "settings.csv").write_text("broken")
        (root / "not a method.txt").write_text("unrelated")
        values["settings"] = {**config["settings"], "dirty_matrix_mode": "true"}
        assert request("/api/targeted/method/save", values)[0] == 400
        assert before == {p.name: p.read_bytes() for p in (root / "original").iterdir()}
        assert [m["name"] for m in request("/api/config")[1]["targeted_methods"]] == [
            "original"
        ]
