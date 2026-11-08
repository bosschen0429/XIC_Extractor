// Completed-result summaries, artifact viewing and Inspector export selection.
import { openCuration } from "./analysis_curation.js";
import { analysis, resultIds } from "./analysis_jobs.js";
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
export function artifactDetails(artifacts, folder = "") {
  const details = el("details", undefined, "artifact-details setup-tools");
  details.append(
    el("summary", "其他檔案"),
  );
  if (folder) {
    details.append(el("p", folder, "subtle mono"));
    details.append(
      button(
        "開啟輸出資料夾",
        () => api("/api/open-local", { path: folder }),
        "",
        "folder",
      ),
    );
  }
  const root = folder.split(/[\\/]/).at(-1);
  const groups = new Map();
  for (const artifact of artifacts) {
    const parent = artifact.path.split(/[\\/]/).at(-2);
    const label = parent && parent !== root ? parent : "整批分析紀錄";
    if (!groups.has(label)) {
      const list = el("div", undefined, "artifact-list");
      details.append(el("h3", label), list);
      groups.set(label, list);
    }
    groups.get(label).append(artifactLink(artifact));
  }
  return details;
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
  head.append(titleBlock);
  container.replaceChildren(head);
  const primaryPath = result.excel_path || result.matrix_tsv;
  const artifacts = result.artifacts || [];
  const downloads = el("div", undefined, "artifact-list");
  const primary = artifacts.find((artifact) => artifact.path === primaryPath);
  if (primary)
    downloads.append(
      artifactLink(primary, {
        targeted: "下載結果 Excel",
        untargeted: "下載 Alignment 矩陣",
        curation: "下載整理後矩陣",
      }[kind]),
    );
  const details = artifactDetails(
    artifacts.filter((artifact) => artifact !== primary),
    folder,
  );
  return { container, downloads, details };
}
export function renderResults() {
  const targeted = analysis.snapshot.results.targeted;
  if (targeted) {
    const { container, downloads, details } = resultBase("targeted", targeted);
    const metrics = el("div", undefined, "result-metrics");
    metric(metrics, targeted.total_files, "RAW 檔案");
    metric(metrics, targeted.targets?.length, "目標");
    container.append(metrics);
    if (targeted.excel_path)
      container.append(
        button(
          "檢閱結果",
          () => openReview(targeted.excel_path, targeted.review_paths),
          "primary",
          "review",
        ),
      );
    container.append(downloads);
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
    container.append(details);
  }
  const result = analysis.snapshot.results.untargeted;
  if (result) {
    const { container, downloads, details } = resultBase("untargeted", result);
    const metrics = el("div", undefined, "result-metrics");
    metric(metrics, result.overview?.matrix_count, "矩陣群組");
    metric(metrics, result.overview?.complete_count, "全部樣本都有值");
    container.append(metrics);
    const actions = el("div", undefined, "button-row");
    const evidence = result.evidence?.path || result.evidence?.report_path;
    if (evidence) {
      actions.append(
        button(
          "檢閱結果",
          () => openReview(evidence, result.review_paths),
          "primary",
          "review",
        ),
      );
      details.append(
        button(
          "自訂 Inspector 匯出",
          () => openInspectorExport(evidence),
          "",
          "download",
        ),
      );
    }
    if (result.matrix_tsv && result.alignment_outputs?.review_tsv)
      actions.append(
        button(
          "整理矩陣",
          () => openCuration(result.output_dir, result.samples),
          "",
          "sliders",
        ),
      );
    container.append(actions, details);
    // The matrix download sits with the result actions, not on its own row.
    actions.append(...downloads.childNodes);
  }
  const curated = analysis.snapshot.results.curation;
  if (curated) {
    const { container, downloads, details } = resultBase("curation", curated);
    const metrics = el("div", undefined, "result-metrics");
    metric(metrics, curated.kept_feature_count, "保留 feature");
    metric(metrics, curated.feature_count, "原始 feature");
    container.append(metrics, downloads, details);
    details.append(el("p", `來源：${curated.source_dir}`, "subtle mono"));
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
      await analysis.start("inspector", {
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
