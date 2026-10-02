"use strict";
(async () => {
  const $ = (id) => document.getElementById(id);
  const state = {
    config: null,
    targets: [],
    samples: [],
    method: null,
    draft: null,
    analysis: { job: null, results: {} },
    reviewBusy: false,
    view: "untargeted",
    dirty: { targeted: false, untargeted: false },
    seenJob: null,
  };
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
  const pathFields = [
    ["raw_dir", "RAW 資料夾", "folder"],
    ["raw_file", "單一 RAW", "raw"],
    ["discovery_batch_index", "Discovery batch index", "csv"],
    ["output_dir", "輸出資料夾", "folder"],
    ["dll_dir", "RawFileReader DLL", "folder"],
  ];
  async function api(path, values) {
    const response = await fetch(path, {
      method: values === undefined ? "GET" : "POST",
      headers: {
        "X-XIC-Client": "workbench",
        ...(values === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(values === undefined ? {} : { body: JSON.stringify(values) }),
    });
    const data = await response.json();
    if (!response.ok)
      throw new Error(data.error || `要求失敗（${response.status}）`);
    return data;
  }
  function status(text, error = false) {
    $("analysis-status").textContent = text;
    $("analysis-status").className = `status${error ? " error" : ""}`;
  }
  function syncReportTheme() {
    for (const frame of document.querySelectorAll("iframe")) {
      if (frame.contentDocument?.documentElement)
        frame.contentDocument.documentElement.dataset.theme =
          document.body.dataset.theme || "light";
    }
  }
  async function act(fn) {
    try {
      return await fn();
    } catch (error) {
      status(error.message, true);
      return null;
    }
  }
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }
  function button(label, fn, cls = "") {
    const node = el("button", label, cls);
    node.type = "button";
    node.addEventListener("click", () => act(fn));
    return node;
  }
  function isBusy() {
    return (
      state.reviewBusy ||
      ["running", "cancelling"].includes(state.analysis.job?.status)
    );
  }
  function lock() {
    document
      .querySelectorAll("[data-analysis-idle]")
      .forEach((node) => (node.disabled = isBusy()));
    document.dispatchEvent(
      new CustomEvent("xic:analysis-busy", {
        detail: ["running", "cancelling"].includes(state.analysis.job?.status),
      }),
    );
  }
  let methodTimer;
  function dirty(kind, value = true) {
    state.dirty[kind] = value;
    $(`${kind === "targeted" ? "target" : "untargeted"}-dirty`).textContent =
      value ? "有尚未儲存的變更；下方結果屬於上次執行。" : "設定已儲存";
    if (kind === "targeted") {
      clearTimeout(methodTimer);
      methodTimer = setTimeout(
        () =>
          act(async () => {
            const summary = await api("/api/targeted/method", {
              settings: settingsValues(),
            });
            $("target-method-state").textContent = summary.label;
          }),
        250,
      );
    }
  }
  function selectView(view, persist = true) {
    state.view = view;
    for (const name of ["targeted", "untargeted", "review"])
      $(`workspace-${name}`).hidden = name !== view;
    document
      .querySelectorAll("[data-view]")
      .forEach((node) =>
        node.setAttribute(
          "aria-current",
          node.dataset.view === view ? "page" : "false",
        ),
      );
    if (persist) act(() => api("/api/preferences", { view }));
  }
  function field(spec, value, prefix) {
    const kind = spec.kind === "choice" ? "select" : spec.kind;
    const label = el(
      "label",
      undefined,
      `field${kind === "checkbox" ? " checkbox" : ""}`,
    );
    const title = el("span", spec.label);
    const input = document.createElement(
      kind === "select" ? "select" : "input",
    );
    input.id = `${prefix}${spec.key}`;
    input.dataset.analysisIdle = "";
    input.dataset.key = spec.key;
    if (kind === "select")
      for (const option of spec.options || spec.choices || []) {
        const node = el("option", option);
        node.value = option;
        input.append(node);
      }
    else
      input.type =
        kind === "checkbox"
          ? "checkbox"
          : kind === "number"
            ? "number"
            : "text";
    if (kind === "number") {
      input.step = "any";
      if (spec.min !== undefined || spec.minimum !== undefined)
        input.min = spec.min ?? spec.minimum;
      if (spec.max !== undefined || spec.maximum !== undefined)
        input.max = spec.max ?? spec.maximum;
    }
    if (kind === "checkbox")
      input.checked = value === true || String(value).toLowerCase() === "true";
    else input.value = value ?? "";
    label.append(title, input);
    if (["folder", "file"].includes(kind)) {
      const browse = el("button", "瀏覽");
      browse.type = "button";
      browse.dataset.browse = input.id;
      browse.dataset.kind = kind;
      browse.dataset.analysisIdle = "";
      label.append(browse);
    }
    return label;
  }
  function pathField(container, key, label, kind, value, prefix = "u-") {
    const wrap = el("div", undefined, "path-line");
    wrap.id = `${prefix}${key}-row`;
    const title = el("label", label);
    title.htmlFor = `${prefix}${key}`;
    const input = el("input");
    input.id = title.htmlFor;
    input.value = value || "";
    input.dataset.analysisIdle = "";
    const browse = el("button", "瀏覽");
    browse.type = "button";
    browse.dataset.browse = input.id;
    browse.dataset.kind = kind;
    browse.dataset.analysisIdle = "";
    wrap.append(title, input, browse);
    container.append(wrap);
    return input;
  }
  function settingsValues() {
    const values = { ...state.config.settings };
    for (const spec of state.config.targeted_form.settings) {
      const input = $(`t-${spec.key}`);
      values[spec.key] =
        spec.kind === "checkbox" ? String(input.checked) : input.value;
    }
    return values;
  }
  function renderSettings(values) {
    const container = $("targeted-settings");
    container.replaceChildren();
    const summary = el("div", undefined, "section-heading");
    summary.append(
      el("p", "平台會提供方法預設；需要調整時再展開進階功能。", "subtle"),
    );
    summary.append(el("span", "", "subtle"));
    summary.lastChild.id = "target-method-state";
    const advanced = el("details");
    advanced.id = "targeted-advanced";
    advanced.append(el("summary", "進階功能（選用）"));
    const environment = el("details");
    environment.append(el("summary", "本機環境 · RawFileReader DLL"));
    environment.open = !values.dll_dir || values.dll_dir.startsWith("<");
    const groups = new Map();
    for (const spec of state.config.targeted_form.settings) {
      if (spec.key === "data_dir") {
        container.append(field(spec, values[spec.key], "t-"));
        continue;
      }
      if (spec.key === "dll_dir") {
        environment.append(field(spec, values[spec.key], "t-"));
        continue;
      }
      if (!groups.has(spec.group)) {
        const section = el("section");
        section.append(el("h3", spec.group));
        const grid = el("div", undefined, "settings-grid");
        section.append(grid);
        advanced.append(section);
        groups.set(spec.group, grid);
      }
      groups.get(spec.group).append(field(spec, values[spec.key], "t-"));
    }
    container.append(summary, advanced, environment);
    $("target-method-state").textContent =
      state.config.targeted_method?.label || "標準方法";
  }
  async function saveTargeted() {
    const settings = settingsValues();
    await api("/api/targeted/save", { settings, targets: state.targets });
    state.config.settings = settings;
    dirty("targeted", false);
    status("Targeted 設定與目標已儲存。");
    if (settings.dll_dir)
      await api("/api/preferences", { dll_dir: settings.dll_dir });
  }
  function targetCount() {
    $("target-count").textContent =
      `${state.targets.length} 個目標 · ${state.targets.filter((row) => String(row.is_istd).toLowerCase() === "true").length} 個 ISTD`;
  }
  const targetColumns = [
    ["label", "名稱"],
    ["is_istd", "ISTD"],
    ["istd_pair", "ISTD 配對"],
    ["mz", "m/z"],
    ["rt_min", "RT min"],
    ["rt_max", "RT max"],
    ["ppm_tol", "ppm"],
    ["neutral_loss_da", "Neutral loss (Da)"],
  ];
  function renderTargets() {
    const head = el("tr");
    for (const [, label] of targetColumns) head.append(el("th", label));
    head.append(el("th", ""));
    $("target-head").replaceChildren(head);
    const body = $("target-rows");
    body.replaceChildren();
    state.targets.forEach((row, index) => {
      const tr = el("tr");
      for (const [key] of targetColumns) {
        const td = el("td");
        const input = el("input");
        input.setAttribute("aria-label", `${key} 第 ${index + 1} 列`);
        if (key === "is_istd") {
          input.type = "checkbox";
          input.checked = String(row[key]).toLowerCase() === "true";
        } else {
          input.value = row[key] || "";
          if (
            ["mz", "rt_min", "rt_max", "ppm_tol", "neutral_loss_da"].includes(
              key,
            )
          )
            input.inputMode = "decimal";
        }
        input.addEventListener("input", () => {
          row[key] = key === "is_istd" ? String(input.checked) : input.value;
          dirty("targeted");
          targetCount();
        });
        td.append(input);
        tr.append(td);
      }
      const td = el("td");
      td.append(
        button("刪除", () => {
          state.targets.splice(index, 1);
          dirty("targeted");
          renderTargets();
        }),
      );
      tr.append(td);
      body.append(tr);
    });
    targetCount();
  }
  function modeFields() {
    const mode = $("u-mode").value;
    $("u-raw_dir-row").hidden = mode === "discovery_only";
    $("u-raw_file-row").hidden = mode !== "discovery_only";
    $("u-discovery_batch_index-row").hidden = mode !== "align_only";
    $("sample-section").hidden = mode === "discovery_only";
    $("istd-path-field").parentElement.hidden = mode === "discovery_only";
  }
  function untargetedValues() {
    const values = {
      ...state.config.untargeted,
      mode: $("u-mode").value,
      preset: $("u-preset").value,
      sequence_source: $("sequence-source").value,
      overrides: {},
      custom_roles: { ...state.config.untargeted.custom_roles },
    };
    for (const [key] of pathFields) values[key] = $(`u-${key}`).value;
    values.targets_csv = $("u-targets_csv").value;
    for (const control of state.method?.tuning_controls || []) {
      const input = $(`tune-${control.key}`);
      values.overrides[control.key] =
        input.type === "number" ? Number(input.value) : input.value;
    }
    for (const sample of state.samples)
      values.custom_roles[sample.raw_stem] = sample.sample_role;
    values.samples = state.samples.length ? state.samples : null;
    if (state.method) values.preset_snapshot = state.method.draft;
    return values;
  }
  async function saveUntargeted() {
    const values = untargetedValues();
    await api("/api/untargeted/save", values);
    state.config.untargeted = values;
    dirty("untargeted", false);
    status("Untargeted 批次設定已儲存。");
    if (values.dll_dir)
      await api("/api/preferences", { dll_dir: values.dll_dir });
  }
  async function loadMethod(preset, overrides = {}) {
    const data = await api("/api/method", { preset });
    state.method = data;
    if (![...$("u-preset").options].some((option) => option.value === preset)) {
      const option = el("option", data.preset.name);
      option.value = preset;
      $("u-preset").append(option);
    }
    $("u-preset").value = preset;
    $("method-tuning").replaceChildren(
      ...data.tuning_controls.map((control) =>
        field(
          control,
          overrides[control.key] ?? data.effective_values[control.key],
          "tune-",
        ),
      ),
    );
    updateMethodPreview();
  }
  function updateMethodPreview() {
    if (!state.method) return;
    const preview = state.method.preview;
    $("method-preview").textContent =
      `${preview.tags.map((tag) => `${tag.name} · ${tag.neutral_loss_da} Da`).join("，")} · ${preview.combine_mode} · RT ${$("tune-rt_min").value}–${$("tune-rt_max").value} min`;
  }
  function renderMethodEditor() {
    $("method-name").value = state.draft.name;
    $("method-description").value = state.draft.description || "";
    $("method-combine").value = state.draft.combine_mode;
    $("method-error").hidden = true;
    const body = $("method-tags");
    body.replaceChildren();
    state.draft.tags.forEach((tag, index) => {
      const tr = el("tr");
      for (const key of ["enabled", "name", "neutral_loss_da"]) {
        const input = el("input");
        input.type =
          key === "enabled"
            ? "checkbox"
            : key === "neutral_loss_da"
              ? "number"
              : "text";
        if (key === "enabled") input.checked = tag.enabled !== false;
        else {
          input.value = tag[key] ?? "";
          if (input.type === "number") input.step = "any";
        }
        input.addEventListener(
          "input",
          () =>
            (tag[key] =
              key === "enabled"
                ? input.checked
                : key === "neutral_loss_da"
                  ? Number(input.value)
                  : input.value),
        );
        const td = el("td");
        td.append(input);
        tr.append(td);
      }
      const td = el("td");
      td.append(
        button("移除", () => {
          state.draft.tags.splice(index, 1);
          renderMethodEditor();
        }),
      );
      tr.append(td);
      body.append(tr);
    });
  }
  async function saveMethod(apply) {
    state.draft.name = $("method-name").value;
    state.draft.description = $("method-description").value;
    state.draft.combine_mode = $("method-combine").value;
    const path = apply
      ? null
      : await requestPath(
          "另存方法 preset",
          `${state.config.config_dir}/my-method.toml`,
        );
    if (!apply && !path) return;
    try {
      const result = await api("/api/method/save", {
        draft: state.draft,
        path,
        apply,
      });
      if (apply) {
        await loadMethod(result.path);
        dirty("untargeted");
        $("method-editor").close();
      } else status(`方法已儲存：${result.path}。目前執行方法維持原設定。`);
    } catch (error) {
      $("method-error").hidden = false;
      $("method-error").textContent = error.message;
    }
  }
  function roleOptions(select, current) {
    for (const role of roles) {
      const option = el("option", roleLabels[role]);
      option.value = role;
      select.append(option);
    }
    select.value = current;
  }
  function renderSamples() {
    $("samples-count").textContent = `${state.samples.length} 個樣本`;
    const filter = $("sample-filter").value.trim().toLowerCase();
    const body = $("sample-rows");
    body.replaceChildren();
    for (const row of state.samples) {
      if (
        filter &&
        ![row.raw_stem, row.sample_name, row.batch_id]
          .join(" ")
          .toLowerCase()
          .includes(filter)
      )
        continue;
      const tr = el("tr");
      const selected = el("input");
      selected.type = "checkbox";
      selected.dataset.sample = row.raw_stem;
      selected.setAttribute("aria-label", `選取 ${row.raw_stem}`);
      const check = el("td");
      check.append(selected);
      tr.append(check);
      for (const key of [
        "raw_stem",
        "batch_id",
        "injection_order",
        "sample_name",
      ])
        tr.append(el("td", row[key] ?? "—"));
      const select = el("select");
      select.setAttribute("aria-label", `${row.raw_stem} 角色`);
      roleOptions(select, row.sample_role);
      select.addEventListener("change", () => {
        row.sample_role = select.value;
        dirty("untargeted");
      });
      const td = el("td");
      td.append(select);
      tr.append(td);
      body.append(tr);
    }
  }
  async function scanSamples() {
    const values = untargetedValues();
    await startJob("samples", {
      raw_dir: values.raw_dir,
      dll_dir: values.dll_dir,
      sequence_source: values.sequence_source,
      custom_roles: values.custom_roles,
    });
  }
  async function startJob(kind, values) {
    if (isBusy()) throw new Error("請等待目前工作完成或先停止。");
    if (["targeted", "untargeted"].includes(kind)) {
      if (kind === "targeted") await saveTargeted();
      else await saveUntargeted();
    }
    state.analysis = await api("/api/analysis/start", { kind, values });
    state.seenJob = null;
    renderJob();
    status("工作已開始，關閉分頁不會停止分析。");
  }
  function renderJob() {
    const job = state.analysis.job;
    $("analysis-job").hidden = !job;
    lock();
    if (!job) return;
    const labels = {
      running: "執行中",
      cancelling: "正在停止",
      succeeded: "已完成",
      failed: "執行失敗",
      cancelled: "已停止",
    };
    const names = {
      targeted: "Targeted",
      untargeted: "Untargeted",
      samples: "樣本資訊",
      quality: "Feature Quality",
      inspector: "Inspector 匯出",
    };
    $("analysis-job-title").textContent =
      `${names[job.kind]} · ${labels[job.status]}`;
    $("analysis-job-message").textContent =
      job.error || `${job.stage} · ${job.message || ""}`;
    $("analysis-job-time").textContent =
      `${job.completed} / ${job.total || "—"} · 已經過 ${Math.floor(job.elapsed_seconds || 0)} 秒${job.output_dir ? ` · ${job.output_dir}` : ""}`;
    if (job.total > 0) {
      $("analysis-progress").max = job.total;
      $("analysis-progress").value = job.completed;
    } else $("analysis-progress").removeAttribute("value");
    $("analysis-stop").hidden = !["running", "cancelling"].includes(job.status);
    $("analysis-stop").disabled =
      job.status === "cancelling" || !job.cancellable;
    $("analysis-job-outputs").replaceChildren();
    for (const item of job.available_outputs || []) {
      if (item.artifact)
        $("analysis-job-outputs").append(artifactLink(item.artifact));
    }
    if (
      ["running", "cancelling"].includes(job.status) ||
      state.seenJob === job.job_id
    )
      return;
    state.seenJob = job.job_id;
    if (job.status === "succeeded") {
      if (job.kind === "samples") {
        const result = state.analysis.results.samples;
        state.samples = result.samples;
        renderSamples();
        $("samples-summary").textContent =
          `RAW ${result.summary.raw_count} 個 · 上機資訊配對 ${result.summary.matched_count} 個${result.summary.unmatched_sequence_names?.length ? ` · ${result.summary.unmatched_sequence_names.length} 筆上機資訊未配對` : ""}`;
        dirty("untargeted");
      } else if (job.kind === "quality")
        status(
          `Feature Quality 匯出完成：${state.analysis.results.quality.output_dir}`,
        );
      else if (job.kind === "inspector") {
        status(
          `Inspector 匯出完成：${state.analysis.results.inspector.output}`,
        );
        const artifact = state.analysis.results.inspector.artifacts?.[0];
        if (artifact) showArtifact(artifact);
      } else {
        dirty(job.kind, false);
        status(`${names[job.kind]} 分析完成，可在下方查看結果。`);
      }
    } else
      status(
        `${job.error || labels[job.status]}。原先結果仍可檢閱。`,
        job.status === "failed",
      );
    renderResults();
  }
  function artifactLink(artifact) {
    const link = el("a", artifact.name);
    link.href = artifact.url;
    link.download = artifact.name;
    if (artifact.name.toLowerCase().endsWith(".html")) {
      link.removeAttribute("download");
      link.addEventListener("click", (event) => {
        event.preventDefault();
        showArtifact(artifact);
      });
    }
    return link;
  }
  function showArtifact(artifact) {
    $("artifact-title").textContent = artifact.name;
    $("artifact-frame").src = artifact.url;
    $("artifact-dialog").showModal();
  }
  function metric(container, value, label) {
    const cell = el("div");
    cell.append(el("strong", value ?? "—"), el("span", label));
    container.append(cell);
  }
  function resultBase(kind, result) {
    const container = $(
      `${kind === "targeted" ? "target" : "untargeted"}-result`,
    );
    container.hidden = false;
    container.replaceChildren(
      el("h2", `${kind === "targeted" ? "Targeted" : "Untargeted"} 分析結果`),
    );
    const artifacts = el("div", undefined, "artifact-list");
    for (const artifact of result.artifacts || [])
      artifacts.append(artifactLink(artifact));
    const folder =
      result.output_dir || result.excel_path?.replace(/[\\/][^\\/]+$/, "");
    if (folder)
      artifacts.append(
        button("開啟輸出資料夾", () =>
          api("/api/open-local", { path: folder }),
        ),
      );
    container.append(artifacts);
    return container;
  }
  function renderResults() {
    const targeted = state.analysis.results.targeted;
    if (targeted) {
      const container = resultBase("targeted", targeted);
      const metrics = el("div", undefined, "result-metrics");
      metric(metrics, targeted.total_files, "RAW 檔案");
      metric(metrics, targeted.targets?.length, "目標");
      metric(metrics, targeted.diagnostics_count, "診斷紀錄");
      container.append(metrics);
      for (const warning of targeted.istd_warnings || [])
        container.append(
          el(
            "p",
            `ISTD ${warning.label}：${warning.detected} / ${warning.total} 個樣本偵測到。`,
            "status",
          ),
        );
      const table = el("table");
      const head = el("tr");
      for (const label of [
        "目標",
        "偵測到 / 總數",
        "NL OK",
        "NL WARN",
        "NL FAIL",
        "NO MS2",
        "面積中位數",
      ])
        head.append(el("th", label));
      table.append(head);
      for (const row of targeted.targets || []) {
        const tr = el("tr");
        for (const value of [
          row.label,
          `${row.detected} / ${row.total}`,
          row.nl_ok,
          row.nl_warn,
          row.nl_fail,
          row.nl_no_ms2,
          row.median_area,
        ])
          tr.append(el("td", value ?? "—"));
        table.append(tr);
      }
      const scroll = el("div", undefined, "table-scroll");
      scroll.append(table);
      container.append(scroll);
    }
    const result = state.analysis.results.untargeted;
    if (result) {
      const container = resultBase("untargeted", result);
      const metrics = el("div", undefined, "result-metrics");
      metric(metrics, result.sample_count, "樣本");
      for (const [key, label] of [
        ["total", "候選"],
        ["HIGH", "HIGH"],
        ["MEDIUM", "MEDIUM"],
        ["LOW", "LOW"],
      ])
        metric(metrics, result.candidate_counts?.[key], label);
      container.append(metrics);
      const actions = el("div", undefined, "button-row");
      const evidence = result.evidence?.path || result.evidence?.report_path;
      if (evidence) {
        actions.append(
          button("在工作區檢閱", () => openReview(evidence), "primary"),
        );
        actions.append(
          button("自訂 Inspector 匯出", () => openInspectorExport(evidence)),
        );
      } else
        container.append(
          el(
            "p",
            "Discovery-only 輸出候選與檢閱表；跨樣本檢閱可在完成 Alignment 後開啟。",
            "subtle",
          ),
        );
      if (result.matrix_tsv && result.alignment_outputs?.matrix_identity_tsv)
        actions.append(
          button("Feature Quality 篩選匯出", () => openQuality(result)),
        );
      container.append(actions);
    }
  }
  function openReview(source) {
    selectView("review");
    document.dispatchEvent(
      new CustomEvent("xic:open-review", { detail: source }),
    );
  }
  function openQuality(result) {
    $("quality-paths").replaceChildren();
    pathField(
      $("quality-paths"),
      "metadata",
      "樣本資料 TSV",
      "metadata",
      "",
      "quality-",
    );
    pathField(
      $("quality-paths"),
      "output",
      "新輸出資料夾",
      "folder",
      `${result.output_dir}/feature_quality`,
      "quality-",
    );
    $("quality-dialog").showModal();
  }
  async function openInspectorExport(source) {
    state.exportSource = source;
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
      input.value = sample.canonical_sample_identity_id;
      label.append(
        input,
        document.createTextNode(
          sample.sample_name ||
            sample.sample_id ||
            sample.canonical_sample_identity_id,
        ),
      );
      $("export-samples").append(label);
    }
    $("inspector-export-dialog").showModal();
  }
  function requestPath(title, value = "") {
    $("path-dialog-title").textContent = title;
    $("path-dialog-value").value = value;
    $("path-dialog").returnValue = "cancel";
    $("path-dialog").showModal();
    return new Promise((resolve) =>
      $("path-dialog").addEventListener(
        "close",
        () =>
          resolve(
            $("path-dialog").returnValue === "ok"
              ? $("path-dialog-value").value.trim()
              : null,
          ),
        { once: true },
      ),
    );
  }
  async function reloadIstd() {
    const result = await api("/api/istd", { path: $("u-targets_csv").value });
    $("istd-summary").textContent =
      result.message ||
      `${result.selection.targets.length} 個內標；略過 ${result.selection.skipped_count} 個一般目標。`;
  }
  async function copyIstd() {
    const destination = await requestPath(
      "另存可編輯內標副本",
      `${state.config.config_dir}/my-targets.csv`,
    );
    if (!destination) return;
    const result = await api("/api/istd/copy", {
      path: $("u-targets_csv").value,
      destination,
    });
    $("u-targets_csv").value = result.path;
    dirty("untargeted");
    await reloadIstd();
  }
  function bind() {
    document.addEventListener(
      "load",
      (event) => {
        if (event.target instanceof HTMLIFrameElement) syncReportTheme();
      },
      true,
    );
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
    $("targeted-form").addEventListener("input", () => dirty("targeted"));
    $("untargeted-form").addEventListener("input", () => dirty("untargeted"));
    $("targeted-form").addEventListener("submit", (event) => {
      event.preventDefault();
      act(() =>
        startJob("targeted", {
          settings: settingsValues(),
          targets: state.targets,
        }),
      );
    });
    $("untargeted-form").addEventListener("submit", (event) => {
      event.preventDefault();
      act(() => startJob("untargeted", untargetedValues()));
    });
    $("target-save").onclick = () => act(saveTargeted);
    $("untargeted-save").onclick = () => act(saveUntargeted);
    $("target-new").onclick = () => {
      $("t-data_dir").value = "";
      $("target-result").hidden = true;
      dirty("targeted");
    };
    $("target-reset").onclick = () => {
      const values = settingsValues();
      state.config.settings = {
        ...state.config.targeted_form.defaults,
        data_dir: values.data_dir,
        dll_dir: values.dll_dir,
      };
      renderSettings(state.config.settings);
      dirty("targeted");
    };
    $("target-edit").onclick = () => {
      renderTargets();
      $("target-editor").showModal();
    };
    $("target-add").onclick = () => {
      state.targets.push({
        label: "",
        mz: "",
        rt_min: "",
        rt_max: "",
        ppm_tol: "20",
        neutral_loss_da: "",
        nl_ppm_warn: "20",
        nl_ppm_max: "50",
        is_istd: "false",
        istd_pair: "",
      });
      dirty("targeted");
      renderTargets();
    };
    $("target-editor-save").onclick = () =>
      act(async () => {
        await saveTargeted();
        $("target-editor").close();
      });
    $("target-import").onclick = () =>
      act(async () => {
        const result = await api("/api/targets/import", {
          path: $("target-import-path").value,
        });
        state.targets = result.targets;
        targetCount();
        dirty("targeted");
        status(`已匯入 ${state.targets.length} 個目標；儲存或開始分析後套用。`);
      });
    $("u-mode").onchange = modeFields;
    $("u-preset").onchange = () =>
      act(async () => {
        await loadMethod($("u-preset").value);
        dirty("untargeted");
      });
    $("method-edit").onclick = () => {
      if (!state.method) return;
      state.draft = structuredClone(state.method.draft);
      state.draft.overrides = untargetedValues().overrides;
      renderMethodEditor();
      $("method-editor").showModal();
    };
    $("method-new").onclick = () => {
      state.draft = {
        name: "新方法",
        description: "",
        combine_mode: "single",
        tags: [
          {
            enabled: true,
            strategy: "neutral_loss",
            name: "Custom NL",
            neutral_loss_da: 116.0474,
          },
        ],
        overrides: { rt_max: 60 },
      };
      renderMethodEditor();
      $("method-editor").showModal();
    };
    $("method-add-tag").onclick = () => {
      state.draft.name = $("method-name").value;
      state.draft.description = $("method-description").value;
      state.draft.combine_mode = $("method-combine").value;
      state.draft.tags.push({
        enabled: true,
        strategy: "neutral_loss",
        name: "Custom NL",
        neutral_loss_da: 0,
      });
      renderMethodEditor();
    };
    $("method-save-as").onclick = () => act(() => saveMethod(false));
    $("method-apply").onclick = () => act(() => saveMethod(true));
    $("method-import").onclick = () =>
      act(async () => {
        const path = await requestPath("匯入 TOML 方法");
        if (path) {
          await loadMethod(path);
          dirty("untargeted");
        }
      });
    $("samples-refresh").onclick = () => act(scanSamples);
    $("samples-import").onclick = () => act(scanSamples);
    $("samples-edit").onclick = () => {
      renderSamples();
      $("sample-editor").showModal();
    };
    $("sample-filter").oninput = renderSamples;
    roleOptions($("sample-bulk-role"), "study_sample");
    $("sample-apply-role").onclick = () => {
      const selected = new Set(
        [...$("sample-rows").querySelectorAll("input:checked")].map(
          (node) => node.dataset.sample,
        ),
      );
      for (const row of state.samples)
        if (selected.has(row.raw_stem))
          row.sample_role = $("sample-bulk-role").value;
      dirty("untargeted");
      renderSamples();
    };
    $("samples-export").onclick = () =>
      act(async () => {
        const path = await requestPath(
          "匯出樣本資料 TSV / CSV",
          `${$("u-output_dir").value}/sample_metadata.tsv`,
        );
        if (path) {
          await api("/api/samples/export", { samples: state.samples, path });
          status(`樣本資料已匯出：${path}`);
        }
      });
    $("istd-reload").onclick = () => act(reloadIstd);
    $("u-targets_csv").addEventListener("change", () => act(reloadIstd));
    $("method-tuning").addEventListener("input", updateMethodPreview);
    $("istd-copy").onclick = () => act(copyIstd);
    $("istd-open").onclick = () =>
      act(async () => {
        if ($("u-targets_csv").value === state.config.targets_example)
          await copyIstd();
        await api("/api/open-local", { path: $("u-targets_csv").value });
      });
    for (const id of ["untargeted-new", "untargeted-clear"])
      $(id).onclick = () =>
        act(async () => {
          for (const key of ["raw_dir", "raw_file", "discovery_batch_index"])
            $(`u-${key}`).value = "";
          $("u-output_dir").value = "";
          $("sequence-source").value = "";
          state.samples = [];
          state.config.untargeted.custom_roles = {};
          renderSamples();
          $("untargeted-result").hidden = true;
          if (id === "untargeted-clear") {
            $("u-targets_csv").value = "";
            await loadMethod("dna_dr");
          }
          dirty("untargeted");
        });
    $("u-raw_dir").addEventListener("change", () => {
      state.samples = [];
      renderSamples();
      const name = $("u-raw_dir")
        .value.replace(/\\/g, "/")
        .replace(/\/$/, "")
        .split("/")
        .pop();
      const current = $("u-output_dir").value.replace(/\\/g, "/");
      if (name && (!current || /\/output(?:\/[^/]+)?$/.test(current)))
        $("u-output_dir").value =
          `${current ? current.replace(/(\/output)(?:\/[^/]+)?$/, "$1") : `${state.config.config_dir}/../output`}/${name}`;
      dirty("untargeted");
    });
    $("sequence-source").addEventListener("change", () => {
      state.samples = [];
      renderSamples();
      dirty("untargeted");
    });
    $("analysis-stop").onclick = () =>
      act(async () => {
        if (
          confirm(
            "停止目前工作？已發布的結果會保留，正在執行的步驟需完成清理。",
          )
        ) {
          state.analysis = await api("/api/analysis/cancel", {
            job_id: state.analysis.job.job_id,
          });
          renderJob();
        }
      });
    $("quality-form").onsubmit = (event) => {
      event.preventDefault();
      act(async () => {
        const result = state.analysis.results.untargeted;
        await startJob("quality", {
          matrix_path: result.matrix_tsv,
          identity_path: result.alignment_outputs.matrix_identity_tsv,
          sample_metadata_path: $("quality-metadata").value,
          output_dir: $("quality-output").value,
          min_finite_fraction: Number($("quality-threshold").value),
        });
        $("quality-dialog").close();
      });
    };
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
          source: state.exportSource,
          output: $("export-output").value,
          selection,
        });
        $("inspector-export-dialog").close();
      });
    };
    $("theme-toggle").onclick = () =>
      act(async () => {
        document.body.dataset.theme =
          document.body.dataset.theme === "dark" ? "light" : "dark";
        $("theme-toggle").textContent =
          document.body.dataset.theme === "dark" ? "淺色模式" : "深色模式";
        syncReportTheme();
        await api("/api/preferences", { theme: document.body.dataset.theme });
      });
    $("about-workspace").onclick = () =>
      status(
        "XIC Extractor · 本機 HTML 工作區。Python 核心執行分析，Inspector 檢閱既有證據。快捷鍵：Ctrl+1 Targeted、Ctrl+2 Untargeted、Ctrl+3 檢閱；Ctrl+. 停止。",
      );
    $("shutdown-workspace").onclick = () =>
      act(async () => {
        if (isBusy())
          throw new Error("請先停止或等待目前工作完成，再結束工作區。");
        await api("/api/shutdown", {});
        status("工作區已結束，可以關閉此分頁。");
        state.closed = true;
      });
    document.addEventListener("xic:review-busy", (event) => {
      state.reviewBusy = event.detail;
      lock();
    });
    document.addEventListener("keydown", (event) => {
      if (!event.ctrlKey) return;
      if (["1", "2", "3"].includes(event.key)) {
        event.preventDefault();
        selectView({ 1: "targeted", 2: "untargeted", 3: "review" }[event.key]);
      }
      if (event.key === "." && isBusy() && state.analysis.job?.cancellable) {
        event.preventDefault();
        $("analysis-stop").click();
      }
      if (event.key.toLowerCase() === "r" && state.view !== "review") {
        event.preventDefault();
        $(`${state.view}-form`).requestSubmit();
      }
    });
  }
  try {
    const review = document.querySelector("body > main");
    review.id = "workspace-review";
    const response = await fetch("/assets/analysis.html");
    if (!response.ok) throw new Error("分析頁面無法載入。");
    const holder = document.createElement("div");
    holder.innerHTML = await response.text();
    review.before(holder);
    await api("/api/session");
    state.config = await api("/api/config");
    state.targets = state.config.targets;
    renderSettings(state.config.settings);
    targetCount();
    for (const [key, label, kind] of pathFields)
      pathField(
        $("untargeted-paths"),
        key,
        label,
        kind,
        state.config.untargeted[key],
      );
    pathField(
      $("istd-path-field"),
      "targets_csv",
      "內標 CSV（留空停用）",
      "csv",
      state.config.untargeted.targets_csv,
    );
    $("u-mode").value = state.config.untargeted.mode;
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
      const option = el(
        "option",
        `無法讀取：${state.config.untargeted.preset}`,
      );
      option.value = state.config.untargeted.preset;
      $("u-preset").append(option);
      $("u-preset").value = option.value;
      status(error.message, true);
    }
    bind();
    modeFields();
    state.analysis = state.config.analysis;
    renderResults();
    renderJob();
    document.body.dataset.theme = state.config.preferences.theme || "light";
    syncReportTheme();
    $("theme-toggle").textContent =
      document.body.dataset.theme === "dark" ? "淺色模式" : "深色模式";
    selectView(
      state.config.preferences.view ||
        (state.config.review_source ? "review" : "untargeted"),
      false,
    );
    if (state.method)
      status("設定已載入。選擇資料與方法後，即可在此開始分析。");
    await act(reloadIstd);
    setInterval(async () => {
      if (state.closed) return;
      try {
        state.analysis = await api("/api/analysis");
        renderJob();
      } catch (error) {
        if (isBusy()) status(`進度連線暫時中斷：${error.message}`, true);
      }
    }, 750);
  } catch (error) {
    if ($("analysis-status")) status(error.message, true);
    else console.error(error);
  }
})();
