"use strict";
import { bindCuration, curation } from "./analysis_curation.js";
import { analysis, bindJobs, renderJob } from "./analysis_jobs.js";
import { bindReader, renderReaderSetup } from "./analysis_reader.js";
import { bindResults, renderResults } from "./analysis_results.js";
import {
  bindSamples,
  mountSampleSteps,
  renderRoleSummary,
  sampleMetadata,
} from "./analysis_samples.js";
import {
  bindSetup,
  checklist,
  prepareResult,
  registerNavigation,
  reloadedOnResult,
  selectView,
  showMode,
  showingResult,
} from "./analysis_setup.js";
import {
  initializeConfig,
  markBooted,
  markClosed,
  state,
} from "./analysis_state.js";
import {
  bindTargeted,
  loadDeltaMass,
  renderCidSuggestions,
  renderSettings,
  saveTargeted,
  targetCount,
} from "./analysis_targeted.js";
import {
  $,
  act,
  api,
  el,
  isBusy,
  pathField,
  pathFields,
  status,
  ui,
} from "./analysis_ui.js";
import {
  bindUntargeted,
  loadMethod,
  reloadIstd,
  saveUntargeted,
} from "./analysis_untargeted.js";

registerNavigation();
window.XicAnalysis = {
  excludeSamples: sampleMetadata.exclude,
  get busy() {
    return analysis?.snapshot.busy || false;
  },
};

function applyThemeButton() {
  const dark = document.documentElement.dataset.theme === "dark";
  const toggle = $("theme-toggle");
  toggle.replaceChildren(ui.icon(dark ? "sun" : "moon"));
  toggle.title = dark ? "切換淺色模式" : "切換深色模式";
  toggle.setAttribute("aria-label", toggle.title);
}
async function shutdown() {
  if (isBusy()) throw new Error("請等待目前工作完成或先停止。");
  const accepted = await ui.confirm({
    title: "結束工作區？",
    message:
      "工作區停止後此分頁無法再操作；已儲存的設定與結果保留。",
    accept: "結束工作區",
    danger: true,
  });
  if (!accepted) return;
  await api("/api/shutdown", {});
  stopWindowTracking?.();
  stopAnalysis?.();
  curation?.dispose();
  markClosed();
  const screen = el("div", undefined, "closed-screen");
  const inner = el("div");
  const glyph = el("span", undefined, "empty-glyph");
  glyph.append(ui.icon("power"));
  inner.append(
    glyph,
    el("h1", "工作區已結束"),
    el("p", "可以關閉此分頁。", "subtle"),
  );
  screen.append(inner);
  document.body.append(screen);
}
let stopWindowTracking;
let stopAnalysis;
try {
  const response = await fetch("/assets/analysis.html");
  if (!response.ok) throw new Error("分析頁面無法載入。");
  $("analysis-root").innerHTML = await response.text();
  $("analysis-result").append($("result-curation"));
  mountSampleSteps();
  await window.XicReview.session;
  stopWindowTracking = ui.trackWindow();
  initializeConfig(await api("/api/config"));
  loadDeltaMass(state.config.delta_mass);
  renderCidSuggestions();
  ui.setTheme(state.config.preferences.theme || "light");
  $("dll-dir").value = state.config.settings.dll_dir || "";
  applyThemeButton();
  renderReaderSetup(state.config.thermo_reader);
  renderSettings(state.config.settings);
  for (const [key, label, kind, help] of pathFields)
    pathField(
      $("untargeted-paths"),
      key,
      label,
      kind,
      state.config.untargeted[key],
      "u-",
      help,
    );
  pathField(
    $("istd-path-field"),
    "targets_csv",
    "內標清單",
    "targets",
    state.config.untargeted.targets_csv,
  ).placeholder = "留空不使用 · 可拖入 XLSX、CSV、TSV";

  $("sequence-source").value = state.config.untargeted.sequence_source || "";
  for (const method of state.config.methods) {
    const option = el("option", method.display_name);
    option.value = method.key;
    $("u-preset").append(option);
  }
  try {
    await loadMethod(
      state.config.untargeted.preset,
      state.config.untargeted.overrides,
    );
  } catch (error) {
    const option = el("option", `無法讀取：${state.config.untargeted.preset}`);
    option.value = state.config.untargeted.preset;
    $("u-preset").append(option);
    $("u-preset").value = option.value;
    status(error.message, true);
  }
  bindReader();
  bindSetup();
  bindTargeted();
  bindUntargeted();
  bindSamples();
  const observeAnalysis = bindJobs(state.config.analysis);
  bindCuration();
  bindResults();
  bindShell();
  targetCount();
  renderRoleSummary("targeted");
  renderRoleSummary("untargeted");
  renderResults();
  selectView(
    ["targeted", "untargeted"].includes(state.config.preferences.view)
      ? state.config.preferences.view
      : "targeted",
    false,
  );
  renderJob();
  const initialReview = await window.XicReview.ready;
  if (initialReview?.page) {
    prepareResult(initialReview.page);
    if (reloadedOnResult || initialReview.opened) showMode("result");
  }
  markBooted();
  checklist("targeted");
  checklist("untargeted");
  await act(reloadIstd);
  stopAnalysis = observeAnalysis();
  document.body.classList.remove("booting");
  document.body.inert = false;
  document.body.removeAttribute("aria-busy");
} catch (error) {
  document.body.classList.remove("booting");
  document.body.inert = false;
  document.body.removeAttribute("aria-busy");
  ui.toast(error.message, "error");
}

function bindShell() {
  $("theme-toggle").onclick = () =>
    act(async () => {
      const theme =
        document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      ui.setTheme(theme);
      applyThemeButton();
      await api("/api/preferences", { theme });
    });

  $("about-workspace").onclick = () => $("about-dialog").showModal();
  $("shutdown-workspace").onclick = () => act(shutdown);
  document.addEventListener("keydown", (event) => {
    if (
      !(event.ctrlKey || event.metaKey) ||
      document.querySelector("dialog[open]")
    )
      return;
    if (["1", "2"].includes(event.key)) {
      event.preventDefault();
      selectView({ 1: "targeted", 2: "untargeted" }[event.key]);
    } else if (event.key === ".") {
      event.preventDefault();
      act(() => analysis.cancel());
    } else if (event.key === "Enter") {
      event.preventDefault();
      // Starting belongs to setup; a shown result never launches a hidden form.
      if (!isBusy() && !showingResult())
        $(`${state.view}-form`).requestSubmit();
    } else if (
      event.key.toLowerCase() === "s" &&
      ["targeted", "untargeted"].includes(state.view)
    ) {
      event.preventDefault();
      if (!isBusy())
        act(() =>
          state.view === "targeted" ? saveTargeted() : saveUntargeted(),
        );
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (!state.closed && (state.dirty.targeted || state.dirty.untargeted))
      event.preventDefault();
  });
}
