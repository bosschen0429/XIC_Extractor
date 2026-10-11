from __future__ import annotations

from collections.abc import Sequence

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.output import metadata
from xic_extractor.output.workbook_styles import (
    _SAMPLE_HEADER,
    BORDER,
    CENTER,
    GREY,
    WHITE,
    _apply,
    _fill,
    _header_style,
)


def _build_metadata_sheet(
    ws, config: ExtractionConfig, *, extra_metadata: Sequence[tuple[str, object]] = ()
) -> None:
    for col_idx, header in enumerate(("Key", "Value"), start=1):
        _apply(
            ws.cell(row=1, column=col_idx, value=header),
            **_header_style(_SAMPLE_HEADER),
        )

    for row_idx, (key, value) in enumerate(
        [*metadata.build_metadata_rows(config), *extra_metadata], start=2
    ):
        fill_hex = GREY if row_idx % 2 == 0 else WHITE
        _apply(
            ws.cell(row=row_idx, column=1, value=key),
            fill=_fill(fill_hex),
            alignment=CENTER,
            border=BORDER,
        )
        _apply(
            ws.cell(row=row_idx, column=2, value=value),
            fill=_fill(fill_hex),
            alignment=CENTER,
            border=BORDER,
        )

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 48
    ws.freeze_panes = "A2"
