// Matrix curation presentation and intention adapters; Session owns the draft.
import { createCurationSession } from "./analysis_curation_session.js";
import { analysis } from "./analysis_jobs.js";
import { showResult } from "./analysis_setup.js";
import { state, updateConfig } from "./analysis_state.js";
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

export let curation = null;

export function loadCurationSource(sourceDir, samples) {
  $("curation-source_dir").value = sourceDir;
  $("curation-sample_metadata").value = "";
  $("curation-istd").value = "";
  curation.load(sourceDir, samples);
}
export function openCuration(sourceDir, samples) {
  showResult();
  $("result-curation").hidden = false;
  $("result-curation").open = true;
  $("result-curation").scrollIntoView({ behavior: "smooth" });
  return loadCurationSource(sourceDir, samples);
}
function renderCuration(snapshot) {
  const { detection, sourceError: error } = snapshot;
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
    const source = detection.sample_source;
    item(
      source ? "ok" : "todo",
      "樣本資料",
      !source
        ? "尚無樣本資訊；請展開「改用其他來源」補充"
        : {
            file: `選擇的檔案 ${pathTail(source.label)}`,
            run: `${source.label}（${detection.sample_count} 個樣本，${detection.study_sample_count} 個未排除研究樣本）`,
            confirmed: "RAW 資料夾已確認的樣本資訊",
            raw: `依 RAW 檔頭判定角色 ${pathTail(source.label)}`,
          }[source.kind],
    );
    const istd = detection.istd_source;
    item(
      istd ? "ok" : "todo",
      "內標清單",
      !istd
        ? "本次沒有宣告內標；若實際有使用，請展開「改用其他來源」補充"
        : `${istd.kind === "file" ? `選擇的檔案 ${pathTail(istd.label)}` : istd.label}（${detection.istd_count} 個內標，一律保留）`,
    );
  }
  // A fully carried-over run shows only the way to choose another source.
  $("curation-source-step").classList.toggle(
    "carried",
    Boolean(
      detection &&
        !error &&
        detection.sample_source?.kind === "run" &&
        detection.istd_source &&
        snapshot.sources.source_dir === snapshot.runSource,
    ),
  );
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
    return;
  }
  for (const [index, step] of detection.steps.entries()) {
    const remembered = snapshot.settings[step.key];
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
      input.checked = remembered.enabled;
    }
    toggle.append(input, el("span", `${index + 1}. ${step.label}`));
    toggle.title = step.description;
    block.append(toggle);
    for (const parameter of step.parameters) {
      const field = el("label", undefined, "field");
      const value = el("input");
      value.type = "number";
      value.step = "any";
      value.min = parameter.minimum;
      value.max = parameter.maximum;
      value.id = `curation-${step.key}-${parameter.key}`;
      value.placeholder = `${parameter.minimum}–${parameter.maximum}`;
      value.title = parameter.help;
      value.value = remembered.parameters[parameter.key];
      if (unavailable) value.disabled = true;
      else value.dataset.analysisIdle = "";
      field.append(
        el("span", parameter.label),
        value,
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
}
function renderPreview(snapshot) {
  const host = $("curation-preview");
  const preview = snapshot.preview;
  if (!preview) {
    host.textContent = snapshot.message;
    return;
  }
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
  for (const name of ["步驟", "輸入", "保留", "本步移除", "ISTD 保護", "無法判定"])
    head.append(el("th", name));
  table.append(head);
  for (const step of preview.steps) {
    const row = el("tr");
    for (const value of [
      step.label,
      step.input,
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
          `至少 ${step.minimum_finite_count}/${step.eligible_sample_count} 個研究樣本有值才保留（QC、Blank 與已排除樣本不計）。`,
        ),
      );
  }
  host.append(table);
  for (const step of snapshot.detection.steps.filter(
    (step) => step.unavailable_reason,
  ))
    host.append(
      el(
        "p",
        `${step.label}：未執行，${preview.feature_count} 列無法判定；${step.unavailable_reason}`,
        "subtle",
      ),
    );
}

export function bindCuration() {
  curation = createCurationSession({
    request: api,
    startJob: (kind, values) => analysis.start(kind, values),
    preferences: state.config.preferences.matrix_curation?.steps || {},
    adoptPreferences: (preferences) => updateConfig({ preferences }),
  });
  pathField(
    $("curation-overrides"),
    "source_dir",
    "Alignment 輸出資料夾",
    "folder",
    "",
    "curation-",
  );
  pathField(
    $("curation-overrides"),
    "sample_metadata",
    "樣本資料 TSV（選填）",
    "samples",
    "",
    "curation-",
    "通常不需選擇：會沿用這次分析的樣本角色、注射順序與排除設定。",
  );
  pathField(
    $("curation-overrides"),
    "istd",
    "內標清單（選填）",
    "targets",
    "",
    "curation-",
    "預設使用該次分析的內標清單；舊輸出找不到時才需要選擇。",
  );
  let displayedDetection;
  let displayedError;
  curation.subscribe((snapshot) => {
    if (
      snapshot.detection !== displayedDetection ||
      snapshot.sourceError !== displayedError
    ) {
      displayedDetection = snapshot.detection;
      displayedError = snapshot.sourceError;
      renderCuration(snapshot);
    }
    renderPreview(snapshot);
    lock();
  });
  $("curation-form").onsubmit = (event) => {
    event.preventDefault();
    act(() => curation.export());
  };
  const sourceFields = {
    source_dir: $("curation-source_dir"),
    sample_metadata_path: $("curation-sample_metadata"),
    istd_path: $("curation-istd"),
  };
  const editSources = (event) => {
    curation.setSources(
      Object.fromEntries(
        Object.entries(sourceFields).map(([key, field]) => [key, field.value]),
      ),
      // A committed selection can reread changed contents at the same path.
      { refresh: event.type === "change" },
    );
  };
  for (const field of Object.values(sourceFields)) {
    field.addEventListener("input", editSources);
    field.addEventListener("change", editSources);
  }
  const editStep = (event) => {
    const block = event.target.closest("[data-step]");
    if (!block) return;
    const key = block.dataset.step;
    const step = curation.snapshot.detection?.steps.find(
      (step) => step.key === key,
    );
    if (!step) return;
    curation.setStep(key, {
      enabled: $(`curation-step-${key}`).checked,
      parameters: Object.fromEntries(
        step.parameters.map((parameter) => [
          parameter.key,
          $(`curation-${key}-${parameter.key}`).value,
        ]),
      ),
    });
  };
  $("curation-steps").addEventListener("input", editStep);
  $("curation-steps").addEventListener("change", editStep);
}
