// Target tables, per-target specificity and Targeted batch settings.
import { analysis } from "./analysis_jobs.js";
import { sampleMetadata, samplePages } from "./analysis_samples.js";
import {
  deltaMassEditor,
  formatOffsets,
  ionsError,
  ionName,
  ionsForOffsets,
  parseOffsets,
} from "./analysis_delta_mass.js";
import { checklist, dirty, setMethodState } from "./analysis_setup.js";
import {
  addTarget,
  removeTarget,
  setDeltaMass,
  setImporting,
  setTargets,
  state,
  updateConfig,
  updateTarget,
  updateTargetedSettings,
} from "./analysis_state.js";
import {
  $,
  OUTPUT_LABEL,
  act,
  api,
  bindListUpload,
  button,
  done,
  el,
  focusField,
  isBusy,
  isTemplatePath,
  lock,
  pathField,
  readListFile,
  requestPath,
} from "./analysis_ui.js";

let pendingTargetImport = null;

export function settingsValues() {
  const values = { ...state.config.settings };
  for (const spec of state.config.targeted_form.settings) {
    const input = $(`t-${spec.key}`);
    if (!input) continue;
    if (spec.kind === "checkbox") values[spec.key] = String(input.checked);
    // The existing placeholder represents an unselected RAW folder, including
    // after starting a new batch; never fall back to the previous real folder.
    else
      values[spec.key] =
        input.value ||
        (isTemplatePath(String(spec.default ?? "")) ? spec.default : "");
  }
  return values;
}
export function renderSettings(values) {
  const data = $("targeted-data");
  const method = $("targeted-settings");
  const outputDir =
    $("t-output_dir")?.value ?? state.config.targeted_output_dir;
  for (const panel of [data, method]) panel.replaceChildren();
  const note = el("div", undefined, "method-note");
  note.id = "target-method-note";
  method.append(
    el(
      "p",
      "目標清單決定名稱、m/z、預期 RT、特異性與 ISTD 配對；其餘沿用平台共用策略。",
      "settings-notice",
    ),
    note,
  );
  // Paths use the same rows as Untargeted: RAW, run inputs, output, machine.
  for (const spec of state.config.targeted_form.settings) {
    if (
      spec.kind === "readonly" ||
      spec.key === "signal_processing_method" ||
      spec.key === "count_no_ms2_as_detected"
    )
      continue;
    if (!["folder", "file"].includes(spec.kind)) {
      continue;
    }
    if (spec.scope === "environment")
      pathField(
        data,
        "output_dir",
        OUTPUT_LABEL,
        "folder",
        outputDir,
        "t-",
      );
    if (spec.key === "injection_order_source") {
      const value = String(values[spec.key] || "");
      $(samplePages.targeted.sequence).value = isTemplatePath(value)
        ? ""
        : value;
      continue;
    }
    pathField(
      data,
      spec.key,
      spec.label,
      spec.browse || spec.kind,
      values[spec.key],
      "t-",
      spec.help,
    );
  }
  setMethodState(state.config.targeted_method);
  $("t-data_dir").dataset.batchPath = values.data_dir || "";
  lock();
}
function applyTargetedDraft(result) {
  setDeltaMass(result.delta_mass);
  updateConfig({ settings: result.settings });
  renderSettings(result.settings);
  setMethodState(result.summary);
  dirty("targeted");
}
export function renderTargetedMethods(methods, selected = "") {
  const select = $("target-method-select");
  select.replaceChildren(new Option("目前工作區設定", ""));
  for (const method of methods)
    select.add(new Option(method.name, method.path));
  select.value = selected;
}
async function loadTargetedMethod(path) {
  if (!path) throw new Error("請先選擇清單。");
  const result = await api("/api/targeted/method/load", {
    path,
    settings: settingsValues(),
  });
  setTargets(result.targets);
  applyTargetedDraft(result);
  targetCount();
  done("已載入目標清單；批次及本機設定保持不變。儲存或開始分析後套用。");
}
async function previewTargetFiles(files) {
  if (isBusy()) return;
  const message = $("target-import-status");
  message.textContent = "";
  message.className = "";
  pendingTargetImport = null;
  try {
    setImporting(true);
    lock();
    message.textContent = "正在讀取檔案…";
    const upload = await readListFile(files);
    const result = await api("/api/targets/upload", {
      ...upload,
      settings: settingsValues(),
    });
    pendingTargetImport = result;
    const istdCount = result.targets.filter(
      (row) => row.role === "istd",
    ).length;
    $("target-import-summary").textContent =
      `${upload.name} · ${result.kind === "method" ? "方法檔（Method + Targets）" : "目標表"} · ${result.targets.length} 個目標（ISTD ${istdCount} 個）`;
    const body = $("target-import-rows");
    body.replaceChildren();
    for (const row of result.targets) {
      const tr = document.createElement("tr");
      for (const value of [
        row.label,
        row.mz,
        row.expected_rt_min,
        row.role === "istd" ? "ISTD" : "Analyte",
        row.istd_pair || "—",
        specificityLabel(row),
      ]) {
        const td = document.createElement("td");
        td.textContent = value || "";
        tr.append(td);
      }
      body.append(tr);
    }
    message.textContent = "已辨識檔案，請確認預覽；取消不會變更目前清單。";
    $("target-import-preview").showModal();
  } catch (error) {
    message.textContent = error.message;
    message.className = "error";
  } finally {
    setImporting(false);
    lock();
  }
}
export async function saveTargeted(quiet = false) {
  const settings = settingsValues();
  const output_dir = $("t-output_dir").value;
  const receipt = await api("/api/targeted/save", {
    settings,
    ...targetedListValues(),
    output_dir,
  });
  updateConfig({
    settings,
    targeted_output_dir: output_dir,
    settings_source: "workspace.json",
    targeted_method: receipt.summary,
  });
  setMethodState(receipt.summary);
  dirty("targeted", false);
  if (!quiet) done("Targeted 設定與目標已儲存。");
}
const isIstd = (row) => row.role === "istd";
// Delta Mass details of one target; the row's Δm/z cell follows from them.
function targetIons(row) {
  return state.deltaMass.ions[row.label] || [];
}
function setTargetIons(row, ions) {
  if (ions.length) state.deltaMass.ions[row.label] = ions;
  else delete state.deltaMass.ions[row.label];
  updateTarget(row, { delta_mass_mz: formatOffsets(ions) });
}
// Δm/z as shown: four decimals, like the ion names; the cell keeps full precision.
function deltaText(row) {
  try {
    return parseOffsets(row.delta_mass_mz).map(ionName).join(", ");
  } catch {
    return String(row.delta_mass_mz || "");
  }
}
export function specificityLabel(row) {
  const loss = String(row.cid_nl_loss_da || "").trim();
  const delta = deltaText(row);
  const parts = [];
  if (loss) parts.push(`CID-NL ${loss}`);
  if (delta) parts.push(`Delta Mass ${delta}`);
  return parts.join(" ＋ ") || "MS1 only";
}
// The payload the server admits with the rows: shared settings and ions.
function deltaMassPayload() {
  const labels = new Set(state.targets.map((row) => row.label));
  const ions = Object.fromEntries(
    Object.entries(state.deltaMass.ions).filter(
      ([label, list]) => labels.has(label) && list.length,
    ),
  );
  return { settings: state.deltaMass.settings, ions };
}
function targetedListValues() {
  return { targets: state.targets, delta_mass: deltaMassPayload() };
}
export function targetCount() {
  const istd = state.targets.filter(isIstd).length;
  $("target-count").textContent = state.targets.length
    ? `${state.targets.length} 個目標 · ${istd} 個 ISTD`
    : "尚未載入目標";
  renderTargetPreview();
  checklist("targeted");
}
function renderTargetPreview() {
  const preview = $("target-preview");
  // An empty list leaves the drop zone as the only thing to act on.
  preview.hidden = !state.targets.length;
  if (!state.targets.length) {
    preview.replaceChildren();
    return;
  }
  const table = el("table");
  const head = el("tr");
  for (const [label, cls] of [
    ["名稱", ""],
    ["m/z", "num"],
    ["預期 RT (min)", "num"],
    ["特異性", ""],
    ["ISTD 配對", ""],
  ])
    head.append(el("th", label, cls));
  const thead = el("thead");
  thead.append(head);
  const body = el("tbody");
  for (const row of state.targets.slice(0, 6)) {
    const tr = el("tr");
    const name = el("td");
    name.append(el("span", row.label || "（未命名）"));
    if (isIstd(row)) name.append(" ", el("span", "ISTD", "istd-flag"));
    tr.append(
      name,
      el("td", row.mz || "—", "num"),
      el("td", row.expected_rt_min || "—", "num"),
      el("td", specificityLabel(row)),
      el("td", row.istd_pair || "—"),
    );
    body.append(tr);
  }
  table.append(thead, body);
  const scroll = el("div", undefined, "table-scroll");
  scroll.append(table);
  const nodes = [scroll];
  if (state.targets.length > 6)
    nodes.push(
      el(
        "p",
        `另外 ${state.targets.length - 6} 個目標，按「檢視與編輯」查看完整清單。`,
        "target-preview-more",
      ),
    );
  preview.replaceChildren(...nodes);
}

// Target editor: rows are selected like samples — drag a band over the row
// numbers, Ctrl adds, Shift extends, and a search selects what it finds.
const selection = { rows: new Set(), anchor: null };
let drag = null;
let suppressClickUntil = 0;
let expandedRow = null;
function visibleTargets() {
  const filter = $("target-filter").value.trim().toLowerCase();
  return state.targets.filter(
    (row) =>
      !filter ||
      [row.label, row.mz, specificityLabel(row)]
        .join(" ")
        .toLowerCase()
        .includes(filter),
  );
}
function renderSelection() {
  const known = new Set(state.targets);
  selection.rows = new Set([...selection.rows].filter((row) => known.has(row)));
  const count = selection.rows.size;
  $("target-selection").textContent = count
    ? `已選 ${count} 個目標`
    : "先框選或搜尋";
  for (const node of $("target-bulk").querySelectorAll("input, button"))
    node.disabled = !count;
  for (const tr of $("target-rows").querySelectorAll("tr[data-target]"))
    tr.classList.toggle(
      "selected",
      selection.rows.has(state.targets[Number(tr.dataset.target)]),
    );
}
function clickHandle(row, event) {
  if (event.shiftKey && selection.anchor !== null) {
    const order = visibleTargets();
    const [a, b] = [order.indexOf(selection.anchor), order.indexOf(row)].sort(
      (x, y) => x - y,
    );
    if (a >= 0) for (const each of order.slice(a, b + 1)) selection.rows.add(each);
  } else if (event.ctrlKey || event.metaKey) {
    if (!selection.rows.delete(row)) selection.rows.add(row);
    selection.anchor = row;
  } else {
    const only = selection.rows.size === 1 && selection.rows.has(row);
    selection.rows = only ? new Set() : new Set([row]);
    selection.anchor = row;
  }
  renderSelection();
}
function startDrag(event) {
  const handle = event.target.closest("td.target-handle");
  if (!handle || event.button !== 0 || event.shiftKey) return;
  const box = $("target-table");
  const bounds = box.getBoundingClientRect();
  drag = {
    box,
    band: null,
    y0: event.clientY - bounds.top + box.scrollTop,
    base: event.ctrlKey || event.metaKey ? new Set(selection.rows) : new Set(),
    last: null,
    moved: false,
  };
  event.preventDefault();
}
function moveDrag(event) {
  if (!drag) return;
  const { box } = drag;
  const bounds = box.getBoundingClientRect();
  const y = event.clientY - bounds.top + box.scrollTop;
  if (!drag.moved && Math.abs(y - drag.y0) < 4) return;
  if (!drag.moved) {
    drag.moved = true;
    drag.band = el("div", undefined, "select-band");
    box.append(drag.band);
  }
  if (event.clientY > bounds.bottom - 28) box.scrollTop += 14;
  else if (event.clientY < bounds.top + 28) box.scrollTop -= 14;
  const y1 = Math.max(0, Math.min(y, box.scrollHeight));
  const top = Math.min(drag.y0, y1);
  const bottom = Math.max(drag.y0, y1);
  drag.band.style.top = `${top}px`;
  drag.band.style.height = `${bottom - top}px`;
  const hit = new Set(drag.base);
  for (const tr of box.querySelectorAll("tr[data-target]")) {
    const rect = tr.getBoundingClientRect();
    const a = rect.top - bounds.top + box.scrollTop;
    if (a + rect.height > top && a < bottom) {
      const row = state.targets[Number(tr.dataset.target)];
      hit.add(row);
      drag.last = row;
    }
  }
  selection.rows = hit;
  renderSelection();
}
function endDrag(event) {
  if (!drag) return;
  const ended = drag;
  drag = null;
  ended.band?.remove();
  if (ended.moved) {
    if (ended.last !== null) selection.anchor = ended.last;
    suppressClickUntil = event.timeStamp + 80;
  }
}

const targetColumns = [
  ["label", "名稱"],
  ["pairing", "角色／ISTD 配對"],
  ["mz", "m/z"],
  ["expected_rt_min", "預期 RT（min）"],
  ["specificity", "特異性"],
  ["cid_nl_loss_da", "CID-NL 中性丟失（Da）"],
  ["delta_mass_mz", "Delta Mass Δm/z"],
];
function renderTargets() {
  const head = el("tr");
  head.append(el("th", "#", "target-handle"));
  for (const [, label] of targetColumns) head.append(el("th", label));
  head.append(el("th", ""));
  $("target-head").replaceChildren(head);
  const body = $("target-rows");
  body.replaceChildren();
  for (const row of visibleTargets()) {
    const index = state.targets.indexOf(row);
    const tr = el("tr");
    tr.dataset.target = String(index);
    const handle = el("td", String(index + 1), "target-handle");
    handle.title = "點選、拖曳框選；Ctrl 加選、Shift 連選";
    tr.append(handle);
    for (const [key, label] of targetColumns) {
      const td = el("td");
      const where = `${label}（第 ${index + 1} 列）`;
      if (key === "specificity") {
        td.textContent = specificityLabel(row);
        td.className = "target-specificity";
      } else if (key === "delta_mass_mz") {
        const open = button(
          deltaText(row) || "—",
          () => {
            expandedRow = expandedRow === row ? null : row;
            renderTargets();
          },
          "target-delta-cell",
        );
        open.setAttribute("aria-label", `編輯 Delta Mass Δm/z（第 ${index + 1} 列）`);
        open.setAttribute("aria-expanded", String(expandedRow === row));
        td.append(open);
      } else if (key === "pairing") {
        td.append(pairingSelect(row, where));
      } else {
        const input = el("input");
        input.setAttribute("aria-label", where);
        input.value = row[key] || "";
        if (key !== "label") input.inputMode = "decimal";
        if (key === "cid_nl_loss_da") {
          input.setAttribute("list", "cid-nl-losses");
          input.placeholder = "—";
        }
        input.addEventListener("input", () => {
          if (key === "label") renameTarget(row, input.value);
          else updateTarget(row, { [key]: input.value.trim() });
          tr.querySelector(".target-specificity").textContent =
            specificityLabel(row);
          if (key === "mz" && expandedRow === row) renderExpanded(tr, row);
          dirty("targeted");
          targetCount();
        });
        td.append(input);
      }
      tr.append(td);
    }
    const td = el("td");
    const remove = button(
      "",
      () => {
        setTargetIons(row, []);
        removeTarget(index);
        if (expandedRow === row) expandedRow = null;
        dirty("targeted");
        renderTargets();
      },
      "icon-button",
      "x",
    );
    remove.setAttribute("aria-label", `刪除第 ${index + 1} 列`);
    remove.title = "刪除這一列";
    td.append(remove);
    tr.append(td);
    body.append(tr);
    if (expandedRow === row) renderExpanded(tr, row);
  }
  renderSelection();
  targetCount();
}
function renameTarget(row, label) {
  const ions = state.deltaMass.ions[row.label];
  delete state.deltaMass.ions[row.label];
  updateTarget(row, { label });
  if (ions) state.deltaMass.ions[label] = ions;
}
function pairingSelect(row, where) {
  const input = el("select");
  input.setAttribute("aria-label", where);
  const choices = [
    ["", "未配對"],
    ["role:istd", "此列是 ISTD"],
  ];
  for (const standard of state.targets.filter(isIstd))
    if (standard !== row) choices.push([`pair:${standard.label}`, standard.label]);
  const value = isIstd(row)
    ? "role:istd"
    : row.istd_pair
      ? `pair:${row.istd_pair}`
      : "";
  if (!choices.some(([key]) => key === value))
    choices.push([value, `${row.istd_pair}（請重新選擇）`]);
  for (const [key, label] of choices) {
    const option = el("option", label);
    option.value = key;
    input.append(option);
  }
  input.value = value;
  input.addEventListener("input", () => {
    updateTarget(row, {
      role: input.value === "role:istd" ? "istd" : "analyte",
      istd_pair: input.value.startsWith("pair:") ? input.value.slice(5) : "",
    });
    dirty("targeted");
    renderTargets();
  });
  return input;
}
// One target's companion ions, shown under its row with the actual m/z.
function renderExpanded(tr, row) {
  tr.nextElementSibling?.classList.contains("target-delta-row") &&
    tr.nextElementSibling.remove();
  const ions = (state.deltaMass.ions[row.label] ||= []);
  const detail = el("tr", undefined, "target-delta-row");
  const td = el("td");
  td.colSpan = targetColumns.length + 2;
  const mz = Number(row.mz);
  td.append(
    deltaMassEditor({
      ions,
      base: `${row.label || "此目標"} · 基準＝目標 m/z（定量）${Number.isFinite(mz) && mz > 0 ? ` ${row.mz}` : ""}`,
      baseMz: Number.isFinite(mz) && mz > 0 ? mz : null,
      allowNegative: true,
      showShape: true,
      onChange: () => {
        const valid = ions.filter((ion) => Number.isFinite(ion.offset_mz));
        updateTarget(row, { delta_mass_mz: formatOffsets(valid) });
        const cell = tr.querySelector(".target-delta-cell");
        cell.lastChild.textContent = deltaText(row) || "—";
        tr.querySelector(".target-specificity").textContent =
          specificityLabel(row);
        dirty("targeted");
        targetCount();
      },
    }),
  );
  detail.append(td);
  tr.after(detail);
}
function renderSharedSettings() {
  const grid = $("target-delta-settings");
  grid.replaceChildren();
  for (const [key, text] of [
    ["mz_tolerance_ppm", "m/z 容差（ppm）"],
    ["apex_floor_sec", "共洗脫容差下限（秒）"],
    ["scan_interval_multiplier", "掃描間隔倍數"],
    ["shape_window_sec", "峰形比對半窗（秒）"],
    ["min_shape_r", "最低峰形 r"],
  ]) {
    const label = el("label", text);
    const input = el("input");
    input.type = "number";
    input.step = "any";
    input.value = state.deltaMass.settings[key] ?? "";
    input.addEventListener("input", () => {
      state.deltaMass.settings[key] =
        input.value === "" ? null : Number(input.value);
      dirty("targeted");
    });
    label.append(input);
    grid.append(label);
  }
}
function targetListError() {
  for (const row of state.targets) {
    const ions = targetIons(row);
    if (!ions.length) continue;
    const problem = ionsError(ions, { allowNegative: true });
    if (problem) return `${row.label || "未命名目標"}：${problem}`;
  }
  return "";
}
function applyBulk(ms1Only) {
  const rows = [...selection.rows];
  if (!rows.length) return;
  const cid = ms1Only ? "" : $("target-bulk-cid").value.trim();
  const deltaText = ms1Only ? "" : $("target-bulk-delta").value.trim();
  const offsets = deltaText ? parseOffsets(deltaText) : null;
  for (const row of rows) {
    if (ms1Only || cid) updateTarget(row, { cid_nl_loss_da: cid });
    // Each target keeps its own copy of the ion details.
    if (ms1Only) setTargetIons(row, []);
    else if (offsets) setTargetIons(row, ionsForOffsets(offsets, targetIons(row)));
  }
  dirty("targeted");
  renderTargets();
  done(
    ms1Only
      ? `已將 ${rows.length} 個目標設為 MS1 only。`
      : `已套用到 ${rows.length} 個目標。`,
  );
}
export function renderCidSuggestions() {
  $("cid-nl-losses").replaceChildren(
    ...state.config.targeted_specificity.cid_nl_losses.map(({ name, loss_da }) => {
      const option = el("option");
      option.value = String(loss_da);
      option.label = `${loss_da}（${name}）`;
      return option;
    }),
  );
}
function openTargetEditor() {
  selection.rows = new Set();
  selection.anchor = null;
  expandedRow = null;
  $("target-filter").value = "";
  $("target-editor-error").hidden = true;
  // Advanced settings start collapsed each time; editing keeps them open.
  $("target-delta-advanced").open = false;
  renderSharedSettings();
  renderTargets();
  $("target-editor").showModal();
}

export function bindTargeted() {
  $("targeted-form").addEventListener("input", () => dirty("targeted"));

  $("targeted-form").addEventListener("submit", (event) => {
    event.preventDefault();
    act(() =>
      analysis.start("targeted", {
        settings: settingsValues(),
        ...targetedListValues(),
        output_dir: $("t-output_dir").value,
      }),
    );
  });

  $("target-save").onclick = () => act(() => saveTargeted());
  $("target-method-load").onclick = () =>
    act(() => loadTargetedMethod($("target-method-select").value));
  $("target-method-save").onclick = () =>
    act(async () => {
      const name = await requestPath(
        "另存目標清單",
        "",
        "清單名稱（不含 .xlsx）",
      );
      if (!name) return;
      const result = await api("/api/targeted/method/save", {
        name,
        settings: settingsValues(),
        ...targetedListValues(),
      });
      updateConfig({ targeted_methods: result.methods });
      renderTargetedMethods(
        result.methods,
        result.methods.find((method) => method.name === result.name)?.path,
      );
      done(`已另存目標清單：${result.name}`);
    });

  $("target-new").onclick = () => {
    $("t-data_dir").value = "";
    $("t-injection_order_source").value = "";
    updateTargetedSettings({ data_dir: "", injection_order_source: "" });
    sampleMetadata.clear("targeted");
    $("target-result").hidden = true;
    dirty("targeted");
    focusField("t-data_dir");
    done(
      "已開始新批次：保留目標清單、方法與結果存放位置，請選擇新的 RAW 資料夾。",
    );
  };
  $("targeted-data").addEventListener("input", (event) => {
    if (
      event.target.id === "t-data_dir" &&
      event.target.value !== event.target.dataset.batchPath
    ) {
      $("t-injection_order_source").value = "";
      event.target.dataset.batchPath = event.target.value;
      sampleMetadata.clear("targeted");
    }
  });
  $("target-edit").onclick = openTargetEditor;
  $("target-filter").oninput = () => {
    selection.rows = new Set();
    selection.anchor = null;
    if ($("target-filter").value.trim())
      selection.rows = new Set(visibleTargets());
    renderTargets();
  };
  $("target-rows").addEventListener("pointerdown", startDrag);
  document.addEventListener("pointermove", moveDrag);
  document.addEventListener("pointerup", endDrag);
  $("target-rows").addEventListener("click", (event) => {
    const handle = event.target.closest("td.target-handle");
    if (!handle || event.timeStamp < suppressClickUntil) return;
    const tr = handle.closest("tr[data-target]");
    clickHandle(state.targets[Number(tr.dataset.target)], event);
  });
  $("target-bulk-apply").onclick = () => act(() => applyBulk(false));
  $("target-bulk-ms1").onclick = () => act(() => applyBulk(true));
  $("target-add").onclick = () => {
    addTarget({
      label: "",
      mz: "",
      expected_rt_min: "",
      role: "analyte",
      istd_pair: "",
      cid_nl_loss_da: state.targets.at(-1)?.cid_nl_loss_da || "",
      delta_mass_mz: "",
    });
    dirty("targeted");
    renderTargets();
    $("target-rows")
      .querySelector("tr[data-target]:last-of-type input")
      ?.focus();
  };
  $("target-editor-save").onclick = () =>
    act(async () => {
      const problem = targetListError();
      $("target-editor-error").textContent = problem;
      $("target-editor-error").hidden = !problem;
      if (problem) return;
      await saveTargeted();
      $("target-editor").close();
    });
  $("target-export").onclick = () =>
    act(async () => {
      const path = await requestPath("匯出目標清單（.xlsx）");
      if (!path) return;
      const result = await api("/api/targets/export", {
        path,
        ...targetedListValues(),
      });
      done(result.message);
    });
  bindListUpload("target", previewTargetFiles);

  $("target-import-preview").addEventListener("close", () => {
    if (pendingTargetImport)
      $("target-import-status").textContent = "已取消匯入，目前清單未變更。";
    pendingTargetImport = null;
  });
  $("target-import-confirm").onclick = () => {
    if (!pendingTargetImport || isBusy()) return;
    const result = pendingTargetImport;
    pendingTargetImport = null;
    setTargets(result.targets);
    if (result.kind === "method") applyTargetedDraft(result);
    else setDeltaMass(null);
    $("target-method-select").value = "";
    targetCount();
    dirty("targeted");
    $("target-import-preview").close();
    $("target-import-status").textContent =
      `已匯入 ${state.targets.length} 個目標；儲存或開始分析後套用。`;
  };
}
