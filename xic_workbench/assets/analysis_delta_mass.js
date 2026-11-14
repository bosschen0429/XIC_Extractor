// Delta Mass ion table and preview shared by the Targeted and Untargeted editors.
// Ions are plain objects {label, offset_mz, ratio_min, ratio_max, compare_shape};
// the base ion (Δ 0) is the header, never a row.
import { button, el } from "./analysis_ui.js";

const SVG = "http://www.w3.org/2000/svg";

// Persisted name of an ion added in the editor: ASCII sign, four decimals.
export function ionName(offset) {
  return `${offset < 0 ? "-" : "+"}${Math.abs(offset).toFixed(4)}`;
}
// Exact text of an offset in a target-table cell.
export function formatOffset(offset) {
  return `${offset < 0 ? "-" : "+"}${Math.abs(offset)}`;
}
export function formatOffsets(ions) {
  return ions.map((ion) => formatOffset(ion.offset_mz)).join(", ");
}
export function parseOffsets(text) {
  const parts = String(text || "")
    .split(/[,，]/)
    .map((part) => part.trim())
    .filter(Boolean);
  const values = parts.map(Number);
  if (values.some((value) => !Number.isFinite(value)))
    throw new Error("Δm/z 請以逗號分隔數字，例如 +1.0021, -2.0157。");
  return values;
}
// Shown to four significant digits; an untouched cell keeps the exact values.
export function formatRatioRange(ion) {
  const show = (value) => String(Number(Number(value).toPrecision(4)));
  return ion.ratio_min === ion.ratio_max
    ? show(ion.ratio_min)
    : `${show(ion.ratio_min)}–${show(ion.ratio_max)}`;
}
// One cell: "0.514–2.056", "0.2-0.8" or "0.2~0.8"; one number means equal bounds.
export function parseRatioRange(text) {
  const match = String(text)
    .trim()
    .match(/^([0-9]*\.?[0-9]+(?:e[+-]?\d+)?)\s*(?:[–~\-－～]\s*([0-9]*\.?[0-9]+(?:e[+-]?\d+)?))?$/i);
  if (!match) return null;
  const low = Number(match[1]);
  const high = match[2] === undefined ? low : Number(match[2]);
  return low > 0 && high >= low ? [low, high] : null;
}
export function newIon(offset, defaults = {}) {
  return {
    label: ionName(offset),
    offset_mz: offset,
    ratio_min: 0.1,
    ratio_max: 10,
    compare_shape: true,
    ...defaults,
  };
}
// Bulk entry keeps an existing ion's details when its Δ is unchanged.
export function ionsForOffsets(offsets, existing = []) {
  return offsets.map((offset) => {
    const kept = existing.find((ion) => ion.offset_mz === offset);
    return kept ? { ...kept } : newIon(offset);
  });
}
// Ions whose ratio cell holds text that is not a range; the stored values are
// stale until the cell is corrected, so saving must stop.
const unreadableRatios = new WeakSet();
export function ionsError(ions, { allowNegative }) {
  for (const [index, ion] of ions.entries()) {
    const where = `第 ${index + 1} 個伴隨離子`;
    if (unreadableRatios.has(ion))
      return `${where}：強度比範圍看不懂，請輸入下限–上限（例如 0.2–0.8）或單一數字。`;
    if (ion.offset_mz === null || !Number.isFinite(ion.offset_mz))
      return `${where}：請填寫 Δm/z。`;
    if (ion.offset_mz === 0) return `${where}：Δm/z 不可為 0（0 是基準離子）。`;
    if (!allowNegative && ion.offset_mz < 0)
      return `${where}：Untargeted 的 Δm/z 只能 ≥ 0，基準是最輕的離子。`;
    if (!(ion.ratio_min > 0 && ion.ratio_max >= ion.ratio_min))
      return `${where}：強度比範圍需大於 0，例如 0.2–0.8。`;
  }
  const names = ions.map((ion) => ion.label);
  if (new Set(names).size !== names.length)
    return "Δm/z 太接近：兩個伴隨離子的名稱（四捨五入到 4 位小數）相同。";
  return "";
}

// options: ions (mutated in place), base ("基準＝…" header text), baseMz
// (actual m/z for the preview, or null for Δ), allowNegative, showShape,
// empty (shown while there is no ion), onChange(), and extraHead/extraCells
// for page-specific columns.
export function deltaMassEditor(options) {
  const root = el("div", undefined, "delta-editor");
  const render = () => {
    const table = el("table", undefined, "method-ion-table delta-ion-table");
    const head = el("tr");
    for (const [text, cls] of [
      ["Δm/z（伴隨 − 基準）", "num"],
      ["強度比（伴隨 ÷ 基準，只記錄）", "num"],
      ...(options.showShape ? [["比對峰形", "center"]] : []),
      ["", ""],
    ])
      head.append(el("th", text, cls));
    const caption = el("caption", options.base);
    table.append(caption, head);
    options.ions.forEach((ion, index) => {
      const where = `第 ${index + 1} 個伴隨離子`;
      const row = el("tr");
      const offset = el("input");
      offset.inputMode = "decimal";
      offset.value = ion.offset_mz === null ? "" : formatOffset(ion.offset_mz);
      offset.setAttribute("aria-label", `Δm/z（${where}）`);
      offset.addEventListener("change", () => {
        const value = offset.value.trim() === "" ? null : Number(offset.value);
        if (value !== ion.offset_mz) {
          ion.offset_mz = Number.isFinite(value) ? value : null;
          // A changed Δ is a new ion; it takes its offset's name.
          if (ion.offset_mz !== null) ion.label = ionName(ion.offset_mz);
        }
        if (ion.offset_mz !== null) offset.value = formatOffset(ion.offset_mz);
        changed();
      });
      const ratio = el("input");
      ratio.value = formatRatioRange(ion);
      ratio.setAttribute("aria-label", `強度比範圍（${where}）`);
      ratio.addEventListener("change", () => {
        const range = parseRatioRange(ratio.value);
        ratio.setCustomValidity(range ? "" : "請輸入 0.2–0.8 或單一數字");
        if (range) {
          [ion.ratio_min, ion.ratio_max] = range;
          unreadableRatios.delete(ion);
        } else unreadableRatios.add(ion);
        changed();
      });
      const cells = [[offset, "num"], [ratio, "num"]];
      if (options.showShape) {
        const shape = el("input");
        shape.type = "checkbox";
        shape.checked = ion.compare_shape;
        shape.setAttribute("aria-label", `比對峰形（${where}）`);
        shape.addEventListener("change", () => {
          ion.compare_shape = shape.checked;
          changed();
        });
        cells.push([shape, "center"]);
      }
      for (const [node, cls] of cells) {
        const td = el("td", undefined, cls);
        td.append(node);
        row.append(td);
      }
      const removeCell = el("td");
      const remove = button(
        "",
        () => {
          options.ions.splice(index, 1);
          changed(true);
        },
        "icon-button",
        "x",
      );
      remove.setAttribute("aria-label", `移除${where}`);
      remove.title = "移除";
      removeCell.append(remove);
      row.append(removeCell);
      table.append(row);
    });
    const scroll = el("div", undefined, "table-scroll");
    scroll.append(table);
    const add = button("＋ 伴隨離子", () => {
      options.ions.push({ ...newIon(0), label: "", offset_mz: null });
      changed(true);
      root.querySelector("tr:last-child input")?.focus();
    });
    add.classList.add("delta-add");
    const error = el("p", "", "inline-status error");
    error.setAttribute("role", "alert");
    const problem = ionsError(options.ions, options);
    error.textContent = problem;
    error.hidden = !problem;
    // What to fill, for someone who has never seen a companion ion.
    const hint = el(
      "p",
      "伴隨離子：同一 RT 一起出現的其他 MS1 離子。Δm/z 填精確質量差，如 +6.0201。",
      "subtle delta-hint",
    );
    const parts = [hint];
    // Without an ion the condition is off; say how it turns on and from which base.
    if (!options.ions.length && options.empty)
      parts.push(el("p", options.empty, "delta-empty"));
    root.replaceChildren(...parts, scroll, add, error, previewPlot(options.ions, options));
  };
  const changed = (structural = false) => {
    options.onChange?.();
    if (structural) render();
    else {
      const problem = ionsError(options.ions, options);
      const error = root.querySelector(".inline-status");
      error.textContent = problem;
      error.hidden = !problem;
      root.querySelector(".delta-plot")?.replaceWith(
        previewPlot(options.ions, options),
      );
    }
  };
  render();
  return root;
}

function svgNode(tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
// Base and companion ions; the intensity-ratio range is a band on a log axis.
export function previewPlot(ions, { baseMz = null } = {}) {
  const shown = ions.filter((ion) => Number.isFinite(ion.offset_mz));
  const points = [
    { label: "基準", offset: 0, low: 1, high: 1 },
    ...shown.map((ion) => ({
      label: ion.label,
      offset: ion.offset_mz,
      low: ion.ratio_min,
      high: ion.ratio_max,
    })),
  ];
  const width = 520;
  const height = 170;
  const pad = { left: 44, right: 16, top: 12, bottom: 34 };
  const offsets = points.map((p) => p.offset);
  let [x0, x1] = [Math.min(...offsets), Math.max(...offsets)];
  const span = Math.max(x1 - x0, 1);
  x0 -= span * 0.12;
  x1 += span * 0.12;
  const ratios = points.flatMap((p) => [p.low, p.high]);
  const y0 = Math.log10(Math.min(0.05, ...ratios) / 1.5);
  const y1 = Math.log10(Math.max(10, ...ratios) * 1.5);
  const x = (v) =>
    pad.left + ((v - x0) / (x1 - x0)) * (width - pad.left - pad.right);
  const y = (v) =>
    pad.top +
    (1 - (Math.log10(v) - y0) / (y1 - y0)) * (height - pad.top - pad.bottom);
  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "delta-plot",
    role: "img",
    "aria-label": "伴隨離子預覽：橫軸 m/z，縱軸強度比（對數）",
  });
  const floor = height - pad.bottom;
  svg.append(
    svgNode("line", { x1: pad.left, x2: width - pad.right, y1: floor, y2: floor, class: "axis" }),
    svgNode("line", { x1: pad.left, x2: pad.left, y1: pad.top, y2: floor, class: "axis" }),
  );
  for (const tick of [0.1, 1, 10]) {
    if (Math.log10(tick) < y0 || Math.log10(tick) > y1) continue;
    svg.append(
      svgNode("line", { x1: pad.left, x2: width - pad.right, y1: y(tick), y2: y(tick), class: "grid" }),
      svgNode("text", { x: pad.left - 6, y: y(tick) + 4, class: "tick", "text-anchor": "end" }, String(tick)),
    );
  }
  for (const point of points) {
    const px = x(point.offset);
    const mid = Math.sqrt(point.low * point.high);
    if (point.high > point.low)
      svg.append(
        svgNode("rect", {
          x: px - 7,
          y: y(point.high),
          width: 14,
          height: Math.max(2, y(point.low) - y(point.high)),
          class: "band",
        }),
      );
    svg.append(
      svgNode("line", { x1: px, x2: px, y1: floor, y2: y(mid), class: point.offset === 0 ? "stick base" : "stick" }),
      svgNode(
        "text",
        { x: px, y: floor + 14, class: "tick", "text-anchor": "middle" },
        baseMz === null
          ? point.offset === 0
            ? "Δ 0"
            : formatOffset(point.offset)
          : (baseMz + point.offset).toFixed(4),
      ),
      svgNode("text", { x: px, y: floor + 28, class: "tick", "text-anchor": "middle" }, point.label),
    );
  }
  return svg;
}
