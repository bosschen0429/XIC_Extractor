"""Tests share helpers through fixture modules, never through another test."""

import ast
from pathlib import Path

TESTS = Path(__file__).parent

# "importer imported" test-module pairs that predate this rule. Shrink-only:
# move the shared helper into a non-test module under tests/ (for example
# tests/membership_fixtures.py) and delete its line. Never add a line.
EXISTING_TEST_MODULE_IMPORTS = """
    test_accepted_membership test_window_feature_membership
    test_alignment_cell_decision test_cell_observations
    test_alignment_cell_decision test_cid_nl_evidence_spine
    test_alignment_cell_decision test_target_pre_cell_projection
    test_alignment_cell_decision test_untargeted_matrix_projection
    test_alignment_drift_evidence test_alignment_edge_scoring
    test_alignment_known_istd_in_memory test_target_extraction
    test_analysis_set test_sample_metadata_sequence_parsing
    test_cell_observations test_cid_nl_evidence_spine
    test_cell_observations test_cross_sample_association_lineage
    test_cid_nl_admission test_physical_peak_evaluation_persistence
    test_cid_nl_multi_group_recovery test_untargeted_cid_nl_real_data_adapter
    test_discovery_csv_physical_state_contract test_discovery_csv
    test_discovery_output_publication test_discovery_csv
    test_discovery_output_publication test_discovery_pipeline
    test_discovery_physical_fact_integration test_discovery_ms1_capture
    test_discovery_physical_fact_integration test_discovery_pipeline
    test_discovery_physical_fact_integration test_physical_peak_evaluation_persistence
    test_discovery_run_spec_pipeline test_discovery_pipeline
    test_discovery_timing_contract test_discovery_ms1_capture
    test_discovery_timing_contract test_discovery_physical_fact_integration
    test_discovery_timing_contract test_discovery_pipeline
    test_extraction_anchors test_result_assembly
    test_finalized_application_result test_target_pre_cell_projection
    test_finalized_application_result test_untargeted_matrix_projection
    test_finalized_scientific_alignment test_cell_observations
    test_finalized_scientific_alignment test_cross_sample_association_lineage
    test_finalized_scientific_alignment test_search_scope_capture
    test_grouping_experiment test_alignment_known_istd_in_memory
    test_grouping_experiment test_untargeted_cid_nl_bounded_runtime
    test_mass_recalibration_contracts test_discovery_pipeline
    test_matrix_curation test_workbench_analysis
    test_method_composition_extensibility test_discovery_physical_fact_persistence
    test_method_composition_extensibility test_processing_run_spec
    test_physical_capture_context test_discovery_ms1_capture
    test_physical_peak_evaluation_persistence test_discovery_physical_fact_persistence
    test_physical_view_closure test_discovery_ms1_capture
    test_processing_identity test_sample_metadata_sequence_parsing
    test_processing_run_spec test_discovery_pipeline
    test_processing_run_spec_entrypoints test_discovery_pipeline
    test_processing_run_spec_entrypoints test_discovery_worker
    test_processing_run_spec_entrypoints test_processing_run_spec
    test_run_alignment test_alignment_drift_evidence
    test_same_compound test_duplicate_quantification
    test_same_compound test_matrix_curation
    test_target_post_cell_projection test_alignment_cell_decision
    test_target_post_cell_projection test_target_pre_cell_projection
    test_target_pre_cell_projection test_cell_observations
    test_target_pre_cell_projection test_cross_sample_association_lineage
    test_target_pre_cell_projection test_search_scope_capture
    test_targeted_delta_mass test_targeted_peak_selection
    test_targeted_identification test_targeted_delta_mass
    test_targeted_identification test_targeted_peak_selection
    test_targeted_output_boundary test_targeted_production_workflow
    test_targeted_sample_metadata test_discovery_pipeline
    test_targeted_sample_metadata test_targeted_production_workflow
    test_untargeted_application test_alignment_drift_evidence
    test_untargeted_application test_discovery_pipeline
    test_untargeted_application test_untargeted_cid_nl_bounded_runtime
    test_untargeted_cid_nl_bounded_runtime test_untargeted_cid_nl_batch_runtime
    test_untargeted_cid_nl_production_batch test_untargeted_cid_nl_batch_runtime
    test_untargeted_cid_nl_real_data_runtime test_untargeted_cid_nl_real_data_adapter
    test_untargeted_cid_nl_result_store test_untargeted_cid_nl_bounded_runtime
    test_untargeted_matrix_projection test_alignment_cell_decision
    test_untargeted_matrix_projection test_cell_observations
    test_untargeted_matrix_projection test_cross_sample_association_lineage
    test_untargeted_matrix_projection test_search_scope_capture
    test_workbench_browser test_analysis_set
    test_workbench_browser test_explanation_review
    test_workbench_browser test_matrix_curation
    test_workbench_browser test_sample_metadata_sequence_parsing
    test_workbench_browser test_targeted_delta_mass
    test_workbench_browser test_workbench_lifecycle
    test_workbench_browser test_xic_inspector_curve_bundle
    test_workbench_browser test_xic_inspector_targeted_workbook
    test_workbench_samples test_sample_metadata_sequence_parsing
    test_xic_inspector_api test_xic_inspector_curve_bundle
    test_xic_inspector_browser test_explanation_review
    test_xic_inspector_browser test_workbench_browser
    test_xic_inspector_browser test_xic_inspector_curve_bundle
    test_xic_inspector_browser test_xic_inspector_targeted_workbook
    test_xic_inspector_curve_bundle test_xic_inspector_html
    test_xic_inspector_targeted_workbook test_output_metadata
    test_xic_workbench test_xic_inspector_targeted_workbook
"""


def _imported_test_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("tests.test_"):
                modules.add(node.module)
            elif node.module == "tests":
                modules.update(
                    f"tests.{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("test_")
                )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.name
                for alias in node.names
                if alias.name.startswith("tests.test_")
            )
    return {module.split(".")[1] for module in modules}


def _test_module_imports() -> set[tuple[str, str]]:
    return {
        (path.stem, imported)
        for path in TESTS.glob("*.py")
        for imported in _imported_test_modules(
            ast.parse(path.read_text(encoding="utf-8"), path.name)
        )
    }


def _existing_pairs() -> set[tuple[str, str]]:
    pairs = set()
    for line in EXISTING_TEST_MODULE_IMPORTS.split("\n"):
        if line.strip():
            importer, imported = line.split()
            pairs.add((importer, imported))
    return pairs


def test_tests_share_helpers_through_fixture_modules():
    found = _test_module_imports()
    existing = _existing_pairs()
    added = sorted(found - existing)
    removed = sorted(existing - found)

    assert not added, (
        "Move the shared helper into a non-test module under tests/ "
        "(docs/agent/testing.md#test-placement) instead of importing another "
        "test module:\n"
        + "\n".join(f"  {importer} imports {module}" for importer, module in added)
    )
    assert not removed, (
        "These imports are gone; delete their lines from "
        "EXISTING_TEST_MODULE_IMPORTS:\n"
        + "\n".join(f"  {importer} {module}" for importer, module in removed)
    )


def test_detects_function_local_and_package_style_test_imports():
    tree = ast.parse(
        "import tests.test_a\n"
        "from tests import test_b, membership_fixtures\n"
        "def helper():\n"
        "    from tests.test_c import _private\n"
        "from tests.membership_fixtures import cid_nl_owner\n"
    )

    assert _imported_test_modules(tree) == {"test_a", "test_b", "test_c"}
