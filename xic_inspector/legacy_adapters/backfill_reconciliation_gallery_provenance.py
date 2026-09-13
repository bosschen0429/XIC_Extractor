"""Provenance and artifact-link rendering for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import text_value

from .backfill_reconciliation_gallery_html import (
    compact_path_label as _compact_path_label,
)
from .backfill_reconciliation_gallery_html import (
    escape_attr as _escape_attr,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_html import (
    href_for_path as _href_for_path,
)
from .backfill_reconciliation_gallery_html import (
    path_link_html as _path_link_html,
)
from .backfill_reconciliation_gallery_inputs import (
    _INPUT_ARTIFACT_LABEL_BY_KEY,
)

OVERLAY_INTERPRETATION_GUIDE_PATH = Path(
    "docs/superpowers/validation/evidence_overlay_interpretation_guide.html",
)


def _interpretation_guide_callout_html(
    summary: Mapping[str, object],
    *,
    html_path: Path,
    local_interpretation_guide: Path | None,
) -> str:
    guide_path = (
        str(local_interpretation_guide)
        if local_interpretation_guide
        else (
            text_value(summary.get("overlay_interpretation_guide_path"))
            or str(OVERLAY_INTERPRETATION_GUIDE_PATH)
        )
    )
    link = _path_link_html(
        guide_path,
        html_path=html_path,
        label="Open overlay interpretation guide",
    )
    return (
        '<div class="interpretation-guide" aria-label="overlay interpretation guide">'
        "<strong>How to read these overlays</strong>"
        "<p>Backfill overlays answer why a matrix/backfill decision is supported; "
        "Discovery differential overlays compare evidence for two row identities, "
        "even when source and successor m/z differ.</p>"
        f"<p>{link}</p>"
        "</div>"
    )


def _write_local_overlay_interpretation_guide(output_dir: Path) -> Path | None:
    source = Path.cwd() / OVERLAY_INTERPRETATION_GUIDE_PATH
    if not source.exists():
        return None
    target = output_dir / OVERLAY_INTERPRETATION_GUIDE_PATH.name
    try:
        if source.resolve() == target.resolve():
            return target
    except OSError:
        return None
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _artifact_links(
    output_paths: Mapping[str, Path],
    *,
    html_path: Path,
) -> list[str]:
    if not output_paths:
        return []
    links: list[str] = []
    label_by_key = {
        "groups_tsv": "groups TSV",
        "representative_cells_tsv": "representatives TSV",
        "summary_json": "summary JSON",
    }
    for key, path in output_paths.items():
        label = label_by_key.get(key, key.replace("_", " "))
        href = _href_for_path(path, html_path)
        links.append(
            f'<a href="{_escape_attr(href)}" title="{_escape_attr(str(path))}">'
            f"{_escape(label)}</a>",
        )
    return [
        '<div class="artifact-strip" aria-label="generated output artifacts">'
        "<span>Outputs</span>"
        f"{' '.join(links)}"
        "</div>",
    ]


def _input_artifact_links(
    input_artifacts: object,
    *,
    html_path: Path,
) -> list[str]:
    if not isinstance(input_artifacts, Mapping):
        return []
    path_rows = _input_artifact_path_rows(input_artifacts)
    source_run_id = text_value(input_artifacts.get("source_run_id"))
    if not path_rows and not source_run_id:
        return []
    file_label = "1 file" if len(path_rows) == 1 else f"{len(path_rows)} files"
    source_label = f" · source={source_run_id}" if source_run_id else ""
    link_items: list[str] = []
    for label, path_text in path_rows:
        link_html = _path_link_html(
            path_text,
            html_path=html_path,
            label=_compact_path_label(path_text),
        )
        link_items.append(
            "<li>"
            f'<span class="artifact-label">{_escape(label)}</span>'
            f"{link_html}"
            "</li>",
        )
    links = "".join(link_items)
    return [
        '<details class="provenance-panel">',
        f"<summary>Input artifacts · {file_label}{_escape(source_label)}</summary>",
        f'<ul class="provenance-list">{links}</ul>',
        "</details>",
    ]


def _source_artifacts_html(
    source_artifacts: Sequence[str],
    input_artifacts: object,
    html_path: Path,
) -> str:
    if not source_artifacts:
        return "none"
    path_map = _input_artifact_paths_by_label(input_artifacts)
    items: list[str] = []
    for artifact in source_artifacts:
        linked_paths = path_map.get(artifact, ())
        if not linked_paths:
            items.append(f"<li>{_escape(artifact)}</li>")
            continue
        for path_text in linked_paths:
            path_link = _path_link_html(
                path_text,
                html_path=html_path,
                label=_compact_path_label(path_text),
            )
            items.append(
                "<li>"
                f"{_escape(artifact)}: "
                f"{path_link}"
                "</li>",
            )
    return '<ul class="path-list">' + "".join(items) + "</ul>"


def _input_artifact_paths_by_label(
    input_artifacts: object,
) -> dict[str, tuple[str, ...]]:
    paths_by_label: dict[str, list[str]] = {}
    if not isinstance(input_artifacts, Mapping):
        return {}
    for label, path_text in _input_artifact_path_rows(input_artifacts):
        paths_by_label.setdefault(label, []).append(path_text)
    return {label: tuple(paths) for label, paths in paths_by_label.items()}


def _input_artifact_path_rows(
    input_artifacts: Mapping[str, object],
) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for key, label in _INPUT_ARTIFACT_LABEL_BY_KEY.items():
        value = input_artifacts.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str):
            rows.extend((label, str(item)) for item in value if item)
        elif value:
            rows.append((label, str(value)))
    return tuple(rows)
