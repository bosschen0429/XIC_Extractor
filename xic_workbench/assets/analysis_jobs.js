// Job submission/cancellation, progress and completion feedback.
import {
  artifactLink,
  openReview,
  renderResults,
  showArtifact,
} from "./analysis_results.js";
import { applySampleResult, renderSampleSummary } from "./analysis_samples.js";
import {
  dirty,
  selectView,
  setMethodState,
  showingResult,
} from "./analysis_setup.js";
import {
  isRunning,
  markJobFinished,
  markJobSeen,
  resetSeenJob,
  sampleAction,
  setAnalysisState,
  setSampleJobAction,
  state,
  updateConfig,
} from "./analysis_state.js";
import {
  $,
  act,
  api,
  button,
  done,
  el,
  isBusy,
  lock,
  status,
  ui,
} from "./analysis_ui.js";

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
export async function startJob(kind, values) {
  if (isBusy()) throw new Error("請等待目前工作完成或先停止。");
  if (kind === "targeted" && !values.targets.length)
    throw new Error("目標清單是空的：按「檢視與編輯」新增，或匯入目標清單。");
  const action = kind === "samples" ? sampleAction(values.page) : null;
  setAnalysisState(await api("/api/analysis/start", { kind, values }));
  if (kind === "untargeted") {
    updateConfig({ untargeted: values });
    dirty("untargeted", false);
  }
  if (action) setSampleJobAction(state.analysis.job.job_id, action);
  if (kind === "targeted") {
    updateConfig({ settings: { ...values.settings } });
    updateConfig({ targeted_method: await api("/api/targeted/method", {}) });
    setMethodState(state.config.targeted_method);
    dirty("targeted", false);
  }
  resetSeenJob();
  renderJob();
  status("工作已開始。關閉分頁不會停止分析。");
}
function renderActivity() {
  const job = state.analysis.job;
  const pill = $("activity");
  const page = pageOf(job);
  const recentlyFinished = state.seenAt && Date.now() - state.seenAt < 20000;
  const visible =
    job && (isRunning() || (state.view !== page && recentlyFinished));
  pill.hidden = !visible;
  if (!visible) return;
  const percent =
    job.total > 0 ? ` ${Math.round((job.completed / job.total) * 100)}%` : "";
  pill.className = `activity-pill${job.status === "succeeded" ? " done" : job.status === "failed" ? " failed" : ""}`;
  pill.replaceChildren(
    isRunning()
      ? el("span", undefined, "spinner")
      : ui.icon(job.status === "succeeded" ? "check-circle" : "alert"),
    `${jobNames[job.kind]} · ${jobLabels[job.status]}${isRunning() ? percent : ""}`,
  );
  pill.onclick = () => selectView(page);
}
export function renderJob() {
  const job = state.analysis.job;
  const sampleSummary =
    job?.kind === "targeted"
      ? job.sample_summary
      : state.analysis.results.targeted?.sample_summary;
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
  card.hidden =
    !job || (onResult ? job.kind !== "curation" : pageOf(job) !== state.view);
  lock();
  renderActivity();
  if (!job) return;
  card.className = `card job-card ${isRunning() ? "running" : job.status}`;
  $("analysis-job-icon").replaceChildren(
    isRunning()
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
  $("analysis-job-message").textContent = isRunning()
    ? [job.stage, job.message].filter(Boolean).join(" · ")
    : job.error ||
      {
        succeeded: "工作已完成。",
        cancelled: "工作已停止；先前的可用結果仍保留。",
        failed: "工作未完成。",
      }[job.status];
  const progressText = job.total ? `${job.completed} / ${job.total} · ` : "";
  $("analysis-job-time").textContent =
    `${progressText}${isRunning() ? "已經過" : "耗時"} ${ui.formatDuration(job.elapsed_seconds)}`;
  const analysisDone = job.status === "succeeded" && job.kind in resultIds;
  $("analysis-job-output").hidden = !job.output_dir || analysisDone;
  $("analysis-job-output").textContent = job.output_dir || "";
  if (job.total > 0) {
    $("analysis-progress").max = job.total;
    $("analysis-progress").value = job.completed;
  } else $("analysis-progress").removeAttribute("value");
  $("analysis-stop").hidden = !isRunning();
  $("analysis-stop").disabled = job.status === "cancelling" || !job.cancellable;
  $("analysis-job-outputs").replaceChildren();
  if (analysisDone)
    $("analysis-job-outputs").append(
      button(
        "查看結果",
        () =>
          $(resultIds[job.kind]).scrollIntoView({
            behavior: "smooth",
            block: "start",
          }),
        "",
        "chevron-right",
      ),
    );
  else
    for (const item of job.available_outputs || []) {
      if (item.artifact)
        $("analysis-job-outputs").append(artifactLink(item.artifact));
    }
  if (isRunning() || state.seenJob === job.job_id) return;
  markJobSeen(job.job_id);
  if (!state.booted) {
    renderResults();
    return;
  }
  markJobFinished();
  if (job.status === "succeeded") {
    if (job.kind === "samples") {
      const result = state.analysis.results.samples;
      const page = result.page || "untargeted";
      const action =
        state.sampleJobAction?.jobId === job.job_id
          ? state.sampleJobAction.action
          : null;
      if (applySampleResult(page, result, action)) {
        if (page === "untargeted") dirty("untargeted");
        done(`已讀取 ${result.samples.length} 個樣本。`);
      }
    } else if (job.kind === "curation")
      done(`矩陣整理完成：${state.analysis.results.curation.output_dir}`);
    else if (job.kind === "inspector") {
      done(`Inspector 匯出完成：${state.analysis.results.inspector.output}`);
      const artifact = state.analysis.results.inspector.artifacts?.[0];
      if (artifact) showArtifact(artifact);
    } else {
      dirty(job.kind, false);
      done(`${jobNames[job.kind]}完成，結果已列在下方。`);
      const result = state.analysis.results[job.kind];
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
}
export async function stopJob() {
  if (!isRunning() || !state.analysis.job.cancellable) return;
  const accepted = await ui.confirm({
    title: `停止${jobNames[state.analysis.job.kind]}？`,
    message:
      "已發布的結果會保留；正在執行的步驟會先完成清理再停止。先前的可用結果不受影響。",
    accept: "停止工作",
    danger: true,
  });
  if (!accepted) return;
  setAnalysisState(
    await api("/api/analysis/cancel", {
      job_id: state.analysis.job.job_id,
    }),
  );
  renderJob();
}

export function bindJobs() {
  $("analysis-stop").onclick = () => act(stopJob);
}
