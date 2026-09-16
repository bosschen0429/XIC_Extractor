import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from xic_extractor.alignment.identity_coherence.models import (
    CandidateIdentityMatch,
    CandidateTrace,
    CellCandidateEvidence,
    CellEvidenceResult,
    EngineeringConfig,
    IdentityCoherenceConfig,
    IdentityDecisionSummary,
    RtCenterResult,
    SeedCandidateEvidence,
    SeedGateConfig,
    SeedGateResult,
)
from xic_extractor.alignment.identity_coherence.request_builder import (
    build_identity_coherence_request,
)
from xic_extractor.alignment.identity_coherence.schema import (
    IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS,
    IDENTITY_COHERENCE_CONTROL_COLUMNS,
    IDENTITY_COHERENCE_DECISION_COLUMNS,
    IDENTITY_COHERENCE_REQUEST_COLUMNS,
    AreaHeightStatus,
    BaselineAuditStatus,
    CellAssessmentStatus,
    CellBlockedReason,
    CellDataQualityReason,
    CellIdentityBasis,
    CellIdentityTier,
    ControlStatus,
    ControlType,
    DecisionReason,
    DecoyGenerationMethod,
    EvidenceStage,
    FragmentMatchStatus,
    FragmentObservationMode,
    IdentityDecision,
    NonRtIdentityResult,
    PositiveControlMappingStatus,
    RequestCandidateIdentityStatus,
    RequestIdentityCompletenessStatus,
    RtCenterDecision,
    RtGateStatus,
    SeedGateClass,
    SeedRejectReason,
    ShapeAuditStatus,
    ShapeReferenceBasis,
    ShapeStatus,
    WeakBasisReason,
    WidthStatus,
)

IDENTITY_COHERENCE_PACKAGE = "xic_extractor.alignment.identity_coherence"
INLINE_ADAPTER_MODULES = (
    "identity_coherence_adapter",
    "identity_coherence_source_mapping",
    "identity_coherence_trace_retrieval",
    "identity_coherence_record_builder",
)
FORBIDDEN_IDENTITY_COHERENCE_SURFACES = (
    "controls",
    "control_evaluation",
    "control_manifest",
    "control_models",
    "control_rows",
    "decoy_controls",
    "output",
    "positive_controls",
)
OUTPUT_MODULE_FORBIDDEN_RELATIVE_SURFACES = {
    "output_formatting.py": (),
    "output_models.py": (
        "controls",
        "output",
        "output_projection",
        "output_summary",
        "output_summary_model",
        "output_validation",
        "output_writers",
    ),
    "output_projection.py": (
        "controls",
        "output",
        "output_summary",
        "output_summary_model",
        "output_writers",
    ),
    "output_validation.py": (
        "controls",
        "output",
        "output_projection",
        "output_summary",
        "output_summary_model",
        "output_writers",
    ),
    "output_summary_model.py": (
        "controls",
        "output",
        "output_projection",
        "output_summary",
        "output_writers",
    ),
    "output_summary.py": (
        "controls",
        "output",
        "output_projection",
        "output_validation",
        "output_writers",
    ),
    "output_writers.py": (
        "controls",
        "output",
    ),
}
FORBIDDEN_OUTPUT_ABSOLUTE_IMPORT_PREFIXES = (
    "scripts",
    "xic_extractor.raw_reader",
    "xic_extractor.output",
    "xic_extractor.gui",
    # The root-level identity_coherence_* shims were deleted; keep them
    # forbidden so the flat spelling cannot come back as an import target.
    "xic_extractor.alignment.identity_coherence_adapter",
    "xic_extractor.alignment.identity_coherence_source_mapping",
    "xic_extractor.alignment.identity_coherence_trace_retrieval",
    "xic_extractor.alignment.identity_coherence_record_builder",
    "xic_extractor.alignment.process_backend",
    "xic_extractor.alignment.owner_backfill",
    "xic_extractor.alignment.backfill",
)
FORBIDDEN_CONTROL_RELATIVE_SURFACES = (
    "controls",
    "output",
    "output_formatting",
    "output_go_no_go",
    "output_models",
    "output_projection",
    "output_summary",
    "output_summary_model",
    "output_validation",
    "output_writers",
)
CONTROL_MODULE_FORBIDDEN_RELATIVE_SURFACES = {
    "controls.py": FORBIDDEN_CONTROL_RELATIVE_SURFACES,
    "control_models.py": FORBIDDEN_CONTROL_RELATIVE_SURFACES,
    "control_manifest.py": (
        *FORBIDDEN_CONTROL_RELATIVE_SURFACES,
        "control_evaluation",
        "decoy_controls",
        "positive_controls",
    ),
    "positive_controls.py": (
        *FORBIDDEN_CONTROL_RELATIVE_SURFACES,
        "control_evaluation",
        "decoy_controls",
    ),
    "decoy_controls.py": (
        *FORBIDDEN_CONTROL_RELATIVE_SURFACES,
        "control_evaluation",
        "positive_controls",
    ),
    "control_evaluation.py": FORBIDDEN_CONTROL_RELATIVE_SURFACES,
    "control_rows.py": (
        *FORBIDDEN_CONTROL_RELATIVE_SURFACES,
        "control_evaluation",
        "control_manifest",
        "decoy_controls",
        "positive_controls",
    ),
}


def _is_forbidden_identity_coherence_surface(surface_name: str) -> bool:
    return (
        surface_name in FORBIDDEN_IDENTITY_COHERENCE_SURFACES
        or surface_name.startswith("output_")
    )


def _is_forbidden_identity_coherence_module(module_name: str) -> bool:
    if module_name == IDENTITY_COHERENCE_PACKAGE:
        return True
    package_prefix = f"{IDENTITY_COHERENCE_PACKAGE}."
    if not module_name.startswith(package_prefix):
        return False
    relative_name = module_name.removeprefix(package_prefix)
    surface_name = relative_name.split(".", maxsplit=1)[0]
    return _is_forbidden_identity_coherence_surface(surface_name)


def _is_relative_forbidden_surface(module_name: str) -> bool:
    surface = module_name.split(".", maxsplit=1)[0]
    return _is_forbidden_identity_coherence_surface(surface)


def test_identity_coherence_domain_does_not_import_inline_adapter() -> None:
    root = Path(__file__).resolve().parents[3]
    package_dir = root / "xic_extractor" / "alignment" / "identity_coherence"
    forbidden = tuple(
        token
        for module_name in INLINE_ADAPTER_MODULES
        for token in (
            module_name,
            f"from xic_extractor.alignment.{module_name}",
        )
    )
    for path in package_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden), path


def test_inline_adapter_does_not_modify_raw_or_backfill_modules() -> None:
    root = Path(__file__).resolve().parents[3]
    # The flat identity_coherence_adapter.py shim is gone; hold the invariant
    # on the module that actually carries the adapter behaviour.
    adapter_text = (
        root
        / "xic_extractor"
        / "alignment"
        / "identity_coherence"
        / "adapter.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "from xic_extractor.raw_reader import",
        "import xic_extractor.raw_reader",
        "from xic_extractor.alignment.owner_backfill import",
        "from xic_extractor.alignment.backfill import",
        "source_for_owner_backfill_backend",
        "timed_owner_backfill_sources",
    )
    assert not any(token in adapter_text for token in forbidden)


def _forbidden_identity_coherence_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_identity_coherence_module(alias.name):
                    violations.append(f"line {node.lineno}: import {alias.name}")
            continue

        if not isinstance(node, ast.ImportFrom):
            continue

        module_name = node.module or ""
        if node.level:
            if module_name and _is_relative_forbidden_surface(module_name):
                violations.append(f"line {node.lineno}: from .{module_name} import")
            elif not module_name:
                for alias in node.names:
                    if _is_forbidden_identity_coherence_surface(alias.name):
                        violations.append(
                            f"line {node.lineno}: from . import {alias.name}"
                        )
            continue

        if _is_forbidden_identity_coherence_module(module_name):
            violations.append(f"line {node.lineno}: from {module_name} import")
        elif module_name == IDENTITY_COHERENCE_PACKAGE:
            for alias in node.names:
                violations.append(
                    f"line {node.lineno}: from {module_name} import {alias.name}"
                )
        elif module_name == "xic_extractor.alignment":
            for alias in node.names:
                if alias.name == "identity_coherence":
                    violations.append(
                        f"line {node.lineno}: from {module_name} import {alias.name}"
                    )

    return violations


def _identity_coherence_import_surfaces(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    imports: list[tuple[int, str]] = []
    package_prefix = f"{IDENTITY_COHERENCE_PACKAGE}."

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == IDENTITY_COHERENCE_PACKAGE:
                    imports.append((node.lineno, "__package__"))
                elif alias.name.startswith(package_prefix):
                    relative_name = alias.name.removeprefix(package_prefix)
                    imports.append((node.lineno, relative_name.split(".", 1)[0]))
            continue

        if not isinstance(node, ast.ImportFrom):
            continue

        module_name = node.module or ""
        if node.level:
            if module_name:
                imports.append((node.lineno, module_name.split(".", 1)[0]))
            elif node.level == 1:
                imports.extend(
                    (node.lineno, alias.name.split(".", 1)[0])
                    for alias in node.names
                )
            continue

        if module_name.startswith(package_prefix):
            relative_name = module_name.removeprefix(package_prefix)
            imports.append((node.lineno, relative_name.split(".", 1)[0]))
        elif module_name == IDENTITY_COHERENCE_PACKAGE:
            imports.extend(
                (node.lineno, alias.name.split(".", 1)[0])
                for alias in node.names
            )
        elif module_name == "xic_extractor.alignment":
            imports.extend(
                (node.lineno, "__package__")
                for alias in node.names
                if alias.name == "identity_coherence"
            )

    return imports


def _absolute_import_modules(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            imports.append((node.lineno, node.module))
            imports.extend(
                (node.lineno, f"{node.module}.{alias.name}")
                for alias in node.names
            )
    return imports


def test_schema_constants_have_no_duplicates():
    for columns in (
        IDENTITY_COHERENCE_REQUEST_COLUMNS,
        IDENTITY_COHERENCE_DECISION_COLUMNS,
        IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS,
        IDENTITY_COHERENCE_CONTROL_COLUMNS,
    ):
        assert len(columns) == len(set(columns))


def test_request_status_enum_values_are_stable_strings():
    assert {value.value for value in RequestIdentityCompletenessStatus} == {
        "complete",
        "missing_fragment_observation_mode",
        "missing_precursor_mz",
        "missing_product_mz",
        "missing_fragment_tags",
        "missing_tolerance",
        "missing_mode_specific_constraint",
    }
    assert {value.value for value in RequestCandidateIdentityStatus} == {
        "not_assessed",
        "match",
        "missing_discovery_candidate_join",
        "missing_diagnostic_fragment_evidence",
        "request_candidate_identity_mismatch",
        "unsupported_fragment_observation_mode",
    }


def test_identity_coherence_facade_exports_stable_contract():
    import xic_extractor.alignment.identity_coherence as identity_coherence

    for name in identity_coherence.__all__:
        assert hasattr(identity_coherence, name), f"Missing public export: {name}"


def test_identity_coherence_facade_exports_output_writer_surface():
    import xic_extractor.alignment.identity_coherence as identity_coherence
    from xic_extractor.alignment.identity_coherence import output as output_module

    exported_names = (
        "IdentityCoherenceOutputContext",
        "IdentityCoherenceOutputPaths",
        "IdentityCoherenceOutputRecord",
        "project_request_row",
        "project_decision_row",
        "project_cell_evidence_row",
        "project_control_row",
        "render_identity_coherence_summary",
        "write_identity_coherence_outputs",
        "write_identity_coherence_requests_tsv",
        "write_identity_coherence_decisions_tsv",
        "write_identity_coherence_cell_evidence_tsv",
        "write_identity_coherence_controls_tsv",
    )

    for name in exported_names:
        assert getattr(identity_coherence, name) is getattr(output_module, name)
        assert name in identity_coherence.__all__
        assert name in output_module.__all__


def test_identity_coherence_output_split_modules_import_directly():
    from xic_extractor.alignment.identity_coherence import (
        output_formatting,
        output_models,
        output_projection,
        output_summary,
        output_summary_model,
        output_validation,
        output_writers,
    )

    assert output_formatting.format_tsv_value is not None
    assert output_models.IdentityCoherenceOutputContext is not None
    assert output_models.IdentityCoherenceOutputPaths is not None
    assert output_models.IdentityCoherenceOutputRecord is not None
    assert output_projection.project_request_row is not None
    assert output_projection.project_decision_row is not None
    assert output_projection.project_cell_evidence_row is not None
    assert output_projection.project_control_row is not None
    assert output_validation.validate_output_record is not None
    assert output_summary_model.build_identity_coherence_summary_model is not None
    assert output_summary_model.engineering_go_no_go_rows is not None
    assert output_summary.render_identity_coherence_summary is not None
    assert output_writers.write_identity_coherence_outputs is not None


def test_identity_coherence_control_split_modules_import_directly():
    from xic_extractor.alignment.identity_coherence import (
        control_evaluation,
        control_manifest,
        control_models,
        control_rows,
        decoy_controls,
        positive_controls,
    )

    assert control_models.IdentityControlManifestEntry is not None
    assert control_models.IdentityControlsConfig is not None
    assert control_models.IdentityDecoySource is not None
    assert control_manifest.read_identity_controls_manifest is not None
    assert control_manifest.read_identity_controls_manifest_tsv is not None
    assert positive_controls.evaluate_positive_control is not None
    assert decoy_controls.evaluate_identity_decoy is not None
    assert control_evaluation.evaluate_identity_controls is not None
    assert control_rows.control_row is not None


def test_identity_coherence_output_record_runtime_type_hints_resolve():
    from typing import get_type_hints

    from xic_extractor.alignment.identity_coherence import (
        IdentityCoherenceOutputRecord,
    )
    from xic_extractor.alignment.identity_coherence.row_evaluator import (
        IdentityCoherenceRowResult,
    )

    hints = get_type_hints(IdentityCoherenceOutputRecord)

    assert hints["seed_gate"] is SeedGateResult
    assert hints["row_result"] is IdentityCoherenceRowResult


def test_identity_coherence_facade_exports_controls_surface():
    import xic_extractor.alignment.identity_coherence as identity_coherence
    from xic_extractor.alignment.identity_coherence import controls as controls_module

    exported_names = (
        "ControlType",
        "ControlStatus",
        "DecoyGenerationMethod",
        "PositiveControlMappingStatus",
        "IdentityControlEvaluationResult",
        "IdentityControlManifestEntry",
        "IdentityControlsConfig",
        "IdentityDecoySource",
        "REQUIRED_MANIFEST_FIELDS",
        "read_identity_controls_manifest",
        "read_identity_controls_manifest_tsv",
        "evaluate_positive_control",
        "evaluate_identity_decoy",
        "evaluate_identity_controls",
    )

    for name in exported_names:
        assert getattr(identity_coherence, name) is getattr(controls_module, name)
        assert name in identity_coherence.__all__
        assert name in controls_module.__all__

    assert identity_coherence.REQUIRED_MANIFEST_FIELDS is not None

    assert identity_coherence.ControlType is ControlType
    assert identity_coherence.ControlStatus is ControlStatus
    assert identity_coherence.DecoyGenerationMethod is DecoyGenerationMethod
    assert (
        identity_coherence.PositiveControlMappingStatus
        is PositiveControlMappingStatus
    )


def test_identity_coherence_facade_exports_process_payload_surface():
    import xic_extractor.alignment.identity_coherence as identity_coherence

    assert identity_coherence.EngineeringConfig is not None
    assert identity_coherence.IdentityCoherenceResult is not None
    assert identity_coherence.IdentityCoherenceTraceRequest is not None
    assert identity_coherence.IdentityCoherenceTraceResult is not None
    assert "EngineeringConfig" in identity_coherence.__all__
    assert "IdentityCoherenceResult" in identity_coherence.__all__
    assert "IdentityCoherenceTraceRequest" in identity_coherence.__all__
    assert "IdentityCoherenceTraceResult" in identity_coherence.__all__


def test_identity_coherence_process_payload_stays_no_raw_boundary() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "xic_extractor/alignment/identity_coherence/process_payload.py"
    ).read_text(encoding="utf-8")
    forbidden_snippets = (
        "raw_reader",
        "ms1_index_source",
        "owner_backfill",
        "alignment.pipeline",
        "scripts.run_alignment",
        "source_for_owner_backfill_backend",
        "xic_extractor.output",
        "workbook",
        "report",
    )

    # Syntactic-only firewall: tighten to an AST import scan once this module
    # grows beyond payload dataclasses and the no-RAW smoke worker.
    for snippet in forbidden_snippets:
        assert snippet not in source


def test_domain_modules_do_not_import_controls_or_output_surfaces():
    package_root = (
        Path(__file__).resolve().parents[3]
        / "xic_extractor"
        / "alignment"
        / "identity_coherence"
    )
    domain_modules = (
        "candidate_matcher.py",
        "cell_evidence.py",
        "decision.py",
        "models.py",
        "request_builder.py",
        "row_evaluator.py",
        "rt_center.py",
        "schema.py",
        "seed_gate.py",
        "shape.py",
        "tags.py",
        "width.py",
    )
    violations = []
    for module_name in domain_modules:
        source = (package_root / module_name).read_text(encoding="utf-8")
        for violation in _forbidden_identity_coherence_imports(source):
            violations.append(f"{module_name}: {violation}")

    assert violations == []


def test_output_modules_follow_declared_dependency_direction():
    package_root = (
        Path(__file__).resolve().parents[3]
        / "xic_extractor"
        / "alignment"
        / "identity_coherence"
    )
    violations = []

    for module_name, forbidden_surfaces in (
        OUTPUT_MODULE_FORBIDDEN_RELATIVE_SURFACES.items()
    ):
        source = (package_root / module_name).read_text(encoding="utf-8")
        relative_imports = _identity_coherence_import_surfaces(source)
        if module_name == "output_formatting.py" and relative_imports:
            for line_number, surface in relative_imports:
                violations.append(
                    f"{module_name}: line {line_number}: local import {surface}"
                )
        for line_number, surface in relative_imports:
            if surface in forbidden_surfaces:
                violations.append(
                    f"{module_name}: line {line_number}: forbidden import {surface}"
                )
        for line_number, module in _absolute_import_modules(source):
            if _is_forbidden_output_absolute_import(module):
                violations.append(
                    f"{module_name}: line {line_number}: forbidden import {module}"
                )

    assert violations == []


def test_control_modules_do_not_import_output_or_adapter_surfaces():
    package_root = (
        Path(__file__).resolve().parents[3]
        / "xic_extractor"
        / "alignment"
        / "identity_coherence"
    )
    violations = []

    for module_name, forbidden_surfaces in (
        CONTROL_MODULE_FORBIDDEN_RELATIVE_SURFACES.items()
    ):
        source = (package_root / module_name).read_text(encoding="utf-8")
        for line_number, surface in _identity_coherence_import_surfaces(source):
            if surface in forbidden_surfaces:
                violations.append(
                    f"{module_name}: line {line_number}: forbidden import {surface}"
                )
        for line_number, module in _absolute_import_modules(source):
            if _is_forbidden_output_absolute_import(module):
                violations.append(
                    f"{module_name}: line {line_number}: forbidden import {module}"
                )

    assert violations == []


@pytest.mark.parametrize(
    "source",
    (
        "import xic_extractor.alignment.identity_coherence as ic\n",
        "from xic_extractor.alignment import identity_coherence\n",
    ),
)
def test_split_module_import_surface_scan_detects_package_facade(source):
    assert _identity_coherence_import_surfaces(source) == [(1, "__package__")]


def _is_forbidden_output_absolute_import(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_OUTPUT_ABSOLUTE_IMPORT_PREFIXES
    )


def test_forbidden_import_scan_ignores_comments_docstrings_and_plain_strings():
    source = '''
"""Mention xic_extractor.alignment.identity_coherence.output in docs."""
# from xic_extractor.alignment.identity_coherence import output
TEXT = "identity_coherence.output"
'''

    assert _forbidden_identity_coherence_imports(source) == []


@pytest.mark.parametrize(
    "source",
    (
        "import xic_extractor.alignment.identity_coherence.controls\n",
        "import xic_extractor.alignment.identity_coherence as ic\n",
        "from xic_extractor.alignment import identity_coherence\n",
        "import xic_extractor.alignment.identity_coherence.output\n",
        "import xic_extractor.alignment.identity_coherence.control_models\n",
        "import xic_extractor.alignment.identity_coherence.control_manifest\n",
        "import xic_extractor.alignment.identity_coherence.control_rows\n",
        "import xic_extractor.alignment.identity_coherence.control_evaluation\n",
        "import xic_extractor.alignment.identity_coherence.positive_controls\n",
        "import xic_extractor.alignment.identity_coherence.decoy_controls\n",
        "from xic_extractor.alignment.identity_coherence.controls import X\n",
        "from xic_extractor.alignment.identity_coherence.output import X\n",
        "from xic_extractor.alignment.identity_coherence.control_models import X\n",
        "from xic_extractor.alignment.identity_coherence.control_manifest import X\n",
        "from xic_extractor.alignment.identity_coherence.control_rows import X\n",
        "from xic_extractor.alignment.identity_coherence.control_evaluation import X\n",
        "from xic_extractor.alignment.identity_coherence.positive_controls import X\n",
        "from xic_extractor.alignment.identity_coherence.decoy_controls import X\n",
        (
            "from xic_extractor.alignment.identity_coherence "
            "import evaluate_identity_controls\n"
        ),
        (
            "from xic_extractor.alignment.identity_coherence "
            "import write_identity_coherence_outputs\n"
        ),
        "from xic_extractor.alignment.identity_coherence import controls\n",
        "from xic_extractor.alignment.identity_coherence import output\n",
        "from . import controls\n",
        "from . import control_models\n",
        "from . import control_manifest\n",
        "from . import control_rows\n",
        "from . import control_evaluation\n",
        "from . import positive_controls\n",
        "from . import decoy_controls\n",
        "from . import output\n",
        "from . import output_models\n",
        "from .control_models import X\n",
        "from .control_manifest import X\n",
        "from .control_rows import X\n",
        "from .control_evaluation import X\n",
        "from .positive_controls import X\n",
        "from .decoy_controls import X\n",
        "from .output_models import X\n",
        "import xic_extractor.alignment.identity_coherence.output_models\n",
        "from xic_extractor.alignment.identity_coherence.output_models import X\n",
        "from .controls import X\n",
        "from .output import X\n",
    ),
)
def test_forbidden_import_scan_detects_controls_and_output_surfaces(source):
    assert _forbidden_identity_coherence_imports(source)


@pytest.mark.parametrize(
    "source",
    (
        "from xic_extractor import raw_reader\n",
        "from xic_extractor.alignment import owner_backfill\n",
        "from xic_extractor.alignment import backfill\n",
        "from xic_extractor.alignment import process_backend\n",
        "from xic_extractor.alignment import identity_coherence_adapter\n",
        "from xic_extractor.alignment import identity_coherence_source_mapping\n",
        "from xic_extractor.alignment import identity_coherence_trace_retrieval\n",
        "from xic_extractor.alignment import identity_coherence_record_builder\n",
    ),
)
def test_absolute_forbidden_import_scan_expands_parent_package_aliases(source):
    assert any(
        _is_forbidden_output_absolute_import(module)
        for _, module in _absolute_import_modules(source)
    )


@dataclass
class CandidateLike:
    candidate_id: str = "CAND-1"
    sample_name: str = "RAW-1"
    precursor_mz: float = 500.0
    product_mz: float = 384.0
    observed_neutral_loss_da: float = 116.0
    matched_tag_names: object = ("MeR", "dR")
    neutral_loss_tag: str = "dR"


def _request():
    return build_identity_coherence_request(
        CandidateLike(),
        request_id="REQ-1",
        decision_id="DEC-1",
        precursor_tolerance_ppm=10.0,
        product_tolerance_ppm=10.0,
        cid_observed_loss_tolerance_ppm=10.0,
        fragment_profile_id="profile-a",
    )


def test_seed_gate_enum_values_are_stable_strings():
    assert {value.value for value in EvidenceStage} == {
        "pre_backfill",
        "backfill_only",
        "post_backfill",
    }
    assert {value.value for value in SeedGateClass} == {
        "coherent_seed",
        "review_only_seed_gate_failed",
        "blocked_seed",
    }
    assert {value.value for value in SeedRejectReason} == {
        "missing_request_identity_constraint",
        "no_quantifiable_owner",
        "missing_discovery_candidate_join",
        "missing_diagnostic_fragment_evidence",
        "ambiguous_owner",
        "duplicate_loser",
        "backfill_only_evidence",
        "nonfinite_peak",
        "seed_rt_outside_owner_peak",
        "low_ms1_scan_support",
        "request_candidate_identity_mismatch",
        "unsupported_fragment_observation_mode",
        "multi_seed_unresolved",
    }


def test_seed_gate_models_hold_a_resolved_gate_result():
    evidence = SeedCandidateEvidence(
        candidate_id="CAND-1",
        precursor_mz=500.0,
        product_mz=384.0,
        cid_observed_loss_da=116.0,
        fragment_tags=("MeR", "dR"),
        best_seed_rt=7.83,
        ms1_scan_support_score=0.80,
    )
    match = CandidateIdentityMatch(
        request_candidate_identity_status=RequestCandidateIdentityStatus.MATCH,
        precursor_error_ppm=0.0,
        product_error_ppm=0.0,
        cid_observed_loss_error_ppm=0.0,
        cid_observed_loss_error_da=0.0,
        missing_fields=(),
        mismatch_fields=(),
        fragment_tags_supported=("dR",),
    )

    result = SeedGateResult(
        resolved_request=_request(),
        seed_gate_class=SeedGateClass.COHERENT_SEED,
        seed_reject_reason=None,
        candidate_match=match,
        review_flags=(),
    )

    assert evidence.evidence_stage is EvidenceStage.PRE_BACKFILL
    assert result.seed_gate_class is SeedGateClass.COHERENT_SEED
    assert result.resolved_request.seed_candidate_id == "CAND-1"
    assert SeedGateConfig().min_ms1_scan_support_score == 0.5


def test_cell_evidence_enum_values_are_stable_strings():
    assert {value.value for value in CellAssessmentStatus} == {
        "assessed",
        "blocked",
        "data_quality_reject",
        "not_assessed",
    }
    assert {value.value for value in CellIdentityTier} == {
        "tier1",
        "tier2",
        "tier3",
        "rt_only",
        "blocked",
        "data_quality",
    }
    assert {value.value for value in CellIdentityBasis} == {
        "rt_fragment_support",
        "rt_shape_similarity",
        "rt_prototype_width",
        "none",
    }
    assert {value.value for value in FragmentMatchStatus} == {
        "pass",
        "fail",
        "ambiguous",
        "not_assessed",
    }
    assert {value.value for value in RtGateStatus} == {
        "pass",
        "fail",
        "not_assessed",
    }
    assert {value.value for value in ShapeStatus} == {
        "pass",
        "fail",
        "low_points",
        "zero_signal",
        "not_assessed",
    }
    assert {value.value for value in ShapeReferenceBasis} == {
        "tier1_supported_medoid",
        "morphology_rt_medoid",
        "seed_fallback",
        "none",
    }
    assert {value.value for value in ShapeAuditStatus} == {
        "pass",
        "fail",
        "shoulder",
        "bimodal",
        "coelution",
        "saturated",
        "clipped",
        "unavailable",
        "not_assessed",
    }
    assert {value.value for value in WidthStatus} == {
        "pass",
        "fail",
        "not_assessed",
    }
    assert {value.value for value in BaselineAuditStatus} == {
        "pass",
        "fail",
        "unavailable",
        "not_assessed",
    }
    assert {value.value for value in AreaHeightStatus} == {
        "pass",
        "fail",
        "not_assessed",
    }
    assert {value.value for value in NonRtIdentityResult} == {
        "pass",
        "fail",
        "not_assessed",
        "blocked",
    }
    assert {value.value for value in CellBlockedReason} == {
        "backfill_only_evidence",
    }
    assert {value.value for value in CellDataQualityReason} == {
        "invalid_peak_morphology",
    }
    assert {value.value for value in DecisionReason} == {
        "tier1_support",
        "tier2_shape_support",
    }


def test_decision_and_center_enum_values_are_stable_strings():
    assert {value.value for value in IdentityDecision} == {
        "would_primary_provisional_identity_family_support",
        "review_only_seed_gate_failed",
        "review_only_rt_only_support",
        "review_only_insufficient_support",
        "review_only_center_unstable",
        "review_only_weak_basis_tier3_only",
        "review_only_weak_basis_single_tier12_plus_tier3",
        "review_only_multi_seed_unresolved",
        "blocked_infrastructure",
    }
    assert {value.value for value in WeakBasisReason} == {
        "none",
        "tier3_only",
        "single_tier12_plus_tier3",
        "seed_shape_fallback_only",
        "rt_only",
    }
    assert {value.value for value in RtCenterDecision} == {
        "seed_anchored",
        "recentered_stable",
        "center_unstable_review_only",
    }


def test_identity_coherence_config_defaults_match_v04_review_values():
    config = IdentityCoherenceConfig()

    assert config.promotion.min_total_coherent_samples == 3
    assert config.promotion.min_non_seed_coherent_samples == 2
    assert config.promotion.min_non_seed_tier12_identity_samples == 2
    assert config.rt.max_rt_sec == 180.0
    assert config.rt.preferred_rt_sec == 60.0
    assert config.rt.seed_center_candidate_sec == 30.0
    assert config.rt.max_center_drift_sec == 30.0
    assert config.shape.min_points == 7
    assert config.shape.resample_points == 25
    assert config.shape.min_cosine == 0.85
    assert config.shape.prototype_min_candidates == 3
    assert config.shape.prototype_min_non_seed_candidates == 2
    assert config.shape.allow_seed_shape_fallback is True
    assert config.shape.allow_morphology_rt_medoid is True
    assert config.width.prototype_min_candidates == 3
    assert config.width.min_ratio == 0.50
    assert config.width.max_ratio == 2.00
    assert isinstance(config.engineering, EngineeringConfig)
    assert config.engineering.max_infrastructure_blocked_fraction == 0.05
    assert config.engineering.max_projected_full_cohort_identity_xic_requests is None


@pytest.mark.parametrize(
    ("engineering_config_kwargs", "message"),
    (
        (
            {"max_infrastructure_blocked_fraction": -0.01},
            "max_infrastructure_blocked_fraction must be nonnegative",
        ),
        (
            {"max_infrastructure_blocked_fraction": 1.01},
            "max_infrastructure_blocked_fraction must be <= 1",
        ),
        (
            {"max_projected_full_cohort_identity_xic_requests": True},
            "max_projected_full_cohort_identity_xic_requests must be nonnegative",
        ),
        (
            {"max_projected_full_cohort_identity_xic_requests": -1},
            "max_projected_full_cohort_identity_xic_requests must be nonnegative",
        ),
    ),
)
def test_engineering_config_rejects_invalid_bounds(
    engineering_config_kwargs,
    message,
):
    with pytest.raises(ValueError, match=message):
        EngineeringConfig(**engineering_config_kwargs)


def test_candidate_trace_is_nested_domain_model_not_flat_schema_columns():
    trace = CandidateTrace(
        rt_min=(7.75, 7.80, 7.85),
        intensity=(1.0, 5.0, 1.0),
        shape_audit_status=ShapeAuditStatus.PASS,
    )

    candidate = CellCandidateEvidence(
        sample_id="S2",
        candidate_evidence=SeedCandidateEvidence(
            candidate_id="C2",
            precursor_mz=500.0,
            product_mz=384.0,
            cid_observed_loss_da=116.0,
            fragment_tags=("MeR", "dR"),
            best_seed_rt=7.80,
            ms1_scan_support_score=0.75,
        ),
        apex_rt=7.80,
        peak_start_rt=7.75,
        peak_end_rt=7.85,
        area=10.0,
        height=5.0,
        point_count=3,
        trace=trace,
    )

    assert candidate.trace is trace
    assert "rt_min" not in IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS
    assert "intensity" not in IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS


def test_candidate_trace_rejects_mismatched_rt_and_intensity_lengths():
    with pytest.raises(ValueError, match="rt_min and intensity"):
        CandidateTrace(
            rt_min=(7.75, 7.80, 7.85),
            intensity=(1.0, 5.0),
            shape_audit_status=ShapeAuditStatus.PASS,
        )


def test_cell_and_decision_models_hold_tier1_slice_state():
    candidate = SeedCandidateEvidence(
        candidate_id="CAND-2",
        precursor_mz=500.0,
        product_mz=384.0,
        cid_observed_loss_da=116.0,
        fragment_tags=("MeR", "dR"),
        best_seed_rt=7.90,
        ms1_scan_support_score=0.75,
    )
    cell_candidate = CellCandidateEvidence(
        sample_id="RAW-2",
        candidate_evidence=candidate,
        apex_rt=7.90,
        peak_start_rt=7.80,
        peak_end_rt=8.00,
        area=1000.0,
        height=200.0,
        point_count=9,
    )
    center = RtCenterResult(
        center_rt_min=7.85,
        center_rt_sec=471.0,
        center_decision=RtCenterDecision.RECENTERED_STABLE,
        center_candidate_count=2,
        center_drift_sec=1.2,
    )
    cell = CellEvidenceResult(
        decision_id="DEC-1",
        identity_family_id="IDF-1",
        sample_id=cell_candidate.sample_id,
        candidate_id=cell_candidate.candidate_evidence.candidate_id,
        cell_assessment_status=CellAssessmentStatus.ASSESSED,
        cell_identity_tier=CellIdentityTier.TIER1,
        cell_identity_basis=CellIdentityBasis.RT_FRAGMENT_SUPPORT,
        fragment_observation_mode=FragmentObservationMode.CID_NEUTRAL_LOSS,
        fragment_match_status=FragmentMatchStatus.PASS,
        fragment_tags_supported=("MeR", "dR"),
        rt_delta_center_sec=3.0,
        rt_gate_status=RtGateStatus.PASS,
        shape_status=ShapeStatus.NOT_ASSESSED,
        shape_similarity_cosine=None,
        shape_reference_basis=ShapeReferenceBasis.NONE,
        shape_reference_candidate_id="",
        shape_fallback_used=False,
        shape_audit_status=ShapeAuditStatus.NOT_ASSESSED,
        width_status=WidthStatus.NOT_ASSESSED,
        width_ratio_to_prototype=None,
        baseline_audit_status=BaselineAuditStatus.NOT_ASSESSED,
        area_height_status=AreaHeightStatus.PASS,
        non_rt_identity_result=NonRtIdentityResult.PASS,
        coherent_count_contribution=True,
        tier12_count_contribution=True,
        blocked_reason="",
        data_quality_reason="",
        forbidden_evidence_seen=False,
    )
    summary = IdentityDecisionSummary(
        decision_id="DEC-1",
        identity_family_id="IDF-1",
        seed_candidate_id="CAND-1",
        seed_sample="RAW-1",
        seed_gate_class=SeedGateClass.COHERENT_SEED,
        request_identity_completeness_status=(
            RequestIdentityCompletenessStatus.COMPLETE
        ),
        request_candidate_identity_status=RequestCandidateIdentityStatus.MATCH,
        decision=IdentityDecision.WOULD_PRIMARY,
        decision_reason=DecisionReason.TIER1_SUPPORT.value,
        total_coherent_sample_count=3,
        non_seed_coherent_sample_count=2,
        tier12_non_seed_identity_sample_count=2,
        tier1_fragment_confirmed_sample_count=2,
        tier2_shape_supported_sample_count=0,
        tier2_seed_shape_fallback_sample_count=0,
        tier3_width_only_sample_count=0,
        min_total_coherent_samples=3,
        min_non_seed_coherent_samples=2,
        min_non_seed_tier12_identity_samples=2,
        weak_basis_reason=WeakBasisReason.NONE,
        shape_reference_basis=ShapeReferenceBasis.NONE,
        shape_reference_candidate_id="",
        prototype_width_sec=None,
        center_rt_source="recentered_stable",
        center=center,
        coherent_fraction=0.375,
        infrastructure_blocked_sample_count=0,
        data_quality_reject_sample_count=0,
        forbidden_evidence_seen=False,
        forbidden_evidence_used=False,
    )

    assert cell.coherent_count_contribution is True
    assert summary.total_coherent_sample_count == 3
