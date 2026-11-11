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
  isBusy,
  lock,
  requestPath,
} from "./analysis_ui.js";

// Editor-only state; sampleMetadata owns committed rows and async adoption.
let editing = "untargeted";
const roles = ["sample", "qc", "std", "blank", "other"];
const roleLabels = {
  sample: "Sample",
  qc: "QC",
  std: "STD",
  blank: "Blank",
  other: "Other",
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
function summaryNotes(summary) {
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
      `讀不到檔頭（要分析時會在執行前停止）：${summary.unreadable_raw.join("、")}`,
    );
  if (summary.unopened_context?.length)
    notes.push(
      `未開啟已排除的 RAW：${summary.unopened_context.join("、")}`,
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
  return notes;
}
function renderNotes(list, notes) {
  list.replaceChildren(...notes.map((note) => el("li", note)));
  list.hidden = !notes.length;
}
// The latest Targeted run reports what it read; the step card lists notes only.
export function renderSampleSummary(summary, prefix = "samples") {
  const notes = summaryNotes(summary);
  const unmatched = summary.unmatched_sequence_names?.length;
  $(`${prefix}-summary`).textContent =
    `RAW ${summary.raw_count} 次上機` +
    (summary.sequence_source ? ` · 上機資訊配對 ${summary.matched_count} 個` : "") +
    (unmatched ? ` · ${unmatched} 筆上機資訊未配對` : "") +
    (notes.length
      ? ` · ${notes.length} 處要確認，注射順序與樣本名稱以 RAW 為準`
      : "");
  renderNotes($(`${prefix}-conflicts`), notes);
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
    $(sampleId(page, "sequence-browse")).dataset.browse = spec.sequence;
  }
}
function renderSequenceLine(page) {
  const path = $(samplePages[page].sequence).value.trim();
  $(sampleId(page, "sequence-name")).textContent = path
    ? path.split(/[\\/]/).pop()
    : "未使用";
  $(sampleId(page, "sequence-name")).title = path;
  $(sampleId(page, "sequence-browse")).textContent = path ? "更換" : "選擇";
}
export function renderRoleSummary(page) {
  const set = sampleMetadata.forPage(page);
  const counts = new Map();
  for (const sample of set.rows.filter((row) => !row.excluded))
    counts.set(sample.sample_role, (counts.get(sample.sample_role) || 0) + 1);
  const excluded = set.rows.filter((row) => row.excluded).length;
  const parts = roles
    .filter((role) => counts.has(role))
    .map((role) => `${roleLabels[role]} ${counts.get(role)}`);
  if (excluded) parts.push(`排除 ${excluded}`);
  $(sampleId(page, "role-summary")).textContent = set.rows.length
    ? parts.join(" · ")
    : "尚未讀取樣本";
  renderSequenceLine(page);
  checklist(page);
}

// Listing reads every RAW header, so roles and order are known before a run.
export async function listSamples(page, importSequence = false) {
  const spec = samplePages[page];
  if (!$(spec.rawDir).value.trim()) return;
  // Another analysis owns the job slot; file names still give every row a role.
  if (isBusy()) {
    // A running sample read already delivers header facts for these inputs;
    // a filename preview would supersede it with less.
    if (analysis?.snapshot.job?.kind === "samples") return;
    await sampleMetadata.preview(page);
    return;
  }
  await analysis.start("samples", {
    page,
    raw_dir: $(spec.rawDir).value,
    dll_dir: $(spec.dll).value,
    sequence_source: $(spec.sequence).value,
    import_sequence: importSequence,
  });
}
const stemKey = (stem) => stem.toLowerCase();
// New RAW from a running instrument appear by name; only then reread headers.
async function relistIfChanged(page) {
  const set = sampleMetadata.forPage(page);
  if (!set.rows.length) return listSamples(page);
  const spec = samplePages[page];
  const listed = await api("/api/samples/preview", {
    raw_dir: $(spec.rawDir).value,
    sequence_source: $(spec.sequence).value,
  });
  const known = new Set(set.rows.map((row) => stemKey(row.raw_stem)));
  const found = new Set(listed.samples.map((row) => stemKey(row.raw_stem)));
  if (
    known.size !== found.size ||
    [...found].some((stem) => !known.has(stem))
  )
    await listSamples(page);
}

// Editor selection: drag a band, Ctrl adds, Shift extends, search selects.
const selection = { stems: new Set(), anchor: null };
let drag = null;
// A drag ends with a click on its last row; that click must not reselect.
let suppressClickUntil = 0;
function visibleRows() {
  const filter = $("sample-filter").value.trim().toLowerCase();
  return sampleMetadata
    .forPage(editing)
    .rows.filter(
      (row) =>
        !filter ||
        [row.raw_stem, row.sample_name].join(" ").toLowerCase().includes(filter),
    );
}
function clearSelection() {
  selection.stems = new Set();
  selection.anchor = null;
}
// Job state changes outside this module call this through lock().
export function renderSampleActions() {
  if ($("sample-editor")?.open) renderSelection();
}
function renderSelection() {
  const count = selection.stems.size;
  const rows = sampleMetadata
    .forPage(editing)
    .rows.filter((row) => selection.stems.has(row.raw_stem));
  const disabled = !count || !sampleMetadata.canEdit;
  $("sample-selection").textContent = count
    ? `選取的 ${count} 個 設為`
    : "先框選或搜尋";
  for (const node of $("sample-actions").querySelectorAll("button"))
    node.disabled = disabled;
  $("sample-exclude").textContent =
    rows.length && rows.every((row) => row.excluded) ? "取消排除" : "排除";
  for (const tr of $("sample-rows").querySelectorAll("tr[data-stem]"))
    tr.classList.toggle("selected", selection.stems.has(tr.dataset.stem));
}
function renderSamples() {
  const set = sampleMetadata.forPage(editing);
  const rows = visibleRows();
  const known = new Set(set.rows.map((row) => row.raw_stem));
  selection.stems = new Set([...selection.stems].filter((s) => known.has(s)));
  $("sample-rows").replaceChildren(
    ...rows.map((row) => {
      const tr = el("tr");
      tr.dataset.stem = row.raw_stem;
      tr.classList.toggle("excluded", Boolean(row.excluded));
      tr.classList.toggle("other", row.sample_role === "other");
      tr.append(
        el("td", row.raw_stem, "mono"),
        el("td", row.sample_name || row.raw_stem),
        el("td", roleLabels[row.sample_role] || row.sample_role),
      );
      return tr;
    }),
  );
  if (!set.rows.length) {
    const tr = el("tr", undefined, "sample-empty");
    const td = el("td", "選好 RAW 資料夾後會自動列出樣本。");
    td.colSpan = 3;
    tr.append(td);
    $("sample-rows").append(tr);
  }
  $("sample-editor-count").textContent =
    rows.length === set.rows.length
      ? `共 ${set.rows.length} 個樣本`
      : `顯示 ${rows.length} ／ 共 ${set.rows.length} 個樣本`;
  renderSelection();
  lock();
}
function apply(change) {
  const stems = [...selection.stems];
  if (!stems.length || !sampleMetadata.canEdit) return;
  clearSelection();
  act(() => sampleMetadata.edit(editing, stems, change));
  renderSamples();
}
function toggleExclusion() {
  const rows = sampleMetadata
    .forPage(editing)
    .rows.filter((row) => selection.stems.has(row.raw_stem));
  apply({ excluded: !rows.every((row) => row.excluded) });
}
function clickRow(stem, event) {
  if (event.shiftKey && selection.anchor !== null) {
    const order = visibleRows().map((row) => row.raw_stem);
    const [a, b] = [order.indexOf(selection.anchor), order.indexOf(stem)].sort(
      (x, y) => x - y,
    );
    if (a >= 0)
      for (const each of order.slice(a, b + 1)) selection.stems.add(each);
  } else if (event.ctrlKey || event.metaKey) {
    if (!selection.stems.delete(stem)) selection.stems.add(stem);
    selection.anchor = stem;
  } else {
    const only = selection.stems.size === 1 && selection.stems.has(stem);
    selection.stems = only ? new Set() : new Set([stem]);
    selection.anchor = stem;
  }
  renderSelection();
}
function startDrag(event) {
  const tr = event.target.closest("tr[data-stem]");
  if (!tr || event.button !== 0 || event.shiftKey) return;
  const box = $("sample-table");
  const bounds = box.getBoundingClientRect();
  drag = {
    box,
    band: null,
    y0: event.clientY - bounds.top + box.scrollTop,
    y: event.clientY,
    base: event.ctrlKey || event.metaKey ? new Set(selection.stems) : new Set(),
    last: null,
    moved: false,
  };
  event.preventDefault();
  // Keep keyboard role shortcuts with the table, not a previously focused field.
  box.focus({ preventScroll: true });
}
function moveDrag(event) {
  if (!drag) return;
  const { box } = drag;
  const bounds = box.getBoundingClientRect();
  drag.y = event.clientY;
  const y = event.clientY - bounds.top + box.scrollTop;
  if (!drag.moved && Math.abs(y - drag.y0) < 4) return;
  if (!drag.moved) {
    drag.moved = true;
    drag.band = el("div", undefined, "select-band");
    box.append(drag.band);
  }
  // Holding near an edge keeps scrolling so a long range needs one gesture.
  if (event.clientY > bounds.bottom - 28) box.scrollTop += 14;
  else if (event.clientY < bounds.top + 28) box.scrollTop -= 14;
  const y1 = Math.max(
    0,
    Math.min(event.clientY - bounds.top + box.scrollTop, box.scrollHeight),
  );
  const top = Math.min(drag.y0, y1);
  const bottom = Math.max(drag.y0, y1);
  drag.band.style.top = `${top}px`;
  drag.band.style.height = `${bottom - top}px`;
  const hit = new Set(drag.base);
  for (const tr of box.querySelectorAll("tr[data-stem]")) {
    const rect = tr.getBoundingClientRect();
    const a = rect.top - bounds.top + box.scrollTop;
    if (a + rect.height > top && a < bottom) {
      hit.add(tr.dataset.stem);
      drag.last = tr.dataset.stem;
    }
  }
  selection.stems = hit;
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

// Jobs

export function bindSamples() {
  sampleMetadata.subscribe(({ pages, replaced }) => {
    for (const page of pages) {
      const summary = sampleMetadata.forPage(page).summary;
      renderNotes(
        $(sampleId(page, "samples-conflicts")),
        summary ? summaryNotes(summary) : [],
      );
      renderRoleSummary(page);
    }
    if (pages.includes(editing) && $("sample-editor").open) {
      if (replaced) renderSamples();
      else renderSelection();
    }
    lock();
  });
  for (const [page, spec] of Object.entries(samplePages)) {
    $(sampleId(page, "samples-edit")).onclick = () =>
      act(async () => {
        editing = page;
        clearSelection();
        $("sample-filter").value = "";
        renderSamples();
        $("sample-editor").showModal();
        await relistIfChanged(page);
      });
    // A different 上機資訊 makes the loaded rows stale; list again with it.
    $(spec.sequence).addEventListener("input", () => {
      sampleMetadata.clear(page);
      renderSequenceLine(page);
    });
    $(spec.sequence).addEventListener("change", () =>
      act(() => listSamples(page, Boolean($(spec.sequence).value.trim()))),
    );
  }
  for (const [index, role] of roles.entries()) {
    const node = el("button", roleLabels[role]);
    node.type = "button";
    node.title = `鍵盤 ${index + 1}`;
    node.onclick = () => apply({ sample_role: role });
    $("sample-actions").append(node);
  }
  const exclude = el("button", "排除");
  exclude.type = "button";
  exclude.id = "sample-exclude";
  exclude.onclick = toggleExclusion;
  $("sample-actions").append(exclude);
  $("sample-filter").oninput = () => {
    clearSelection();
    if ($("sample-filter").value.trim())
      selection.stems = new Set(visibleRows().map((row) => row.raw_stem));
    renderSamples();
  };
  $("sample-rows").addEventListener("pointerdown", startDrag);
  document.addEventListener("pointermove", moveDrag);
  document.addEventListener("pointerup", endDrag);
  $("sample-rows").addEventListener("click", (event) => {
    const tr = event.target.closest("tr[data-stem]");
    if (!tr || event.timeStamp < suppressClickUntil) return;
    clickRow(tr.dataset.stem, event);
  });
  $("sample-editor").addEventListener("keydown", (event) => {
    if (event.target.closest("input, select, textarea")) return;
    const role = roles[Number(event.key) - 1];
    if (/^[1-5]$/.test(event.key) && selection.stems.size && role) {
      event.preventDefault();
      apply({ sample_role: role });
    }
  });
  $("sample-export").onclick = () =>
    act(async () => {
      const page = editing;
      const path = await requestPath(
        "匯出樣本資料 TSV / CSV",
        `${$(samplePages[page].output).value.replace(/[\\/]+$/, "")}\\sample_metadata.tsv`,
      );
      if (path) {
        await api("/api/samples/export", {
          samples: sampleMetadata.forPage(page).rows,
          path,
        });
        done(`樣本資料已匯出：${path}`);
      }
    });
}
