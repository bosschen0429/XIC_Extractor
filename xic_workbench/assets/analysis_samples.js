// Sample steps, grouping cards, annotation import and the sample editor.
import { startJob } from "./analysis_jobs.js";
import { checklist } from "./analysis_setup.js";
import {
  applySampleAnnotations,
  applySampleCard,
  cardRows,
  clearSampleSet,
  confirmSampleSet,
  currentSampleAction,
  mergeSampleCard,
  mirrorSampleSet,
  receiveConfirmedSamples,
  renameSampleCard,
  replaceSamples,
  sampleAction,
  state,
  suggestionCards,
  updateSample,
} from "./analysis_state.js";
import {
  $,
  act,
  api,
  button,
  done,
  el,
  requestPath,
  ui,
} from "./analysis_ui.js";

// Editor-only state; committed sample rows are owned by analysis_state.js.
let editing = "untargeted";
let annotation;
const pendingExclusions = new Set();
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
  if (summary.chronology_pending)
    notes.push(
      "已完成文件／檔名配對，尚未讀取 RAW 檔頭；未知角色、注射順序與樣本名稱將在分析前依 RAW 確認。",
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
  if (summary.unmatched_raw_stems?.length)
    notes.push(
      `尚無上機資訊配對的 RAW：${summary.unmatched_raw_stems.join("、")}`,
    );
  const unmatched = summary.unmatched_sequence_names?.length;
  $(`${prefix}-summary`).textContent =
    `RAW ${summary.raw_count} ${summary.chronology_pending ? "個檔案" : "次上機"} · 上機資訊配對 ${summary.matched_count} 個` +
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
    "使用這個 RAW 資料夾已確認的樣品資訊。要改用新的上機資訊，請按「匯入上機資訊」。",
  sequence: "樣品資訊來自上機資訊；修改後會存成這個 RAW 資料夾的樣品資訊。",
};
export function renderRoleSummary(page) {
  const set = state.sampleSets[page];
  const counts = new Map();
  for (const sample of set.rows)
    counts.set(sample.sample_role, (counts.get(sample.sample_role) || 0) + 1);
  const excluded = set.rows.filter((row) => row.excluded).length;
  $(sampleId(page, "role-summary")).replaceChildren(
    ...(set.rows.length ? [] : []),
    ...roles
      .filter((role) => counts.has(role))
      .map((role) =>
        el(
          "span",
          `${roleLabels[role]} ${counts.get(role)}`,
          `chip${role === "study_sample" ? " accent" : ""}`,
        ),
      ),
    ...(excluded ? [el("span", `排除 ${excluded}`, "chip warn")] : []),
    ...(set.overview?.groups || []).map((entry) => {
      const chip = el("span", undefined, "chip group-chip");
      chip.dataset.group = entry.group;
      chip.append(groupSwatch(entry.color), `${entry.group} ${entry.count}`);
      return chip;
    }),
  );
  $(sampleId(page, "samples-count")).textContent = set.rows.length
    ? `${set.rows.length} 個樣本`
    : "尚未讀取樣本";
  $(sampleId(page, "samples-source")).textContent = set.rows.length
    ? sourceNotes[set.source] || ""
    : "";
  renderGroupCards(page);
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
// Naming-pattern cards group the server's suggestions; a card is a group
// name until applied, and applying writes that name to its members.
async function renameCard(page, card, label) {
  if (!label || label === card.label) return renderGroupCards(page);
  const changed = renameSampleCard(page, card, label);
  if (changed) await persistSamples(page);
  else renderGroupCards(page);
}
async function mergeCard(page, card, target) {
  const changed = mergeSampleCard(page, card, target);
  if (changed) await persistSamples(page);
  else renderGroupCards(page);
}
async function applyCard(page, card, overwrite) {
  applySampleCard(page, card, overwrite);
  await persistSamples(page);
}
function renderGroupCards(page) {
  const set = state.sampleSets[page];
  const box = $(sampleId(page, "group-cards"));
  const cards = set.rows.length ? suggestionCards(set) : [];
  box.hidden = !cards.length;
  if (!cards.length) return box.replaceChildren();
  // Study samples need a group; QC resolves by role and others are not
  // analyzed, so their cards wait in a collapsed section.
  const list = el("div", undefined, "group-card-list");
  const unneeded = el("div", undefined, "group-card-list");
  for (const card of cards) {
    const rows = cardRows(set, card);
    const open = rows.filter((row) => !row.group.trim()).length;
    const applied = rows.filter(
      (row) => row.group.trim() === card.label,
    ).length;
    const others = rows.length - open - applied;
    const node = el("div", undefined, "group-card");
    node.dataset.card = card.label;
    const head = el("div", undefined, "group-card-head");
    const name = el("input");
    name.value = card.label;
    name.setAttribute("aria-label", `建議組別 ${card.label} 的名稱`);
    name.dataset.analysisIdle = "";
    name.addEventListener("change", () =>
      act(() => renameCard(page, card, name.value.trim())),
    );
    head.append(name, el("strong", `×${rows.length}`, "group-card-count"));
    node.append(head);
    node.append(
      el(
        "small",
        applied === rows.length
          ? "已全部套用"
          : `${applied ? `已套用 ${applied} · ` : ""}${open} 個尚未標組別` +
              (others ? ` · ${others} 個已有其他組別` : ""),
        "subtle",
      ),
    );
    const actions = el("div", undefined, "group-card-actions");
    const apply = button(`套用到 ${open} 個`, () =>
      applyCard(page, card, false),
    );
    apply.disabled = !open;
    apply.setAttribute("aria-label", `套用 ${card.label} 到尚未標組別的樣本`);
    actions.append(apply);
    if (others) {
      const overwrite = button(
        `覆蓋 ${others} 個`,
        () => applyCard(page, card, true),
        "ghost",
      );
      overwrite.title = "連同已標其他組別的樣本一起改成這個組別";
      overwrite.setAttribute(
        "aria-label",
        `以 ${card.label} 覆蓋已標組別的樣本`,
      );
      actions.append(overwrite);
    }
    if (cards.length > 1) {
      const merge = el("select");
      merge.setAttribute("aria-label", `把 ${card.label} 合併到`);
      merge.append(el("option", "合併到…"));
      merge.options[0].value = "";
      for (const target of cards)
        if (target !== card) {
          const option = el("option", target.label);
          option.value = target.label;
          merge.append(option);
        }
      merge.addEventListener("change", () => {
        const target = cards.find((other) => other.label === merge.value);
        if (target) act(() => mergeCard(page, card, target));
      });
      actions.append(merge);
    }
    node.append(actions);
    const members = el("details", undefined, "group-card-members");
    members.append(
      el("summary", "成員"),
      el("p", card.members.join("、"), "mono"),
    );
    node.append(members);
    (rows.some((row) => row.sample_role === "study_sample")
      ? list
      : unneeded
    ).append(node);
  }
  const rest = el("details", undefined, "group-card-others");
  rest.append(
    el(
      "summary",
      `其他命名樣式（QC、Blank 等不需組別的樣本，${unneeded.children.length} 張）`,
    ),
    unneeded,
  );
  box.replaceChildren(
    el(
      "p",
      "依命名樣式建議的組別：確認名稱後套用。已有組別的樣本只在按「覆蓋」時才會改。",
      "subtle",
    ),
    ...(list.children.length ? [list] : []),
    ...(unneeded.children.length ? [rest] : []),
  );
}
// Words for click-to-group: separators, a capital after lowercase, and a
// code of capitals plus digits each end a word; matching is whole words.
const nameWords = (name) =>
  String(name || "").match(/[A-Z]+\d+|[A-Z]+[a-z]*|[a-z]+|\d+/g) || [];
function wordCell(row) {
  const td = el("td", undefined, "sample-words");
  const name = row.sample_name || row.raw_stem || "";
  let at = 0;
  for (const match of name.matchAll(/[A-Z]+\d+|[A-Z]+[a-z]*|[a-z]+|\d+/g)) {
    if (match.index > at) td.append(name.slice(at, match.index));
    const word = el("button", match[0], "word");
    word.type = "button";
    word.title = `選取名稱含「${match[0]}」的所有樣本`;
    word.addEventListener("click", () => selectWord(match[0]));
    td.append(word);
    at = match.index + match[0].length;
  }
  if (at < name.length) td.append(name.slice(at));
  return td;
}
function selectWord(word) {
  const wanted = word.toLowerCase();
  const set = state.sampleSets[editing];
  const hits = new Set(
    set.rows
      .filter((row) =>
        nameWords(row.sample_name || row.raw_stem)
          .map((part) => part.toLowerCase())
          .includes(wanted),
      )
      .map((row) => row.raw_stem),
  );
  let shown = 0;
  for (const box of $("sample-rows").querySelectorAll("input[data-sample]")) {
    box.checked = hits.has(box.dataset.sample);
    if (box.checked) shown += 1;
  }
  ui.toast(
    `已選取 ${shown} 個名稱含「${word}」的樣本，可在上方套用組別或排除。`,
  );
  $("sample-bulk-group").focus();
}
// Collaborator tables and earlier batches: preview the match, then apply.
const annotationModes = {
  table: {
    title: "匯入合作者樣品表",
    help: "選 Excel、CSV、TSV 檔或貼上表格，指定名稱欄與組別欄；對應到 RAW 的方式與上機資訊相同。",
  },
  carry: {
    title: "沿用上一批分組",
    help: "選先前匯出的樣品資訊，或工作區中已確認的批次（例如搬過的資料夾）；同一 RAW 直接對應，其餘依樣品名稱對應。",
  },
};
const columnFields = ["name", "group", "excluded", "reason"];
function columnSelect(field, columns, chosen) {
  const select = $(`annotation-col-${field}`);
  select.replaceChildren();
  if (field !== "name") select.append(el("option", "（不使用）"));
  if (field !== "name") select.options[0].value = "";
  columns.forEach((name, index) => {
    const option = el("option", name);
    option.value = String(index);
    select.append(option);
  });
  select.value = chosen === null || chosen === undefined ? "" : String(chosen);
}
function chosenColumns() {
  const columns = {};
  for (const field of columnFields) {
    const value = $(`annotation-col-${field}`).value;
    columns[field] = value === "" ? null : Number(value);
  }
  return columns;
}
async function openAnnotations(page, mode, path = "") {
  const set = state.sampleSets[page];
  if (!set.rows.length) {
    ui.toast("請先列出樣本檔名。");
    return;
  }
  annotation = { page, mode, preview: null };
  $("annotation-title").textContent = annotationModes[mode].title;
  $("annotation-help").textContent = annotationModes[mode].help;
  $("annotation-path").value = path;
  $("annotation-text").value = "";
  $("annotation-paste").hidden = mode !== "table";
  $("annotation-confirmed").hidden = mode !== "carry";
  $("annotation-columns").hidden = true;
  $("annotation-preview").hidden = true;
  $("annotation-overwrite").checked = false;
  $("annotation-apply").disabled = true;
  $("annotation-dialog").showModal();
  if (mode === "carry") {
    const listed = await api("/api/samples/annotations", {});
    const select = $("annotation-confirmed");
    select.replaceChildren(el("option", "工作區中已確認的批次…"));
    select.options[0].value = "";
    for (const item of listed.confirmed) {
      const option = el("option", item.name);
      option.value = item.path;
      select.append(option);
    }
  }
  if (path) await readAnnotations();
}
async function readAnnotations(columns) {
  const { page } = annotation;
  const raw_stems = state.sampleSets[page].rows.map((row) => row.raw_stem);
  const values = {
    raw_stems,
    path: $("annotation-path").value.trim(),
    text: annotation.mode === "table" ? $("annotation-text").value : "",
    ...(columns ? { columns } : {}),
  };
  let result = await api("/api/samples/annotations", values);
  if (result.kind === "table" && !columns) {
    for (const field of columnFields)
      columnSelect(field, result.columns, result.guess[field]);
    $("annotation-columns").hidden = false;
    result = await api("/api/samples/annotations", {
      ...values,
      columns: chosenColumns(),
    });
  } else if (result.kind === "metadata") $("annotation-columns").hidden = true;
  renderAnnotationPreview(result);
}
function renderAnnotationPreview(result) {
  annotation.preview = result;
  const matched = result.matched;
  $("annotation-counts").textContent =
    `對到 ${matched.length} 個 RAW · 表格中沒對到 ${result.unmatched.length} 筆 · ` +
    `有歧義 ${result.ambiguous.length} 筆（不會套用）`;
  $("annotation-rows").replaceChildren(
    ...matched.map((row) => {
      const tr = el("tr");
      tr.append(
        el("td", row.raw_stem, "mono"),
        el("td", row.name),
        el("td", row.group || "—"),
        el(
          "td",
          row.excluded
            ? `排除${row.exclusion_reason ? `：${row.exclusion_reason}` : ""}`
            : "—",
        ),
      );
      return tr;
    }),
  );
  for (const [id, names, label] of [
    ["annotation-unmatched", result.unmatched, "表格中沒對到的名稱"],
    ["annotation-ambiguous", result.ambiguous, "有歧義、對到多個 RAW 的名稱"],
  ]) {
    $(id).hidden = !names.length;
    $(id).querySelector("summary").textContent = `${label}（${names.length}）`;
    $(id).querySelector("p").textContent = names.join("、");
  }
  $("annotation-preview").hidden = false;
  $("annotation-apply").disabled = !matched.length;
  $("annotation-apply").textContent = `套用到 ${matched.length} 個樣本`;
}
async function applyAnnotations() {
  const { page, preview } = annotation;
  const overwrite = $("annotation-overwrite").checked;
  applySampleAnnotations(
    page,
    preview,
    overwrite,
    `${annotationModes[annotation.mode].title}標記排除`,
  );
  $("annotation-dialog").close();
  await persistSamples(page);
  done(`已套用 ${preview.matched.length} 個樣本的組別與排除。`);
}
// Colours come from the server's group-colour owner, shared with Inspector.
function groupSwatch(color) {
  const swatch = el("span", undefined, "group-swatch");
  if (color) swatch.style.background = color;
  else swatch.hidden = true;
  return swatch;
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
  $("sample-group-options").replaceChildren(
    ...(set.overview?.groups || [])
      .filter((entry) => entry.group !== "Other")
      .map((entry) => {
        const option = el("option");
        option.value = entry.group;
        return option;
      }),
  );
  const filter = $("sample-filter").value.trim().toLowerCase();
  const body = $("sample-rows");
  body.replaceChildren();
  let shown = 0;
  for (const row of set.rows) {
    if (
      filter &&
      ![row.raw_stem, row.sample_name, row.batch_id, row.group]
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
    tr.append(el("td", row.batch_id ?? "—"));
    tr.append(el("td", row.injection_order ?? "—", "num"));
    tr.append(wordCell(row));
    const select = el("select");
    select.setAttribute("aria-label", `${row.raw_stem} 角色`);
    roleOptions(select, row.sample_role);
    select.addEventListener("change", () => {
      updateSample(row, { sample_role: select.value });
      act(() => persistSamples(page));
    });
    tr.append(sampleCell(select));
    const group = el("input");
    group.value = row.group || "";
    group.setAttribute("list", "sample-group-options");
    group.setAttribute("aria-label", `${row.raw_stem} 組別`);
    group.placeholder = row.sample_role === "study_sample" ? "Other" : "";
    group.addEventListener("change", () => {
      updateSample(row, { group: group.value.trim() });
      act(() => persistSamples(page));
    });
    const groupCell = el("div", undefined, "group-cell");
    groupCell.append(
      groupSwatch(set.overview?.row_groups?.[row.raw_stem]?.color),
      group,
    );
    tr.append(sampleCell(groupCell));
    const pending = pendingExclusions.has(row.raw_stem);
    const excluded = el("input");
    excluded.type = "checkbox";
    excluded.checked = Boolean(row.excluded) || pending;
    excluded.setAttribute("aria-label", `${row.raw_stem} 排除`);
    const reason = el("input");
    reason.value = row.exclusion_reason || "";
    reason.disabled = !excluded.checked;
    reason.placeholder = excluded.checked ? "必填" : "";
    reason.required = excluded.checked;
    reason.setAttribute("aria-label", `${row.raw_stem} 排除原因`);
    // A checked box waits as a pending mark until its reason is entered;
    // only then does the row, which runs read, become excluded.
    excluded.addEventListener("change", () => {
      if (excluded.checked) {
        pendingExclusions.add(row.raw_stem);
        renderSamples();
        $("sample-rows")
          .querySelector(`input[aria-label="${row.raw_stem} 排除原因"]`)
          ?.focus();
        return;
      }
      pendingExclusions.delete(row.raw_stem);
      const saved = row.excluded;
      updateSample(row, { excluded: false, exclusion_reason: "" });
      renderSamples();
      if (saved) act(() => persistSamples(page));
    });
    reason.addEventListener("change", () => {
      const text = reason.value.trim();
      if (!text) {
        reason.value = row.exclusion_reason || "";
        ui.toast("排除原因不可空白；要取消排除請取消勾選。");
        return;
      }
      pendingExclusions.delete(row.raw_stem);
      updateSample(row, { excluded: true, exclusion_reason: text });
      act(() => persistSamples(page));
    });
    tr.append(sampleCell(excluded), sampleCell(reason));
    body.append(tr);
  }
  if (!set.rows.length) {
    // With nothing listed, the only useful action is to list the RAW names.
    const tr = el("tr", undefined, "sample-empty");
    const td = el("td");
    td.colSpan = 9;
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
  if (editing === page && $("sample-editor").open) renderSamples();
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
    $(sampleId(page, "samples-table")).onclick = () =>
      act(() => openAnnotations(page, "table"));
    $(sampleId(page, "samples-carry")).onclick = () =>
      act(() => openAnnotations(page, "carry"));
    // A different 上機資訊 makes the loaded rows stale until read again.
    $(spec.sequence).addEventListener("input", () => clearSamples(page));
  }
  $("sample-filter").oninput = renderSamples;
  // Pending marks without a reason never outlive the editor.
  $("sample-editor").addEventListener("close", () => {
    if (!pendingExclusions.size) return;
    pendingExclusions.clear();
    ui.toast("未填排除原因的勾選已取消。");
  });
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
  $("annotation-read").onclick = () => act(() => readAnnotations());
  $("annotation-confirmed").onchange = () => {
    if (!$("annotation-confirmed").value) return;
    $("annotation-path").value = $("annotation-confirmed").value;
    act(() => readAnnotations());
  };
  for (const field of columnFields)
    $(`annotation-col-${field}`).onchange = () =>
      act(() => readAnnotations(chosenColumns()));
  $("annotation-apply").onclick = () => act(applyAnnotations);
  $("sample-apply-role").onclick = () =>
    applyToSelected({ sample_role: $("sample-bulk-role").value });
  $("sample-apply-group").onclick = () =>
    applyToSelected({ group: $("sample-bulk-group").value.trim() });
  $("sample-exclude").onclick = () => {
    const reason = $("sample-bulk-reason").value.trim();
    if (!reason) {
      ui.toast("請先填寫排除原因。");
      $("sample-bulk-reason").focus();
      return;
    }
    applyToSelected({ excluded: true, exclusion_reason: reason });
  };
  $("sample-include").onclick = () =>
    applyToSelected({ excluded: false, exclusion_reason: "" });
}
