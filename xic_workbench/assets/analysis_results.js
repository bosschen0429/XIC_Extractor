// Completed-result summaries, artifact viewing and Inspector export selection.
import { openCuration } from "./analysis_curation.js";
import { resultIds, startJob } from "./analysis_jobs.js";
import { state } from "./analysis_state.js";
import { $, act, api, button, el, ui } from "./analysis_ui.js";

let exportSource;
export function artifactLink(artifact, label = artifact.name) {
  const link = el("a");
  const html = artifact.name.toLowerCase().endsWith(".html");
  link.append(ui.icon(html ? "external" : "file"), label);
  link.href = artifact.url;
  link.download = artifact.name;
  link.title = html ? "在工作區開啟報告" : `下載 ${artifact.name}`;
  if (html) {
    link.removeAttribute("download");
    link.addEventListener("click", (event) => {
      event.preventDefault();
      showArtifact(artifact);
    });
  }
  return link;
}
export function showArtifact(artifact) {
  $("artifact-title").textContent = artifact.name;
  $("artifact-frame").src = artifact.url;
  $("artifact-open").href = artifact.url;
  $("artifact-dialog").showModal();
}
function metric(container, value, label) {
  const cell = el("div");
  cell.append(el("strong", value ?? "—"), el("span", label));
  container.append(cell);
}
function resultBase(kind, result) {
  const container = $(resultIds[kind]);
  container.hidden = false;
  const head = el("div", undefined, "result-head");
  const title = el("h2");
  title.append(
    ui.icon("check-circle"),
    {
      targeted: "Targeted 分析結果",
      untargeted: "Untargeted 分析結果",
      curation: "矩陣整理結果",
    }[kind],
  );
  const titleBlock = el("div");
  titleBlock.append(title);
  const folder =
    result.output_dir || result.excel_path?.replace(/[\\/][^\\/]+$/, "");
  if (folder) titleBlock.append(el("p", folder, "subtle mono"));
  head.append(titleBlock);
  if (folder)
    head.append(
      button(
        "開啟輸出資料夾",
        () => api("/api/open-local", { path: folder }),
        "",
        "folder",
      ),
    );
  container.replaceChildren(head);
  const artifacts = el("div", undefined, "artifact-list");
  for (const artifact of result.artifacts || []) {
    const label =
      artifact.path === result.excel_path
        ? artifact.name
        : artifact.path === result.matrix_tsv
          ? "下載矩陣 TSV"
          : artifact.name;
    artifacts.append(artifactLink(artifact, label));
  }
  return { container, artifacts };
}
export function renderResults() {
  const targeted = state.analysis.results.targeted;
  if (targeted) {
    const { container, artifacts } = resultBase("targeted", targeted);
    const metrics = el("div", undefined, "result-metrics");
    metric(metrics, targeted.total_files, "RAW 檔案");
    metric(metrics, targeted.targets?.length, "目標");
    container.append(metrics);
    if (targeted.excel_path)
      container.append(
        button(
          "在結果檢閱開啟",
          () => openReview(targeted.excel_path, targeted.review_paths),
          "primary",
          "review",
        ),
      );
    container.append(artifacts);
    const warnings = el("div");
    for (const warning of targeted.istd_warnings || []) {
      const line = el("p", undefined, "result-warning");
      line.append(
        ui.icon("alert"),
        `ISTD ${warning.label}：${warning.detected} / ${warning.total} 個樣本通過確認；未通過確認不代表沒有訊號。請到結果檢閱查看原因。`,
      );
      warnings.append(line);
    }
    if (warnings.childElementCount) container.append(warnings);
    container.append(
      el(
        "p",
        "樣本量值與需檢閱項目集中在結果檢閱；XLSX 保留同一份結果。",
        "subtle",
      ),
    );
  }
  const result = state.analysis.results.untargeted;
  if (result) {
    const { container, artifacts } = resultBase("untargeted", result);
    const metrics = el("div", undefined, "result-metrics");
    metric(metrics, result.sample_count, "樣本");
    metric(metrics, result.overview?.matrix_count, "最終矩陣群組");
    metric(metrics, result.overview?.complete_count, "全部樣本有量值的群組");
    metric(metrics, result.overview?.unresolved_count, "含未解決定量的群組");
    container.append(metrics);
    const actions = el("div", undefined, "button-row");
    const evidence = result.evidence?.path || result.evidence?.report_path;
    if (evidence) {
      actions.append(
        button(
          "在結果檢閱開啟",
          () => openReview(evidence, result.review_paths),
          "",
          "review",
        ),
      );
      actions.append(
        button(
          "自訂 Inspector 匯出",
          () => openInspectorExport(evidence),
          "",
          "download",
        ),
      );
    }
    if (result.matrix_tsv && result.alignment_outputs?.review_tsv)
      actions.prepend(
        button(
          "選用：整理矩陣",
          () => openCuration(result.output_dir, result.samples),
          "",
          "sliders",
        ),
      );
    container.append(actions, artifacts);
  }
  const curated = state.analysis.results.curation;
  if (curated) {
    const { container, artifacts } = resultBase("curation", curated);
    const metrics = el("div", undefined, "result-metrics");
    metric(metrics, curated.kept_feature_count, "保留 feature");
    metric(metrics, curated.feature_count, "原始 feature");
    container.append(metrics, artifacts);
    container.append(el("p", `來源：${curated.source_dir}`, "subtle mono"));
  }
}
export function openReview(source, reviewPaths) {
  document.dispatchEvent(
    new CustomEvent("xic:open-review", { detail: { source, reviewPaths } }),
  );
}
// Matrix curation
async function openInspectorExport(source) {
  exportSource = source;
  $("export-output").value = source.replace(
    /[\\/][^\\/]+$/,
    "/xic_inspector.html",
  );
  const summary = await api("/api/evidence-summary", { source });
  $("export-samples").replaceChildren();
  for (const sample of summary.samples) {
    const label = el("label");
    const input = el("input");
    input.type = "checkbox";
    input.value = sample.sample_key;
    label.append(
      input,
      document.createTextNode(
        sample.sample_name || sample.sample_id || sample.sample_key,
      ),
    );
    $("export-samples").append(label);
  }
  $("inspector-export-dialog").showModal();
}

export function bindResults() {
  $("export-strategy").onchange = () =>
    ($("export-query").hidden = $("export-strategy").value !== "query");
  $("inspector-export-form").onsubmit = (event) => {
    event.preventDefault();
    act(async () => {
      const selection = {
        strategy: $("export-strategy").value,
        limit: Number($("export-limit").value),
        seed: Number($("export-seed").value),
        sample_ids: [
          ...$("export-samples").querySelectorAll("input:checked"),
        ].map((node) => node.value),
      };
      if (selection.strategy === "query") {
        for (const key of ["mz_min", "mz_max", "rt_min", "rt_max"]) {
          const value = $(`export-${key.replace("_", "-")}`).value;
          if (value) selection[key] = Number(value);
        }
        selection.offset = Number($("export-offset").value);
        selection.candidate_ids = $("export-candidates")
          .value.split(/\n/)
          .map((v) => v.trim())
          .filter(Boolean);
        selection.group_ids = $("export-groups")
          .value.split(/\n/)
          .map((v) => v.trim())
          .filter(Boolean);
      }
      await startJob("inspector", {
        source: exportSource,
        output: $("export-output").value,
        selection,
      });
      $("inspector-export-dialog").close();
    });
  };
  $("artifact-dialog").addEventListener(
    "close",
    () => ($("artifact-frame").src = "about:blank"),
  );
}
