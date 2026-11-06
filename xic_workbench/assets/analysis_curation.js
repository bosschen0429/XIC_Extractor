// Matrix curation source detection, parameters, preview and export.
import { startJob } from "./analysis_jobs.js";
import { showResult } from "./analysis_setup.js";
import {
  beginCurationDetection,
  state,
  updateConfig,
  updateCuration,
} from "./analysis_state.js";
import {
  $,
  act,
  api,
  el,
  lock,
  pathField,
  pathTail,
  ui,
} from "./analysis_ui.js";

export function openCuration(sourceDir, samples) {
  updateCuration({ runSource: sourceDir, samples: samples ?? null });
  $("curation-source_dir").value = sourceDir;
  $("curation-sample_metadata").value = "";
  $("curation-istd").value = "";
  showResult();
  $("result-curation").hidden = false;
  $("result-curation").open = true;
  $("result-curation").scrollIntoView({ behavior: "smooth" });
  return detectCuration();
}
function curationPreferences() {
  return state.config.preferences.matrix_curation?.steps || {};
}
async function detectCuration() {
  invalidateCurationPreview();
  const request = beginCurationDetection();
  const sourceDir = $("curation-source_dir").value.trim();
  updateCuration({ detection: null });
  if (!sourceDir) {
    renderCuration(null, "");
    return;
  }
  try {
    const detection = await api("/api/matrix-curation/source", {
      source_dir: sourceDir,
      sample_metadata_path: $("curation-sample_metadata").value.trim(),
      istd_path: $("curation-istd").value.trim(),
      samples:
        sourceDir === state.curation.runSource ? state.curation.samples : null,
    });
    if (request !== state.curation.request) return;
    updateCuration({ detection });
    renderCuration(detection, "");
  } catch (error) {
    if (request === state.curation.request) renderCuration(null, error.message);
  }
}
function renderCuration(detection, error) {
  const list = $("curation-source");
  list.replaceChildren();
  const item = (tone, label, detail) => {
    const line = el("li", undefined, tone);
    const body = el("div", undefined, "check-item");
    body.title = detail;
    body.append(
      ui.icon(tone === "ok" ? "check-circle" : "dot"),
      el("strong", label),
      el("small", detail, "mono"),
    );
    line.append(body);
    list.append(line);
  };
  if (error) item("todo", "Alignment 輸出", error);
  else if (detection) {
    item("ok", "Matrix", pathTail(detection.matrix_path));
    item("ok", "Review", pathTail(detection.review_path));
    const source = detection.sample_source;
    item(
      source ? "ok" : "todo",
      "樣本資料",
      !source
        ? "找不到；需要時請在上方選擇樣本資料 TSV"
        : {
            file: `選擇的檔案 ${pathTail(source.label)}`,
            run: source.label,
            confirmed: "RAW 資料夾已確認的樣本資訊",
            raw: `依 RAW 檔頭判定角色 ${pathTail(source.label)}`,
          }[source.kind],
    );
    const istd = detection.istd_source;
    item(
      istd ? "ok" : "todo",
      "內標清單",
      !istd
        ? "找不到；內標不受保護。需要時請在上方選擇目標清單"
        : `${istd.kind === "file" ? `選擇的檔案 ${pathTail(istd.label)}` : istd.label}（${detection.istd_count} 個內標，一律保留）`,
    );
  }
  $("curation-output").textContent = detection
    ? `新資料夾：${detection.output_dir.replace(/_\d{8}_\d{6}$/, "_<開始時間>")}`
    : "選擇 Alignment 輸出後顯示";
  const container = $("curation-steps");
  container.replaceChildren();
  if (!detection) {
    const note = el("p", undefined, error ? "result-warning" : "subtle");
    if (error) note.append(ui.icon("alert"));
    note.append(error || "選擇 Alignment 輸出資料夾後，會列出可用的整理步驟。");
    container.append(note);
    lock();
    return;
  }
  const saved = curationPreferences();
  for (const step of detection.steps) {
    const remembered = saved[step.key] || {};
    const unavailable = Boolean(step.unavailable_reason);
    const block = el("div", undefined, "curation-step");
    block.dataset.step = step.key;
    const toggle = el("label", undefined, "field checkbox");
    const input = el("input");
    input.type = "checkbox";
    input.id = `curation-step-${step.key}`;
    // Unavailable controls stay disabled; lock() only manages idle ones.
    if (unavailable) input.disabled = true;
    else {
      input.dataset.analysisIdle = "";
      input.checked = remembered.enabled ?? true;
    }
    toggle.append(input, el("span", step.label));
    block.append(toggle, el("p", step.description, "subtle"));
    for (const parameter of step.parameters) {
      const field = el("label", undefined, "field");
      const value = el("input");
      value.type = "number";
      value.step = "any";
      value.min = parameter.minimum;
      value.max = parameter.maximum;
      value.id = `curation-${step.key}-${parameter.key}`;
      value.value = remembered.parameters?.[parameter.key] ?? "";
      if (unavailable) value.disabled = true;
      else value.dataset.analysisIdle = "";
      field.append(
        el("span", parameter.label),
        value,
        el("small", parameter.help),
      );
      block.append(field);
    }
    if (unavailable) {
      const reason = el("p", undefined, "result-warning");
      reason.append(
        ui.icon("alert"),
        `目前無法使用：${step.unavailable_reason}`,
      );
      block.append(reason);
    }
    container.append(block);
  }
  lock();
}
function curationRequest() {
  const detection = state.curation.detection;
  if (!detection) throw new Error("請先選擇可讀取的 Alignment 輸出資料夾。");
  const steps = {};
  const remembered = { ...curationPreferences() };
  for (const step of detection.steps) {
    if (step.unavailable_reason) continue;
    const enabled = $(`curation-step-${step.key}`).checked;
    const parameters = {};
    for (const parameter of step.parameters) {
      const input = $(`curation-${step.key}-${parameter.key}`);
      if (enabled && input.value.trim() === "")
        throw new Error(`請輸入「${step.label}」的${parameter.label}。`);
      if (input.value.trim() !== "")
        parameters[parameter.key] = Number(input.value);
    }
    remembered[step.key] = { enabled, parameters };
    if (enabled) steps[step.key] = parameters;
  }
  if (!Object.keys(steps).length)
    throw new Error("請至少啟用一個可用的整理步驟。");
  const sourceDir = detection.source_dir;
  return {
    remembered,
    values: {
      source_dir: sourceDir,
      sample_metadata_path: $("curation-sample_metadata").value.trim(),
      istd_path: $("curation-istd").value.trim(),
      samples:
        sourceDir === state.curation.runSource ? state.curation.samples : null,
      steps,
    },
  };
}
function invalidateCurationPreview() {
  updateCuration({ previewRequest: null });
  $("curation-preview").textContent = "設定已變更，請更新預覽。";
  lock();
}
async function previewCuration() {
  if (state.curation.previewBusy) return;
  const { values } = curationRequest();
  const submitted = JSON.stringify(values);
  updateCuration({ previewBusy: true, previewRequest: null });
  $("curation-preview-refresh").disabled = true;
  $("curation-preview").textContent = "正在計算預覽；仍可修改設定或檢閱結果。";
  lock();
  try {
    const preview = await api("/api/matrix-curation/preview", values);
    if (JSON.stringify(curationRequest().values) !== submitted) return;
    const host = $("curation-preview");
    host.replaceChildren(
      el(
        "p",
        `原始 ${preview.feature_count} 列 → 保留 ${preview.kept} 列、移除 ${preview.removed} 列。`,
      ),
    );
    host.append(
      el(
        "p",
        preview.istd_count
          ? `本次 ${preview.istd_count} 個內標，保護 ${preview.protected} 列。`
          : "本次沒有 ISTD 保護。",
      ),
    );
    const table = el("table");
    const head = el("tr");
    for (const name of ["步驟", "保留", "移除", "ISTD 保護", "無法判定"])
      head.append(el("th", name));
    table.append(head);
    for (const step of preview.steps) {
      const row = el("tr");
      for (const value of [
        step.label,
        step.kept,
        step.removed,
        step.protected,
        step.undetermined,
      ])
        row.append(el("td", String(value)));
      table.append(row);
      if (step.eligible_sample_count != null)
        host.append(
          el(
            "p",
            `至少 ${step.minimum_finite_count}/${step.eligible_sample_count} 個研究樣本有值（僅計未排除的 study_sample）；這是數值可用率，不是偵測率。`,
          ),
        );
    }
    host.append(table);
    for (const step of state.curation.detection.steps.filter(
      (step) => step.unavailable_reason,
    ))
      host.append(
        el(
          "p",
          `${step.label}：未執行，${preview.feature_count} 列無法判定；${step.unavailable_reason}`,
          "subtle",
        ),
      );
    updateCuration({ previewRequest: submitted });
  } finally {
    updateCuration({ previewBusy: false });
    $("curation-preview-refresh").disabled = false;
    lock();
  }
}
async function startCuration() {
  const { values, remembered } = curationRequest();
  if (JSON.stringify(values) !== state.curation.previewRequest)
    throw new Error("請先更新預覽，確認保留與移除列數。");
  updateConfig({
    preferences: await api("/api/preferences", {
      matrix_curation: { steps: remembered },
    }),
  });
  await startJob("curation", values);
}

export function bindCuration() {
  pathField(
    $("curation-paths"),
    "source_dir",
    "Alignment 輸出資料夾",
    "folder",
    "",
    "curation-",
  );
  pathField(
    $("curation-paths"),
    "sample_metadata",
    "樣本資料 TSV（選填）",
    "samples",
    "",
    "curation-",
    "只有找不到樣本資料，或要改用另一份時才需要選擇。",
  );
  pathField(
    $("curation-paths"),
    "istd",
    "內標清單（選填）",
    "targets",
    "",
    "curation-",
    "預設使用該次分析的內標清單；舊輸出找不到時才需要選擇。",
  );
  for (const id of [
    "curation-source_dir",
    "curation-sample_metadata",
    "curation-istd",
  ])
    $(id).addEventListener("change", () => act(detectCuration));
  renderCuration(null, "");
  $("curation-form").onsubmit = (event) => {
    event.preventDefault();
    act(startCuration);
  };

  $("curation-preview-refresh").onclick = () => act(previewCuration);
  $("curation-form").addEventListener("input", invalidateCurationPreview);
}
