from __future__ import annotations

import configparser
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import check_architecture_census as census

ROOT = Path(__file__).resolve().parents[1]


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


def test_persisted_semantic_validation_is_proportional_and_root_routed() -> None:
    root_contract = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    public_contract = (
        ROOT / "docs/agent/architecture-public-contracts.md"
    ).read_text(encoding="utf-8")
    normalized_root = " ".join(root_contract.split())
    normalized_public = " ".join(public_contract.split())

    assert "### Persisted Semantic Rehydration" in public_contract
    for required_rule in (
        "reachable boundary",
        "decision-bearing",
        "audit-only provenance",
        "Git review",
        "self-sealing hash chain",
    ):
        assert required_rule in normalized_public

    assert (
        "docs/agent/architecture-public-contracts.md#persisted-semantic-rehydration"
        in root_contract
    )
    assert "A review finding needs a reachable actor" in normalized_root
    assert "Audit-only metadata" in normalized_root


def test_alignment_owner_summaries_match_canonical_contract() -> None:
    paths = {
        relative_path: (ROOT / relative_path).read_text(encoding="utf-8")
        for relative_path in (
            "docs/adr/0003-separate-evidence-archive-explanation-and-delivery.md",
            "docs/agent/architecture-public-contracts.md",
            "docs/architecture-contract.md",
            "docs/product/domain-glossary.md",
            "docs/product/shared-processing-kernel.md",
            "docs/product/targeted-selection.md",
        )
    }

    assert "status: accepted" in paths[
        "docs/adr/0003-separate-evidence-archive-explanation-and-delivery.md"
    ]
    for relative_path, text in paths.items():
        normalized = " ".join(text.split())
        assert "archive implementation locality remains `proposed`" not in normalized, (
            relative_path
        )
        assert "archive-owner seam remains proposed" not in normalized, relative_path
        assert "must be refrozen before its implementation ticket" not in normalized, (
            relative_path
        )

    lifecycle_stages = (
        "SearchScope",
        "Discovery/capture",
        "PeakHypothesis",
        "CrossSample",
        "ModeTrack proposal",
        "ObservationRepair",
        "CrossSample reconciliation",
        "final ModeTrack proposal/application",
        "FinalizedScientificAlignment",
        "projection binding/subjects",
        "ProductionCellDecision",
        "projection completion",
        "result finalizer",
        "archive/explanation/delivery",
    )
    for relative_path in (
        "docs/product/domain-glossary.md",
        "docs/product/shared-processing-kernel.md",
    ):
        lifecycle_lines = [
            line.strip().strip("`")
            for line in paths[relative_path].splitlines()
            if line.strip().startswith("`SearchScope ->")
        ]
        assert len(lifecycle_lines) == 1, relative_path
        text = lifecycle_lines[0]
        cursor = -1
        for stage in lifecycle_stages:
            next_cursor = text.find(stage, cursor + 1)
            assert next_cursor > cursor, (
                f"{relative_path}: missing/out-of-order {stage}"
            )
            cursor = next_cursor

    targeted = " ".join(
        paths["docs/product/targeted-selection.md"].split()
    ).casefold()
    assert "reason rendering" not in targeted
    assert "renders the existing review queue and delivery" not in targeted
    assert "explanation renders the human-facing reasons" in targeted
    assert "| `explanation/` | Human reason rendering" in paths[
        "docs/architecture-contract.md"
    ]


def test_runtime_low_level_exports_evaluate_to_exact_immutable_tuples() -> None:
    assert census._runtime_export_invariant_problems() == []


def test_runtime_boundary_authority_rejects_projection_and_reducer_bypasses(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/targeted_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution_runtime_finalization import (
    propose_attempt_terminal as propose,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeFinalizationReceipt as Receipt,
    apply_runtime_worker_batch as apply_batch,
)

FinalReceipt = Receipt

def renamed_partial_path(
    aggregate,
    batch,
    observability_ledger,
    logical_xic_request_count,
):
    applied = apply_batch(aggregate, batch)
    return FinalReceipt(propose(applied.state), observability_ledger)
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "apply_runtime_worker_batch is owner-internal" in problem
        for problem in problems
    )
    assert any("observability_ledger" in problem for problem in problems)
    assert any("logical_xic_request_count" in problem for problem in problems)
    assert any(
        "RuntimeFinalizationReceipt is owner-internal" in problem
        for problem in problems
    )
    assert any(
        "propose_attempt_terminal is owner-internal" in problem for problem in problems
    )


def test_runtime_boundary_authority_is_origin_aware_for_same_name_methods(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/targeted_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution_runtime_owner import RuntimeTelemetrySamples

class LocalHelper:
    def settle_work_unit(self):
        return "unrelated"

def inspect_local(helper: LocalHelper) -> str:
    return helper.settle_work_unit()
""".lstrip(),
        encoding="utf-8",
    )

    assert census.validate_runtime_boundary_authority(tmp_path, (adapter,)) == []


def test_runtime_boundary_allows_owner_bound_cancellation_start_fact(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/targeted_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution_runtime_owner import (
    RuntimeCancellationRequest,
    start_runtime_attempt,
)

def start_cancelled(attempt):
    return start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("targeted:a",),
        owner_completeness_attestation="targeted:v1",
        cancellation_request=RuntimeCancellationRequest(attempt=attempt),
    )
""".lstrip(),
        encoding="utf-8",
    )

    assert census.validate_runtime_boundary_authority(tmp_path, (adapter,)) == []


def test_runtime_boundary_authority_rejects_nested_and_branched_partial_paths(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/alignment/runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution_runtime_owner import (
    _apply_runtime_worker_batch as apply_batch,
    _prepare_runtime_attempt as prepare,
    execute_runtime_attempt,
)

def _settle_only(aggregate, batch):
    return apply_batch(aggregate, batch)

def nested_bypass(aggregate, batch):
    return _settle_only(aggregate, batch)

def branched_bypass(runtime, reservations, worker, telemetry, persister, skip):
    if skip:
        return prepare(runtime, reservations)
    return execute_runtime_attempt(
        runtime,
        reservations,
        worker_batch_provider=worker,
        telemetry_provider=telemetry,
        persister=persister,
    )
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "_apply_runtime_worker_batch is owner-internal" in item for item in problems
    )
    assert any(
        "_prepare_runtime_attempt is owner-internal" in item for item in problems
    )


def test_runtime_boundary_authority_scans_scripts_for_low_level_calls(
    tmp_path: Path,
) -> None:
    script = Path("scripts/runtime_bypass.py")
    source = tmp_path / script
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution_runtime_finalization import mark_worker_lost

def bypass(state):
    return mark_worker_lost(state, worker_key="worker:a")
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (script,))

    assert any("mark_worker_lost is owner-internal" in item for item in problems)


def test_runtime_boundary_authority_rejects_low_level_public_exports(
    tmp_path: Path,
) -> None:
    runtime = Path("xic_extractor/execution_runtime_finalization.py")
    source = tmp_path / runtime
    source.parent.mkdir(parents=True)
    source.write_text(
        '__all__ = ("propose_attempt_terminal", "RuntimeResourceKind")\n',
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (runtime,))

    assert any("publicly exports propose_attempt_terminal" in item for item in problems)


def test_runtime_boundary_authority_rejects_finalization_model_record_exports(
    tmp_path: Path,
) -> None:
    models = Path("xic_extractor/execution_runtime_finalization_models.py")
    source = tmp_path / models
    source.parent.mkdir(parents=True)
    source.write_text(
        '__all__ = ("CoordinatorTerminalRecord", "RuntimeResourceKind")\n',
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (models,))

    assert any(
        "publicly exports CoordinatorTerminalRecord" in item for item in problems
    )


@pytest.mark.parametrize(
    "source_text",
    (
        "class RuntimeResourceKind: pass\n",
        (
            'BASE = ["RuntimeResourceKind"]\n'
            '__all__ = BASE + ["CoordinatorTerminalRecord"]\n'
        ),
        (
            '__all__ = ("RuntimeResourceKind",)\n'
            '__all__.append("CoordinatorTerminalRecord")\n'
        ),
        (
            '__all__ = ("RuntimeResourceKind",)\n'
            "if should_expand:\n"
            '    __all__ = (*__all__, "CoordinatorTerminalRecord")\n'
        ),
        ('__all__ = ("RuntimeResourceKind",)\nif remove_exports:\n    del __all__\n'),
        (
            '__all__ = exported = ("RuntimeResourceKind",)\n'
            'exported.append("CoordinatorTerminalRecord")\n'
        ),
        (
            '__all__ = ("RuntimeResourceKind",)\n'
            "exported = __all__\n"
            'exported.append("CoordinatorTerminalRecord")\n'
        ),
        (
            '__all__ = ("RuntimeResourceKind",)\n'
            "def __all__():\n"
            '    return ["CoordinatorTerminalRecord"]\n'
        ),
        (
            '__all__ = ("RuntimeResourceKind",)\n'
            'globals()["__all__"].append("CoordinatorTerminalRecord")\n'
        ),
    ),
)
def test_runtime_boundary_authority_requires_literal_immutable_all(
    tmp_path: Path,
    source_text: str,
) -> None:
    models = Path("xic_extractor/execution_runtime_finalization_models.py")
    source = tmp_path / models
    source.parent.mkdir(parents=True)
    source.write_text(source_text, encoding="utf-8")

    problems = census.validate_runtime_boundary_authority(tmp_path, (models,))

    assert any("exactly one literal, immutable __all__" in item for item in problems)


def test_runtime_boundary_authority_resolves_relative_higher_order_bypasses(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/targeted_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from functools import partial
from ..execution_runtime_owner import _apply_runtime_worker_batch as apply_batch

def callback_bypass(aggregate, batch, register):
    deferred = partial(apply_batch, aggregate, batch)
    register(callback=apply_batch)
    return deferred
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "_apply_runtime_worker_batch is owner-internal" in item
        and "higher-order value" in item
        for item in problems
    )


@pytest.mark.parametrize(
    "bypass_source",
    (
        (
            "def bypass(aggregate, batch, "
            "callback=_apply_runtime_worker_batch):\n"
            "    return callback(aggregate, batch)\n"
        ),
        (
            "def bypass(flag, safe):\n"
            "    callback = _apply_runtime_worker_batch if flag else safe\n"
            "    return callback\n"
        ),
        (
            "def bypass(flag, safe):\n"
            "    callback = flag and _apply_runtime_worker_batch or safe\n"
            "    return callback\n"
        ),
    ),
)
def test_runtime_boundary_authority_rejects_indirect_callback_aliases(
    tmp_path: Path,
    bypass_source: str,
) -> None:
    adapter = Path("xic_extractor/extraction/future_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        (
            "from ..execution_runtime_owner import "
            "_apply_runtime_worker_batch\n\n"
            f"{bypass_source}"
        ),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "_apply_runtime_worker_batch is owner-internal" in item
        and "higher-order value" in item
        for item in problems
    )


def test_runtime_boundary_alias_resolution_is_lexically_scoped(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/future_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from ..execution_runtime_owner import _apply_runtime_worker_batch as callback

def leak(register):
    register(callback)

def unrelated(safe):
    callback = safe
    return callback()
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))
    higher_order = [
        item
        for item in problems
        if "_apply_runtime_worker_batch is owner-internal" in item
        and "higher-order value" in item
    ]

    assert len(higher_order) == 1
    assert f"{adapter.as_posix()}:4:" in higher_order[0]


def test_runtime_boundary_rejects_owner_internal_bridge_imports(
    tmp_path: Path,
) -> None:
    bridge = Path("xic_extractor/extraction/runtime_bridge.py")
    adapter = Path("xic_extractor/extraction/future_runtime_adapter.py")
    bridge_source = tmp_path / bridge
    adapter_source = tmp_path / adapter
    bridge_source.parent.mkdir(parents=True)
    bridge_source.write_text(
        "from ..execution_runtime_owner import _apply_runtime_worker_batch\n",
        encoding="utf-8",
    )
    adapter_source.write_text(
        """
from .runtime_bridge import _apply_runtime_worker_batch

def bypass(aggregate, batch):
    return _apply_runtime_worker_batch(aggregate, batch)
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(
        tmp_path,
        (bridge, adapter),
    )

    assert any(
        bridge.as_posix() in item
        and "may not be imported or re-exported as an owner-internal bridge" in item
        for item in problems
    )


def test_runtime_boundary_rejects_function_local_authority_imports(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/future_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
def bypass(state):
    from ..execution_runtime_owner import RuntimeAttemptAggregate
    return RuntimeAttemptAggregate(state=state)
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "function-local runtime authority import is forbidden" in item
        for item in problems
    )


def test_runtime_boundary_rejects_typed_projection_inputs_by_position(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/future_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from dataclasses import dataclass
from ..execution_runtime_observability import RuntimeObservabilityLedger as Projection
from ..execution_runtime_owner import execute_runtime_attempt

def bypass(runtime, reservations, projection: Projection):
    return execute_runtime_attempt(runtime, reservations), projection

@dataclass
class CallerEnvelope:
    renamed: Projection
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "caller input may not carry owner-derived projection type "
        "RuntimeObservabilityLedger" in item
        for item in problems
    )
    assert any(
        "generated dataclass input may not carry owner-derived projection type "
        "RuntimeObservabilityLedger" in item
        for item in problems
    )


def test_runtime_boundary_rejects_quoted_projection_and_aliased_dataclass(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/future_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from dataclasses import dataclass as record
from ..execution_runtime_observability import RuntimeObservabilityLedger as Projection
from ..execution_runtime_owner import execute_runtime_attempt

def bypass(runtime, projection: "Projection | None"):
    return execute_runtime_attempt(runtime), projection

@record(frozen=True)
class CallerEnvelope:
    renamed: "Projection"
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "caller input may not carry owner-derived projection type "
        "RuntimeObservabilityLedger" in item
        for item in problems
    )
    assert any(
        "generated dataclass input may not carry owner-derived projection type "
        "RuntimeObservabilityLedger" in item
        for item in problems
    )


def test_runtime_boundary_distinguishes_metric_vocabulary_from_authority(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/future_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from ..execution_runtime_observability import REQUIRED_RUNTIME_METRIC_NAMES
from ..execution_runtime_owner import execute_runtime_attempt

def untrusted_copy(runtime):
    receipt = execute_runtime_attempt(runtime)
    from_schema = {name: 0 for name in REQUIRED_RUNTIME_METRIC_NAMES}
    from_receipt = {
        metric.name: 0 for metric in receipt.observability_ledger.metrics
    }
    return from_schema, from_receipt

def forbidden_ingress(runtime, attempt_wall_seconds):
    return execute_runtime_attempt(runtime), attempt_wall_seconds
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "caller-authored projection parameter attempt_wall_seconds is forbidden"
        in item
        for item in problems
    )
    assert not any("canonical owner metric label" in item for item in problems)


def test_runtime_boundary_authority_detects_relative_import_future_consumers(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/future_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from ..execution_runtime_owner import execute_runtime_attempt

def bypass(observability_ledger):
    return execute_runtime_attempt(observability_ledger)
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any(
        "caller-authored projection parameter observability_ledger is forbidden" in item
        for item in problems
    )


def test_runtime_boundary_authority_allows_owner_facade_construction(
    tmp_path: Path,
) -> None:
    owner = Path("xic_extractor/execution_runtime_owner.py")
    source = tmp_path / owner
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution_runtime_finalization import propose_attempt_terminal
from xic_extractor.execution_runtime_owner import RuntimeFinalizationReceipt

def finalize(state, supervisor):
    return RuntimeFinalizationReceipt(
        propose_attempt_terminal(state),
        supervisor,
    )
""".lstrip(),
        encoding="utf-8",
    )

    assert census.validate_runtime_boundary_authority(tmp_path, (owner,)) == []


def test_runtime_boundary_authority_rejects_shared_ledger_test_oracles(
    tmp_path: Path,
) -> None:
    caller = Path("tests/test_runtime_consumer.py")
    source = tmp_path / caller
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from tests.runtime_observability_fixtures import make_ledger
from xic_extractor.execution_runtime_finalization import propose_attempt_terminal
from xic_extractor.execution_runtime_observability import (
    RuntimeObservabilityLedger as Ledger,
)
from xic_extractor.execution_runtime_owner import RuntimeFinalizationReceipt

def build(attempt):
    return make_ledger(attempt)

def forge(attempt):
    return Ledger(attempt=attempt, work_unit_keys=(), events=(), metrics=())

def forge_finalization(state, supervisor):
    return RuntimeFinalizationReceipt(
        propose_attempt_terminal(state),
        supervisor,
    )
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (caller,))

    assert any(
        "may not import a runtime state-model test owner" in problem
        for problem in problems
    )
    assert any(
        "RuntimeObservabilityLedger is owner-internal" in problem
        for problem in problems
    )
    assert any(
        "RuntimeFinalizationReceipt is owner-internal" in problem
        for problem in problems
    )
    assert any(
        "propose_attempt_terminal is owner-internal" in problem for problem in problems
    )


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

    assert not (
        census._GIT_LOCAL_ENVIRONMENT_VARIABLES & environment.keys()
    )
    assert not any(
        name.startswith("GIT_CONFIG_KEY_")
        or name.startswith("GIT_CONFIG_VALUE_")
        for name in environment
    )
    assert environment["CENSUS_TEST_UNRELATED"] == "preserved"


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
        and not config[section].getboolean("allow_indirect_imports")
        for section in contracts
    )
    expected_migration_debt = {
        "importlinter:contract:peak-detection-stays-inner": (
            "xic_extractor.peak_detection.candidate_selection "
            "-> xic_extractor.decision_policy",
            "xic_extractor.peak_detection.decision_policy "
            "-> xic_extractor.decision_policy",
            "xic_extractor.peak_detection.evidence_facts "
            "-> xic_extractor.evidence_semantics",
            "xic_extractor.peak_detection.hypotheses "
            "-> xic_extractor.evidence_semantics",
            "xic_extractor.peak_detection.hypotheses -> xic_extractor.neutral_loss",
            "xic_extractor.peak_detection.model_selection "
            "-> xic_extractor.decision_policy",
            "xic_extractor.peak_detection.model_selection "
            "-> xic_extractor.evidence_semantics",
            "xic_extractor.peak_detection.selection_decision "
            "-> xic_extractor.evidence_semantics",
        ),
        "importlinter:contract:output-does-not-read-raw": (
            "xic_extractor.output.csv_writers -> xic_extractor.neutral_loss",
            "xic_extractor.output.csv_writers -> xic_extractor.signal_processing",
            "xic_extractor.output.excel_pipeline -> xic_extractor.extractor",
            "xic_extractor.output.messages -> xic_extractor.neutral_loss",
            "xic_extractor.output.messages -> xic_extractor.signal_processing",
            "xic_extractor.output.peak_candidate_boundaries "
            "-> xic_extractor.extraction.peak_candidate_boundaries",
            "xic_extractor.output.peak_candidates "
            "-> xic_extractor.extraction.peak_candidate_table",
            "xic_extractor.output.target_pair_rt_auto_reselection "
            "-> xic_extractor.extraction.paired_area_ratio_projection",
            "xic_extractor.output.target_pair_rt_auto_reselection "
            "-> xic_extractor.peak_detection.model_selection",
        ),
    }
    for section, expected_ignores in expected_migration_debt.items():
        assert (
            tuple(
                line
                for line in config[section]["ignore_imports"].splitlines()
                if line.strip()
            )
            == expected_ignores
        )
        assert config[section]["unmatched_ignore_imports_alerting"] == "error"
    assert all(
        "ignore_imports" not in config[section]
        for section in contracts - expected_migration_debt.keys()
    )


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
        check=False,
    )
    assert stale_ignore_result.returncode == 1
    assert "no matches for ignored import app.legacy -> middle.bridge" in (
        stale_ignore_result.stdout.lower()
    )


def test_census_records_declarations_without_claiming_runtime_closure() -> None:
    observed = census.build_architecture_census(ROOT)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    find = project["tool"]["setuptools"]["packages"]["find"]

    assert observed.project_entrypoint_declarations == tuple(
        f"{name}={target}" for name, target in sorted(scripts.items())
    )
    assert observed.package_discovery_patterns == tuple(
        f"{key}:{pattern}" for key in ("include", "exclude") for pattern in find[key]
    )
    assert {pattern.removesuffix("*") for pattern in find["include"]} == set(
        census.REPOSITORY_ROOTS
    )


def test_raw_open_receipt_private_minter_is_restricted_to_raw_reader(
    tmp_path: Path,
) -> None:
    forbidden = Path("xic_extractor/discovery/forged_raw_open.py")
    forbidden_source = tmp_path / forbidden
    forbidden_source.parent.mkdir(parents=True)
    forbidden_source.write_text(
        (
            "from xic_extractor.acquisition_contract import "
            "_mint_raw_acquisition_session_receipt\n\n"
            "def forge(capability):\n"
            "    return _mint_raw_acquisition_session_receipt(capability)\n"
        ),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(
        tmp_path,
        (forbidden,),
    )

    assert any(
        "RAW-open receipt minter is private to raw_reader" in problem
        for problem in problems
    )


def test_raw_open_receipt_private_minter_allows_raw_reader(tmp_path: Path) -> None:
    allowed = Path("xic_extractor/raw_reader.py")
    allowed_source = tmp_path / allowed
    allowed_source.parent.mkdir(parents=True)
    allowed_source.write_text(
        (
            "from xic_extractor.acquisition_contract import "
            "_mint_raw_acquisition_session_receipt\n\n"
            "def mint_after_open(capability):\n"
            "    return _mint_raw_acquisition_session_receipt(capability)\n"
        ),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(
        tmp_path,
        (allowed,),
    )

    assert not any(
        "RAW-open receipt minter is private to raw_reader" in problem
        for problem in problems
    )


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
