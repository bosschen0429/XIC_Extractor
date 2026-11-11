// Untargeted method selection/editor, tuning controls and ISTD imports.
import { deltaMassEditor, ionsError } from "./analysis_delta_mass.js";
import { analysis } from "./analysis_jobs.js";
import { sampleMetadata } from "./analysis_samples.js";
import { checklist, dirty } from "./analysis_setup.js";
import {
  setImporting,
  setMethod,
  state,
  updateConfig,
  updateUntargetedSettings,
} from "./analysis_state.js";
import {
  $,
  act,
  api,
  bindListUpload,
  button,
  done,
  el,
  field,
  focusField,
  isBusy,
  lock,
  pathFields,
  readListFile,
  requestPath,
} from "./analysis_ui.js";

// This uncommitted editor copy is never shared with another view.
let methodDraft = null;
function untargetedValues() {
  const values = {
    ...state.config.untargeted,
    preset: state.method?.key || $("u-preset").value,
    sequence_source: $("sequence-source").value,
    overrides: {},
  };
  for (const [key] of pathFields) values[key] = $(`u-${key}`).value;
  values.targets_csv = $("u-targets_csv").value;
  for (const control of state.method?.tuning_controls || []) {
    const input = $(`tune-${control.key}`);
    if (
      ["rt_min", "rt_max"].includes(control.key) &&
      (!$("u-rt-crop").checked || input.value.trim() === "")
    )
      continue;
    values.overrides[control.key] =
      input.type === "number" ? Number(input.value) : input.value;
  }
  const rows = sampleMetadata.forPage("untargeted").rows;
  values.samples = rows.length ? rows : null;
  if (state.method) {
    values.preset_snapshot = structuredClone(state.method.draft);
    const snapshotOverrides = values.preset_snapshot.overrides;
    for (const key of ["rt_min", "rt_max"]) {
      delete snapshotOverrides[key];
    }
    // Retain valid imported parameters that have no editor control. Only
    // displayed controls override them; unchecked RT cropping removes bounds.
    Object.assign(snapshotOverrides, values.overrides);
  }
  return values;
}
export async function saveUntargeted(quiet = false) {
  const values = untargetedValues();
  await api("/api/untargeted/save", values);
  updateConfig({ untargeted: values });
  dirty("untargeted", false);
  if (!quiet) done("Untargeted 批次設定已儲存。");
}
export async function loadMethod(preset, overrides = {}) {
  let data;
  try {
    data = await api("/api/method", { preset });
  } catch (error) {
    if (state.method) $("u-preset").value = state.method.key;
    throw error;
  }
  await applyMethodDetails(data, preset, overrides);
}
async function applyMethodDetails(data, preset, overrides = {}) {
  // An applied draft is the current choice, not a reusable method file.
  // Switching away must not leave an option that reloads absent or old bytes.
  const previousDraft = state.method?.path;
  if (previousDraft && previousDraft !== preset) {
    for (const item of [...$("u-preset").options])
      if (item.value === previousDraft) item.remove();
  }
  setMethod(data, preset);
  let option = [...$("u-preset").options].find((item) => item.value === preset);
  if (!option) {
    option = el("option", data.preset.name);
    option.value = preset;
    $("u-preset").append(option);
  }
  if (data.path) option.textContent = data.preset.name;
  $("u-preset").value = preset;
  const tuning = $("method-tuning");
  tuning.replaceChildren();
  const cropFields = el("div", undefined, "settings-grid span-all");
  cropFields.id = "method-rt-crop-fields";
  let hasCrop = false;
  for (const control of data.tuning_controls) {
    const value = Object.hasOwn(overrides, control.key)
      ? overrides[control.key]
      : data.effective_values[control.key];
    const node = field(control, value, "tune-");
    if (["rt_min", "rt_max"].includes(control.key)) {
      hasCrop ||= value !== null && value !== undefined && value !== "";
      const input = node.querySelector("input");
      input.dataset.rtBound = "true";
      input.placeholder =
        control.key === "rt_min"
          ? "RAW 採集起點（自動）"
          : "RAW 採集終點（自動）";
      cropFields.append(node);
    } else tuning.append(node);
  }
  const cropLabel = el("label", undefined, "field checkbox span-all");
  const crop = el("input");
  crop.type = "checkbox";
  crop.id = "u-rt-crop";
  crop.dataset.analysisIdle = "";
  crop.checked = hasCrop;
  cropLabel.append(crop, el("span", "限制分析 RT 範圍（選用）"));
  const cropNote = el(
    "p",
    "預設分析完整 RAW 採集範圍（自動）。需要裁切時才啟用；任一端留空會使用該 RAW 的採集起點／終點。",
    "settings-notice span-all",
  );
  cropFields.hidden = !hasCrop;
  crop.addEventListener("change", () => {
    cropFields.hidden = !crop.checked;
    lock();
    updateMethodPreview();
  });
  // Delta Mass Discovery always reads the complete acquisition.
  if (cropFields.childElementCount) tuning.append(cropLabel, cropNote, cropFields);
  lock();
  updateMethodPreview();
  if (state.booted) await act(reloadIstd);
}
function updateMethodPreview() {
  if (!state.method) return;
  const preview = state.method.preview;
  const chips = preview.tags.map((tag) =>
    el("span", `CID-NL ${tag.name} · ${tag.neutral_loss_da} Da`, "chip accent"),
  );
  if (preview.delta_mass_companions !== undefined)
    chips.push(
      el(
        "span",
        `Delta Mass · 基準＋${preview.delta_mass_companions} 個伴隨離子`,
        "chip accent",
      ),
    );
  // A combination exists only with more than one piece of evidence.
  if (preview.combine_mode !== "single")
    chips.push(
      el(
        "span",
        preview.combine_mode === "union" ? "任一符合" : "全部符合",
        "chip",
      ),
    );
  const crop = $("u-rt-crop")?.checked;
  const lower = crop ? $("tune-rt_min")?.value.trim() : "";
  const upper = crop ? $("tune-rt_max")?.value.trim() : "";
  chips.push(
    el(
      "span",
      !lower && !upper
        ? "完整 RAW 採集範圍（自動）"
        : `RT ${lower || "RAW 起點"}–${upper || "RAW 終點"} min`,
      "chip",
    ),
  );
  $("method-preview").replaceChildren(...chips);
  checklist("untargeted");
}

// Method editor: CID-NL and Delta Mass cards over one preset draft. Delta
// Mass ions carry their own role flags; names in the preset follow from them.
const DM_THRESHOLDS = [
  ["delta_mass_mz_tolerance_ppm", "m/z 容差（ppm）"],
  ["apex_floor_sec", "共洗脫容差下限（秒）"],
  ["scan_interval_multiplier", "掃描間隔倍數"],
  ["shape_window_sec", "峰形比對半窗（秒）"],
  ["min_shape_r", "最低峰形 r"],
  ["background_inner_sec", "背景內窗（秒）"],
  ["background_outer_sec", "背景外窗（秒）"],
  ["min_height_above_background", "最弱離子淨高下限"],
  ["min_background_sigma", "最弱離子背景倍數（σ）"],
  ["min_base_support", "基準支撐下限"],
];
// Delta Mass Discovery keys that a CID-NL-only method does not accept.
const DELTA_MASS_ONLY_KEYS = new Set([
  "method_label",
  "positions",
  "anchor_positions",
  "discriminating_positions",
  "lowest_position",
  "base_support_positions",
  "detector",
  ...DM_THRESHOLDS.map(([key]) => key),
]);
let deltaDefaults = null;
let methodAdvancedOpen = false;
async function deltaMassDefaults() {
  // New Delta Mass evidence starts from the built-in Pt method's thresholds.
  deltaDefaults ||= (await api("/api/method", { preset: "pt_pattern" })).draft
    .overrides;
  return deltaDefaults;
}
function editorFromDraft(draft) {
  const overrides = draft.overrides || {};
  const dmTag = draft.tags.find((tag) => tag.strategy === "delta_mass");
  const losses = draft.tags
    .filter((tag) => tag.strategy !== "delta_mass" && tag.enabled !== false)
    .map((tag) => ({ name: tag.name, neutral_loss_da: tag.neutral_loss_da }));
  const editor = {
    source: draft,
    name: draft.name,
    combine: draft.combine_mode === "union" ? "any" : "all",
    cidOn: losses.length > 0,
    losses: losses.length
      ? losses
      : [{ name: "DNA_dR", neutral_loss_da: 116.0474 }],
    dmOn: Boolean(dmTag),
    dmTag: dmTag || { name: "Delta Mass", pattern_id: "delta_mass" },
    base: null,
    ions: [],
    thresholds: {},
    smoothing: null,
  };
  if (dmTag) loadDeltaMass(editor, overrides);
  return editor;
}
function loadDeltaMass(editor, overrides) {
  const positions = [...(overrides.positions || [])].sort(
    (a, b) => a.delta_da - b.delta_da,
  );
  const [base, ...rest] = positions;
  const anchors = new Set(overrides.anchor_positions || []);
  const shapes = new Set(overrides.discriminating_positions || []);
  const supports = new Set(overrides.base_support_positions || []);
  editor.base = base
    ? { ...base, anchor: anchors.has(base.label) }
    : { label: "base", delta_da: 0, ratio_min: 1, ratio_max: 1, anchor: true };
  editor.ions = rest.map((position) => ({
    label: position.label,
    offset_mz: position.delta_da,
    ratio_min: position.ratio_min,
    ratio_max: position.ratio_max,
    compare_shape: shapes.has(position.label),
    anchor: anchors.has(position.label),
    support: supports.has(position.label),
    lowest: overrides.lowest_position === position.label,
  }));
  editor.thresholds = Object.fromEntries(
    DM_THRESHOLDS.map(([key]) => [key, overrides[key]]),
  );
  editor.smoothing = overrides.detector?.smoothing_window_sec ?? null;
}
function editorTags(editor) {
  return [
    ...(editor.dmOn
      ? [
          {
            enabled: true,
            strategy: "delta_mass",
            name: editor.dmTag.name,
            pattern_id: editor.dmTag.pattern_id,
          },
        ]
      : []),
    ...(editor.cidOn
      ? editor.losses.map((loss) => ({
          enabled: true,
          strategy: "neutral_loss",
          name: loss.name,
          neutral_loss_da: loss.neutral_loss_da,
        }))
      : []),
  ];
}
function editorDraft(editor) {
  const source = editor.source;
  const tags = editorTags(editor);
  let overrides;
  if (editor.dmOn) {
    const ions = [...editor.ions].sort((a, b) => a.offset_mz - b.offset_mz);
    const kept = Object.fromEntries(
      ["mass_recalibration", "method_label"]
        .filter((key) => source.overrides?.[key] !== undefined)
        .map((key) => [key, source.overrides[key]]),
    );
    const hadDeltaMass = source.tags.some((tag) => tag.strategy === "delta_mass");
    overrides = {
      // New Delta Mass evidence restores Easy-IC masses like CID-NL methods;
      // an existing Delta Mass method keeps its policy, including omitted (none).
      ...(hadDeltaMass ? {} : { mass_recalibration: "easy_ic_restore" }),
      ...kept,
      positions: [
        {
          label: editor.base.label,
          delta_da: 0.0,
          ratio_min: editor.base.ratio_min,
          ratio_max: editor.base.ratio_max,
        },
        ...ions.map((ion) => ({
          label: ion.label,
          delta_da: ion.offset_mz,
          ratio_min: ion.ratio_min,
          ratio_max: ion.ratio_max,
        })),
      ],
      anchor_positions: [
        ...(editor.base.anchor ? [editor.base.label] : []),
        ...ions.filter((ion) => ion.anchor).map((ion) => ion.label),
      ],
      discriminating_positions: ions
        .filter((ion) => ion.compare_shape)
        .map((ion) => ion.label),
      lowest_position: ions.find((ion) => ion.lowest)?.label || "",
      base_support_positions: ions
        .filter((ion) => ion.support)
        .map((ion) => ion.label),
      ...Object.fromEntries(
        Object.entries(editor.thresholds).filter(([, value]) => value !== null),
      ),
      detector: {
        ...(source.overrides?.detector || {}),
        ...(editor.smoothing !== null
          ? { smoothing_window_sec: editor.smoothing }
          : {}),
      },
    };
  } else {
    // Keep every CID-NL value, including ones without a page control.
    overrides = Object.fromEntries(
      Object.entries(source.overrides || {}).filter(
        ([key]) => !DELTA_MASS_ONLY_KEYS.has(key),
      ),
    );
  }
  return {
    name: editor.name,
    description: source.description || "Workbench 建立的方法",
    tags,
    combine_mode:
      tags.length < 2
        ? "single"
        : editor.combine === "any"
          ? "union"
          : "intersection",
    overrides,
    alignment_overrides: source.alignment_overrides || {},
    source: source.source || "workbench:draft",
  };
}
function editorError(editor) {
  if (!editor.name.trim()) return "請輸入方法名稱。";
  if (!editor.cidOn && !editor.dmOn)
    return "請至少啟用一種證據：CID-NL 中性丟失或 Delta Mass。";
  if (editor.cidOn) {
    if (!editor.losses.length) return "CID-NL：請至少加入一個中性丟失。";
    for (const loss of editor.losses)
      if (!loss.name.trim() || !(loss.neutral_loss_da > 0))
        return "CID-NL：每個中性丟失都需要名稱與大於 0 的質量（Da）。";
  }
  if (editor.dmOn) {
    if (!editor.ions.length) return "Delta Mass：請至少加入一個伴隨離子。";
    const problem = ionsError(editor.ions, { allowNegative: false });
    if (problem) return `Delta Mass：${problem}`;
    if (![editor.base, ...editor.ions].some((ion) => ion.anchor))
      return "Delta Mass：請至少指定一個「可當搜尋起點」的離子。";
  }
  return "";
}
// Delta Mass Discovery combines only with the dR loss, and only when both match.
const DELTA_MASS_LOSS = { name: "DNA_dR", neutral_loss_da: 116.0474 };
const bothEvidence = () => methodDraft.dmOn && methodDraft.cidOn;
function renderCombine() {
  const both = bothEvidence();
  if (both) {
    methodDraft.combine = "all";
    methodDraft.losses = [{ ...DELTA_MASS_LOSS }];
  }
  $("method-combine-field").hidden =
    both || !(methodDraft.cidOn && methodDraft.losses.length > 1);
  $("method-combine-fixed").hidden = !both;
  $("method-combine").value = methodDraft.combine;
}
function renderCidCard() {
  const body = $("method-cid-body");
  body.hidden = !methodDraft.cidOn;
  if (!methodDraft.cidOn) return body.replaceChildren();
  const table = el("table", undefined, "method-ion-table");
  const head = el("tr");
  for (const [text, cls] of [
    ["名稱", ""],
    ["中性丟失（Da）", "num"],
    ["", ""],
  ])
    head.append(el("th", text, cls));
  table.append(head);
  const locked = bothEvidence();
  methodDraft.losses.forEach((loss, index) => {
    const row = el("tr");
    const name = el("input");
    name.disabled = locked;
    name.value = loss.name;
    name.setAttribute("aria-label", `中性丟失名稱（第 ${index + 1} 列）`);
    name.oninput = () => (loss.name = name.value);
    const value = el("input");
    value.disabled = locked;
    value.type = "number";
    value.step = "any";
    value.value = loss.neutral_loss_da ?? "";
    value.setAttribute("list", "cid-nl-losses");
    value.setAttribute("aria-label", `中性丟失 Da（第 ${index + 1} 列）`);
    value.oninput = () => (loss.neutral_loss_da = Number(value.value));
    const remove = button(
      "",
      () => {
        methodDraft.losses.splice(index, 1);
        renderMethodEditor();
      },
      "icon-button",
      "x",
    );
    remove.setAttribute("aria-label", `移除第 ${index + 1} 個中性丟失`);
    remove.hidden = locked;
    for (const [node, cls] of [
      [name, ""],
      [value, "num"],
      [remove, ""],
    ]) {
      const td = el("td", undefined, cls);
      td.append(node);
      row.append(td);
    }
    table.append(row);
  });
  const add = button("＋ 中性丟失", () => {
    methodDraft.losses.push({ name: "", neutral_loss_da: null });
    renderMethodEditor();
  });
  body.replaceChildren(
    table,
    locked
      ? el(
          "p",
          "搭配 Delta Mass 時固定為 DNA_dR 116.0474 Da：目前引擎只支援這個組合。",
          "subtle",
        )
      : add,
  );
}
function renderDeltaCard() {
  const body = $("method-dm-body");
  body.hidden = !methodDraft.dmOn;
  if (!methodDraft.dmOn) return body.replaceChildren();
  const editor = deltaMassEditor({
    ions: methodDraft.ions,
    base: "基準＝最輕的離子（Δ 0）；Δm/z 只能 ≥ 0",
    baseMz: null,
    allowNegative: false,
    showShape: false,
    onChange: () => renderRoles(),
  });
  const advanced = el("details", undefined, "method-advanced");
  advanced.open = methodAdvancedOpen;
  advanced.addEventListener("toggle", () => {
    methodAdvancedOpen = advanced.open;
  });
  advanced.append(el("summary", "進階：離子角色與篩選門檻"));
  const roles = el("div");
  roles.id = "method-roles";
  const grid = el("div", undefined, "method-advanced-grid");
  for (const [key, text] of [
    ...DM_THRESHOLDS,
    ["smoothing", "偵測平滑窗（秒）"],
  ]) {
    const label = el("label", text);
    const input = el("input");
    input.type = "number";
    input.step = "any";
    input.value =
      (key === "smoothing" ? methodDraft.smoothing : methodDraft.thresholds[key]) ??
      "";
    input.oninput = () => {
      const value = input.value === "" ? null : Number(input.value);
      if (key === "smoothing") methodDraft.smoothing = value;
      else methodDraft.thresholds[key] = value;
    };
    label.append(input);
    grid.append(label);
  }
  advanced.append(roles, grid);
  body.replaceChildren(editor, advanced);
  renderRoles();
}
// Role flags per ion; renaming an ion keeps its roles.
function renderRoles() {
  const holder = $("method-roles");
  if (!holder) return;
  const table = el("table", undefined, "method-ion-table method-role-table");
  const head = el("tr");
  for (const text of [
    "離子",
    "可當搜尋起點",
    "比對峰形",
    "最弱且高於背景",
    "支撐基準強度",
  ])
    head.append(el("th", text, text === "離子" ? "" : "center"));
  table.append(head);
  const check = (ion, key, label) => {
    const input = el("input");
    input.type = key === "lowest" ? "radio" : "checkbox";
    if (key === "lowest") input.name = "method-lowest";
    input.checked = Boolean(ion[key]);
    input.setAttribute("aria-label", `${label}（${ion.label || "未填 Δm/z"}）`);
    input.onchange = () => {
      if (key === "lowest")
        for (const each of methodDraft.ions) each.lowest = each === ion;
      else ion[key] = input.checked;
    };
    const td = el("td", undefined, "center");
    td.append(input);
    return td;
  };
  const baseRow = el("tr");
  baseRow.append(
    el("td", `${methodDraft.base.label}（基準）`, "mono"),
    check(methodDraft.base, "anchor", "可當搜尋起點"),
    el("td", "參考", "center subtle"),
    el("td", "", "center"),
    el("td", "", "center"),
  );
  table.append(baseRow);
  for (const ion of methodDraft.ions) {
    const row = el("tr");
    row.append(
      el("td", ion.label || "（未填 Δm/z）", "mono"),
      check(ion, "anchor", "可當搜尋起點"),
      check(ion, "compare_shape", "比對峰形"),
      check(ion, "lowest", "最弱且高於背景"),
      check(ion, "support", "支撐基準強度"),
    );
    table.append(row);
  }
  const scroll = el("div", undefined, "table-scroll");
  scroll.append(table);
  holder.replaceChildren(scroll);
}
function renderMethodEditor() {
  $("method-name").value = methodDraft.name;
  $("method-cid-on").checked = methodDraft.cidOn;
  $("method-dm-on").checked = methodDraft.dmOn;
  $("method-error").hidden = true;
  renderCombine();
  renderCidCard();
  renderDeltaCard();
}
function openMethodEditor(draft) {
  methodDraft = editorFromDraft(draft);
  // Advanced settings start collapsed for each method; editing keeps them open.
  methodAdvancedOpen = false;
  renderMethodEditor();
  $("method-editor").showModal();
}
async function saveMethod(apply) {
  methodDraft.name = $("method-name").value;
  const problem = editorError(methodDraft);
  if (problem) {
    $("method-error").hidden = false;
    $("method-error").textContent = problem;
    return;
  }
  const draft = editorDraft(methodDraft);
  const path = apply
    ? null
    : await requestPath(
        "另存方法檔",
        `${state.config.config_dir}/my-method.toml`,
      );
  if (!apply && !path) return;
  try {
    if (apply) {
      const result = await api("/api/method", { draft });
      await applyMethodDetails(result, result.path);
      dirty("untargeted");
      $("method-editor").close();
      done("方法已套用至批次設定；尚未保存。開始分析時也會一併保存。");
    } else {
      const result = await api("/api/method/save", { draft, path });
      done(`方法檔已另存：${result.path}。目前批次設定不變。`);
    }
  } catch (error) {
    $("method-error").hidden = false;
    $("method-error").textContent = error.message;
  }
}
function renderIstd(result) {
  const selection = result.selection;
  $("istd-summary").textContent =
    result.message ||
    `${selection.targets.length} 個適用內標；${selection.method_skipped.length} 個不適用此方法；略過 ${selection.skipped_count} 個一般目標。${selection.targets.length ? "" : " 本次不使用內標參考。"}`;
  const preview = $("istd-preview");
  preview.replaceChildren();
  if (!selection) return;
  const table = el("table");
  const head = el("tr");
  for (const label of [
    "內標名稱",
    "CID-NL 中性丟失（Da）",
    "m/z",
    "預期 RT (min)",
    "本方法使用狀態",
  ])
    head.append(el("th", label));
  table.append(head);
  for (const [targets, status] of [
    [selection.targets, "適用"],
    [selection.method_skipped, "不適用此方法（略過）"],
  ]) {
    for (const target of targets) {
      const row = el("tr");
      for (const value of [
        target.label,
        target.neutral_loss_da,
        target.mz,
        target.expected_rt_min,
        status,
      ])
        row.append(el("td", String(value ?? "")));
      table.append(row);
    }
  }
  preview.append(table);
}
export async function reloadIstd() {
  if (!state.method) {
    renderIstd({ message: "請先選擇有效方法，才能判定適用內標。" });
    return;
  }
  $("istd-preview").replaceChildren();
  $("istd-summary").textContent = "正在讀取清單…";
  try {
    renderIstd(
      await api("/api/istd", {
        path: $("u-targets_csv").value,
        preset_snapshot: state.method.draft,
      }),
    );
  } catch (error) {
    $("istd-summary").textContent = "清單未載入，請修正檔案或清空路徑停用。";
    throw error;
  }
}
async function importIstdFiles(files) {
  if (isBusy()) return;
  const message = $("istd-import-status");
  message.className = "";
  try {
    setImporting(true);
    lock();
    message.textContent = "正在讀取檔案…";
    const upload = await readListFile(files);
    if (!state.method) throw new Error("請先選擇有效方法，再匯入內標清單。");
    const result = await api("/api/istd/upload", {
      ...upload,
      preset_snapshot: state.method.draft,
    });
    $("u-targets_csv").value = result.path;
    renderIstd(result);
    dirty("untargeted");
    message.textContent = `已匯入 ${upload.name}；儲存或開始分析後套用。`;
  } catch (error) {
    message.textContent = error.message;
    message.className = "error";
  } finally {
    setImporting(false);
    lock();
  }
}
async function copyIstd() {
  const destination = await requestPath(
    "另存可編輯內標副本",
    `${state.config.config_dir}/my-targets.${$("u-targets_csv").value.split(".").pop() || "xlsx"}`,
  );
  if (!destination) return;
  const result = await api("/api/istd/copy", {
    path: $("u-targets_csv").value,
    destination,
  });
  $("u-targets_csv").value = result.path;
  dirty("untargeted");
  await reloadIstd();
}

export function bindUntargeted() {
  $("untargeted-form").addEventListener("input", () => dirty("untargeted"));

  $("untargeted-form").addEventListener("submit", (event) => {
    event.preventDefault();
    act(() => analysis.start("untargeted", untargetedValues()));
  });

  $("untargeted-save").onclick = () => act(() => saveUntargeted());

  // The ISTD list is a path row: browse for a file there, or drop one on it.
  bindListUpload("istd", importIstdFiles, $("istd-path-field"));

  $("u-preset").onchange = () =>
    act(async () => {
      await loadMethod($("u-preset").value);
      dirty("untargeted");
    });
  $("method-edit").onclick = () => {
    if (!state.method) return;
    openMethodEditor(untargetedValues().preset_snapshot);
  };
  $("method-new").onclick = () =>
    openMethodEditor({
      name: "新方法",
      description: "",
      combine_mode: "single",
      tags: [
        {
          enabled: true,
          strategy: "neutral_loss",
          name: "DNA_dR",
          neutral_loss_da: 116.0474,
        },
      ],
      overrides: {},
    });
  $("method-name").oninput = () => (methodDraft.name = $("method-name").value);
  $("method-combine").onchange = () =>
    (methodDraft.combine = $("method-combine").value);
  $("method-cid-on").onchange = () => {
    methodDraft.cidOn = $("method-cid-on").checked;
    renderMethodEditor();
  };
  $("method-dm-on").onchange = () =>
    act(async () => {
      methodDraft.dmOn = $("method-dm-on").checked;
      if (methodDraft.dmOn && !methodDraft.base) {
        const defaults = await deltaMassDefaults();
        loadDeltaMass(methodDraft, { ...defaults, positions: [] });
        methodDraft.base.anchor = true;
      }
      renderMethodEditor();
    });
  $("method-save-as").onclick = () => act(() => saveMethod(false));
  $("method-apply").onclick = () => act(() => saveMethod(true));
  $("method-import").onclick = () =>
    act(async () => {
      const path = await requestPath("匯入 TOML 方法");
      if (path) {
        await loadMethod(path);
        dirty("untargeted");
      }
    });

  $("istd-reload").onclick = () => act(reloadIstd);
  $("u-targets_csv").addEventListener("change", () => act(reloadIstd));
  $("method-tuning").addEventListener("input", updateMethodPreview);
  $("istd-copy").onclick = () => act(copyIstd);
  $("istd-open").onclick = () =>
    act(async () => {
      if ($("u-targets_csv").value === state.config.targets_example)
        await copyIstd();
      await api("/api/open-local", { path: $("u-targets_csv").value });
    });
  let sampleRawDir = $("u-raw_dir").value;
  $("untargeted-new").onclick = () => {
    $("u-raw_dir").value = "";
    sampleRawDir = "";
    $("sequence-source").value = "";
    sampleMetadata.clear("untargeted");
    $("untargeted-result").hidden = true;
    dirty("untargeted");
    focusField("u-raw_dir");
    done("已開始新批次：保留方法、內標與結果存放位置，請選擇新的 RAW 資料夾。");
  };
  $("u-raw_dir").addEventListener("input", () => {
    const nextRawDir = $("u-raw_dir").value;
    if (nextRawDir === sampleRawDir) return;
    sampleRawDir = nextRawDir;
    $("sequence-source").value = "";
    updateUntargetedSettings({ sequence_source: "" });
    sampleMetadata.clear("untargeted");
    dirty("untargeted");
  });
  $("sequence-source").addEventListener("change", () => dirty("untargeted"));
}
