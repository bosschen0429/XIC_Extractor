// Shared sample roles, exclusions and optional acquisition-information import.
import { startJob } from "./analysis_jobs.js";
import { checklist } from "./analysis_setup.js";
import {
  clearSampleSet,
  confirmSampleSet,
  currentSampleAction,
  mirrorSampleSet,
  receiveConfirmedSamples,
  replaceSamples,
  sampleAction,
  state,
  updateSample,
} from "./analysis_state.js";
import {
  $,
  act,
  api,
  done,
  el,
  requestPath,
  ui,
} from "./analysis_ui.js";

// Editor-only state; committed sample rows are owned by analysis_state.js.
let editing = "untargeted";
const roles = [
  "study_sample",
  "qc",
  "pooled_qc",
  "blank",
  "calibrator",
  "solvent",
  "system_suitability",
  "unknown",
];
const roleLabels = {
  study_sample: "研究樣本",
  qc: "QC",
  pooled_qc: "Pooled QC",
  blank: "Blank",
  calibrator: "校正品",
  solvent: "溶劑",
  system_suitability: "系統適用性",
  unknown: "未指定",
};
export const samplePages = {
  targeted: {
    prefix: "t-",
    sequence: "t-injection_order_source",
    rawDir: "t-data_dir",
    dll: "t-dll_dir",
    output: "t-output_dir",
    step: 3,
  },
  untargeted: {
    prefix: "",
    sequence: "sequence-source",
    rawDir: "u-raw_dir",
    dll: "u-dll_dir",
    output: "u-output_dir",
    step: 3,
  },
};
export const sampleId = (page, part) => `${samplePages[page].prefix}${part}`;
export function renderSampleSummary(summary, prefix = "samples") {
  const notes = [];
  for (const conflict of summary.conflicts || []) {
    const names = conflict.sequence_names.join("、");
    if (conflict.kind === "duplicate_order")
      notes.push(
        `序號 ${conflict.detail} 出現 ${conflict.sequence_names.length} 次：${names}`,
      );
    else {
      const [file, text] = conflict.detail.split(" / ");
      notes.push(`${names}：檔名是 ${file}，描述寫 ${text}`);
    }
  }
  if (summary.unreadable_raw?.length)
    notes.push(
      `讀不到檔頭，已排除（不分析）：${summary.unreadable_raw.join("、")}`,
    );
  if (summary.unopened_context?.length)
    notes.push(
      `未開啟非分析 RAW（角色或排除已明確指定）：${summary.unopened_context.join("、")}`,
    );
  if (summary.unmatched_sequence_names?.length)
    notes.push(
      `未配對的上機資訊：${summary.unmatched_sequence_names.join("、")}`,
    );
  if (summary.ambiguous_sequence_names?.length)
    notes.push(
      `需確認的重複／歧義配對：${summary.ambiguous_sequence_names.join("、")}`,
    );
  if (summary.sequence_source && summary.unmatched_raw_stems?.length)
    notes.push(
      `尚無上機資訊配對的 RAW：${summary.unmatched_raw_stems.join("、")}`,
    );
  const unmatched = summary.unmatched_sequence_names?.length;
  $(`${prefix}-summary`).textContent =
    `RAW ${summary.raw_count} ${summary.chronology_pending ? "個檔案" : "次上機"}` +
    (summary.sequence_source ? ` · 上機資訊配對 ${summary.matched_count} 個` : "") +
    (unmatched ? ` · ${unmatched} 筆上機資訊未配對` : "") +
    (notes.length
      ? ` · ${notes.length} 處要確認，注射順序與樣本名稱以 RAW 為準`
      : "");
  $(`${prefix}-conflicts`).replaceChildren(
    ...notes.map((note) => el("li", note)),
  );
  $(`${prefix}-conflicts`).hidden = !notes.length;
}
function roleOptions(select, current) {
  for (const role of roles) {
    const option = el("option", roleLabels[role]);
    option.value = role;
    select.append(option);
  }
  select.value = current;
}
export function mountSampleSteps() {
  for (const [page, spec] of Object.entries(samplePages)) {
    const section = document.querySelector(`[data-sample-step="${page}"]`);
    section.replaceChildren($("sample-step-template").content.cloneNode(true));
    for (const node of section.querySelectorAll("[data-part]"))
      node.id =
        node.dataset.part === "sequence-source"
          ? spec.sequence
          : sampleId(page, node.dataset.part);
    $(sampleId(page, "step-no")).textContent = spec.step;
    $(sampleId(page, "sequence-label")).htmlFor = spec.sequence;
    $(sampleId(page, "sequence-browse")).dataset.browse = spec.sequence;
  }
}
const sourceNotes = {
  confirmed:
    "已沿用這個 RAW 資料夾確認的角色與排除設定；後續資料整理直接共用。",
  sequence: "樣本資訊來自上機資訊；修改後自動儲存，兩種分析與資料整理共用。",
};
export function renderRoleSummary(page) {
  const set = state.sampleSets[page];
  const counts = new Map();
  for (const sample of set.rows.filter((row) => !row.excluded))
    counts.set(sample.sample_role, (counts.get(sample.sample_role) || 0) + 1);
  const excluded = set.rows.filter((row) => row.excluded).length;
  $(sampleId(page, "role-summary")).replaceChildren(
    ...roles
      .filter((role) => counts.has(role))
      .map((role) =>
        el(
          "span",
          role === "unknown"
            ? `${counts.get(role)} 個待確認；執行時自動讀取 RAW 角色`
            : `${roleLabels[role]} ${counts.get(role)}`,
          `chip${role === "study_sample" ? " accent" : ""}`,
        ),
      ),
    ...(excluded ? [el("span", `排除 ${excluded}`, "chip warn")] : []),
  );
  $(sampleId(page, "samples-count")).textContent = set.rows.length
    ? `${set.rows.length} 個樣本`
    : "尚未讀取樣本";
  $(sampleId(page, "samples-source")).textContent = set.rows.length
    ? sourceNotes[set.source] || ""
    : "";
  checklist(page);
}
export function applySampleResult(page, result, action) {
  if (!currentSampleAction(action)) return false;
  replaceSamples(page, result);
  renderSampleSummary(result.summary, sampleId(page, "samples"));
  renderRoleSummary(page);
  if (result.summary?.confirmed) mirrorSamples(page, action);
  if (editing === page && $("sample-editor").open) renderSamples();
  return true;
}
// The other flow on the same folder shows the same confirmed rows.
function mirrorSamples(page, action) {
  const rawDir = $(samplePages[page].rawDir).value;
  for (const [other, spec] of Object.entries(samplePages))
    if (
      other !== page &&
      $(spec.rawDir).value === rawDir &&
      state.sampleRevisions[other] === action.revisions[other]
    ) {
      mirrorSampleSet(page, other);
      renderRoleSummary(other);
    }
}
export function clearSamples(page) {
  sampleAction(page);
  clearSampleSet(page);
  $(sampleId(page, "samples-summary")).textContent = "";
  $(sampleId(page, "samples-conflicts")).hidden = true;
  renderRoleSummary(page);
  if (editing === page && $("sample-editor").open) renderSamples();
}
function sampleCell(control) {
  const td = el("td");
  td.append(control);
  return td;
}
function renderSamples() {
  const page = editing;
  const set = state.sampleSets[page];
  renderRoleSummary(page);
  const filter = $("sample-filter").value.trim().toLowerCase();
  const body = $("sample-rows");
  body.replaceChildren();
  let shown = 0;
  for (const row of set.rows) {
    if (
      filter &&
      ![row.raw_stem, row.sample_name]
        .join(" ")
        .toLowerCase()
        .includes(filter)
    )
      continue;
    shown += 1;
    const tr = el("tr");
    if (row.excluded) tr.className = "excluded";
    const selected = el("input");
    selected.type = "checkbox";
    selected.dataset.sample = row.raw_stem;
    selected.setAttribute("aria-label", `選取 ${row.raw_stem}`);
    tr.append(sampleCell(selected));
    tr.append(el("td", row.raw_stem ?? "—", "mono"));
    tr.append(el("td", row.sample_name || row.raw_stem));
    const select = el("select");
    select.setAttribute("aria-label", `${row.raw_stem} 角色`);
    roleOptions(select, row.sample_role);
    select.addEventListener("change", () => {
      updateSample(row, { sample_role: select.value });
      act(() => persistSamples(page));
    });
    tr.append(sampleCell(select));
    const excluded = el("input");
    excluded.type = "checkbox";
    excluded.checked = Boolean(row.excluded);
    excluded.setAttribute("aria-label", `${row.raw_stem} 排除`);
    excluded.addEventListener("change", () => {
      updateSample(row, { excluded: excluded.checked });
      tr.classList.toggle("excluded", excluded.checked);
      act(() => persistSamples(page));
    });
    tr.append(sampleCell(excluded));
    body.append(tr);
  }
  if (!set.rows.length) {
    // With nothing listed, the only useful action is to list the RAW names.
    const tr = el("tr", undefined, "sample-empty");
    const td = el("td");
    td.colSpan = 5;
    const list = el("button", "列出樣本檔名");
    list.type = "button";
    list.dataset.analysisIdle = "";
    list.onclick = () => act(() => scanSamples(page));
    td.append(el("span", "尚未讀取樣本", "subtle"), list);
    tr.append(td);
    body.append(tr);
  }
  $("sample-select-all").checked = false;
  $("sample-editor-count").textContent = filter
    ? `顯示 ${shown} ／ 共 ${set.rows.length} 個樣本`
    : `共 ${set.rows.length} 個樣本`;
}
function selectedSamples() {
  const selected = new Set(
    [...$("sample-rows").querySelectorAll("input[data-sample]:checked")].map(
      (node) => node.dataset.sample,
    ),
  );
  if (!selected.size) ui.toast("請先勾選要套用的樣本。");
  return state.sampleSets[editing].rows.filter((row) =>
    selected.has(row.raw_stem),
  );
}
function applyToSelected(change) {
  const rows = selectedSamples();
  if (!rows.length) return;
  for (const row of rows) updateSample(row, change);
  renderSamples();
  act(() => persistSamples(editing));
}
// Edits confirm the folder's sample metadata at once; both flows read it.
async function persistSamples(page) {
  const set = state.sampleSets[page];
  if (!set.rows.length) return;
  const rawDir = $(samplePages[page].rawDir).value;
  const action = sampleAction(page);
  const saved = await api("/api/samples/save", {
    raw_dir: rawDir,
    samples: set.rows,
  });
  if (!currentSampleAction(action)) return;
  confirmSampleSet(page, saved.overview);
  mirrorSamples(page, action);
  renderRoleSummary(page);
}
export async function previewSamples(page, action) {
  const spec = samplePages[page];
  const values = {
    raw_dir: $(spec.rawDir).value,
    sequence_source: $(spec.sequence).value,
  };
  const result = await api("/api/samples/preview", values);
  if (
    $(spec.rawDir).value !== values.raw_dir ||
    $(spec.sequence).value !== values.sequence_source
  )
    return;
  applySampleResult(page, result, action);
}
async function scanSamples(page, importSequence = false) {
  const spec = samplePages[page];
  await startJob("samples", {
    page,
    raw_dir: $(spec.rawDir).value,
    dll_dir: $(spec.dll).value,
    sequence_source: $(spec.sequence).value,
    import_sequence: importSequence,
  });
}

// Jobs

export function bindSamples() {
  for (const [page, spec] of Object.entries(samplePages)) {
    for (const part of ["samples-refresh", "samples-import"])
      $(sampleId(page, part)).addEventListener("pointerdown", () =>
        sampleAction(page),
      );
    $(sampleId(page, "samples-refresh")).onclick = () =>
      act(() => scanSamples(page));
    $(sampleId(page, "samples-import")).onclick = () =>
      act(() => scanSamples(page, true));
    $(sampleId(page, "samples-edit")).onclick = () => {
      editing = page;
      renderSamples();
      $("sample-editor").showModal();
    };
    $(sampleId(page, "samples-export")).onclick = () =>
      act(async () => {
        const path = await requestPath(
          "匯出樣本資料 TSV / CSV",
          `${$(spec.output).value}/sample_metadata.tsv`,
        );
        if (path) {
          await api("/api/samples/export", {
            samples: state.sampleSets[page].rows,
            path,
          });
          done(`樣本資料已匯出：${path}`);
        }
      });
    // A different 上機資訊 makes the loaded rows stale until read again.
    $(spec.sequence).addEventListener("input", () => clearSamples(page));
  }
  $("sample-filter").oninput = renderSamples;
  $("sample-select-all").onchange = () =>
    $("sample-rows")
      .querySelectorAll("input[data-sample]")
      .forEach((node) => (node.checked = $("sample-select-all").checked));
  roleOptions($("sample-bulk-role"), "study_sample");
  // A change confirmed elsewhere, such as an exclusion marked in review.
  document.addEventListener("xic:samples-confirmed", (event) => {
    const { raw_dir, samples, overview } = event.detail;
    for (const [page, spec] of Object.entries(samplePages)) {
      const set = state.sampleSets[page];
      if (!set.rows.length || $(spec.rawDir).value !== raw_dir) continue;
      receiveConfirmedSamples(page, samples, overview);
      renderRoleSummary(page);
    }
  });
  $("sample-apply-role").onclick = () =>
    applyToSelected({ sample_role: $("sample-bulk-role").value });
  $("sample-exclude").onclick = () => applyToSelected({ excluded: true });
  $("sample-include").onclick = () => applyToSelected({ excluded: false });
}
