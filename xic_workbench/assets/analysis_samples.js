// Shared sample roles, exclusions and optional acquisition-information import.
import { analysis } from "./analysis_jobs.js";
import { checklist } from "./analysis_setup.js";
import { createSampleMetadata } from "./analysis_sample_metadata.js";
import {
  $,
  act,
  api,
  done,
  el,
  lock,
  requestPath,
  ui,
} from "./analysis_ui.js";

// Editor-only state; sampleMetadata owns committed rows and async adoption.
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
export const sampleMetadata = createSampleMetadata({
  request: api,
  jobBusy: () => Boolean(analysis?.snapshot.busy),
  inputs: (page) => {
    const spec = samplePages[page];
    return {
      raw_dir: $(spec.rawDir).value,
      dll_dir: $(spec.dll).value,
      sequence_source: $(spec.sequence).value,
    };
  },
});
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
  confirmed: "沿用已確認的角色與排除",
  sequence: "來自上機資訊",
};
export function renderRoleSummary(page) {
  const set = sampleMetadata.forPage(page);
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
function sampleCell(control) {
  const td = el("td");
  td.append(control);
  return td;
}
function renderSamples() {
  const page = editing;
  const set = sampleMetadata.forPage(page);
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
    select.dataset.sampleEdit = "";
    select.setAttribute("aria-label", `${row.raw_stem} 角色`);
    roleOptions(select, row.sample_role);
    select.addEventListener("change", () => {
      act(() =>
        sampleMetadata.edit(page, [row.raw_stem], {
          sample_role: select.value,
        }),
      );
    });
    tr.append(sampleCell(select));
    const excluded = el("input");
    excluded.dataset.sampleEdit = "";
    excluded.type = "checkbox";
    excluded.checked = Boolean(row.excluded);
    excluded.setAttribute("aria-label", `${row.raw_stem} 排除`);
    excluded.addEventListener("change", () => {
      tr.classList.toggle("excluded", excluded.checked);
      act(() =>
        sampleMetadata.edit(page, [row.raw_stem], {
          excluded: excluded.checked,
        }),
      );
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
  lock();
}
function selectedSamples() {
  const selected = new Set(
    [...$("sample-rows").querySelectorAll("input[data-sample]:checked")].map(
      (node) => node.dataset.sample,
    ),
  );
  if (!selected.size) ui.toast("請先勾選要套用的樣本。");
  return sampleMetadata.forPage(editing).rows.filter((row) =>
    selected.has(row.raw_stem),
  );
}
function applyToSelected(change) {
  const rows = selectedSamples();
  if (!rows.length) return;
  act(() => sampleMetadata.edit(editing, rows.map((row) => row.raw_stem), change));
  renderSamples();
}
async function scanSamples(page, importSequence = false) {
  const spec = samplePages[page];
  await analysis.start("samples", {
    page,
    raw_dir: $(spec.rawDir).value,
    dll_dir: $(spec.dll).value,
    sequence_source: $(spec.sequence).value,
    import_sequence: importSequence,
  });
}

// Jobs

export function bindSamples() {
  sampleMetadata.subscribe(({ pages, replaced }) => {
    for (const page of pages) {
      const summary = sampleMetadata.forPage(page).summary;
      if (summary) renderSampleSummary(summary, sampleId(page, "samples"));
      else {
        $(sampleId(page, "samples-summary")).textContent = "";
        $(sampleId(page, "samples-conflicts")).hidden = true;
      }
      renderRoleSummary(page);
    }
    if (replaced && pages.includes(editing) && $("sample-editor").open)
      renderSamples();
    lock();
  });
  for (const [page, spec] of Object.entries(samplePages)) {
    for (const part of ["samples-refresh", "samples-import"])
      $(sampleId(page, part)).addEventListener("pointerdown", (event) => {
        if (!event.currentTarget.disabled) sampleMetadata.interrupt(page);
      });
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
            samples: sampleMetadata.forPage(page).rows,
            path,
          });
          done(`樣本資料已匯出：${path}`);
        }
      });
    // A different 上機資訊 makes the loaded rows stale until read again.
    $(spec.sequence).addEventListener("input", () => sampleMetadata.clear(page));
  }
  $("sample-filter").oninput = renderSamples;
  $("sample-select-all").onchange = () =>
    $("sample-rows")
      .querySelectorAll("input[data-sample]")
      .forEach((node) => (node.checked = $("sample-select-all").checked));
  roleOptions($("sample-bulk-role"), "study_sample");
  for (const id of ["sample-apply-role", "sample-exclude", "sample-include"])
    $(id).dataset.sampleEdit = "";
  $("sample-apply-role").onclick = () =>
    applyToSelected({ sample_role: $("sample-bulk-role").value });
  $("sample-exclude").onclick = () => applyToSelected({ excluded: true });
  $("sample-include").onclick = () => applyToSelected({ excluded: false });
}
