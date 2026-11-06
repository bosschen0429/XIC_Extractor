// Target tables, named identification methods and Targeted batch settings.
import { startJob } from "./analysis_jobs.js";
import { clearSamples, samplePages } from "./analysis_samples.js";
import { checklist, dirty, setMethodState } from "./analysis_setup.js";
import {
  addTarget,
  removeTarget,
  setIdentificationMethods,
  setImporting,
  setTargets,
  state,
  updateConfig,
  updateTarget,
  updateTargetedSettings,
} from "./analysis_state.js";
import {
  $,
  OUTPUT_HELP,
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
      "目標清單決定名稱、m/z、預期 RT、辨認方法與 ISTD 配對；其餘沿用平台共用策略。",
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
        OUTPUT_HELP,
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
  setIdentificationMethods(result.identification_methods || {});
  renderIdentificationSummary();
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
    targets: state.targets,
    output_dir,
    identification_methods: state.identificationMethods,
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
    ["辨認方法", ""],
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
      el("td", methodLabel(row.method || "")),
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
const targetColumns = [
  ["label", "名稱"],
  ["pairing", "角色／ISTD 配對"],
  ["mz", "m/z"],
  ["expected_rt_min", "預期 RT（min）"],
  ["method", "辨認方法"],
];
function renderTargets() {
  const columns = targetColumns;
  fillMethodOptions($("target-bulk-method"));
  const head = el("tr");
  for (const [, label] of columns) head.append(el("th", label));
  head.append(el("th", ""));
  $("target-head").replaceChildren(head);
  const body = $("target-rows");
  body.replaceChildren();
  state.targets.forEach((row, index) => {
    const tr = el("tr");
    for (const [key, label] of columns) {
      const td = el("td");
      const input = el(
        ["pairing", "method"].includes(key) ? "select" : "input",
      );
      input.setAttribute("aria-label", `${label}（第 ${index + 1} 列）`);
      if (key === "pairing") {
        const choices = [
          ["", "未配對"],
          ["role:istd", "此列是 ISTD"],
        ];
        for (const standard of state.targets.filter(isIstd)) {
          if (standard !== row)
            choices.push([`pair:${standard.label}`, standard.label]);
        }
        const value = isIstd(row)
          ? "role:istd"
          : row.istd_pair
            ? `pair:${row.istd_pair}`
            : "";
        if (!choices.some(([key]) => key === value))
          choices.push([value, `${row.istd_pair}（請重新選擇）`]);
        for (const [value, label] of choices) {
          const option = el("option", label);
          option.value = value;
          input.append(option);
        }
        input.value = value;
      } else if (key === "method") {
        fillMethodOptions(input, row.method || "");
      } else {
        input.value = row[key] || "";
        if (["mz", "expected_rt_min"].includes(key))
          input.inputMode = "decimal";
      }
      input.addEventListener("input", () => {
        if (key === "pairing") {
          updateTarget(row, {
            role: input.value === "role:istd" ? "istd" : "analyte",
            istd_pair: input.value.startsWith("pair:")
              ? input.value.slice(5)
              : "",
          });
          renderTargets();
        } else if (key === "method") {
          updateTarget(row, { method: input.value });
          renderTargets();
        } else updateTarget(row, { [key]: input.value });
        dirty("targeted");
        targetCount();
      });
      td.append(input);
      tr.append(td);
    }
    const td = el("td");
    const remove = button(
      "",
      () => {
        removeTarget(index);
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
  });
  targetCount();
}

// A target's method is blank (MS1 only), a built-in CID-NL tag, or the name
// of a user-defined identification method.
const CID_NL_TAGS = ["dR", "R", "MeR"];
function methodLabel(name) {
  if (!name) return "MS1 only";
  if (CID_NL_TAGS.includes(name)) return `CID-NL · ${name}`;
  const method = state.identificationMethods[name];
  if (!method) return `${name}（未定義，請重新選擇）`;
  return `${name}（${method.combine_mode === "any" ? "任一" : "全部"}符合 ${method.conditions.length} 個條件）`;
}
function fillMethodOptions(select, value = select.value) {
  select.replaceChildren(new Option("MS1 only", ""));
  for (const tag of CID_NL_TAGS) select.add(new Option(methodLabel(tag), tag));
  for (const name of Object.keys(state.identificationMethods))
    select.add(new Option(methodLabel(name), name));
  if (![...select.options].some((option) => option.value === value))
    select.add(new Option(methodLabel(value), value));
  select.value = value;
}
export function renderIdentificationSummary() {
  const names = Object.keys(state.identificationMethods);
  $("target-identification-summary").textContent = names.length
    ? names.join("、")
    : "尚未建立；內建的 MS1 only 與 CID-NL dR／R／MeR 可直接在目標表選用。";
}
let identificationDraft = null;
let identificationEditingName = "";
// Keep the quantitation role fixed even while a draft has duplicate offsets.
const draftQuantitationIons = new WeakMap();
function newDeltaCondition() {
  const condition = {
    kind: "delta_mass",
    positions: [
      {
        label: "quant",
        offset_mz: 0,
        ratio_min: 0.1,
        ratio_max: 10,
        compare_shape: false,
      },
      {
        label: "partner_1",
        offset_mz: null,
        ratio_min: 0.1,
        ratio_max: 10,
        compare_shape: true,
      },
    ],
    apex_floor_sec: 1,
    scan_interval_multiplier: 1.25,
    shape_window_sec: 10,
    min_shape_r: 0.9,
  };
  draftQuantitationIons.set(condition, condition.positions[0]);
  return condition;
}
function loadIdentificationDraft(name = "") {
  identificationEditingName = name;
  identificationDraft = structuredClone(
    state.identificationMethods[name] || {
      combine_mode: "all",
      mz_tolerance_ppm: 20,
      conditions: [],
    },
  );
  for (const condition of identificationDraft.conditions) {
    if (condition.kind === "delta_mass")
      draftQuantitationIons.set(
        condition,
        condition.positions.find((position) => position.offset_mz === 0),
      );
  }
  const select = $("target-identification-select");
  select.replaceChildren();
  if (!name) select.add(new Option("（新方法，尚未儲存）", ""));
  for (const existing of Object.keys(state.identificationMethods))
    select.add(new Option(existing, existing));
  select.value = name;
  $("target-identification-delete").disabled = !name;
  $("target-identification-name").value = name;
  $("target-identification-combine").value = identificationDraft.combine_mode;
  $("target-identification-ppm").value = identificationDraft.mz_tolerance_ppm;
  $("target-identification-error").textContent = "";
  renderIdentificationConditions();
}
function numericEditor(value, label, update) {
  const input = el("input");
  input.type = "number";
  input.step = "any";
  input.value = value ?? "";
  input.setAttribute("aria-label", label);
  input.oninput = () => update(input.value === "" ? null : Number(input.value));
  return input;
}
function removeButton(label, fn) {
  const remove = button("", fn, "icon-button", "x");
  remove.setAttribute("aria-label", label);
  remove.title = label;
  return remove;
}
function renderIdentificationConditions() {
  const body = $("target-identification-conditions");
  body.replaceChildren();
  if (!identificationDraft.conditions.length)
    body.append(
      el(
        "p",
        "尚未加入條件。用下方按鈕加入 CID-NL 或 Delta Mass 條件。",
        "method-empty",
      ),
    );
  identificationDraft.conditions.forEach((condition, index) => {
    const number = index + 1;
    const card = el("section", undefined, "method-condition");
    const head = el("div", undefined, "method-condition-head");
    head.append(
      el(
        "h3",
        `${number}. ${condition.kind === "cid_nl" ? "CID-NL" : "Delta Mass"}`,
      ),
    );
    if (condition.kind === "cid_nl") {
      const label = el("label", "中性丟失", "method-inline-field");
      const select = el("select");
      select.setAttribute("aria-label", `CID-NL 類型（條件 ${number}）`);
      for (const tag of CID_NL_TAGS) select.add(new Option(tag, tag));
      select.value = condition.tag;
      select.onchange = () => {
        condition.tag = select.value;
      };
      label.append(select);
      head.append(label);
    }
    head.append(
      removeButton(`移除條件 ${number}`, () => {
        identificationDraft.conditions.splice(index, 1);
        renderIdentificationConditions();
      }),
    );
    card.append(head);
    if (condition.kind === "delta_mass")
      renderDeltaMassCondition(card, condition, number);
    body.append(card);
  });
}
function renderDeltaMassCondition(card, condition, number) {
  const table = el("table", undefined, "method-ion-table");
  const header = el("tr");
  for (const [text, cls] of [
    ["離子名稱", ""],
    ["Δ m/z（相對定量離子）", "num"],
    ["比例下限", "num"],
    ["比例上限", "num"],
    ["比較峰形", "center"],
    ["", ""],
  ])
    header.append(el("th", text, cls));
  table.append(header);
  condition.positions.forEach((position, positionIndex) => {
    const where = `條件 ${number}，第 ${positionIndex + 1} 個離子`;
    const quant = position === draftQuantitationIons.get(condition);
    const row = el("tr");
    const nameCell = el("td");
    const name = el("input");
    name.value = position.label;
    name.setAttribute("aria-label", `離子名稱（${where}）`);
    name.oninput = () => {
      position.label = name.value;
    };
    nameCell.append(name);
    row.append(nameCell);
    if (quant) {
      const offsetCell = el("td", undefined, "num");
      offsetCell.append(
        el("span", "0", "method-fixed"),
        " ",
        el("span", "定量", "method-quant-flag"),
      );
      row.append(offsetCell);
      for (const cls of ["num", "num", "center"])
        row.append(el("td", "—", `${cls} subtle`));
      row.append(el("td"));
    } else {
      for (const [key, text] of [
        ["offset_mz", "Δ m/z"],
        ["ratio_min", "比例下限"],
        ["ratio_max", "比例上限"],
      ]) {
        const cell = el("td", undefined, "num");
        cell.append(
          numericEditor(position[key], `${text}（${where}）`, (value) => {
            position[key] = value;
          }),
        );
        row.append(cell);
      }
      const shapeCell = el("td", undefined, "center");
      const check = el("input");
      check.type = "checkbox";
      check.checked = position.compare_shape;
      check.setAttribute("aria-label", `比較峰形（${where}）`);
      check.onchange = () => {
        position.compare_shape = check.checked;
      };
      shapeCell.append(check);
      row.append(shapeCell);
      const removeCell = el("td");
      removeCell.append(
        removeButton(`移除離子（${where}）`, () => {
          condition.positions.splice(positionIndex, 1);
          renderIdentificationConditions();
        }),
      );
      row.append(removeCell);
    }
    table.append(row);
  });
  const scroll = el("div", undefined, "table-scroll");
  scroll.append(table);
  card.append(scroll);
  const add = button("＋ 新增離子", () => {
    const used = new Set(condition.positions.map((position) => position.label));
    let next = 1;
    while (used.has(`partner_${next}`)) next += 1;
    condition.positions.push({
      label: `partner_${next}`,
      offset_mz: null,
      ratio_min: 0.1,
      ratio_max: 10,
      compare_shape: true,
    });
    renderIdentificationConditions();
  });
  add.setAttribute("aria-label", `新增離子（條件 ${number}）`);
  card.append(
    add,
    el(
      "p",
      "Δ m/z 可正可負，請先依電荷換算。比例只做註記，不淘汰候選峰。",
      "subtle",
    ),
  );
  const advanced = el("details");
  advanced.append(el("summary", "進階：共洗脫與峰形"));
  const grid = el("div", undefined, "method-advanced-grid");
  for (const [key, text] of [
    ["apex_floor_sec", "共洗脫容差下限（秒）"],
    ["scan_interval_multiplier", "掃描間隔倍數"],
    ["shape_window_sec", "峰形比較半窗（秒）"],
    ["min_shape_r", "最低峰形 r"],
  ]) {
    const label = el("label", text);
    label.append(
      numericEditor(condition[key], `${text}（條件 ${number}）`, (value) => {
        condition[key] = value;
      }),
    );
    grid.append(label);
  }
  advanced.append(
    grid,
    el(
      "p",
      "共洗脫容差取「下限」與「MS1 掃描間隔 × 倍數」中較大者；峰形以定量離子為參考。",
      "subtle",
    ),
  );
  card.append(advanced);
}
function identificationDraftError(name) {
  if (!name) return "請輸入方法名稱。";
  if (CID_NL_TAGS.includes(name))
    return "dR、R、MeR 是內建 CID-NL 名稱，請換一個方法名稱。";
  if (
    name !== identificationEditingName &&
    Object.hasOwn(state.identificationMethods, name)
  )
    return "方法名稱已存在；請從「方法」選單選它來編輯，或換一個名稱。";
  if (!(identificationDraft.mz_tolerance_ppm > 0)) return "m/z 容差需大於 0。";
  if (!identificationDraft.conditions.length)
    return "請至少加入一個條件。不需要辨認條件時，在目標表把辨認方法設為 MS1 only。";
  for (const [index, condition] of identificationDraft.conditions.entries()) {
    if (condition.kind !== "delta_mass") continue;
    const where = `Delta Mass 條件 ${index + 1}`;
    const labels = condition.positions.map((position) => position.label.trim());
    const offsets = condition.positions.map((position) => position.offset_mz);
    if (condition.positions.length < 2)
      return `${where}：至少需要一個配對離子。`;
    if (labels.some((label) => !label))
      return `${where}：請填寫每個離子的名稱。`;
    if (offsets.some((offset) => offset === null || Number.isNaN(offset)))
      return `${where}：請填寫每個配對離子的 Δ m/z。`;
    if (new Set(labels).size !== labels.length)
      return `${where}：離子名稱不可重複。`;
    if (new Set(offsets).size !== offsets.length)
      return `${where}：Δ m/z 不可重複（定量離子已是 0）。`;
    if (
      condition.positions.some(
        (position) =>
          !(position.ratio_min > 0 && position.ratio_max >= position.ratio_min),
      )
    )
      return `${where}：比例下限需大於 0，且不大於上限。`;
  }
  return "";
}

// Untargeted

export function bindTargeted() {
  $("targeted-form").addEventListener("input", () => dirty("targeted"));

  $("targeted-form").addEventListener("submit", (event) => {
    event.preventDefault();
    act(() =>
      startJob("targeted", {
        settings: settingsValues(),
        targets: state.targets,
        identification_methods: state.identificationMethods,
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
        targets: state.targets,
        identification_methods: state.identificationMethods,
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
    clearSamples("targeted");
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
      clearSamples("targeted");
    }
  });
  $("target-edit").onclick = () => {
    renderTargets();
    $("target-editor").showModal();
  };
  $("target-bulk-apply").onclick = () => {
    const value = $("target-bulk-method").value;
    for (const row of state.targets) updateTarget(row, { method: value });
    dirty("targeted");
    renderTargets();
    done(
      `已將 ${state.targets.length} 個目標的辨認方法設為「${methodLabel(value)}」。`,
    );
  };
  $("target-identification-edit").onclick = () => {
    loadIdentificationDraft(Object.keys(state.identificationMethods)[0] || "");
    $("target-identification-editor").showModal();
  };
  $("target-identification-select").onchange = () =>
    loadIdentificationDraft($("target-identification-select").value);
  $("target-identification-new").onclick = () => loadIdentificationDraft("");
  $("target-identification-delete").onclick = () => {
    const name = identificationEditingName;
    const users = state.targets.filter((row) => row.method === name).length;
    if (users) {
      $("target-identification-error").textContent =
        `還有 ${users} 個目標使用「${name}」；請先在目標表改用其他方法再刪除。`;
      return;
    }
    const methods = { ...state.identificationMethods };
    delete methods[name];
    setIdentificationMethods(methods);
    renderIdentificationSummary();
    dirty("targeted");
    loadIdentificationDraft(Object.keys(methods)[0] || "");
    done(`已刪除辨認方法「${name}」。儲存或開始分析後生效。`);
  };
  $("target-condition-add-cid").onclick = () => {
    identificationDraft.conditions.push({ kind: "cid_nl", tag: "dR" });
    renderIdentificationConditions();
  };
  $("target-condition-add-delta").onclick = () => {
    identificationDraft.conditions.push(newDeltaCondition());
    renderIdentificationConditions();
  };
  $("target-identification-combine").onchange = () => {
    identificationDraft.combine_mode = $("target-identification-combine").value;
  };
  $("target-identification-ppm").oninput = () => {
    const value = $("target-identification-ppm").value;
    identificationDraft.mz_tolerance_ppm = value === "" ? null : Number(value);
  };
  $("target-identification-apply").onclick = async () => {
    try {
      const name = $("target-identification-name").value.trim();
      const problem = identificationDraftError(name);
      if (problem) throw new Error(problem);
      for (const condition of identificationDraft.conditions)
        if (condition.kind === "delta_mass")
          for (const position of condition.positions)
            position.label = position.label.trim();
      const methods = {
        ...state.identificationMethods,
        [name]: identificationDraft,
      };
      if (identificationEditingName && identificationEditingName !== name)
        delete methods[identificationEditingName];
      const result = await api("/api/targeted/method/validate", {
        identification_methods: methods,
      });
      if (!result.valid) throw new Error(`方法設定無法使用：${result.error}`);
      setIdentificationMethods(
        result.identification_methods,
        identificationEditingName,
        name,
      );
      renderIdentificationSummary();
      dirty("targeted");
      targetCount();
      loadIdentificationDraft(name);
      done(
        `已儲存辨認方法「${name}」。在目標表的「辨認方法」欄選用；儲存工作或開始分析後保留。`,
      );
    } catch (error) {
      $("target-identification-error").textContent = error.message;
    }
  };
  $("target-add").onclick = () => {
    addTarget({
      label: "",
      mz: "",
      expected_rt_min: "",
      method: state.targets.at(-1)?.method || "",
      role: "analyte",
      istd_pair: "",
    });
    dirty("targeted");
    renderTargets();
    $("target-rows").lastElementChild?.querySelector("input")?.focus();
  };
  $("target-editor-save").onclick = () =>
    act(async () => {
      await saveTargeted();
      $("target-editor").close();
    });
  $("target-export").onclick = () =>
    act(async () => {
      const path = await requestPath("匯出目標清單（.xlsx）");
      if (!path) return;
      const result = await api("/api/targets/export", {
        path,
        targets: state.targets,
        identification_methods: state.identificationMethods,
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
    $("target-method-select").value = "";
    targetCount();
    dirty("targeted");
    $("target-import-preview").close();
    $("target-import-status").textContent =
      `已匯入 ${state.targets.length} 個目標；儲存或開始分析後套用。`;
  };
}
