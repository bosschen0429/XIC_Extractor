// Analysis workflow composition, job presentation and completion effects.
import {
  artifactDetails,
  openReview,
  renderResults,
  showArtifact,
} from "./analysis_results.js";
import {
  sampleMetadata,
  renderSampleSummary,
} from "./analysis_samples.js";
import { createAnalysisSession } from "./analysis_session.js";
import {
  dirty,
  selectView,
  setMethodState,
  showingResult,
} from "./analysis_setup.js";
import { state, updateConfig } from "./analysis_state.js";
import {
  $,
  act,
  api,
  done,
  el,
  isBusy,
  lock,
  status,
  ui,
} from "./analysis_ui.js";

export let analysis = null;

const jobLabels = {
  running: "執行中",
  cancelling: "正在停止",
  succeeded: "已完成",
  failed: "執行失敗",
  cancelled: "已停止",
};
const jobNames = {
  targeted: "Targeted 分析",
  untargeted: "Untargeted 分析",
  samples: "讀取樣本資訊",
  curation: "矩陣整理",
  inspector: "Inspector 匯出",
};
const jobPage = { targeted: "targeted", curation: "untargeted" };
export const resultIds = {
  targeted: "target-result",
  untargeted: "untargeted-result",
  curation: "curation-result",
};
export const pageOf = (job) => job?.page || jobPage[job?.kind] || "untargeted";
function prepareStart(kind, values) {
  if (isBusy()) throw new Error("請等待目前工作完成或先停止。");
  if (kind === "targeted" && !values.targets.length)
    throw new Error("目標清單是空的：按「檢視與編輯」新增，或匯入目標清單。");
  return kind === "samples" ? sampleMetadata.prepareJob(values) : null;
}

async function acceptedStart(kind, values, job, acceptSampleRead) {
  if (kind === "untargeted") {
    updateConfig({ untargeted: values });
    dirty("untargeted", false);
  }
  if (acceptSampleRead) acceptSampleRead(job);
  if (kind === "targeted") {
    updateConfig({ settings: { ...values.settings } });
    updateConfig({ targeted_method: await api("/api/targeted/method", {}) });
    setMethodState(state.config.targeted_method);
    dirty("targeted", false);
  }
  status("工作已開始。關閉分頁不會停止分析。");
}

function renderActivity() {
  const snapshot = analysis.snapshot;
  const job = snapshot.job;
  const pill = $("activity");
  const page = pageOf(job);
  const recentlyFinished =
    snapshot.lastCompletionAt && Date.now() - snapshot.lastCompletionAt < 20000;
  const visible =
    job && (snapshot.running || (state.view !== page && recentlyFinished));
  pill.hidden = !visible;
  if (!visible) return;
  const percent =
    job.total > 0 ? ` ${Math.round((job.completed / job.total) * 100)}%` : "";
  pill.className = `activity-pill${job.status === "succeeded" ? " done" : job.status === "failed" ? " failed" : ""}`;
  pill.replaceChildren(
    snapshot.running
      ? el("span", undefined, "spinner")
      : ui.icon(job.status === "succeeded" ? "check-circle" : "alert"),
    `${jobNames[job.kind]} · ${jobLabels[job.status]}${snapshot.running ? percent : ""}`,
  );
  pill.onclick = () => selectView(page);
}
const curationResultShown = () =>
  showingResult() &&
  !$("result-curation").hidden &&
  $("result-curation").open &&
  !$("curation-result").hidden;
export function renderJob() {
  const snapshot = analysis.snapshot;
  const job = snapshot.job;
  const sampleSummary =
    job?.kind === "targeted"
      ? job.sample_summary
      : snapshot.results.targeted?.sample_summary;
  $("target-samples-review").hidden = !sampleSummary;
  if (sampleSummary) renderSampleSummary(sampleSummary, "target-samples");
  const card = $("analysis-job");
  // Curation starts from a result; every other job belongs to a setup page.
  // A hidden card leaves completion and failure to the toast below.
  const onResult = showingResult();
  const slot = document.querySelector(
    `[data-job-slot="${onResult ? "curation" : state.view}"]`,
  );
  if (slot && card.parentElement !== slot) slot.append(card);
  // A completed analysis is summarized by its result card at the top of the
  // setup page; a completed curation only while its card is on screen.
  const summarizedByResult =
    job?.status === "succeeded" &&
    job.kind in resultIds &&
    (!onResult || job.kind !== "curation" || curationResultShown());
  card.hidden =
    !job ||
    summarizedByResult ||
    (onResult ? job.kind !== "curation" : pageOf(job) !== state.view);
  lock();
  renderActivity();
  if (!job) return;
  card.className = `card job-card ${snapshot.running ? "running" : job.status}`;
  $("analysis-job-icon").replaceChildren(
    snapshot.running
      ? el("span", undefined, "spinner")
      : ui.icon(
          job.status === "succeeded"
            ? "check-circle"
            : job.status === "failed"
              ? "alert"
              : "stop",
        ),
  );
  $("analysis-job-title").textContent =
    `${jobNames[job.kind]} · ${jobLabels[job.status]}`;
  $("analysis-job-message").textContent = snapshot.running
    ? [job.stage, job.message].filter(Boolean).join(" · ")
    : job.error ||
      {
        succeeded: "工作已完成。",
        cancelled: "工作已停止；先前的可用結果仍保留。",
        failed: "工作未完成。",
      }[job.status];
  const progressText = job.total ? `${job.completed} / ${job.total} · ` : "";
  $("analysis-job-time").textContent =
    `${progressText}${snapshot.running ? "已經過" : "耗時"} ${ui.formatDuration(job.elapsed_seconds)}`;
  $("analysis-job-output").hidden = !job.output_dir;
  $("analysis-job-output").textContent = job.output_dir || "";
  if (job.total > 0) {
    $("analysis-progress").max = job.total;
    $("analysis-progress").value = job.completed;
  } else $("analysis-progress").removeAttribute("value");
  $("analysis-stop").hidden = !snapshot.running;
  $("analysis-stop").disabled = !snapshot.canCancel;
  const filesOpen = $("analysis-job-outputs").querySelector("details")?.open;
  $("analysis-job-outputs").replaceChildren();
  const artifacts = (job.available_outputs || []).flatMap((item) =>
    item.artifact ? [item.artifact] : [],
  );
  if (artifacts.length) {
    const files = artifactDetails(artifacts, job.output_dir);
    files.open = Boolean(filesOpen);
    $("analysis-job-outputs").append(files);
  }
}

function completeJob(snapshot, job) {
  const card = $("analysis-job");
  const samplesApplied =
    job.kind === "samples" &&
    sampleMetadata.completeJob(job, snapshot.results.samples);
  if (job.status === "succeeded") {
    if (job.kind === "samples") {
      const result = snapshot.results.samples;
      const page = result.page || "untargeted";
      if (samplesApplied) {
        if (page === "untargeted") dirty("untargeted");
        done(`已讀取 ${result.samples.length} 個樣本。`);
      }
    } else if (job.kind === "curation") {
      // A visible curation result card is the notice.
      renderResults();
      if (!curationResultShown())
        done(`矩陣整理完成：${snapshot.results.curation.output_dir}`);
    }
    else if (job.kind === "inspector") {
      done(`Inspector 匯出完成：${snapshot.results.inspector.output}`);
      const artifact = snapshot.results.inspector.artifacts?.[0];
      if (artifact) showArtifact(artifact);
    } else {
      dirty(job.kind, false);
      done(`${jobNames[job.kind]}完成，結果已列在下方。`);
      const result = snapshot.results[job.kind];
      const source =
        result.excel_path ||
        result.evidence?.path ||
        result.evidence?.report_path;
      if (
        source &&
        (job.kind === "untargeted" ||
          result.artifacts?.some((item) => item.path === source))
      )
        openReview(source, result.review_paths);
    }
  } else if (card.hidden)
    status(
      `${jobNames[job.kind]}${jobLabels[job.status]}：${job.error || "先前的結果仍可檢閱。"}`,
      job.status === "failed",
    );
  renderResults();
  renderJob();
}
function confirmCancel(job) {
  return ui.confirm({
    title: `停止${jobNames[job.kind]}？`,
    message:
      "已發布的結果會保留；正在執行的步驟會先完成清理再停止。先前的可用結果不受影響。",
    accept: "停止工作",
    danger: true,
  });
}

export function bindJobs(initial) {
  analysis = createAnalysisSession(initial, {
    request: api,
    prepareStart,
    acceptedStart,
    confirmCancel,
  });
  sampleMetadata.restore(analysis.snapshot);
  $("analysis-stop").onclick = () => act(() => analysis.cancel());
  let wasBusy = false;
  // Bootstrap attaches observation once the initial views are ready. Merely
  // restoring a job must not race the initial Review/ISTD restoration.
  return () =>
    analysis.subscribe((snapshot, completed) => {
      if (wasBusy !== snapshot.busy) {
        wasBusy = snapshot.busy;
        document.dispatchEvent(new CustomEvent("xic:analysis-busy"));
      }
      const offline = snapshot.connection === "offline";
      document.body.classList.toggle("offline", offline);
      $("offline-banner").hidden = !offline;
      renderJob();
      if (completed) completeJob(snapshot, completed);
    });
}
