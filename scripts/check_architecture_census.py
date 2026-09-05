"""Build and validate a disposable checkout architecture census.

The census records facts derived from tracked or unignored Python source and
``pyproject.toml``. Grimp supplies the static module graph. Import Linter owns
the separate blocking dependency contracts in ``.importlinter``.

Neither surface interprets dynamic loaders or proves installed-package,
console-wrapper, or frozen-application behavior.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import grimp

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOTS = ("xic_extractor", "gui", "scripts", "tools")
DURABLE_PYTHON_ROOTS = (*REPOSITORY_ROOTS, "tests")
STATIC_DEPENDENCY_SCOPE = "grimp_static_import_graph"
CONSTRUCTION_COORDINATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?i:(?:issue|ticket|pull[_ -]?request|pr|task|epic)"
    r"(?:\s+|[_#:-])?\d+|"
    r"(?:phase|milestone|sprint|wave|package|fix)(?:\s+|[_-])?\d+|"
    r"(?:8[_-]?raw|85[_-]?raw|raw[_-]?(?:8|85))|"
    r"gate(?:\s+|[_-])[a-z])|"
    r"[Mm]\d+|[Pp][1-9][A-Za-z]?"
    r")"
    r"(?![A-Za-z0-9])",
)
_HISTORICAL_REVIEW_SCHEMA_IDENTIFIERS = frozenset(
    {
        "issue180_m9_gate_c_manual_review_v1",
        "issue180_m9_gate_c_observation_replay_v1",
    }
)
_CONSTRUCTION_COORDINATE_ORACLE_SAMPLES = frozenset(
    {
        "run_issue367_bundle.py",
        "Issue 367",
        "run_ticket_42_bundle.py",
        "ticket-42",
        "run_m7_bundle.py",
        "M7",
        "run_phase7_bundle.py",
        "phase 7",
        "run_milestone4_bundle.py",
        "milestone-4",
        "run_p7_bundle.py",
        "P7",
        "run_package2_bundle.py",
        "package 2",
        "run_85raw_bundle.py",
        "85RAW",
        "run_raw85_bundle.py",
        "raw85",
        "run_gate_c_bundle.py",
        "Gate C",
        "run_sprint4_bundle.py",
        "sprint 4",
        "run_wave3_bundle.py",
        "wave 3",
        "run_fix3_bundle.py",
        "fix3",
    }
)
_RETAINED_CID_NL_CLOSURE_IDENTIFIERS = frozenset(
    {
        "cid_nl_85raw_universe_closure_summary.json",
        "cid_nl_85raw_universe_closure_checks.tsv",
        "cid_nl_85raw_universe_closure_manifest.tsv",
        "cid_nl_85raw_universe_closure_v1",
        "current_cid_nl_85raw_fix3_successor_universe",
        "85raw_fix3_input_binding",
        "raw_or_85raw_ran",
        "discovery_expanded_85raw_alignment_rows",
    }
)
ALLOWED_CONSTRUCTION_COORDINATES_BY_PATH: Mapping[str, frozenset[str]] = {
    "scripts/check_architecture_census.py": (
        _HISTORICAL_REVIEW_SCHEMA_IDENTIFIERS
        | _CONSTRUCTION_COORDINATE_ORACLE_SAMPLES
        | _RETAINED_CID_NL_CLOSURE_IDENTIFIERS
    ),
    "scripts/check_cid_nl_default_activation_universe_closure.py": (
        _RETAINED_CID_NL_CLOSURE_IDENTIFIERS
    ),
    "scripts/check_cid_nl_discovery_release_slice.py": frozenset(
        {
            "discovery_expanded_85raw_alignment_rows",
            "raw_or_85raw_ran",
        }
    ),
    "scripts/build_cid_nl_default_product_activation.py": frozenset(
        {
            "discovery_expanded_85raw_alignment_rows",
            "raw_or_85raw_ran",
        }
    ),
    "tests/test_docs_architecture_census.py": (
        _HISTORICAL_REVIEW_SCHEMA_IDENTIFIERS | _CONSTRUCTION_COORDINATE_ORACLE_SAMPLES
    ),
    "xic_extractor/alignment/mode_track_table_finalization.py": (
        _HISTORICAL_REVIEW_SCHEMA_IDENTIFIERS
    ),
}
IMMUTABLE_PROVENANCE_PREFIXES = (
    "docs/history/",
    "docs/superpowers/fixtures/",
    "docs/superpowers/validation/",
)
OPERATIONAL_TEST_MODULES = frozenset(
    {
        "tests/mode_track_raw_validation_fixture.py",
        "tests/mode_track_closed_writer_validation_runner.py",
    }
)
_GIT_LOCAL_ENVIRONMENT_VARIABLES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)

ARCHITECTURE_OWNER = "docs/architecture-contract.md"
PUBLIC_CONTRACT_OWNER = "docs/agent/architecture-public-contracts.md"
LAYOUT_OWNER = "docs/project-layout.md"

# Pointers only. The referenced files retain authority for their claims.
CENSUS_OWNER_REFS: Mapping[str, str] = {
    "python_modules": LAYOUT_OWNER,
    "internal_static_imports": ARCHITECTURE_OWNER,
    "project_entrypoint_declarations": PUBLIC_CONTRACT_OWNER,
    "package_discovery_patterns": LAYOUT_OWNER,
}

RUNTIME_CONSTRUCTION_OWNER_PATHS = frozenset(
    {
        "xic_extractor/execution_runtime.py",
        "xic_extractor/execution_runtime_finalization.py",
        "xic_extractor/execution_runtime_finalization_models.py",
        "xic_extractor/execution_runtime_observability.py",
        "xic_extractor/execution_runtime_owner.py",
    }
)
RUNTIME_CONSUMER_PATHS = frozenset(
    {
        "xic_extractor/alignment/runtime_adapter.py",
        "xic_extractor/extraction/targeted_runtime_adapter.py",
        "xic_extractor/extraction/targeted_service.py",
    }
)
RUNTIME_AUTHORITY_MODULES = frozenset(
    {
        "xic_extractor.execution_runtime",
        "xic_extractor.execution_runtime_finalization",
        "xic_extractor.execution_runtime_finalization_models",
        "xic_extractor.execution_runtime_observability",
        "xic_extractor.execution_runtime_owner",
    }
)
RAW_OPEN_RECEIPT_MINTER_REFERENCE = (
    "xic_extractor.acquisition_contract._mint_raw_acquisition_session_receipt"
)
RAW_OPEN_RECEIPT_MINTER_ALLOWED_PATHS = frozenset({"xic_extractor/raw_reader.py"})
RUNTIME_LOW_LEVEL_ALLOWED_EXPORTS: Mapping[str, frozenset[str]] = {
    "xic_extractor/execution_runtime.py": frozenset(
        {
            "AttemptLifecycleContractError",
            "AttemptLifecycleEventKind",
            "AttemptTerminalOutcome",
            "WorkUnitFailureCategory",
            "WorkUnitFailureDetail",
            "WorkUnitSettlementIntegrityFailureKind",
            "WorkUnitTerminalOutcome",
        }
    ),
    "xic_extractor/execution_runtime_finalization.py": frozenset(
        {
            "RuntimeArtifactDisposition",
            "RuntimeFinalizationContractError",
            "RuntimeLifecycleEventKind",
            "RuntimeLifecycleFailureKind",
            "RuntimeResourceCleanupOutcome",
            "RuntimeResourceKind",
            "RuntimeResourceOpenOutcome",
            "RuntimeResourceReservation",
            "SupervisorFinalizationEventKind",
            "canonical_coordinator_terminal_record_bytes",
        }
    ),
    "xic_extractor/execution_runtime_finalization_models.py": frozenset(
        {
            "RuntimeArtifactDisposition",
            "RuntimeFinalizationContractError",
            "RuntimeLifecycleEventKind",
            "RuntimeLifecycleFailureKind",
            "RuntimeResourceCleanupOutcome",
            "RuntimeResourceKind",
            "RuntimeResourceOpenOutcome",
            "SupervisorFinalizationEventKind",
        }
    ),
    "xic_extractor/execution_runtime_observability.py": frozenset(
        {
            "REQUIRED_RUNTIME_METRIC_NAMES",
            "RuntimeMetricAvailability",
            "RuntimeMetricScope",
            "RuntimeObservabilityContractError",
            "RuntimeObservationSubject",
            "canonical_runtime_observability_ledger_bytes",
            "validate_runtime_acquisition_metrics",
        }
    ),
}
RUNTIME_EXTERNAL_CALLS_BY_MODULE: Mapping[str, frozenset[str]] = {
    "xic_extractor.execution_runtime": RUNTIME_LOW_LEVEL_ALLOWED_EXPORTS[
        "xic_extractor/execution_runtime.py"
    ],
    "xic_extractor.execution_runtime_finalization": (
        RUNTIME_LOW_LEVEL_ALLOWED_EXPORTS[
            "xic_extractor/execution_runtime_finalization.py"
        ]
    ),
    "xic_extractor.execution_runtime_finalization_models": (
        RUNTIME_LOW_LEVEL_ALLOWED_EXPORTS[
            "xic_extractor/execution_runtime_finalization_models.py"
        ]
    ),
    "xic_extractor.execution_runtime_observability": (
        RUNTIME_LOW_LEVEL_ALLOWED_EXPORTS[
            "xic_extractor/execution_runtime_observability.py"
        ]
    ),
    "xic_extractor.execution_runtime_owner": frozenset(
        {
            "RuntimeAcquisitionMetrics",
            "RuntimeAcquisitionReceipt",
            "RuntimeArtifactIoSample",
            "RuntimeArtifactRegistrationReceipt",
            "RuntimeCancellationRequest",
            "RuntimeExecutionPhase",
            "RuntimeExecutionPhaseError",
            "RuntimePeakFactCostMetrics",
            "RuntimePeakFactIdentityStatus",
            "RuntimePeakFactOperation",
            "RuntimePeakFactWorkReceipt",
            "RuntimeProcessTreePeakRssSample",
            "RuntimeRawOpenMetrics",
            "RuntimeResourceSettlementReceipt",
            "RuntimeTelemetrySamples",
            "RuntimeWorkerBatchReceipt",
            "RuntimeWorkerPeakRssSample",
            "accept_runtime_finalization_receipt",
            "derive_runtime_acquisition_metrics",
            "derive_runtime_peak_fact_cost_metrics",
            "derive_runtime_raw_open_metrics",
            "execute_runtime_attempt",
            "mint_work_unit_settlement",
            "runtime_observation_event_count",
            "runtime_started_worker_keys",
            "start_runtime_attempt",
        }
    ),
}
RUNTIME_CALLER_PROJECTION_PARAMETERS = frozenset(
    {
        "adapter_chromatogram_call_count",
        "coordinator_record",
        "finalization_record",
        "logical_xic_request_count",
        "observability_ledger",
        "raw_open_count",
        "trace_point_count",
        "xic_batch_count",
    }
)
RUNTIME_CALLER_PROJECTION_TYPES = frozenset(
    {
        "CoordinatorTerminalRecord",
        "RuntimeObservabilityLedger",
        "RuntimeRawOpenMetrics",
        "SupervisorFinalizationRecord",
    }
)
RUNTIME_OWNER_METRIC_NAMES = frozenset(
    {
        "adapter_chromatogram_call_count",
        "artifact_read_bytes",
        "artifact_read_count",
        "artifact_write_bytes",
        "artifact_write_count",
        "attempt_wall_seconds",
        "coordinator_work_seconds",
        "logical_xic_request_count",
        "process_tree_peak_rss_bytes",
        "raw_open_count",
        "stage_or_unit_elapsed_seconds",
        "trace_point_count",
        "worker_peak_rss_bytes",
        "xic_batch_count",
    }
)
RUNTIME_STATE_MODEL_TEST_OWNER_PATHS = frozenset(
    {
        "tests/runtime_observability_fixtures.py",
        "tests/test_execution_runtime_events.py",
        "tests/test_execution_runtime_finalization.py",
        "tests/test_execution_runtime_marker.py",
        "tests/test_execution_runtime_observability.py",
        "tests/test_execution_runtime_owner.py",
    }
)
RUNTIME_STATE_MODEL_TEST_OWNER_MODULES = frozenset(
    path.removesuffix(".py").replace("/", ".")
    for path in RUNTIME_STATE_MODEL_TEST_OWNER_PATHS
)


@dataclass(frozen=True)
class StaticImportReference:
    source_path: str
    source_module: str
    imported_module: str
    line: int


@dataclass(frozen=True)
class FunctionScopeBindings:
    local_names: frozenset[str]
    global_names: frozenset[str]


@dataclass(frozen=True)
class ArchitectureCensus:
    python_modules: tuple[str, ...]
    internal_static_imports: tuple[StaticImportReference, ...]
    project_entrypoint_declarations: tuple[str, ...]
    package_discovery_patterns: tuple[str, ...]

    def serializable(self) -> dict[str, list[str] | list[dict[str, Any]]]:
        return {
            "python_modules": list(self.python_modules),
            "internal_static_imports": [
                asdict(reference) for reference in self.internal_static_imports
            ],
            "project_entrypoint_declarations": list(
                self.project_entrypoint_declarations
            ),
            "package_discovery_patterns": list(self.package_discovery_patterns),
        }


def build_architecture_census(repo_root: Path = ROOT) -> ArchitectureCensus:
    repo_root = repo_root.resolve()
    python_paths = repository_python_paths(repo_root)
    project = _read_toml(repo_root / "pyproject.toml")
    find_config = (
        project.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
    )
    entrypoints = project.get("project", {}).get("scripts", {})
    return ArchitectureCensus(
        python_modules=tuple(
            path.as_posix()
            for path in python_paths
            if path.parts and path.parts[0] in (*REPOSITORY_ROOTS, "tests")
        ),
        internal_static_imports=_build_internal_static_imports(
            repo_root,
            python_paths,
        ),
        project_entrypoint_declarations=tuple(
            f"{name}={target}" for name, target in sorted(entrypoints.items())
        ),
        package_discovery_patterns=tuple(
            f"{key}:{pattern}"
            for key in ("include", "exclude")
            for pattern in find_config.get(key, ())
        ),
    )


def validate_architecture_census(
    repo_root: Path,
    census: ArchitectureCensus,
) -> list[str]:
    """Validate census integrity and executable architecture ownership gates."""
    problems: list[str] = []
    tracked_paths = repository_tracked_path_keys(repo_root)
    for section, owner_ref in CENSUS_OWNER_REFS.items():
        if not getattr(census, section):
            problems.append(f"architecture census section is empty: {section}")
        if owner_ref not in tracked_paths:
            problems.append(
                f"architecture census owner is not an exact tracked path: {owner_ref}"
            )
        owner = _repo_reference_path(repo_root, owner_ref)
        if owner is None or not owner.is_file():
            problems.append(f"architecture census owner does not resolve: {owner_ref}")
    problems.extend(validate_runtime_boundary_authority(repo_root))
    problems.extend(validate_durable_repository_language(repo_root))
    return problems


def validate_durable_repository_language(
    repo_root: Path,
    python_paths: Sequence[Path] | None = None,
) -> list[str]:
    """Reject construction coordinates from durable repository language."""

    paths = (
        repository_tracked_paths(repo_root)
        if python_paths is None
        else tuple(python_paths)
    )
    problems: list[str] = []
    for relative_path in paths:
        path_key = relative_path.as_posix()
        if path_key.startswith(IMMUTABLE_PROVENANCE_PREFIXES):
            continue
        path_match = CONSTRUCTION_COORDINATE_PATTERN.search(path_key)
        if path_match is not None:
            problems.append(
                f"{path_key}: durable repository path uses construction "
                "coordinate identity"
            )
        if (
            relative_path.suffix != ".py"
            or not relative_path.parts
            or relative_path.parts[0] not in DURABLE_PYTHON_ROOTS
        ):
            continue
        source_path = repo_root / relative_path
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{path_key}: durable module language scan failed: {exc}")
            continue
        try:
            tree = ast.parse(source, filename=path_key)
        except SyntaxError as exc:
            problems.append(
                f"{path_key}:{exc.lineno or 1}: durable name scan could not parse "
                f"Python source: {exc.msg}"
            )
            continue
        definition_only = (
            relative_path.parts[0] == "tests"
            and path_key not in OPERATIONAL_TEST_MODULES
        )
        for node in ast.walk(tree):
            candidates: tuple[str, ...] = ()
            if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)):
                candidates = (node.name,)
            elif not definition_only and isinstance(
                node, (ast.Name, ast.Attribute, ast.arg)
            ):
                candidates = (
                    node.id
                    if isinstance(node, ast.Name)
                    else node.attr
                    if isinstance(node, ast.Attribute)
                    else node.arg,
                )
            elif (
                not definition_only
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and not any(character.isspace() for character in node.value)
                and "/" not in node.value
                and "\\" not in node.value
            ):
                candidates = (node.value,)
            for candidate in candidates:
                durable_candidate = candidate
                allowed_coordinates = ALLOWED_CONSTRUCTION_COORDINATES_BY_PATH.get(
                    path_key, ()
                )
                for allowed_coordinate in sorted(
                    allowed_coordinates,
                    key=len,
                    reverse=True,
                ):
                    durable_candidate = durable_candidate.replace(
                        allowed_coordinate, ""
                    )
                if (
                    CONSTRUCTION_COORDINATE_PATTERN.search(durable_candidate)
                    is not None
                ):
                    problems.append(
                        f"{path_key}:{getattr(node, 'lineno', 1)}: durable identifier "
                        "uses construction coordinate identity; name the owned "
                        "role instead"
                    )
    return sorted(set(problems))


def validate_runtime_boundary_authority(
    repo_root: Path,
    python_paths: Sequence[Path] | None = None,
) -> list[str]:
    """Reject runtime construction, projection, export, and lifecycle bypasses."""

    paths = (
        repository_python_paths(repo_root)
        if python_paths is None
        else tuple(python_paths)
    )
    problems: list[str] = []
    for relative_path in paths:
        path_key = relative_path.as_posix()
        source_path = repo_root / relative_path
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"), path_key)
        except (OSError, SyntaxError) as exc:
            problems.append(f"{path_key}: runtime boundary AST scan failed: {exc}")
            continue
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        aliases = _runtime_symbol_aliases(
            tree,
            source_path=relative_path,
            parents=parents,
        )
        scope_bindings = _function_scope_bindings(tree)
        annotation_nodes = _annotation_nodes(tree)
        is_runtime_owner = path_key in RUNTIME_CONSTRUCTION_OWNER_PATHS
        is_test = path_key.startswith("tests/")
        is_state_model_test_owner = path_key in RUNTIME_STATE_MODEL_TEST_OWNER_PATHS
        is_runtime_consumer = (
            not is_test
            and (
                path_key in RUNTIME_CONSUMER_PATHS
                or _imports_runtime_owner_facade(
                    tree,
                    source_path=relative_path,
                )
            )
            and not is_runtime_owner
        )
        problems.extend(
            _runtime_import_boundary_problems(
                tree,
                path_key=path_key,
                source_path=relative_path,
                parents=parents,
                is_runtime_owner=is_runtime_owner,
                is_test=is_test,
                is_state_model_test_owner=is_state_model_test_owner,
            )
        )
        if path_key in RUNTIME_LOW_LEVEL_ALLOWED_EXPORTS:
            allowed_exports = RUNTIME_LOW_LEVEL_ALLOWED_EXPORTS[path_key]
            observed_exports = _exported_symbol_names(tree)
            if observed_exports is None:
                problems.append(
                    _runtime_boundary_problem(
                        path_key,
                        1,
                        (
                            "low-level runtime module must define exactly one "
                            "literal, immutable __all__"
                        ),
                    )
                )
            else:
                for symbol in sorted(observed_exports - allowed_exports):
                    problems.append(
                        _runtime_boundary_problem(
                            path_key,
                            1,
                            f"low-level runtime module publicly exports {symbol}",
                        )
                    )
                missing_exports = allowed_exports - observed_exports
                if missing_exports:
                    problems.append(
                        _runtime_boundary_problem(
                            path_key,
                            1,
                            (
                                "low-level runtime module __all__ is not the exact "
                                f"allowlist; missing={sorted(missing_exports)!r}"
                            ),
                        )
                    )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                reference = _runtime_symbol_reference(
                    node.func,
                    aliases,
                    parents=parents,
                    scope_bindings=scope_bindings,
                )
                call_symbol = _runtime_symbol_leaf(reference)
                if (
                    not is_runtime_owner
                    and not is_state_model_test_owner
                    and _is_runtime_authority_reference(reference)
                    and not _is_allowed_runtime_external_call(reference, call_symbol)
                ):
                    problems.append(
                        _runtime_boundary_problem(
                            path_key,
                            node.lineno,
                            (
                                f"{call_symbol} is owner-internal; use "
                                "execute_runtime_attempt or a typed public fact API"
                            ),
                        )
                    )
            if isinstance(node, (ast.Name, ast.Attribute)):
                raw_receipt_reference = _runtime_symbol_reference(
                    node,
                    aliases,
                    parents=parents,
                    scope_bindings=scope_bindings,
                )
                if (
                    not is_test
                    and path_key not in RAW_OPEN_RECEIPT_MINTER_ALLOWED_PATHS
                    and raw_receipt_reference == RAW_OPEN_RECEIPT_MINTER_REFERENCE
                ):
                    problems.append(
                        _runtime_boundary_problem(
                            path_key,
                            node.lineno,
                            "RAW-open receipt minter is private to raw_reader",
                        )
                    )
            if (
                not is_runtime_owner
                and not is_state_model_test_owner
                and isinstance(node, (ast.Name, ast.Attribute))
            ):
                reference = _runtime_symbol_reference(
                    node,
                    aliases,
                    parents=parents,
                    scope_bindings=scope_bindings,
                )
                referenced_symbol = _runtime_symbol_leaf(reference)
                if (
                    _is_runtime_authority_reference(reference)
                    and not _is_allowed_runtime_external_call(
                        reference,
                        referenced_symbol,
                    )
                    and node not in annotation_nodes
                    and _runtime_reference_escapes_as_value(
                        node,
                        parents=parents,
                        aliases=aliases,
                        scope_bindings=scope_bindings,
                    )
                ):
                    problems.append(
                        _runtime_boundary_problem(
                            path_key,
                            node.lineno,
                            (
                                f"{referenced_symbol} is owner-internal and may "
                                "not escape as a higher-order value"
                            ),
                        )
                    )
            if is_runtime_consumer and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                for argument in _function_arguments(node):
                    if argument.arg in (
                        RUNTIME_CALLER_PROJECTION_PARAMETERS
                        | RUNTIME_OWNER_METRIC_NAMES
                    ):
                        problems.append(
                            _runtime_boundary_problem(
                                path_key,
                                argument.lineno,
                                (
                                    f"caller-authored projection parameter "
                                    f"{argument.arg} is forbidden"
                                ),
                            )
                        )
                    projection_type = _runtime_projection_type_in_annotation(
                        argument.annotation,
                        aliases=aliases,
                        parents=parents,
                        scope_bindings=scope_bindings,
                    )
                    if projection_type is not None:
                        problems.append(
                            _runtime_boundary_problem(
                                path_key,
                                argument.lineno,
                                (
                                    "caller input may not carry owner-derived "
                                    f"projection type {projection_type}"
                                ),
                            )
                        )
            if (
                is_runtime_consumer
                and isinstance(node, ast.ClassDef)
                and _is_dataclass_definition(
                    node,
                    aliases=aliases,
                    parents=parents,
                    scope_bindings=scope_bindings,
                )
            ):
                for statement in node.body:
                    if not isinstance(statement, ast.AnnAssign):
                        continue
                    projection_type = _runtime_projection_type_in_annotation(
                        statement.annotation,
                        aliases=aliases,
                        parents=parents,
                        scope_bindings=scope_bindings,
                    )
                    if projection_type is not None:
                        problems.append(
                            _runtime_boundary_problem(
                                path_key,
                                statement.lineno,
                                (
                                    "generated dataclass input may not carry "
                                    f"owner-derived projection type {projection_type}"
                                ),
                            )
                        )
    if python_paths is None and repo_root.resolve() == ROOT.resolve():
        problems.extend(_runtime_export_invariant_problems())
    return sorted(set(problems))


def repository_tracked_paths(repo_root: Path = ROOT) -> tuple[Path, ...]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_git_subprocess_environment(),
    )
    candidates = sorted(
        Path(line) for line in completed.stdout.splitlines() if line.strip()
    )
    return tuple(path for path in candidates if (repo_root / path).is_file())


def repository_python_paths(repo_root: Path = ROOT) -> tuple[Path, ...]:
    return tuple(
        path for path in repository_tracked_paths(repo_root) if path.suffix == ".py"
    )


def _runtime_export_invariant_problems() -> list[str]:
    problems: list[str] = []
    for path_key, allowed_exports in RUNTIME_LOW_LEVEL_ALLOWED_EXPORTS.items():
        module_name = module_name_from_path(Path(path_key))
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - diagnostic import failure
            problems.append(
                _runtime_boundary_problem(
                    path_key,
                    1,
                    f"low-level runtime module import failed: {exc}",
                )
            )
            continue
        observed = getattr(module, "__all__", None)
        if not isinstance(observed, tuple) or observed != tuple(
            item for item in observed if isinstance(item, str)
        ):
            problems.append(
                _runtime_boundary_problem(
                    path_key,
                    1,
                    "runtime __all__ must evaluate to an immutable tuple of names",
                )
            )
            continue
        if frozenset(observed) != allowed_exports or len(observed) != len(
            allowed_exports
        ):
            problems.append(
                _runtime_boundary_problem(
                    path_key,
                    1,
                    "runtime __all__ does not evaluate to the exact allowlist",
                )
            )
    return problems


def repository_tracked_path_keys(repo_root: Path = ROOT) -> frozenset[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--cached"],
        check=True,
        capture_output=True,
        text=True,
        env=_git_subprocess_environment(),
    )
    return frozenset(line for line in completed.stdout.splitlines() if line)


def module_name_from_path(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _build_internal_static_imports(
    repo_root: Path,
    python_paths: Sequence[Path],
) -> tuple[StaticImportReference, ...]:
    module_paths = {
        module_name_from_path(path): path
        for path in python_paths
        if path.parts and path.parts[0] in REPOSITORY_ROOTS
    }
    with _prepend_sys_path(repo_root):
        graph = grimp.build_graph(
            *REPOSITORY_ROOTS,
            include_external_packages=False,
            cache_dir=None,
            exclude_type_checking_imports=False,
        )
    references: list[StaticImportReference] = []
    allowed_modules = frozenset(module_paths)
    for source_module in sorted(graph.modules & allowed_modules):
        source_path = module_paths[source_module]
        for imported_module in sorted(
            graph.find_modules_directly_imported_by(source_module)
        ):
            if imported_module not in allowed_modules:
                continue
            details = graph.get_import_details(
                importer=source_module,
                imported=imported_module,
            )
            references.extend(
                StaticImportReference(
                    source_path=source_path.as_posix(),
                    source_module=source_module,
                    imported_module=imported_module,
                    line=int(detail["line_number"]),
                )
                for detail in details
            )
    return tuple(sorted(set(references), key=_import_sort_key))


@contextmanager
def _prepend_sys_path(path: Path) -> Iterator[None]:
    value = str(path.resolve())
    sys.path.insert(0, value)
    try:
        yield
    finally:
        sys.path.remove(value)


def _repo_reference_path(repo_root: Path, value: str) -> Path | None:
    relative = Path(value)
    if not value or relative.is_absolute() or relative.drive or ".." in relative.parts:
        return None
    resolved = (repo_root / relative).resolve()
    return resolved if resolved.is_relative_to(repo_root.resolve()) else None


def _runtime_import_boundary_problems(
    tree: ast.AST,
    *,
    path_key: str,
    source_path: Path,
    parents: Mapping[ast.AST, ast.AST],
    is_runtime_owner: bool,
    is_test: bool,
    is_state_model_test_owner: bool,
) -> list[str]:
    problems: list[str] = []
    source_package = _source_package_from_path(source_path)
    for node in ast.walk(tree):
        imported_modules: list[str] = []
        import_line: int | None = None
        if isinstance(node, ast.ImportFrom):
            import_line = node.lineno
            base_module = _resolve_import_from_module(
                source_package=source_package,
                imported_module=node.module,
                level=node.level,
            )
            if base_module is not None:
                imported_modules.append(base_module)
                imported_modules.extend(
                    f"{base_module}.{imported.name}" for imported in node.names
                )
            if (
                base_module in RUNTIME_AUTHORITY_MODULES
                and not is_runtime_owner
                and not is_state_model_test_owner
            ):
                if _has_lexical_scope_ancestor(node, parents=parents):
                    problems.append(
                        _runtime_boundary_problem(
                            path_key,
                            node.lineno,
                            (
                                "function-local runtime authority import is "
                                "forbidden; import public facts at module scope"
                            ),
                        )
                    )
                for imported in node.names:
                    reference = f"{base_module}.{imported.name}"
                    local_name = imported.asname or imported.name
                    has_runtime_use = any(
                        isinstance(candidate, ast.Name)
                        and isinstance(candidate.ctx, ast.Load)
                        and candidate.id == local_name
                        for candidate in ast.walk(tree)
                    )
                    if (
                        imported.name == "*"
                        or imported.name.startswith("_")
                        or (
                            not _is_allowed_runtime_external_call(
                                reference,
                                imported.name,
                            )
                            and not has_runtime_use
                        )
                    ):
                        problems.append(
                            _runtime_boundary_problem(
                                path_key,
                                node.lineno,
                                (
                                    f"{imported.name} may not be imported or "
                                    "re-exported as an owner-internal bridge"
                                ),
                            )
                        )
        elif isinstance(node, ast.Import):
            import_line = node.lineno
            imported_modules.extend(imported.name for imported in node.names)

        if (
            import_line is not None
            and not is_state_model_test_owner
            and any(
                module in RUNTIME_STATE_MODEL_TEST_OWNER_MODULES
                for module in imported_modules
            )
        ):
            caller_kind = "shared test" if is_test else "production caller"
            problems.append(
                _runtime_boundary_problem(
                    path_key,
                    import_line,
                    (
                        f"{caller_kind} may not import a runtime state-model "
                        "test owner; supply typed receipts or telemetry facts"
                    ),
                )
            )
    return problems


def _runtime_projection_type_in_annotation(
    annotation: ast.expr | None,
    *,
    aliases: Mapping[str, str],
    parents: Mapping[ast.AST, ast.AST],
    scope_bindings: Mapping[ast.AST, FunctionScopeBindings],
) -> str | None:
    if annotation is None:
        return None
    roots = [annotation]
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            parsed_annotation = ast.parse(annotation.value, mode="eval")
        except SyntaxError:
            parsed_annotation = None
        if isinstance(parsed_annotation, ast.Expression):
            roots.append(parsed_annotation.body)
    for root in roots:
        for node in ast.walk(root):
            if not isinstance(node, (ast.Name, ast.Attribute)):
                continue
            reference = _runtime_symbol_reference(
                node,
                aliases,
                parents=parents,
                scope_bindings=scope_bindings,
            )
            if reference is None or reference.startswith("local:"):
                dotted_reference = _dotted_expression_name(node)
                if dotted_reference is not None and any(
                    dotted_reference.startswith(f"{module}.")
                    for module in RUNTIME_AUTHORITY_MODULES
                ):
                    reference = dotted_reference
            symbol = _runtime_symbol_leaf(reference)
            if (
                symbol in RUNTIME_CALLER_PROJECTION_TYPES
                and _is_runtime_authority_reference(reference)
            ):
                return symbol
    return None


def _is_dataclass_definition(
    node: ast.ClassDef,
    *,
    aliases: Mapping[str, str],
    parents: Mapping[ast.AST, ast.AST],
    scope_bindings: Mapping[ast.AST, FunctionScopeBindings],
) -> bool:
    for decorator in node.decorator_list:
        candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
        if not isinstance(candidate, (ast.Name, ast.Attribute)):
            continue
        if (
            _runtime_symbol_reference(
                candidate,
                aliases,
                parents=parents,
                scope_bindings=scope_bindings,
            )
            == "dataclasses.dataclass"
        ):
            return True
    return False


def _dotted_expression_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_expression_name(node.value)
        return None if prefix is None else f"{prefix}.{node.attr}"
    return None


def _import_sort_key(
    reference: StaticImportReference,
) -> tuple[str, int, str]:
    return reference.source_path, reference.line, reference.imported_module


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _runtime_symbol_aliases(
    tree: ast.AST,
    *,
    source_path: Path,
    parents: Mapping[ast.AST, ast.AST],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    source_package = _source_package_from_path(source_path)
    for node in ast.walk(tree):
        if _has_lexical_scope_ancestor(node, parents=parents):
            continue
        if isinstance(node, ast.ImportFrom):
            imported_module = _resolve_import_from_module(
                source_package=source_package,
                imported_module=node.module,
                level=node.level,
            )
            if imported_module is None:
                continue
            for imported in node.names:
                aliases[imported.asname or imported.name] = (
                    f"{imported_module}.{imported.name}"
                )
        elif isinstance(node, ast.Import):
            for imported in node.names:
                local_name = imported.asname or imported.name.split(".")[0]
                aliases[local_name] = imported.name if imported.asname else local_name
    return aliases


def _has_lexical_scope_ancestor(
    node: ast.AST,
    *,
    parents: Mapping[ast.AST, ast.AST],
) -> bool:
    current = node
    while (parent := parents.get(current)) is not None:
        if isinstance(
            parent,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            return True
        current = parent
    return False


class _LocalBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.local_names: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.local_names.add(node.id)

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:  # noqa: N802
        self.nonlocal_names.update(node.names)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.local_names.update(
            imported.asname or imported.name.split(".")[0] for imported in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.local_names.update(
            imported.asname or imported.name for imported in node.names
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.local_names.add(node.name)

    def visit_AsyncFunctionDef(  # noqa: N802
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:
        self.local_names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.local_names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        del node

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        del node

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        del node

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        del node

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:  # noqa: N802
        del node


def _function_scope_bindings(
    tree: ast.AST,
) -> Mapping[ast.AST, FunctionScopeBindings]:
    result: dict[ast.AST, FunctionScopeBindings] = {}
    for scope in ast.walk(tree):
        if not isinstance(
            scope,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda),
        ):
            continue
        collector = _LocalBindingCollector()
        arguments = (
            _function_arguments(scope)
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
            else (
                *scope.args.posonlyargs,
                *scope.args.args,
                *scope.args.kwonlyargs,
                *(item for item in (scope.args.vararg, scope.args.kwarg) if item),
            )
        )
        collector.local_names.update(argument.arg for argument in arguments)
        body = scope.body if isinstance(scope.body, list) else (scope.body,)
        for statement in body:
            collector.visit(statement)
        collector.local_names.difference_update(
            collector.global_names | collector.nonlocal_names
        )
        result[scope] = FunctionScopeBindings(
            local_names=frozenset(collector.local_names),
            global_names=frozenset(collector.global_names),
        )
    return result


def _name_is_lexically_local(
    node: ast.AST,
    *,
    name: str,
    parents: Mapping[ast.AST, ast.AST],
    scope_bindings: Mapping[ast.AST, FunctionScopeBindings],
) -> bool:
    current = node
    while (parent := parents.get(current)) is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            in_runtime_scope = (
                current in parent.body
                if isinstance(parent.body, list)
                else current is parent.body
            )
            if in_runtime_scope:
                bindings = scope_bindings[parent]
                if name in bindings.global_names:
                    return False
                if name in bindings.local_names:
                    return True
        current = parent
    return False


def _source_package_from_path(source_path: Path) -> str:
    source_module = module_name_from_path(source_path)
    return (
        source_module
        if source_path.name == "__init__.py"
        else source_module.rpartition(".")[0]
    )


def _resolve_import_from_module(
    *,
    source_package: str,
    imported_module: str | None,
    level: int,
) -> str | None:
    if level == 0:
        return imported_module
    package_parts = source_package.split(".") if source_package else []
    parent_count = level - 1
    if parent_count > len(package_parts):
        return None
    prefix = package_parts[: len(package_parts) - parent_count]
    suffix = imported_module.split(".") if imported_module else []
    resolved = ".".join((*prefix, *suffix))
    return resolved or None


def _imports_runtime_owner_facade(
    tree: ast.AST,
    *,
    source_path: Path,
) -> bool:
    owner_module = "xic_extractor.execution_runtime_owner"
    source_package = _source_package_from_path(source_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_module = _resolve_import_from_module(
                source_package=source_package,
                imported_module=node.module,
                level=node.level,
            )
            if imported_module == owner_module:
                return True
            if imported_module is not None and any(
                f"{imported_module}.{imported.name}" == owner_module
                for imported in node.names
            ):
                return True
        if isinstance(node, ast.Import) and any(
            imported.name == owner_module for imported in node.names
        ):
            return True
    return False


def _runtime_symbol_reference(
    node: ast.expr,
    aliases: Mapping[str, str],
    *,
    parents: Mapping[ast.AST, ast.AST],
    scope_bindings: Mapping[ast.AST, FunctionScopeBindings],
) -> str | None:
    if isinstance(node, ast.Name):
        if _name_is_lexically_local(
            node,
            name=node.id,
            parents=parents,
            scope_bindings=scope_bindings,
        ):
            return f"local:{node.id}"
        return aliases.get(node.id, f"local:{node.id}")
    if isinstance(node, ast.Attribute):
        parent = _runtime_symbol_reference(
            node.value,
            aliases,
            parents=parents,
            scope_bindings=scope_bindings,
        )
        if parent is None or parent.startswith("local:"):
            return None
        return f"{parent}.{node.attr}"
    return None


def _runtime_symbol_leaf(reference: str | None) -> str | None:
    return None if reference is None else reference.rsplit(".", 1)[-1]


def _is_runtime_authority_reference(reference: str | None) -> bool:
    if reference is None or reference.startswith("local:"):
        return False
    module = reference.rsplit(".", 1)[0]
    return module in RUNTIME_AUTHORITY_MODULES


def _is_allowed_runtime_external_call(
    reference: str | None,
    symbol: str | None,
) -> bool:
    if reference is None or symbol is None or reference.startswith("local:"):
        return False
    module = reference.rsplit(".", 1)[0]
    return symbol in RUNTIME_EXTERNAL_CALLS_BY_MODULE.get(module, frozenset())


def _runtime_reference_escapes_as_value(
    node: ast.expr,
    *,
    parents: Mapping[ast.AST, ast.AST],
    aliases: Mapping[str, str],
    scope_bindings: Mapping[ast.AST, FunctionScopeBindings],
) -> bool:
    current: ast.AST = node
    while (parent := parents.get(current)) is not None:
        if isinstance(parent, ast.Call):
            if parent.func is current:
                return False
            callee = (
                parent.func.id
                if isinstance(parent.func, ast.Name)
                else _runtime_symbol_leaf(
                    _runtime_symbol_reference(
                        parent.func,
                        aliases,
                        parents=parents,
                        scope_bindings=scope_bindings,
                    )
                )
            )
            return callee not in {"isinstance", "issubclass"}
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            return parent.value is current
        if isinstance(parent, (ast.Return, ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(
            parent,
            (ast.Dict, ast.List, ast.Set, ast.Starred, ast.Tuple, ast.keyword),
        ):
            current = parent
            continue
        # Any other expression context is an escape, including function defaults,
        # conditional aliases, boolean aliases, decorators, and annotations. Keep
        # the gate fail-closed: the only exemptions above are a direct call and
        # isinstance/issubclass inspection.
        return True
    return True


def _annotation_nodes(tree: ast.AST) -> frozenset[ast.AST]:
    roots: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
    return frozenset(descendant for root in roots for descendant in ast.walk(root))


def _exported_symbol_names(tree: ast.AST) -> frozenset[str] | None:
    assignments: list[tuple[ast.Assign | ast.AnnAssign, ast.expr]] = []
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.Assign):
            if any(_is_dunder_all_target(target) for target in node.targets):
                assignments.append((node, node.value))
        elif isinstance(node, ast.AnnAssign) and _is_dunder_all_target(node.target):
            if node.value is None:
                return None
            assignments.append((node, node.value))
    if len(assignments) != 1:
        return None
    assignment, value_node = assignments[0]
    if isinstance(assignment, ast.Assign) and (
        len(assignment.targets) != 1 or not _is_dunder_all_target(assignment.targets[0])
    ):
        return None
    if _mutates_dunder_all(tree, allowed_assignment=assignment):
        return None
    try:
        value = ast.literal_eval(value_node)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
        return None
    if len(value) != len(set(value)):
        return None
    return frozenset(value)


def _is_dunder_all_target(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "__all__"


def _mutates_dunder_all(
    tree: ast.AST,
    *,
    allowed_assignment: ast.Assign | ast.AnnAssign,
) -> bool:
    allowed_target = (
        allowed_assignment.targets[0]
        if isinstance(allowed_assignment, ast.Assign)
        else allowed_assignment.target
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "__all__":
            return True
        if (
            isinstance(node, ast.Name)
            and node.id == "__all__"
            and node is not allowed_target
        ):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "__all__":
                return True
        if isinstance(node, ast.alias):
            bound_name = node.asname or node.name.split(".")[0]
            if bound_name == "__all__":
                return True
        if isinstance(node, ast.ExceptHandler) and node.name == "__all__":
            return True
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == "__all__":
            return True
        if isinstance(node, ast.MatchMapping) and node.rest == "__all__":
            return True
    return False


def _function_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.arg, ...]:
    positional = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    optional = tuple(
        argument
        for argument in (node.args.vararg, node.args.kwarg)
        if argument is not None
    )
    return (*positional, *optional)


def _runtime_boundary_problem(path: str, line: int, message: str) -> str:
    return f"{path}:{line}: runtime boundary authority: {message}"


def _source_identity(
    repo_root: Path,
    census: ArchitectureCensus,
) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        env=_git_subprocess_environment(),
    )
    head = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=_git_subprocess_environment(),
    ).stdout.strip()
    encoded = json.dumps(
        census.serializable(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "head": head,
        "worktree_dirty": bool(status.stdout.strip()),
        "census_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _output_is_allowed(path: Path, repo_root: Path) -> bool:
    resolved = path.resolve()
    root = repo_root.resolve()
    if not resolved.is_relative_to(root):
        return True
    relative = resolved.relative_to(root)
    completed = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--quiet", "--", str(relative)],
        check=False,
        env=_git_subprocess_environment(),
    )
    return completed.returncode == 0


def _git_subprocess_environment() -> dict[str, str]:
    """Drop caller-repository bindings before running Git against an exact root."""

    environment = dict(os.environ)
    for name in tuple(environment):
        if (
            name in _GIT_LOCAL_ENVIRONMENT_VARIABLES
            or name.startswith("GIT_CONFIG_KEY_")
            or name.startswith("GIT_CONFIG_VALUE_")
        ):
            environment.pop(name)
    return environment


def write_architecture_census_json(
    path: Path,
    census: ArchitectureCensus,
    repo_root: Path = ROOT,
) -> None:
    if not _output_is_allowed(path, repo_root):
        raise ValueError(
            "architecture census JSON must be outside the repo or git-ignored"
        )
    payload = {
        "schema_version": "architecture_census_v2",
        "claim": "derived_disposable_not_authority",
        "static_dependency_scope": STATIC_DEPENDENCY_SCOPE,
        "static_dependency_gate": ".importlinter",
        "source_identity": _source_identity(repo_root, census),
        "owners": dict(CENSUS_OWNER_REFS),
        "census": census.serializable(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json-output",
        type=Path,
        help=(
            "Optional disposable JSON output outside the repo or under an ignored path."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    census = build_architecture_census(ROOT)
    problems = validate_architecture_census(ROOT, census)
    if args.json_output is not None:
        try:
            write_architecture_census_json(args.json_output, census, ROOT)
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        print("Architecture census failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    counts = {key: len(value) for key, value in census.serializable().items()}
    summary = ", ".join(f"{key}={count}" for key, count in counts.items())
    print(f"Architecture census valid ({summary}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
