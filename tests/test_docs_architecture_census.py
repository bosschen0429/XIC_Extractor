from __future__ import annotations

import ast
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


@pytest.fixture(scope="module")
def current_architecture_census() -> census.ArchitectureCensus:
    return census.build_architecture_census(ROOT)


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


@pytest.mark.current_repository_census
def test_current_architecture_census_is_valid_and_static_only(
    current_architecture_census: census.ArchitectureCensus,
) -> None:
    observed = current_architecture_census

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


def test_semantic_identifier_gate_scans_python_values_and_maintained_docs(
    tmp_path: Path,
) -> None:
    python_path = Path("xic_extractor/example.py")
    markdown_path = Path("docs/product/example.md")
    for path in (python_path, markdown_path):
        (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / python_path).write_text(
        "LABEL = 'sample_" + "v2'\nTICKET = 'sample_" + "by_455'\n",
        encoding="utf-8",
    )
    (tmp_path / markdown_path).write_text(
        "Current `sample_" + "v2` and `sample_" + "by_455`.\n",
        encoding="utf-8",
    )

    problems = census.validate_semantic_identifiers(
        tmp_path, (python_path, markdown_path)
    )

    version_label = "sample_" + "v2"
    ticket_label = "sample_" + "by_455"
    expected = {
        f"xic_extractor/example.py:1: versioned identifier {version_label}",
        f"xic_extractor/example.py:2: versioned identifier {ticket_label}",
        f"docs/product/example.md:1: versioned identifier {version_label}",
        f"docs/product/example.md:1: versioned identifier {ticket_label}",
    }
    assert expected <= set(problems)


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


def test_runtime_low_level_exports_evaluate_to_exact_immutable_tuples() -> None:
    assert census._runtime_export_invariant_problems() == []


def test_runtime_phase_exception_public_types_are_allowlisted() -> None:
    from xic_extractor.execution import attempt as execution_runtime_owner

    owner_exports = set(execution_runtime_owner.__all__)
    owner_allowlist = census.RUNTIME_EXTERNAL_CALLS_BY_MODULE[
        "xic_extractor.execution.attempt"
    ]

    assert {"RuntimeExecutionPhase", "RuntimeExecutionPhaseError"} <= owner_exports
    assert {
        "RuntimeExecutionPhase",
        "RuntimeExecutionPhaseError",
    } <= owner_allowlist
    assert "RuntimeFinalizationReceipt" not in owner_allowlist


def test_runtime_compaction_is_a_public_fact_projection_not_a_private_entry(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        "from xic_extractor.execution.attempt import "
        "compact_runtime_acquisition_receipt as compact\n"
        "def summarize(receipt):\n    return compact(receipt)\n",
        encoding="utf-8",
    )
    assert census.validate_runtime_boundary_authority(tmp_path, (adapter,)) == []
    source.write_text(
        "from xic_extractor.execution.attempt import "
        "_runtime_acquisition_digest as digest\n"
        "def summarize(receipt):\n    return digest(receipt)\n",
        encoding="utf-8",
    )
    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))
    assert any(
        "_runtime_acquisition_digest is owner-internal" in item for item in problems
    )


def test_runtime_boundary_authority_rejects_projection_and_reducer_bypasses(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/targeted_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution._finalization import (
    propose_attempt_terminal as propose,
)
from xic_extractor.execution.attempt import (
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
from xic_extractor.execution.attempt import RuntimeTelemetrySamples

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
from xic_extractor.execution.attempt import (
    RuntimeCancellationRequest,
    start_runtime_attempt,
)

def start_cancelled(attempt):
    return start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("targeted:a",),
        owner_completeness_attestation="targeted",
        cancellation_request=RuntimeCancellationRequest(attempt=attempt),
    )
""".lstrip(),
        encoding="utf-8",
    )

    assert census.validate_runtime_boundary_authority(tmp_path, (adapter,)) == []


def test_runtime_boundary_authority_rejects_nested_and_branched_partial_paths(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/targeted_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution.attempt import (
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
from xic_extractor.execution._finalization import mark_worker_lost

def bypass(state):
    return mark_worker_lost(state, worker_key="worker:a")
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (script,))

    assert any("mark_worker_lost is owner-internal" in item for item in problems)


def test_runtime_receipt_internal_allowlist_is_path_scoped(
    tmp_path: Path,
) -> None:
    adapter = Path("xic_extractor/extraction/targeted_runtime_adapter.py")
    source = tmp_path / adapter
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution._lifecycle import WorkUnitSettlement

def inspect_settlement(value):
    return (WorkUnitSettlement,)
""".lstrip(),
        encoding="utf-8",
    )

    problems = census.validate_runtime_boundary_authority(tmp_path, (adapter,))

    assert any("WorkUnitSettlement is owner-internal" in item for item in problems)


def test_runtime_boundary_authority_rejects_low_level_public_exports(
    tmp_path: Path,
) -> None:
    runtime = Path("xic_extractor/execution/_finalization.py")
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
    models = Path("xic_extractor/execution/_finalization.py")
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
    models = Path("xic_extractor/execution/_finalization.py")
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
from ..execution.attempt import _apply_runtime_worker_batch as apply_batch

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
            "from ..execution.attempt import "
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
from ..execution.attempt import _apply_runtime_worker_batch as callback

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
        "from ..execution.attempt import _apply_runtime_worker_batch\n",
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
    from ..execution.attempt import RuntimeAttemptAggregate
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
from ..execution._observability import RuntimeObservabilityLedger as Projection
from ..execution.attempt import execute_runtime_attempt

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
from ..execution._observability import RuntimeObservabilityLedger as Projection
from ..execution.attempt import execute_runtime_attempt

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
from ..execution._observability import REQUIRED_RUNTIME_METRIC_NAMES
from ..execution.attempt import execute_runtime_attempt

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
        "caller-authored projection parameter attempt_wall_seconds is forbidden" in item
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
from ..execution.attempt import execute_runtime_attempt

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
    owner = Path("xic_extractor/execution/attempt.py")
    source = tmp_path / owner
    source.parent.mkdir(parents=True)
    source.write_text(
        """
from xic_extractor.execution._finalization import propose_attempt_terminal
from xic_extractor.execution.attempt import RuntimeFinalizationReceipt

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
from xic_extractor.execution._finalization import propose_attempt_terminal
from xic_extractor.execution._observability import (
    RuntimeObservabilityLedger as Ledger,
)
from xic_extractor.execution.attempt import RuntimeFinalizationReceipt

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


@pytest.mark.current_repository_census
def test_grimp_census_records_only_importable_internal_modules(
    current_architecture_census: census.ArchitectureCensus,
) -> None:
    observed = current_architecture_census
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


@pytest.mark.current_repository_census
def test_alignment_to_extraction_imports_are_exact_owner_edges(
    current_architecture_census: census.ArchitectureCensus,
) -> None:
    observed = {
        (reference.source_module, reference.imported_module)
        for reference in current_architecture_census.internal_static_imports
        if reference.source_module.startswith("xic_extractor.alignment")
        and reference.imported_module.startswith("xic_extractor.extraction")
    }

    assert observed == set()


@pytest.mark.current_repository_census
def test_discovery_to_extraction_imports_are_exact_owner_edges(
    current_architecture_census: census.ArchitectureCensus,
) -> None:
    observed = {
        (reference.source_module, reference.imported_module)
        for reference in current_architecture_census.internal_static_imports
        if reference.source_module.startswith("xic_extractor.discovery")
        and reference.imported_module.startswith("xic_extractor.extraction")
    }

    assert observed == set()


@pytest.mark.current_repository_census
def test_discovery_does_not_import_alignment_or_presets(
    current_architecture_census: census.ArchitectureCensus,
) -> None:
    observed = {
        (reference.source_module, reference.imported_module)
        for reference in current_architecture_census.internal_static_imports
        if reference.source_module.startswith("xic_extractor.discovery")
        and reference.imported_module.startswith(
            ("xic_extractor.alignment", "xic_extractor.presets")
        )
    }

    assert observed == set()


@pytest.mark.current_repository_census
def test_default_production_activation_loci_are_exact() -> None:
    production_calls: dict[str, set[Path]] = {
        "run_untargeted_application": set(),
        "complete_untargeted_alignment": set(),
        "execute_untargeted_plan": set(),
        'execute_partition': set(),
    }
    for path in census.repository_python_paths(ROOT):
        if not path.parts or path.parts[0] == "tests":
            continue
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if called in production_calls:
                production_calls[called].add(path)

    outer_callers = production_calls["run_untargeted_application"]
    assert outer_callers == {
        Path("scripts/run_alignment.py"),
        Path("xic_workbench/untargeted.py"),
        Path("xic_extractor/applications/untargeted/provisional_matrix.py"),
    }
    application_path = Path("xic_extractor/applications/untargeted/application.py")
    activation_loci = outer_callers | {application_path}
    assert activation_loci == {
        application_path,
        Path("scripts/run_alignment.py"),
        Path("xic_workbench/untargeted.py"),
        Path("xic_extractor/applications/untargeted/provisional_matrix.py"),
    }
    assert production_calls["complete_untargeted_alignment"] == {application_path}
    assert production_calls["execute_untargeted_plan"] == {
        Path("xic_extractor/untargeted_execution/alignment.py")
    }
    assert production_calls['execute_partition'] == {
        Path("xic_extractor/untargeted_execution/execution.py")
    }


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

    assert not (census._GIT_LOCAL_ENVIRONMENT_VARIABLES & environment.keys())
    assert not any(
        name.startswith("GIT_CONFIG_KEY_") or name.startswith("GIT_CONFIG_VALUE_")
        for name in environment
    )
    assert environment["CENSUS_TEST_UNRELATED"] == "preserved"


def test_import_linter_config_owns_the_static_dependency_contracts() -> None:
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
        "importlinter:contract:inner-layers-do-not-import-diagnostics",
        "importlinter:contract:inner-layers-do-not-import-applications",
        "importlinter:contract:targeted-untargeted-independent",
    }
    independence_section = "importlinter:contract:targeted-untargeted-independent"
    assert all(
        config[section]["type"] == "forbidden"
        and not config[section].getboolean("allow_indirect_imports")
        for section in contracts - {independence_section}
    )
    independence = config[independence_section]
    assert independence["type"] == "independence"
    assert independence["modules"].split() == [
        "xic_extractor.extraction",
        "xic_extractor.untargeted_execution",
    ]
    assert not independence.get("ignore_imports", "").strip()


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


@pytest.mark.current_repository_census
def test_census_records_declarations_without_claiming_runtime_closure(
    current_architecture_census: census.ArchitectureCensus,
) -> None:
    observed = current_architecture_census
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
    assert set(
        census.CENSUS_OWNER_REFS.values()
    ) <= census.repository_tracked_path_keys(ROOT)


@pytest.mark.current_repository_census
def test_optional_json_census_has_content_identity_and_scope(
    tmp_path: Path,
    current_architecture_census: census.ArchitectureCensus,
) -> None:
    observed = current_architecture_census
    output = tmp_path / "architecture_census.json"

    census.write_architecture_census_json(output, observed, ROOT)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "architecture_census"
    assert payload["claim"] == "derived_disposable_not_authority"
    assert payload["static_dependency_scope"] == "grimp_static_import_graph"
    assert payload["static_dependency_gate"] == ".importlinter"
    assert payload["source_identity"]["head"]
    assert len(payload["source_identity"]["census_sha256"]) == 64
    assert isinstance(payload["source_identity"]["worktree_dirty"], bool)
    assert set(payload["census"]) == set(census.CENSUS_OWNER_REFS)


@pytest.mark.current_repository_census
def test_json_census_rejects_nonignored_repo_path(
    current_architecture_census: census.ArchitectureCensus,
) -> None:
    observed = current_architecture_census

    with pytest.raises(ValueError, match="outside the repo or git-ignored"):
        census.write_architecture_census_json(
            ROOT / "docs" / "architecture_census.json",
            observed,
            ROOT,
        )
