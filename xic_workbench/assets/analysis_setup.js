// Setup/result navigation, readiness checklists and unsaved-setting feedback.
import { curation, loadCurationSource } from "./analysis_curation.js";
import { analysis, renderJob } from "./analysis_jobs.js";
import { sampleId, sampleMetadata, samplePages } from "./analysis_samples.js";
import { setDirty, setView, state } from "./analysis_state.js";
import { settingsValues } from "./analysis_targeted.js";
import {
  $,
  OUTPUT_LABEL,
  act,
  api,
  el,
  focusField,
  pathTail,
  status,
  ui,
} from "./analysis_ui.js";

let methodTimer;
export function dirty(kind, value = true) {
  setDirty(kind, value);
  const note = $(`${kind === "targeted" ? "target" : "untargeted"}-dirty`);
  note.textContent = value
    ? "批次設定尚未保存；按「保存批次設定」或開始分析才會留下變更。"
    : "";
  note.className = `dirty-note${value ? " unsaved" : ""}`;
  if (kind === "targeted") {
    clearTimeout(methodTimer);
    methodTimer = setTimeout(
      () =>
        act(async () => {
          const summary = await api("/api/targeted/method", {
            settings: settingsValues(),
          });
          setMethodState(summary);
        }),
      250,
    );
  }
  checklist(kind);
}
export function setMethodState(summary) {
  const label = summary?.label || "共用 Targeted 策略";
  const custom = new Set(summary?.custom_keys || []);
  const chip = $("target-method-state");
  chip.textContent = label;
  chip.className = custom.size ? "settings-warning" : "";
  const notes = $("target-method-note");
  notes.replaceChildren();
  notes.append(
    el(
      "p",
      `設定檔：${state.config.settings_source || "workspace.json"}`,
      "settings-source",
    ),
  );
  for (const warning of summary?.warnings || []) {
    notes.append(el("p", warning, "settings-warning"));
  }
  checklist("targeted");
}
// The page shows either the next analysis's setup or one opened result,
// never a result appended under an unrelated setup form.
export const showingResult = () => document.body.dataset.mode === "result";
// A reload returns to the result it showed; a new window starts at setup.
export const reloadedOnResult = (() => {
  try {
    return sessionStorage.getItem("xic-mode") === "result";
  } catch {
    return false;
  }
})();
export function showMode(mode) {
  document.body.dataset.mode = mode;
  try {
    sessionStorage.setItem("xic-mode", mode);
  } catch {
    /* per-tab convenience only */
  }
  $("analysis-root").hidden = mode === "result";
  $("analysis-result").hidden = mode !== "result";
  // The job card follows the visible page, so feedback is never hidden.
  if (state.config) renderJob();
  document
    .querySelectorAll(".view-tabs [data-view]")
    .forEach((node) =>
      node.setAttribute(
        "aria-current",
        mode === "setup" && node.dataset.view === state.view ? "page" : "false",
      ),
    );
}
export function prepareResult(page) {
  $("result-curation").open = false;
  $("result-curation").hidden = page.overview?.kind !== "untargeted";
  $("result-curation").ontoggle = () => {
    if (!$("result-curation").open) return;
    const folder = page.source.replace(/[\\/][^\\/]+$/, "");
    if (curation.snapshot.runSource !== folder) {
      const run = analysis.snapshot.results.untargeted;
      act(() => loadCurationSource(folder, run?.output_dir === folder ? run.samples : null));
    }
  };
}
export function showResult() {
  if (!showingResult()) window.XicUi.navigation.push();
  showMode("result");
  window.scrollTo(0, 0);
  // Do not leave focus on a run control moved inside the optional, closed panel.
  $("batch-title").tabIndex = -1;
  $("batch-title").focus({ preventScroll: true });
}
export function selectView(view, persist = true) {
  if (persist && (view !== state.view || showingResult()))
    window.XicUi.navigation.push();
  setView(view);
  document.body.dataset.view = view;
  for (const name of ["targeted", "untargeted"])
    $(`workspace-${name}`).hidden = name !== view;
  showMode("setup");
  if (persist) act(() => api("/api/preferences", { view }));
}
export function registerNavigation() {
  window.XicUi.navigation.register("workspace", {
    capture: () => ({ view: state.view, mode: document.body.dataset.mode }),
    restore: (saved) => {
      selectView(saved.view, false);
      showMode(saved.mode || "setup");
    },
  });
}

function checkItem(tone, label, detail, focus, mono = false) {
  return { tone, label, detail, focus, mono };
}
function pathItem(label, id, missing) {
  const value = $(id)?.value.trim() || "";
  const usable = value && $(id)?.dataset.validatedPath === value;
  return checkItem(
    usable ? "ok" : "todo",
    label,
    usable
      ? pathTail(value)
      : value
        ? $(`${id}-problem`)?.textContent || "尚未驗證"
        : missing,
    id,
    usable,
  );
}
function readerItem() {
  const reader = state.config.thermo_reader;
  const ready = !reader.dll_errors.length && !reader.dotnet_errors.length;
  return checkItem(
    ready ? "ok" : "todo",
    "RAW 讀取環境",
    ready ? "已備妥" : "需要修復讀取環境",
    "reader-setup",
  );
}
export function checklist(kind) {
  if (!state.config) return;
  const items = [];
  if (kind === "targeted") {
    items.push(pathItem("RAW 資料夾", "t-data_dir", "尚未選擇資料夾"));
    items.push(pathItem(OUTPUT_LABEL, "t-output_dir", "尚未選擇位置"));
    items.push(readerItem("t-dll_dir"));
    const istd = state.targets.filter((row) => row.role === "istd").length;
    items.push(
      checkItem(
        state.targets.length ? "ok" : "todo",
        "目標清單",
        state.targets.length
          ? `${state.targets.length} 個目標 · ${istd} 個 ISTD`
          : "清單是空的，請匯入或新增目標",
        "target-import",
      ),
    );
    items.push(...sampleItems("targeted"));
  } else {
    items.push(pathItem("RAW 資料夾", "u-raw_dir", "尚未選擇資料夾"));
    items.push(pathItem(OUTPUT_LABEL, "u-output_dir", "尚未選擇位置"));
    items.push(readerItem("u-dll_dir"));
    const preset = $("u-preset");
    items.push(
      checkItem(
        state.method ? "ok" : "todo",
        "分析方法",
        preset.selectedOptions[0]?.textContent || "尚未選擇方法",
        "u-preset",
      ),
    );
    items.push(...sampleItems("untargeted"));
  }
  const list = $(`${kind === "targeted" ? "target" : "untargeted"}-checklist`);
  list.replaceChildren(
    ...items.map((item) => {
      const li = el("li", undefined, item.tone);
      const target = el("button", undefined, "check-item");
      target.type = "button";
      target.append(
        ui.icon(
          item.tone === "ok"
            ? "check-circle"
            : item.tone === "todo"
              ? "dot"
              : "info",
        ),
        el("strong", item.label),
        el("small", item.detail, item.mono ? "mono" : ""),
      );
      target.title = item.detail;
      target.addEventListener("click", () => focusField(item.focus));
      li.append(target);
      return li;
    }),
  );
}
// Sample readiness covers roles and exclusions; statistical groups are not inputs.
function sampleItems(page) {
  const set = sampleMetadata.forPage(page);
  const edit = sampleId(page, "samples-edit");
  if (!set.rows.length) {
    const order = $(samplePages[page].sequence)?.value.trim();
    return [
      checkItem(
        "info",
        "樣本角色（選用）",
        order
          ? `上機資訊 ${pathTail(order)}；選好 RAW 資料夾後自動列出樣本`
          : "選好 RAW 資料夾後自動列出樣本與角色",
        edit,
      ),
    ];
  }
  const items = [
    checkItem("ok", "樣本角色", `${set.rows.length} 個樣本已讀取`, edit),
  ];
  const overview = set.overview;
  if (!overview) return items;
  if (overview.excluded.length) {
    items.push(
      checkItem(
        "info",
        "排除的樣本",
        `${overview.excluded.length} 個：` +
          overview.excluded.map((row) => row.raw_stem).join("、"),
        edit,
      ),
    );
  }
  return items;
}

export function bindSetup() {
  document
    .querySelectorAll("[data-view]")
    .forEach((node) =>
      node.addEventListener("click", () => selectView(node.dataset.view)),
    );
  document
    .querySelectorAll("[data-close]")
    .forEach((node) =>
      node.addEventListener("click", () => $(node.dataset.close).close()),
    );
  for (const dialog of document.querySelectorAll("dialog"))
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog && dialog.id !== "confirm-dialog")
        dialog.close();
    });
  let invalidShown = 0;
  for (const form of [$("targeted-form"), $("untargeted-form")])
    form.addEventListener(
      "invalid",
      (event) => {
        if (Date.now() - invalidShown < 500) return;
        invalidShown = Date.now();
        const input = event.target;
        const name =
          input.closest("label")?.querySelector("span")?.textContent ||
          input.getAttribute("aria-label") ||
          "欄位";
        focusField(input.id);
        status(`「${name}」的值無效：${input.validationMessage}`, true);
      },
      true,
    );

  $("open-result").onclick = () => {
    showResult();
    $("source-settings").open = true;
    $("source").focus();
  };
  $("result-back").onclick = () => selectView(state.view);
  // A result restored at startup is ready behind 開啟既有結果; only an
  // explicit open, or reloading the tab that showed it, switches to it.
  document.addEventListener("xic:result-opened", (event) => {
    prepareResult(event.detail);
    showResult();
  });
}
