// Transport and reusable DOM/form controls shared by analysis views.
import { previewSamples } from "./analysis_samples.js";
import { checklist } from "./analysis_setup.js";
import {
  currentSampleAction,
  isRunning,
  sampleAction,
  state,
} from "./analysis_state.js";

export const $ = (id) => document.getElementById(id);
export const ui = window.XicUi;
// The location holds every run; each analysis writes a new folder inside it.
export const OUTPUT_LABEL = "結果存放位置";
export const OUTPUT_HELP =
  "每次分析會在這裡新建一個資料夾（日期時間_類型_RAW 資料夾名），不覆蓋舊結果。";
export const pathFields = [
  ["raw_dir", "RAW 資料夾", "folder"],
  ["output_dir", OUTPUT_LABEL, "folder", OUTPUT_HELP],
  ["dll_dir", "RawFileReader DLL 資料夾", "folder"],
];
export async function api(path, values) {
  const response = await fetch(path, {
    method: values === undefined ? "GET" : "POST",
    headers: {
      "X-XIC-Client": "workbench",
      ...(values === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(values === undefined ? {} : { body: JSON.stringify(values) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || `要求失敗（${response.status}）`);
  return data;
}
export function status(text, error = false) {
  ui.toast(text, error ? "error" : "info");
}
export function done(text) {
  ui.toast(text, "ok");
}
export async function act(fn) {
  try {
    return await fn();
  } catch (error) {
    status(error.message, true);
    return null;
  }
}
export function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
export function button(label, fn, cls = "", iconName = "") {
  const node = el("button", undefined, cls);
  node.type = "button";
  if (iconName) node.append(ui.icon(iconName));
  node.append(label);
  node.addEventListener("click", () => act(fn));
  return node;
}
export function pathTail(path, parts = 2) {
  const segments = String(path || "")
    .replace(/[\\/]+$/, "")
    .split(/[\\/]/)
    .filter(Boolean);
  return segments.length > parts
    ? `…\\${segments.slice(-parts).join("\\")}`
    : String(path || "");
}
export function isBusy() {
  return state.importing || window.XicReview.busy || isRunning();
}
export function lock() {
  document
    .querySelectorAll("[data-analysis-idle]")
    .forEach(
      (node) =>
        (node.disabled =
          isBusy() ||
          (node.dataset.rtBound === "true" && !$("u-rt-crop")?.checked)),
    );
  if ($("curation-export"))
    $("curation-export").disabled = isBusy() || !state.curation?.previewRequest;
}
export const isTemplatePath = (value) =>
  value.startsWith("<") || /[\\/]your[\\/]/i.test(value);
export function field(spec, value, prefix) {
  const kind = spec.kind === "choice" ? "select" : spec.kind;
  const label = el(
    "label",
    undefined,
    `field${kind === "checkbox" ? " checkbox" : ""}`,
  );
  const title = el("span", spec.label);
  const input = document.createElement(kind === "select" ? "select" : "input");
  input.id = `${prefix}${spec.key}`;
  input.dataset.analysisIdle = "";
  input.dataset.key = spec.key;
  if (kind === "select")
    for (const option of spec.options || spec.choices || []) {
      const node = el("option", option);
      node.value = option;
      input.append(node);
    }
  else
    input.type =
      kind === "checkbox" ? "checkbox" : kind === "number" ? "number" : "text";
  if (kind === "number") {
    input.step = spec.step ?? "any";
    if (spec.min !== undefined || spec.minimum !== undefined)
      input.min = spec.min ?? spec.minimum;
    if (spec.max !== undefined || spec.maximum !== undefined)
      input.max = spec.max ?? spec.maximum;
  }
  if (["folder", "file"].includes(kind)) input.className = "path";
  if (kind === "checkbox")
    input.checked = value === true || String(value).toLowerCase() === "true";
  else input.value = value ?? "";
  if (spec.placeholder) input.placeholder = spec.placeholder;
  const help = spec.help ? el("small", spec.help) : null;
  if (["folder", "file"].includes(kind)) {
    const browse = el("button", "瀏覽");
    browse.type = "button";
    browse.dataset.browse = input.id;
    browse.dataset.kind = kind;
    browse.dataset.analysisIdle = "";
    const row = el("span", undefined, "input-action");
    row.append(input, browse);
    label.append(title, row);
    if (help) label.append(help);
  } else if (kind === "checkbox") {
    if (help) title.append(help);
    label.append(input, title);
  } else {
    label.append(title, input);
    if (help) label.append(help);
  }
  return label;
}
// 上機資訊 only annotates; disagreements are listed and the RAW decides.
export function pathField(
  container,
  key,
  label,
  kind,
  value,
  prefix = "u-",
  help = "",
) {
  const wrap = el("div", undefined, "path-line");
  wrap.id = `${prefix}${key}-row`;
  const title = el("label", label);
  title.htmlFor = `${prefix}${key}`;
  const input = el("input", undefined, "path");
  input.id = title.htmlFor;
  input.value = isTemplatePath(String(value || "")) ? "" : value || "";
  input.dataset.analysisIdle = "";
  const browse = el("button", "瀏覽");
  browse.type = "button";
  browse.dataset.browse = input.id;
  browse.dataset.kind = kind;
  browse.dataset.analysisIdle = "";
  wrap.append(title, input, browse);
  if (help) wrap.append(el("small", help, "field-help"));
  container.append(wrap);
  if (key === "dll_dir") wrap.hidden = true;
  const problem = el("small", "", "field-help");
  problem.id = `${input.id}-problem`;
  problem.setAttribute("role", "status");
  input.setAttribute("aria-describedby", problem.id);
  wrap.append(problem);
  const validate = async () => {
    const value = input.value.trim();
    delete input.dataset.validatedPath;
    problem.textContent = value ? "檢查路徑中…" : "";
    const result = await api("/api/paths/validate", {
      path: value,
      kind: key === "output_dir" ? "output" : kind,
    });
    if (input.value.trim() !== value) return false;
    input.dataset.validatedPath = result.usable ? value : "";
    problem.textContent = result.usable ? "" : result.message;
    input.setAttribute("aria-invalid", String(!result.usable));
    checklist(prefix === "t-" ? "targeted" : "untargeted");
    return result.usable;
  };
  input.addEventListener("input", () => {
    delete input.dataset.validatedPath;
    checklist(prefix === "t-" ? "targeted" : "untargeted");
  });
  input.addEventListener("change", () =>
    act(async () => {
      const page = prefix === "t-" ? "targeted" : "untargeted";
      const action = ["data_dir", "raw_dir"].includes(key)
        ? sampleAction(page)
        : null;
      if ((await validate()) && action && currentSampleAction(action))
        await previewSamples(page, action);
    }),
  );
  if (input.value && key !== "dll_dir") act(validate);
  return input;
}

// Readiness
export function focusField(id) {
  const node = $(id);
  if (!node) return;
  for (let parent = node.parentElement; parent; parent = parent.parentElement)
    if (parent.tagName === "DETAILS") parent.open = true;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  if (node.tagName !== "DETAILS") node.focus({ preventScroll: true });
}

// Targeted
export function bindListUpload(
  prefix,
  receive,
  zone = $(`${prefix}-dropzone`),
) {
  const chooser = $(`${prefix}-import-file`);
  if (chooser) {
    $(`${prefix}-import`).onclick = () => chooser.click();
    chooser.onchange = (event) => {
      receive(event.target.files);
      event.target.value = "";
    };
  }
  for (const type of ["dragenter", "dragover", "dragleave", "drop"]) {
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.toggle(
        "drag-over",
        !isBusy() && ["dragenter", "dragover"].includes(type),
      );
      if (type === "drop") receive(event.dataTransfer.files);
    });
  }
}
export async function readListFile(files) {
  if (files.length !== 1) throw new Error("請一次選擇一個方法檔或目標表。");
  const file = files[0];
  if (!/\.(xlsx|csv|tsv)$/i.test(file.name))
    throw new Error("請選擇 XLSX、CSV 或 TSV 檔案；不支援 .xls。");
  if (!file.size || file.size > 1024 * 1024)
    throw new Error("請選擇非空白且不超過 1 MiB 的檔案。");
  const content = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(new Error("無法讀取檔案，請重新選擇。"));
    reader.readAsDataURL(file);
  });
  return { name: file.name, content };
}
export function requestPath(title, value = "", label = "完整路徑") {
  $("path-dialog-title").textContent = title;
  $("path-dialog-label").textContent = label;
  $("path-dialog-value").value = value;
  $("path-dialog").returnValue = "cancel";
  $("path-dialog").showModal();
  return new Promise((resolve) =>
    $("path-dialog").addEventListener(
      "close",
      () =>
        resolve(
          $("path-dialog").returnValue === "ok"
            ? $("path-dialog-value").value.trim()
            : null,
        ),
      { once: true },
    ),
  );
}
