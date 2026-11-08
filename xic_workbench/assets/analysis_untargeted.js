// Untargeted method selection/editor, tuning controls and ISTD imports.
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
  tuning.append(cropLabel, cropNote, cropFields);
  lock();
  updateMethodPreview();
  if (state.booted) await act(reloadIstd);
}
function updateMethodPreview() {
  if (!state.method) return;
  const preview = state.method.preview;
  const chips = preview.tags.map((tag) =>
    el("span", `${tag.name} · ${tag.neutral_loss_da} Da`, "chip accent"),
  );
  chips.push(el("span", preview.combine_mode, "chip"));
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
function renderMethodEditor() {
  $("method-name").value = methodDraft.name;
  $("method-combine").value = methodDraft.combine_mode;
  $("method-error").hidden = true;
  const body = $("method-tags");
  body.replaceChildren();
  methodDraft.tags.forEach((tag, index) => {
    const tr = el("tr");
    for (const key of ["enabled", "name", "neutral_loss_da"]) {
      const input = el("input");
      input.type =
        key === "enabled"
          ? "checkbox"
          : key === "neutral_loss_da"
            ? "number"
            : "text";
      input.setAttribute(
        "aria-label",
        `${{ enabled: "啟用", name: "標籤名稱", neutral_loss_da: "Neutral loss" }[key]}（第 ${index + 1} 列）`,
      );
      if (key === "enabled") input.checked = tag.enabled !== false;
      else {
        input.value = tag[key] ?? "";
        if (input.type === "number") input.step = "any";
      }
      input.addEventListener(
        "input",
        () =>
          (tag[key] =
            key === "enabled"
              ? input.checked
              : key === "neutral_loss_da"
                ? Number(input.value)
                : input.value),
      );
      const td = el("td");
      td.append(input);
      tr.append(td);
    }
    const td = el("td");
    const remove = button(
      "",
      () => {
        methodDraft.tags.splice(index, 1);
        renderMethodEditor();
      },
      "icon-button",
      "x",
    );
    remove.setAttribute("aria-label", `移除第 ${index + 1} 個標籤`);
    td.append(remove);
    tr.append(td);
    body.append(tr);
  });
}
async function saveMethod(apply) {
  methodDraft.name = $("method-name").value;
  methodDraft.combine_mode = $("method-combine").value;
  const path = apply
    ? null
    : await requestPath(
        "另存方法檔",
        `${state.config.config_dir}/my-method.toml`,
      );
  if (!apply && !path) return;
  try {
    if (apply) {
      const result = await api("/api/method", { draft: methodDraft });
      await applyMethodDetails(result, result.path);
      dirty("untargeted");
      $("method-editor").close();
      done("方法已套用至批次設定；尚未保存。開始分析時也會一併保存。");
    } else {
      const result = await api("/api/method/save", {
        draft: methodDraft,
        path,
      });
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
    "CID-NL tag",
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
        target.cid_nl_tag,
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
    methodDraft = untargetedValues().preset_snapshot;
    renderMethodEditor();
    $("method-editor").showModal();
  };
  $("method-new").onclick = () => {
    methodDraft = {
      name: "新方法",
      description: "",
      combine_mode: "single",
      tags: [
        {
          enabled: true,
          strategy: "neutral_loss",
          name: "Custom NL",
          neutral_loss_da: 116.0474,
        },
      ],
      overrides: {},
    };
    renderMethodEditor();
    $("method-editor").showModal();
  };
  $("method-add-tag").onclick = () => {
    methodDraft.name = $("method-name").value;
    methodDraft.combine_mode = $("method-combine").value;
    methodDraft.tags.push({
      enabled: true,
      strategy: "neutral_loss",
      name: "Custom NL",
      neutral_loss_da: 0,
    });
    renderMethodEditor();
  };
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
