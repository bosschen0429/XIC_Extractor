from __future__ import annotations

import configparser
import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts import check_architecture_census as census

ROOT = Path(__file__).resolve().parents[1]


def test_current_architecture_census_is_valid_and_static_only() -> None:
    observed = census.build_architecture_census(ROOT)

    assert census.validate_architecture_census(ROOT, observed) == []
    assert set(observed.serializable()) == set(census.CENSUS_OWNER_REFS)
    assert all(observed.serializable().values())
    assert not {
        "productization_lanes",
        "authority_scopes",
        "retained_artifacts",
        "installed_entrypoints",
        "frozen_bundle_inputs",
    } & set(observed.serializable())


def test_grimp_census_records_only_importable_internal_modules() -> None:
    observed = census.build_architecture_census(ROOT)
    importable_modules = {
        census.module_name_from_path(path)
        for path in census.repository_python_paths(ROOT)
        if path.parts and path.parts[0] in census.REPOSITORY_ROOTS
    }

    assert {
        reference.source_module for reference in observed.internal_static_imports
    } <= importable_modules
    assert {
        reference.imported_module for reference in observed.internal_static_imports
    } <= importable_modules


def test_census_excludes_ignored_modules_from_grimp_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGraph:
        modules = {
            "xic_extractor.keep",
            "xic_extractor.other",
            "xic_extractor.ignored",
        }

        @staticmethod
        def find_modules_directly_imported_by(module: str) -> set[str]:
            return {
                "xic_extractor.keep": {
                    "xic_extractor.other",
                    "xic_extractor.ignored",
                },
                "xic_extractor.other": set(),
                "xic_extractor.ignored": {"xic_extractor.keep"},
            }[module]

        @staticmethod
        def get_import_details(
            *,
            importer: str,
            imported: str,
        ) -> list[dict[str, int]]:
            del importer, imported
            return [{"line_number": 1}]

    (tmp_path / "pyproject.toml").write_text(
        """
[project.scripts]
example = "xic_extractor.keep:main"

[tool.setuptools.packages.find]
include = ["xic_extractor*"]
exclude = ["tests*"]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        census,
        "repository_python_paths",
        lambda repo_root: (
            Path("xic_extractor/keep.py"),
            Path("xic_extractor/other.py"),
        ),
    )
    monkeypatch.setattr(
        census.grimp,
        "build_graph",
        lambda *args, **kwargs: FakeGraph(),
    )

    observed = census.build_architecture_census(tmp_path)

    assert observed.internal_static_imports == (
        census.StaticImportReference(
            source_path="xic_extractor/keep.py",
            source_module="xic_extractor.keep",
            imported_module="xic_extractor.other",
            line=1,
        ),
    )


def test_repository_python_paths_excludes_unstaged_tracked_deletions(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
    )
    package = tmp_path / "xic_extractor"
    package.mkdir()
    deleted = package / "deleted.py"
    deleted.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "xic_extractor/deleted.py"],
        cwd=tmp_path,
        check=True,
    )
    deleted.unlink()
    kept = package / "kept.py"
    kept.write_text("VALUE = 2\n", encoding="utf-8")

    paths = census.repository_python_paths(tmp_path)

    assert paths == (Path("xic_extractor/kept.py"),)


def test_import_linter_config_owns_the_four_static_dependency_contracts() -> None:
    config = configparser.ConfigParser()
    config.read(ROOT / ".importlinter", encoding="utf-8")

    assert config["importlinter"]["root_packages"].split() == list(
        census.REPOSITORY_ROOTS
    )
    assert config["importlinter"].getboolean("exclude_type_checking_imports") is False
    contracts = {
        section
        for section in config.sections()
        if section.startswith("importlinter:contract:")
    }
    assert contracts == {
        "importlinter:contract:domain-no-outer-layer",
        "importlinter:contract:peak-detection-stays-inner",
        "importlinter:contract:configuration-stays-inner",
        "importlinter:contract:output-does-not-read-raw",
    }
    assert all(
        config[section]["type"] == "forbidden"
        and config[section].getboolean("allow_indirect_imports")
        for section in contracts
    )


def test_import_linter_static_scope_does_not_claim_dynamic_resolution(
    tmp_path: Path,
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "outer").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "outer" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "outer" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "app" / "bad.py").write_text(
        "from outer import helper\n",
        encoding="utf-8",
    )
    config = tmp_path / ".importlinter"
    config.write_text(
        """
[importlinter]
root_packages =
    app
    outer
exclude_type_checking_imports = False

[importlinter:contract:cross-root]
name = App cannot import outer
type = forbidden
source_modules =
    app
forbidden_modules =
    outer
allow_indirect_imports = True
""".lstrip(),
        encoding="utf-8",
    )
    executable = shutil.which("lint-imports")
    assert executable is not None

    static_result = subprocess.run(
        [executable, "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert static_result.returncode == 1

    (tmp_path / "app" / "bad.py").write_text(
        'import importlib\nimportlib.import_module("outer.helper")\n',
        encoding="utf-8",
    )
    dynamic_result = subprocess.run(
        [executable, "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert dynamic_result.returncode == 0


def test_census_records_declarations_without_claiming_runtime_closure() -> None:
    observed = census.build_architecture_census(ROOT)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    find = project["tool"]["setuptools"]["packages"]["find"]

    assert observed.project_entrypoint_declarations == tuple(
        f"{name}={target}" for name, target in sorted(scripts.items())
    )
    assert observed.package_discovery_patterns == tuple(
        f"{key}:{pattern}"
        for key in ("include", "exclude")
        for pattern in find[key]
    )
    assert {
        pattern.removesuffix("*") for pattern in find["include"]
    } == set(census.REPOSITORY_ROOTS)


def test_census_owner_refs_use_exact_tracked_path_spelling() -> None:
    assert set(census.CENSUS_OWNER_REFS.values()) <= census.repository_tracked_paths(
        ROOT
    )


def test_optional_json_census_has_content_identity_and_scope(tmp_path: Path) -> None:
    observed = census.build_architecture_census(ROOT)
    output = tmp_path / "architecture_census.json"

    census.write_architecture_census_json(output, observed, ROOT)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "architecture_census_v2"
    assert payload["claim"] == "derived_disposable_not_authority"
    assert payload["static_dependency_scope"] == "grimp_static_import_graph"
    assert payload["static_dependency_gate"] == ".importlinter"
    assert payload["source_identity"]["head"]
    assert len(payload["source_identity"]["census_sha256"]) == 64
    assert isinstance(payload["source_identity"]["worktree_dirty"], bool)
    assert set(payload["census"]) == set(census.CENSUS_OWNER_REFS)


def test_json_census_rejects_nonignored_repo_path() -> None:
    observed = census.build_architecture_census(ROOT)

    with pytest.raises(ValueError, match="outside the repo or git-ignored"):
        census.write_architecture_census_json(
            ROOT / "docs" / "architecture_census.json",
            observed,
            ROOT,
        )
