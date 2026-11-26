from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import check_architecture_census as census


def _defense_gate_repo(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "xic_extractor" / "example.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")
    env = census._git_subprocess_environment()
    for args in (
        ("init",),
        ("add", "."),
        (
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "baseline",
        ),
    ):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            env=env,
            check=True,
            capture_output=True,
        )
    return path


_HOOK = "class Ref:\n def __post_init__(self):\n  require(self.value)\n"


@pytest.mark.parametrize(
    "after",
    (
        _HOOK,
        "class Ref:\n def __post_init__(self):\n  if self.value < 0:\n"
        "   raise ValueError\n",
        "class Ref:\n def __init_subclass__(cls):\n  raise TypeError\n",
        "if condition:\n" + "".join(" " + line + "\n" for line in _HOOK.splitlines()),
    ),
)
def test_internal_defense_growth_is_rejected(tmp_path: Path, after: str) -> None:
    path = _defense_gate_repo(tmp_path, "")
    path.write_text(after, encoding="utf-8")

    problems = census.validate_internal_defense_growth(tmp_path, "HEAD")

    assert any(
        "construction-hook" in problem and "xic_extractor/example.py:" in problem
        for problem in problems
    )


@pytest.mark.parametrize(
    ("before", "after"),
    (
        (_HOOK, _HOOK),
        (_HOOK, ""),
        (_HOOK + "  require(self.other)\n", _HOOK),
        (
            _HOOK,
            'class Ref:\n """Updated explanation."""\n def __post_init__(self):\n'
            '  """Updated explanation."""\n  require(self.value)\n',
        ),
        (_HOOK, "class Ref:\n def __post_init__(self):\n  require(self.renamed)\n"),
    ),
)
def test_internal_defense_gate_preserves_existing_boundaries_and_cleanup(
    tmp_path: Path,
    before: str,
    after: str,
) -> None:
    path = _defense_gate_repo(tmp_path, before)
    path.write_text(after, encoding="utf-8")

    assert census.validate_internal_defense_growth(tmp_path, "HEAD") == []


@pytest.mark.parametrize("staged", (False, True))
@pytest.mark.parametrize("growth", (False, True))
def test_internal_defense_gate_tracks_module_renames(
    tmp_path: Path, staged: bool, growth: bool
) -> None:
    path = _defense_gate_repo(tmp_path, _HOOK)
    destination = path.with_name("renamed.py")
    subprocess.run(
        ["git", "-C", str(tmp_path), "mv", str(path), str(destination)],
        check=True,
        capture_output=True,
        env=census._git_subprocess_environment(),
    )
    if growth:
        destination.write_text(destination.read_text() + "  require(self.other)\n")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "."],
            check=True,
            capture_output=True,
            env=census._git_subprocess_environment(),
        )
    assert (
        bool(census.validate_internal_defense_growth(tmp_path, "HEAD", staged=staged))
        is growth
    )


@pytest.mark.parametrize(
    ("remaining", "moved", "growth"),
    (
        ("", _HOOK, False),
        (None, _HOOK, False),
        ("", _HOOK + "  require(self.other)\n", True),
    ),
)
def test_internal_defense_gate_tracks_scopes_moved_between_modules(
    tmp_path: Path, remaining: str | None, moved: str, growth: bool
) -> None:
    path = _defense_gate_repo(tmp_path, _HOOK + "def keep():\n pass\n" * 9)
    if remaining is None:
        path.unlink()
    else:
        path.write_text(remaining, encoding="utf-8")
    path.with_name("destination.py").write_text(moved, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"],
        check=True,
        capture_output=True,
        env=census._git_subprocess_environment(),
    )
    problems = census.validate_internal_defense_growth(tmp_path, "HEAD", staged=True)
    assert bool(problems) is growth
    assert all("xic_extractor/destination.py:" in problem for problem in problems)


@pytest.mark.parametrize(
    "source",
    (
        "def _validate_target_relationships(rows):\n"
        " assert all(row.parent in rows for row in rows)\n",
        "class Accumulator:\n def __init__(self, samples):\n"
        "  self.samples = tuple(samples)\n  self.rows = {}\n",
        "def parse(text):\n if not text:\n  raise ValueError('empty')\n"
        " return text.split()\n",
        "import hashlib\ndef fingerprint(path):\n"
        " return hashlib.sha256(path.read_bytes()).hexdigest()\n",
    ),
)
def test_internal_defense_gate_allows_ordinary_functions(
    tmp_path: Path, source: str
) -> None:
    path = _defense_gate_repo(tmp_path, "")
    path.write_text(source, encoding="utf-8")
    assert census.validate_internal_defense_growth(tmp_path, "HEAD") == []


def test_internal_defense_gate_compares_duplicate_scopes_by_occurrence(
    tmp_path: Path,
) -> None:
    empty = "class Ref:\n def __post_init__(self):\n  pass\n"
    nested = "if condition:\n" + "".join(
        " " + line + "\n" for line in empty.splitlines()
    )
    path = _defense_gate_repo(tmp_path, empty + nested)
    path.write_text(
        empty + "  require(self.value)\n" + nested,
        encoding="utf-8",
    )
    assert any(
        "construction-hook" in p
        for p in census.validate_internal_defense_growth(tmp_path, "HEAD")
    )


@pytest.mark.parametrize(
    ("before", "after", "growth"),
    (
        (_HOOK, _HOOK.replace("class Ref", "class NewRef"), False),
        (
            _HOOK,
            _HOOK + _HOOK.replace("class Ref", "class CopyRef"),
            True,
        ),
        (
            _HOOK.replace("class Ref", "class Old"),
            _HOOK.replace("class Ref", "class First")
            + _HOOK.replace("class Ref", "class Second"),
            True,
        ),
    ),
)
def test_internal_defense_gate_preserves_renamed_but_not_copied_scopes(
    tmp_path: Path, before: str, after: str, growth: bool
) -> None:
    path = _defense_gate_repo(tmp_path, before)
    path.write_text(after, encoding="utf-8")
    assert bool(census.validate_internal_defense_growth(tmp_path, "HEAD")) is growth


def test_internal_defense_gate_uses_index_not_unstaged_source(tmp_path: Path) -> None:
    path = _defense_gate_repo(tmp_path, "")
    path.write_text(_HOOK, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "."],
        check=True,
        env=census._git_subprocess_environment(),
        capture_output=True,
    )
    path.write_text("", encoding="utf-8")

    assert census.validate_internal_defense_growth(tmp_path, "HEAD", staged=True)
    assert census.validate_internal_defense_growth(tmp_path, "HEAD") == []
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "."],
        check=True,
        env=census._git_subprocess_environment(),
        capture_output=True,
    )
    path.write_text(_HOOK, encoding="utf-8")
    assert census.validate_internal_defense_growth(tmp_path, "HEAD", staged=True) == []
    assert census.validate_internal_defense_growth(tmp_path, "HEAD")


def test_internal_defense_cli_blocks_growth_and_missing_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _defense_gate_repo(tmp_path, "")
    monkeypatch.setattr(census, "ROOT", tmp_path)
    assert census.main(["--internal-defense-base", "HEAD"]) == 0
    path.write_text(_HOOK, encoding="utf-8")
    assert census.main(["--internal-defense-base", "HEAD"]) == 1
    assert "construction-hook" in capsys.readouterr().err
    assert census.main(["--internal-defense-base", "missing-ref"]) == 1
    assert "baseline" in capsys.readouterr().err


def test_internal_defense_cli_also_blocks_handwritten_ppm_arithmetic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _defense_gate_repo(tmp_path, "")
    monkeypatch.setattr(census, "ROOT", tmp_path)
    path.write_text("def f(x, ppm):\n    return x * ppm / 1e6\n", encoding="utf-8")
    assert census.main(["--internal-defense-base", "HEAD"]) == 1
    assert "xic_extractor/example.py:2: ppm arithmetic" in capsys.readouterr().err
    path.write_text("", encoding="utf-8")
    assert census.main(["--internal-defense-base", "HEAD"]) == 0


def test_census_cli_rejects_a_redundant_typed_input_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _defense_gate_repo(tmp_path, "")
    monkeypatch.setattr(census, "ROOT", tmp_path)
    path.write_text(
        "def count(value: int) -> int:\n"
        "    if not isinstance(value, int):\n"
        "        raise TypeError('expected an int')\n"
        "    return value\n",
        encoding="utf-8",
    )

    assert census.main(["--internal-defense-base", "HEAD"]) == 1
    assert capsys.readouterr().err.strip() == (
        "xic_extractor/example.py:2: redundant type guard for value; "
        "the input annotation is already covered by the runtime types"
    )


@pytest.mark.parametrize(
    ("annotation", "condition", "rejects"),
    [
        ("int", "type(value) is not int or value < 1", True),
        ("str | int", "not isinstance(value, (str, int))", True),
        ("Optional[str]", "not isinstance(value, (str, type(None)))", True),
        ("str | int", "not isinstance(value, str | int)", True),
        ("tuple[int, ...]", "not isinstance(value, tuple)", True),
        ("str | int", "not isinstance(value, str)", False),
        ("Optional[str]", "not isinstance(value, str)", False),
        ("object", "not isinstance(value, str)", False),
        ("Any", "not isinstance(value, str)", False),
        ("int", "isinstance(value, bool) or value < 1", False),
        ("int", "not isinstance(parsed, int)", False),
    ],
)
def test_typed_guard_gate_distinguishes_redundancy_from_narrowing(
    tmp_path: Path, annotation: str, condition: str, rejects: bool
) -> None:
    relative = Path("xic_extractor/example.py")
    path = tmp_path / relative
    path.parent.mkdir()
    path.write_text(
        f"def count(*, value: {annotation}):\n"
        "    parsed = json.loads(data)\n"
        f"    if {condition}:\n"
        "        raise ValueError('not usable')\n",
        encoding="utf-8",
    )
    assert bool(census.validate_typed_input_guards(tmp_path, (relative,))) is rejects


def test_typed_guard_gate_rejects_dataclass_field_guard(tmp_path: Path) -> None:
    relative = Path("xic_extractor/example.py")
    path = tmp_path / relative
    path.parent.mkdir()
    path.write_text(
        "@dataclass(frozen=True)\n"
        "class Model:\n"
        "    value: int\n"
        "    def __post_init__(self):\n"
        "        if isinstance(self.value, bool) or not isinstance(self.value, int):\n"
        "            raise ValueError('not usable')\n",
        encoding="utf-8",
    )
    assert census.validate_typed_input_guards(tmp_path, (relative,)) == [
        "xic_extractor/example.py:5: redundant type guard for self.value; "
        "the input annotation is already covered by the runtime types"
    ]


@pytest.mark.parametrize(
    "line",
    (
        "x * ppm / 1e6",
        "x * ppm * 1e-6",
        "ppm / 1_000_000",
        "(a - b) / b * 1_000_000.0",
        "1e6 * (a - b) / b",
        "min(a * p, b * q) / 1e6",
    ),
)
def test_ppm_arithmetic_gate_rejects_conversions_outside_the_owner(
    tmp_path: Path, line: str
) -> None:
    source = Path("tools/example.py")
    (tmp_path / source).parent.mkdir()
    (tmp_path / source).write_text(f"value = {line}\n", encoding="utf-8")

    assert census.validate_ppm_arithmetic(tmp_path, (source,)) == [
        "tools/example.py:1: ppm arithmetic outside xic_extractor/mass_accuracy.py; "
        "call its functions"
    ]


def test_ppm_arithmetic_gate_accepts_other_uses_and_the_owner(tmp_path: Path) -> None:
    other = Path("xic_extractor/example.py")
    owner = Path(census.PPM_ARITHMETIC_OWNER)
    (tmp_path / other).parent.mkdir()
    (tmp_path / other).write_text(
        "key = round(mz * 1_000_000)\n"
        "same = math.isclose(a, b, rel_tol=1e-6)\n"
        "COST = 1_000_000.0\n"
        "sigma = max(width / 6.0, 1e-6)\n"
        "total = area * 1e3\n",
        encoding="utf-8",
    )
    (tmp_path / owner).write_text("tolerance = mz * ppm * 1e-6\n", encoding="utf-8")

    assert census.validate_ppm_arithmetic(tmp_path, (other, owner)) == []


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.current_repository_census
def test_current_repository_passes_every_gate() -> None:
    assert census.validate_repository(ROOT) == []


@pytest.mark.parametrize("path", ["scripts/run_extraction.py", "xic_workbench/cli.py"])
def test_frozen_entrypoints_call_freeze_support_first(tmp_path, path):
    for relative in ("scripts/run_extraction.py", "xic_workbench/cli.py"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "def main():\n multiprocessing.freeze_support()", encoding="utf-8"
        )
    assert census.validate_frozen_entrypoints(tmp_path) == []

    (tmp_path / path).write_text(
        "def main():\n prepare()\n multiprocessing.freeze_support()",
        encoding="utf-8",
    )

    problems = census.validate_frozen_entrypoints(tmp_path)
    assert len(problems) == 1
    assert "freeze_support first" in problems[0]


def _lint_imports_executable() -> str:
    executable = shutil.which("lint-imports")
    if executable is None:
        entrypoint_name = (
            "lint-imports.exe" if sys.platform == "win32" else "lint-imports"
        )
        sibling_entrypoint = Path(sys.executable).with_name(entrypoint_name)
        if sibling_entrypoint.is_file():
            executable = str(sibling_entrypoint)
    assert executable is not None
    return executable


def test_semantic_identifier_gate_scans_python_values_and_maintained_docs(
    tmp_path: Path,
) -> None:
    python_path = Path("xic_extractor/example.py")
    markdown_path = Path("docs/product/example.md")
    for path in (python_path, markdown_path):
        (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / python_path).write_text(
        "LABEL = 'sample_" + "v2'\nTICKET = 'sample_" + "by_455'\n"
        "SCHEMA = 'sample_schema." + "v3'\n",
        encoding="utf-8",
    )
    (tmp_path / markdown_path).write_text(
        "Current `sample_" + "v2` and `sample_" + "by_455`.\n"
        "Schema `sample_schema." + "v3`.\n",
        encoding="utf-8",
    )

    problems = census.validate_semantic_identifiers(
        tmp_path, (python_path, markdown_path)
    )

    version_label = "sample_" + "v2"
    ticket_label = "sample_" + "by_455"
    schema_label = "sample_schema." + "v3"
    expected = {
        f"xic_extractor/example.py:1: versioned identifier {version_label}",
        f"xic_extractor/example.py:2: versioned identifier {ticket_label}",
        f"xic_extractor/example.py:3: versioned identifier {schema_label}",
        f"docs/product/example.md:1: versioned identifier {version_label}",
        f"docs/product/example.md:1: versioned identifier {ticket_label}",
        f"docs/product/example.md:2: versioned identifier {schema_label}",
    }
    assert expected <= set(problems)


def test_documented_code_name_gate_rejects_names_no_source_defines(
    tmp_path: Path,
) -> None:
    doc_path = Path("docs/product/example.md")
    source_path = Path("xic_extractor/example.py")
    for path in (doc_path, source_path):
        (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / source_path).write_text("current_name = 1\n", encoding="utf-8")
    retired = "retired" + "_name"
    (tmp_path / doc_path).write_text(
        f"Reads `current_name`.\nStill describes `{retired}`.\n",
        encoding="utf-8",
    )

    problems = census.validate_documented_code_names(
        tmp_path, (doc_path,), (source_path,)
    )

    assert problems == [
        f"docs/product/example.md:2: documented name {retired} "
        "is not defined in any tracked source"
    ]


def test_historical_identifier_exemption_is_exact_and_requires_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = Path("docs/product/example.md")
    source = tmp_path / source_path
    source.parent.mkdir(parents=True)
    label = "sample_" + "v2"
    source.write_text(f"`{label}`\n", encoding="utf-8")
    monkeypatch.setattr(
        census,
        "HISTORICAL_IDENTIFIER_ALLOWLIST",
        {(source_path.as_posix(), label): ""},
    )

    problems = census.validate_semantic_identifiers(tmp_path, (source_path,))
    assert any("needs a reason" in problem for problem in problems)

    monkeypatch.setattr(
        census,
        "HISTORICAL_IDENTIFIER_ALLOWLIST",
        {(source_path.as_posix(), label): "Immutable fixture name."},
    )
    assert census.validate_semantic_identifiers(tmp_path, (source_path,)) == []

    source.write_text("Current semantic name only.\n", encoding="utf-8")
    monkeypatch.setattr(census, "repository_tracked_paths", lambda root: (source_path,))
    assert any(
        "unused historical identifier exemption" in problem
        for problem in census.validate_semantic_identifiers(tmp_path)
    )


def test_repository_python_paths_excludes_unstaged_tracked_deletions(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        env=census._git_subprocess_environment(),
    )
    package = tmp_path / "xic_extractor"
    package.mkdir()
    deleted = package / "deleted.py"
    deleted.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "xic_extractor/deleted.py"],
        cwd=tmp_path,
        check=True,
        env=census._git_subprocess_environment(),
    )
    deleted.unlink()
    kept = package / "kept.py"
    kept.write_text("VALUE = 2\n", encoding="utf-8")

    paths = census.repository_python_paths(tmp_path)

    assert paths == (Path("xic_extractor/kept.py"),)


def test_git_subprocess_environment_drops_hook_repository_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in census._GIT_LOCAL_ENVIRONMENT_VARIABLES:
        monkeypatch.setenv(name, "hook-owned")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.setenv("CENSUS_TEST_UNRELATED", "preserved")

    environment = census._git_subprocess_environment()

    assert not (census._GIT_LOCAL_ENVIRONMENT_VARIABLES & environment.keys())
    assert not any(
        name.startswith("GIT_CONFIG_KEY_") or name.startswith("GIT_CONFIG_VALUE_")
        for name in environment
    )
    assert environment["CENSUS_TEST_UNRELATED"] == "preserved"


def test_import_linter_config_owns_the_static_dependency_contracts() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)["tool"]["importlinter"]

    assert config["root_packages"] == list(census.REPOSITORY_ROOTS)
    assert config["exclude_type_checking_imports"] is False
    contracts = {contract["id"]: contract for contract in config["contracts"]}
    assert set(contracts) == {
        "domain-no-outer-layer",
        "peak-detection-stays-inner",
        "configuration-stays-inner",
        "output-does-not-read-raw",
        "inner-layers-do-not-import-diagnostics",
        "inner-layers-do-not-import-applications",
        "targeted-untargeted-independent",
        "explanation-readers-stay-readonly",
        "main-chain-layers",
        "acquisition-stays-inner",
        "extraction-backends-delegate",
        "completed-evidence-readers-do-not-load-raw",
        "alignment-domain-models-stay-pure",
        "alignment-consumes-quality-facts",
        "targeted-contracts-stay-headless",
        "delta-mass-method-stays-inward",
        "delta-mass-outside-product-routes",
        "product-routes-ignore-delta-mass",
        "alignment-stays-off-pipelines-and-io",
        "discovery-pipeline-independent-of-targeted-entry",
        "discovery-does-not-load-presets",
        "processing-identity-stays-inner",
        "grouping-and-cell-consume-method-answers",
        "mass-restoration-stays-pure",
        "only-execution-opens-pools",
    }
    direct_only = {
        "only-execution-opens-pools",
        "alignment-domain-models-stay-pure",
        "alignment-consumes-quality-facts",
        "targeted-contracts-stay-headless",
        "delta-mass-method-stays-inward",
        "delta-mass-outside-product-routes",
        "product-routes-ignore-delta-mass",
    }
    special = {
        "targeted-untargeted-independent",
        "main-chain-layers",
        "grouping-and-cell-consume-method-answers",
        *direct_only,
    }
    assert all(
        contract["type"] == "forbidden" and contract["allow_indirect_imports"] is False
        for contract_id, contract in contracts.items()
        if contract_id not in special
    )
    method = contracts["grouping-and-cell-consume-method-answers"]
    assert method["type"] == "forbidden"
    assert method["allow_indirect_imports"] is True
    assert not method.get("ignore_imports")
    for contract_id in direct_only:
        assert contracts[contract_id]["type"] == "forbidden"
        assert contracts[contract_id]["allow_indirect_imports"] is True
    independence = contracts["targeted-untargeted-independent"]
    assert independence["type"] == "independence"
    assert independence["modules"] == [
        "xic_extractor.extraction",
        "xic_extractor.untargeted_execution",
    ]
    assert not independence.get("ignore_imports")
    layers = contracts["main-chain-layers"]
    assert layers["type"] == "layers"
    assert layers["layers"] == [
        "xic_extractor.applications",
        "xic_extractor.untargeted_execution | xic_extractor.extraction",
        "xic_extractor.alignment",
        "xic_extractor.discovery",
        "xic_extractor.peak_detection",
    ]


def test_import_linter_static_scope_does_not_claim_dynamic_resolution(
    tmp_path: Path,
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "middle").mkdir()
    (tmp_path / "outer").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "middle" / "__init__.py").write_text("", encoding="utf-8")
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
    middle
    outer
exclude_type_checking_imports = False

[importlinter:contract:cross-root]
name = App cannot import outer
type = forbidden
source_modules =
    app
forbidden_modules =
    outer
allow_indirect_imports = False
""".lstrip(),
        encoding="utf-8",
    )
    executable = _lint_imports_executable()

    static_result = subprocess.run(
        [executable, "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert static_result.returncode == 1

    (tmp_path / "middle" / "bridge.py").write_text(
        "from outer import helper\n",
        encoding="utf-8",
    )
    (tmp_path / "app" / "bad.py").write_text(
        "from middle import bridge\n",
        encoding="utf-8",
    )
    indirect_result = subprocess.run(
        [executable, "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert indirect_result.returncode == 1

    (tmp_path / "app" / "bad.py").write_text(
        'import importlib\nimportlib.import_module("outer.helper")\n',
        encoding="utf-8",
    )
    dynamic_result = subprocess.run(
        [executable, "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert dynamic_result.returncode == 0


def test_import_linter_ignore_is_exact_and_expires_with_legacy_edge(
    tmp_path: Path,
) -> None:
    for package in ("app", "middle", "outer"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "outer" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "middle" / "bridge.py").write_text(
        "from outer import helper\n",
        encoding="utf-8",
    )
    legacy = tmp_path / "app" / "legacy.py"
    legacy.write_text("from middle import bridge\n", encoding="utf-8")
    config = tmp_path / ".importlinter"
    config.write_text(
        """
[importlinter]
root_packages =
    app
    middle
    outer

[importlinter:contract:cross-root]
name = App cannot import outer
type = forbidden
source_modules =
    app
forbidden_modules =
    outer
allow_indirect_imports = False
ignore_imports =
    app.legacy -> middle.bridge
unmatched_ignore_imports_alerting = error
""".lstrip(),
        encoding="utf-8",
    )
    executable = _lint_imports_executable()

    grandfathered_result = subprocess.run(
        [executable, "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert grandfathered_result.returncode == 0

    new_caller = tmp_path / "app" / "new_caller.py"
    new_caller.write_text("from middle import bridge\n", encoding="utf-8")
    new_caller_result = subprocess.run(
        [executable, "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert new_caller_result.returncode == 1
    assert "app.new_caller" in new_caller_result.stdout

    new_caller.unlink()
    legacy.write_text("VALUE = 1\n", encoding="utf-8")
    stale_ignore_result = subprocess.run(
        [executable, "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert stale_ignore_result.returncode == 1
    assert "no matches for ignored import app.legacy -> middle.bridge" in (
        stale_ignore_result.stdout.lower()
    )


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        (
            'monkeypatch.setattr("xic_extractor.example.missing", 1, raising=False)',
            "permissive-production-patch",
        ),
        (
            "from scripts import run_extraction as cli\n"
            'monkeypatch.setattr(cli, "missing", 1, raising=False)',
            "permissive-production-patch",
        ),
        (
            "def module():\n"
            " import xic_workbench.targeted as target\n"
            " return target\n"
            'monkeypatch.setattr(module(), "missing", 1, raising=False)',
            "permissive-production-patch",
        ),
        (
            'from pathlib import Path\nPath("xic_inspector/api.py").read_text()',
            "production-source-read",
        ),
        (
            "from pathlib import Path\n"
            "import xic_extractor\n"
            "root = Path(xic_extractor.__file__).parent\n"
            'for path in root.rglob("*.py"):\n'
            " path.read_bytes()",
            "production-source-read",
        ),
        (
            "from inspect import getsource as source\n"
            "from xic_extractor import extractor\n"
            "source(extractor)",
            "production-source-read",
        ),
        (
            "from pathlib import Path\n"
            "def read(path):\n"
            " return path.read_text()\n"
            "def test_entry():\n"
            ' read(Path("scripts/run_extraction.py"))',
            "production-source-read",
        ),
        ('open("tools/testing/test_selection.py").read()', "production-source-read"),
        (
            "from pathlib import Path as P\n"
            'P("tools/testing/test_selection.py").open()',
            "production-source-read",
        ),
    ],
)
def test_test_source_gate_rejects_production_shortcuts(tmp_path, source, rule):
    relative = Path("tests/test_example.py")
    path = tmp_path / relative
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")

    problems = census.validate_test_source_boundaries(tmp_path, [relative])

    assert len(problems) == 1
    assert problems[0].startswith("tests/test_example.py:")
    assert rule in problems[0]


@pytest.mark.parametrize(
    "source",
    [
        "import sys\n"
        'monkeypatch.setattr(sys, "frozen", True, raising=False)\n'
        'monkeypatch.delenv("XIC_HOME", raising=False)',
        "from xic_extractor.configuration import paths\n"
        'monkeypatch.setattr(paths.sys, "frozen", True, raising=False)',
        "from xic_extractor import extractor\n"
        'monkeypatch.setattr(extractor, "run", replacement)',
        "from xic_extractor import extractor\n"
        "class Fake:\n def __init__(self, module): pass\n"
        "fake = Fake(extractor)\n"
        'monkeypatch.setattr(fake, "missing", 1, raising=False)',
        "from pathlib import Path\n"
        'Path("tests/test_example.py").read_text()\n'
        "Path(__file__).read_text()",
        "from pathlib import Path\n"
        "def test_gate(tmp_path):\n"
        ' source = tmp_path / "xic_extractor" / "example.py"\n'
        ' source.write_text("import xic_extractor")\n'
        " source.read_text()",
        "from scripts import check_architecture_census as census\n"
        "def test_gate(tmp_path):\n"
        " for path in census.repository_python_paths(tmp_path):\n"
        "  path.read_text()",
        "from pathlib import Path\n"
        'Path("xic_extractor/configuration/data/example.json").read_text()',
    ],
)
def test_test_source_gate_allows_nonproduction_and_strict_patches(tmp_path, source):
    relative = Path("tests/test_example.py")
    path = tmp_path / relative
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")

    assert census.validate_test_source_boundaries(tmp_path, [relative]) == []


@pytest.mark.parametrize(
    "source",
    [
        'monkeypatch.setattr("xic_extractor.example.name", 1, raising=False)',
        'from pathlib import Path\nPath("scripts/run_extraction.py").read_text()',
    ],
)
def test_internal_defense_cli_also_enforces_test_source_boundaries(
    tmp_path, monkeypatch, capsys, source
):
    _defense_gate_repo(tmp_path, "")
    path = tmp_path / "tests" / "test_example.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(census, "ROOT", tmp_path)

    assert census.main(["--internal-defense-base", "HEAD"]) == 1
    assert "tests/test_example.py:" in capsys.readouterr().err
    path.write_text("", encoding="utf-8")
    assert census.main(["--internal-defense-base", "HEAD"]) == 0
