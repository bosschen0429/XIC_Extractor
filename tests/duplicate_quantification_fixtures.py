"""Helpers shared by test_duplicate_quantification and other test modules."""

from tests.alignment_explanation_fixtures import run_alignment_fixture
from xic_extractor.diagnostics.alignment_explanation_projection import (
    write_alignment_explanation,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.output_levels import EXPLANATION_ARTIFACT_NAME
from xic_extractor.output.alignment.pipeline_outputs import (
    output_paths,
    write_completed_untargeted_outputs_atomic,
)


def publish_owner_run(tmp_path, monkeypatch, level):
    """A published Alignment folder of one owner run, with its explanation."""
    completed, candidates, _, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    output_dir = tmp_path / "published"
    outputs = output_paths(
        output_dir,
        output_level=level,
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    write_completed_untargeted_outputs_atomic(
        outputs,
        completed.application_result,
        metadata={},
        timing_recorder=TimingRecorder.disabled("writer"),
    )
    write_alignment_explanation(
        output_dir / EXPLANATION_ARTIFACT_NAME,
        completed=completed,
        peaks_by_sample=candidates,
        matrix_path=outputs.matrix_tsv,
        alignment_config=config,
    )
    return output_dir
