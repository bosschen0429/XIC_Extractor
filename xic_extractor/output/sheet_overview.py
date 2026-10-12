"""The workbook starts with saved outcomes and compact batch comparisons."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from math import ceil, floor

from openpyxl.chart import BarChart, Reference, ScatterChart, Series
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.marker import Marker
from openpyxl.chart.series import SeriesLabel
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties
from openpyxl.drawing.text import Font as DrawingFont
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU

from xic_extractor.output.targeted_overview import (
    TargetedOverview,
    _counted,
    _number,
    summarize_targeted_results,
)
from xic_extractor.output.workbook_styles import (
    ACCENT,
    BORDER,
    GREY,
    INK,
    MUTED,
    REPORT_FONT,
    WHITE,
    _apply,
    _fill,
    _header_style,
    _prepare_print_view,
)
from xic_extractor.output.workbook_values import _excel_text


def build_overview_sheet(
    ws,
    rows: Sequence[Mapping[str, object]],
    review_rows: Sequence[Mapping[str, object]],
    *,
    has_diagnostics: bool = False,
) -> None:
    overview = summarize_targeted_results(rows, review_rows)
    for column in range(1, 13):
        ws.column_dimensions[get_column_letter(column)].width = 10.5
    for row in range(1, 34):
        ws.row_dimensions[row].height = 14
    for row in (1, 5, 9, 11, 23, 24, 26):
        ws.row_dimensions[row].height = 6
    _line(ws, 2, "Targeted 結果總覽", size=20, bold=True)
    _line(
        ws,
        3,
        f"{overview.sample_count} 個樣本，{len(overview.targets)} 個目標；"
        f"共 {overview.result_count} 筆已保存結果。",
    )
    for column, label, location in (
        (1, "全部量測結果", "'XIC Results'!A1"),
        (7, "目標設定", "'Targets'!A1"),
        (10, "分析條件與版本", "'Run Metadata'!A1"),
    ):
        _link(ws, 4, column, label, location, span=3)
    if has_diagnostics:
        _link(ws, 4, 4, "檢閱原因與下一步", "'Diagnostics'!A1", span=3)
    cards = (
        (1, "計入偵測", f"{overview.counted_count} / {overview.result_count}"),
        (
            5,
            "未計入偵測",
            overview.result_count - overview.counted_count - overview.count_unknown,
        ),
        (9, "待處理結果", overview.review_count),
    )
    for column, label, value in cards:
        for row in (6, 7, 8):
            for offset in range(4):
                ws.cell(row, column + offset).fill = _fill(WHITE)
        ws.merge_cells(
            start_row=6, start_column=column, end_row=6, end_column=column + 3
        )
        ws.merge_cells(
            start_row=7, start_column=column, end_row=8, end_column=column + 3
        )
        _apply(
            ws.cell(6, column, label),
            font=Font(name=REPORT_FONT, size=10, color=MUTED),
            alignment=Alignment(horizontal="left", vertical="center"),
        )
        _apply(
            ws.cell(7, column, value),
            font=Font(name=REPORT_FONT, size=22, bold=True, color=INK),
            alignment=Alignment(horizontal="left", vertical="center"),
        )
    ws.row_dimensions[6].height = 20
    ws.row_dimensions[7].height = 18
    ws.row_dimensions[8].height = 16
    note = "僅列未確認、證據衝突或品質問題；一般 RT 漂移及已確認的回填不列為待處理。"
    if overview.count_unknown:
        note = (
            f"另有 {overview.count_unknown} 筆未記錄計數判定，未併入已計入或未計入。"
            + note
        )
    _line(ws, 10, note)
    for column, title in (
        (1, "各目標的計數結果（筆）"),
        (7, "已計入結果的 RT 分布（min）"),
    ):
        ws.merge_cells(
            start_row=11, start_column=column, end_row=11, end_column=column + 5
        )
        ws.cell(11, column, title).font = Font(
            name=REPORT_FONT, size=11, bold=True, color=INK
        )
    ws.row_dimensions[11].height = 26
    if overview.targets:
        _add_count_chart(ws, overview)
    if not _add_rt_chart(ws, rows, overview):
        ws.merge_cells("G16:L18")
        ws["G16"] = "沒有可繪圖的已計入 RT 量測。"
        ws["G16"].font = Font(name=REPORT_FONT, color=MUTED)
    _line(
        ws,
        25,
        "RT 圖只顯示已計入且有 RT 的結果。橫軸採結果表的樣本順序，不代表進樣時間。",
        size=10,
    )
    _line(ws, 27, "各目標結果", size=14, bold=True)
    groups = ((1, 3), (4, 2), (6, 2), (8, 3), (11, 2))
    for (column, span), title in zip(
        groups,
        ("目標", "計入 / 全部", "待處理", "已計入 RT 範圍 (min)", "查看結果"),
        strict=True,
    ):
        ws.merge_cells(
            start_row=28, start_column=column, end_row=28, end_column=column + span - 1
        )
        for offset in range(span):
            _apply(ws.cell(28, column + offset), **_header_style(INK))
        ws.cell(28, column, title)
    ws.row_dimensions[28].height = 32
    first_row: dict[str, int] = {}
    for index, record in enumerate(rows, 2):
        first_row.setdefault(str(record["Target"]), index)
    for index, target in enumerate(overview.targets, 29):
        counted = f"{target.counted} / {target.total}"
        if target.count_unknown:
            counted += f"（{target.count_unknown} 未記錄）"
        rt_range = (
            "未提供"
            if target.rt_min is None
            else f"{target.rt_min:.4f} – {target.rt_max:.4f}"
        )
        for (column, span), table_value in zip(
            groups,
            (target.label, counted, target.review, rt_range, "開啟"),
            strict=True,
        ):
            ws.merge_cells(
                start_row=index,
                start_column=column,
                end_row=index,
                end_column=column + span - 1,
            )
            for offset in range(span):
                _apply(
                    ws.cell(index, column + offset),
                    fill=_fill(GREY if index % 2 else WHITE),
                    border=BORDER,
                )
            cell = ws.cell(index, column, _excel_text(table_value))
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True,
            )
        _link(ws, index, 11, "查看量測", f"'XIC Results'!A{first_row[target.label]}")
        ws.row_dimensions[index].height = 26
    end = 30 + len(overview.targets)
    _line(
        ws,
        end,
        "閱讀結果表：TRUE＝已計入；FALSE＝未計入。"
        "待處理原因集中在 Diagnostics；完整證據可展開查看。",
        size=10,
    )
    _line(
        ws,
        end + 1,
        "數值是已保存量測；本報告不重新選峰或更改科學判定。"
        "進一步檢查請在 Inspector 開啟此 XLSX。",
        size=10,
    )
    ws.freeze_panes = "A5"
    _prepare_print_view(ws, area=f"A1:L{end + 1}", repeat_rows="2:4")
    # The landing report is one printed page for a small target list; larger
    # batches continue naturally rather than shrinking their text to fit.
    if len(overview.targets) <= 6:
        ws.page_setup.fitToHeight = 1
    ws.row_dimensions[4].height = 22
    ws.row_dimensions[28].height = 28


def _line(ws, row: int, text: str, *, size: int = 11, bold: bool = False) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=12)
    cell = ws.cell(row, 1, _excel_text(text))
    cell.font = Font(
        name=REPORT_FONT, size=size, bold=bold, color=INK if size > 10 else MUTED
    )
    cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 32 if size > 18 else 24 if bold else 22


def _link(
    ws, row: int, column: int, text: str, location: str, *, span: int = 1
) -> None:
    if span > 1:
        ws.merge_cells(
            start_row=row,
            start_column=column,
            end_row=row,
            end_column=column + span - 1,
        )
    cell = ws.cell(row, column, _excel_text(text))
    cell.hyperlink = f"#{location}"
    cell.font = Font(name=REPORT_FONT, size=11, color=ACCENT, underline="single")
    cell.alignment = Alignment(vertical="center")


def _chart_text() -> RichText:
    return RichText(
        p=[
            Paragraph(
                pPr=ParagraphProperties(
                    defRPr=CharacterProperties(
                        sz=900,
                        solidFill=MUTED,
                        latin=DrawingFont(typeface=REPORT_FONT),
                        ea=DrawingFont(typeface=REPORT_FONT),
                    )
                )
            )
        ]
    )


def _style_chart(chart, *, left: float) -> None:
    """Specify native Excel layout instead of relying on renderer defaults."""
    chart.style = 2
    chart.graphical_properties = GraphicalProperties(solidFill=WHITE)
    chart.graphical_properties.line.noFill = True
    chart.plot_area.spPr = GraphicalProperties(solidFill=WHITE)
    chart.plot_area.spPr.line.noFill = True
    chart.layout = Layout(
        manualLayout=ManualLayout(
            x=left,
            y=0.04,
            w=0.95 - left,
            h=0.64,
            layoutTarget="inner",
            xMode="factor",
            yMode="factor",
            wMode="factor",
            hMode="factor",
        )
    )
    chart.legend.position = "b"
    chart.legend.overlay = False
    chart.legend.layout = Layout(
        manualLayout=ManualLayout(
            x=0.06,
            y=0.88,
            w=0.88,
            h=0.10,
            xMode="factor",
            yMode="factor",
            wMode="factor",
            hMode="factor",
        )
    )
    chart.legend.txPr = _chart_text()
    chart.txPr = _chart_text()
    # Captions and units are worksheet text, keeping chart labels away from
    # legends in Excel's native layout (which differs from preview renderers).
    chart.title = None
    for axis in (chart.x_axis, chart.y_axis):
        axis.title = None
        axis.delete = False
        axis.tickLblPos = "nextTo"
        axis.txPr = _chart_text()
        axis.majorTickMark = "none"
        axis.majorGridlines = None
        axis.spPr = GraphicalProperties()
        axis.spPr.line.solidFill = "D6DEE7"
    grid = GraphicalProperties()
    grid.line.solidFill = "E7ECF1"
    grid.line.width = 6350
    chart.y_axis.majorGridlines = ChartLines(spPr=grid)


def _add_count_chart(ws, overview: TargetedOverview) -> None:
    for column, header in enumerate(("目標", "已計入", "未計入", "未記錄"), 14):
        ws.cell(1, column, header)
    for row, target in enumerate(overview.targets, 2):
        for column, value in enumerate(
            (
                target.label,
                target.counted,
                target.total - target.counted - target.count_unknown,
                target.count_unknown,
            ),
            14,
        ):
            ws.cell(row, column, _excel_text(value))
    chart = BarChart()
    chart.type = "bar"
    chart.grouping = "stacked"
    chart.overlap = 100
    chart.gapWidth = 70
    chart.title = "各目標的計數結果"
    chart.x_axis.title = None
    chart.x_axis.axPos = "l"
    chart.y_axis.title = "結果筆數"
    chart.y_axis.axPos = "b"
    chart.y_axis.scaling.min = 0
    chart.y_axis.scaling.max = max(target.total for target in overview.targets)
    chart.y_axis.majorUnit = max(1, ceil(chart.y_axis.scaling.max / 5))
    chart.y_axis.numFmt = "0"
    _style_chart(chart, left=0.24)
    chart.visible_cells_only = False
    chart.add_data(
        Reference(
            ws, min_col=15, max_col=17, min_row=1, max_row=len(overview.targets) + 1
        ),
        titles_from_data=True,
    )
    chart.set_categories(
        Reference(ws, min_col=14, min_row=2, max_row=len(overview.targets) + 1)
    )
    for series, color, title in zip(
        chart.series,
        ("497F6B", "D3A15A", "B8C1CD"),
        ("已計入", "未計入", "未記錄"),
        strict=True,
    ):
        series.tx = SeriesLabel(v=title)
        series.graphicalProperties.solidFill = color
        series.graphicalProperties.line.noFill = True
    ws.add_chart(
        chart,
        TwoCellAnchor(
            _from=AnchorMarker(col=0, row=11),
            to=AnchorMarker(col=6, row=23, colOff=-pixels_to_EMU(10)),
        ),
    )
    ws.column_dimensions.group("N", "Q", hidden=True)


def _add_rt_chart(
    ws, rows: Sequence[Mapping[str, object]], overview: TargetedOverview
) -> bool:
    samples = {
        name: index
        for index, name in enumerate(
            dict.fromkeys(str(row["SampleName"]) for row in rows), 1
        )
    }
    chart = ScatterChart()
    chart.title = "已計入結果的 RT 分布"
    chart.x_axis.title = "結果表樣本順序"
    chart.y_axis.title = "RT (min)"
    chart.x_axis.axPos = "b"
    chart.y_axis.axPos = "l"
    chart.x_axis.numFmt = "0"
    chart.y_axis.numFmt = "0.0"
    chart.x_axis.scaling.min = 0
    chart.x_axis.scaling.max = max(2, len(samples) + 1)
    chart.x_axis.majorUnit = max(1, ceil((len(samples) + 1) / 6))
    _style_chart(chart, left=0.14)
    chart.scatterStyle = "marker"
    chart.visible_cells_only = False
    palette = ("356D95", "8E678F", "497F6B", "BA844C", "737EB1", "7A8572")
    column = 19
    rt_values: list[float] = []
    by_target: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in rows:
        rt = _number(row.get("RT"))
        if _counted(row) == "TRUE" and rt is not None:
            by_target[str(row["Target"])].append((samples[str(row["SampleName"])], rt))
            rt_values.append(rt)
    for index, target in enumerate(overview.targets):
        points = by_target[target.label]
        if not points:
            continue
        ws.cell(1, column, "樣本順序")
        ws.cell(1, column + 1, _excel_text(target.label))
        for row_index, (sample_index, rt) in enumerate(points, 2):
            ws.cell(row_index, column, sample_index)
            ws.cell(row_index, column + 1, rt)
        series = Series(
            Reference(ws, min_col=column + 1, min_row=2, max_row=len(points) + 1),
            Reference(ws, min_col=column, min_row=2, max_row=len(points) + 1),
            title=target.label,
        )
        color = palette[index % len(palette)]
        series.marker = Marker(symbol="circle", size=5)
        series.marker.graphicalProperties.solidFill = color
        series.marker.graphicalProperties.line.noFill = True
        series.graphicalProperties.line.noFill = True
        chart.series.append(series)
        column += 2
    if not chart.series:
        return False
    chart.y_axis.scaling.min = floor(min(rt_values) - 0.1)
    chart.y_axis.scaling.max = ceil(max(rt_values) + 0.1)
    ws.add_chart(
        chart,
        TwoCellAnchor(
            _from=AnchorMarker(col=6, row=11),
            to=AnchorMarker(col=12, row=23, colOff=-pixels_to_EMU(10)),
        ),
    )
    ws.column_dimensions.group("S", get_column_letter(column - 1), hidden=True)
    return True
