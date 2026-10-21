"use strict";
(async () => {
  const $ = (id) => document.getElementById(id);
  const ui = window.XicUi;
  const state = {
    config: null,
    targets: [],
    sampleSets: { targeted: null, untargeted: null },
    editing: "untargeted",
    method: null,
    draft: null,
    analysis: { job: null, results: {} },
    reviewBusy: false,
    importing: false,
    view: "targeted",
    dirty: { targeted: false, untargeted: false },
    seenJob: null,
    offlineCount: 0,
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
    ["dll_dir", "RawFileReader DLL 資料夾", "folder"],
  ];
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
    quality: "Feature Quality 匯出",
    inspector: "Inspector 匯出",
  };
  const jobPage = { targeted: "targeted" };
  const pageOf = (job) => job?.page || jobPage[job?.kind] || "untargeted";
  // One sample step for both flows; edits confirm the RAW folder's metadata.
  const samplePages = {
    targeted: {
      prefix: "t-",
      sequence: "t-injection_order_source",
      rawDir: "t-data_dir",
      dll: "t-dll_dir",
      output: "t-output_dir",
      step: 2,
    },
    untargeted: {
      prefix: "",
      sequence: "sequence-source",
      rawDir: "u-raw_dir",
      dll: "u-dll_dir",
      output: "u-output_dir",
      step: 4,
    },
  };
  const sampleId = (page, part) => `${samplePages[page].prefix}${part}`;
  const emptySamples = () => ({
    rows: [],
    overview: null,
    suggestions: {},
    source: "",
    earlier: [],
  });
  state.sampleSets = { targeted: emptySamples(), untargeted: emptySamples() };
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
    ui.toast(text, error ? "error" : "info");
  }
  function done(text) {
    ui.toast(text, "ok");
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
  function button(label, fn, cls = "", iconName = "") {
    const node = el("button", undefined, cls);
    node.type = "button";
    if (iconName) node.append(ui.icon(iconName));
    node.append(label);
    node.addEventListener("click", () => act(fn));
    return node;
  }
  function pathTail(path, parts = 2) {
    const segments = String(path || "")
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .filter(Boolean);
    return segments.length > parts
      ? `…\\${segments.slice(-parts).join("\\")}`
      : String(path || "");
  }
  function isRunning() {
    return ["running", "cancelling"].includes(state.analysis.job?.status);
  }
  function isBusy() {
    return state.importing || state.reviewBusy || isRunning();
  }
  function lock() {
    document
      .querySelectorAll("[data-analysis-idle]")
      .forEach((node) => (node.disabled = isBusy() ||
        (node.dataset.rtBound === "true" && !$("u-rt-crop")?.checked)));
    document.dispatchEvent(
      new CustomEvent("xic:analysis-busy", { detail: isRunning() }),
    );
  }
  let methodTimer;
  function dirty(kind, value = true) {
    state.dirty[kind] = value;
    const note = $(`${kind === "targeted" ? "target" : "untargeted"}-dirty`);
    note.textContent = value ? "有尚未儲存的變更；開始分析時會一併儲存。" : "";
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
  function setMethodState(summary) {
    const label = summary?.label || "共用 CID-NL 策略";
    const custom = new Set(summary?.custom_keys || []);
    const chip = $("target-method-state");
    chip.textContent = label;
    chip.className = custom.size ? "settings-warning" : "";
    const notes = $("target-method-note");
    notes.replaceChildren();
    notes.append(el("p", `設定檔：${state.config.settings_source || "workspace.json"}`, "settings-source"));
    for (const warning of summary?.warnings || []) {
      notes.append(el("p", warning, "settings-warning"));
    }
    checklist("targeted");
  }
  function selectView(view, persist = true) {
    if (persist && view !== state.view) window.XicUi.navigation.push();
    state.view = view;
    document.body.dataset.view = view;
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
    const slot = document.querySelector(`[data-job-slot="${view}"]`);
    if (slot) slot.append($("analysis-job"));
    if (state.config) renderJob();
    if (persist) act(() => api("/api/preferences", { view }));
  }
  window.XicUi.navigation.register("workspace", {
    capture: () => state.view,
    restore: view => selectView(view, false),
  });

  // Form fields
  // Example templates ship placeholder paths that are not real inputs.
  const isTemplatePath = (value) =>
    value.startsWith("<") || /[\\/]your[\\/]/i.test(value);
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
      input.step = spec.step ?? "any";
      if (spec.min !== undefined || spec.minimum !== undefined)
        input.min = spec.min ?? spec.minimum;
      if (spec.max !== undefined || spec.maximum !== undefined)
        input.max = spec.max ?? spec.maximum;
    }
    if (["folder", "file"].includes(kind)) input.className = "path";
    if (kind === "checkbox")
      input.checked = value === true || String(value).toLowerCase() === "true";
    else input.value = value ?? "";
    const help = spec.help ? el("small", spec.help) : null;
    if (["folder", "file"].includes(kind)) {
      const browse = el("button", "瀏覽");
      browse.type = "button";
      browse.dataset.browse = input.id;
      browse.dataset.kind = kind;
      browse.dataset.analysisIdle = "";
      const row = el("span", undefined, "input-action");
      row.append(input, browse);
      label.append(title, row);
      if (help) label.append(help);
    } else if (kind === "checkbox") {
      if (help) title.append(help);
      label.append(input, title);
    } else {
      label.append(title, input);
      if (help) label.append(help);
    }
    return label;
  }
  // 上機資訊 only annotates; disagreements are listed and the RAW decides.
  function renderSampleSummary(summary, prefix = "samples") {
    const notes = [];
    for (const conflict of summary.conflicts || []) {
      const names = conflict.sequence_names.join("、");
      if (conflict.kind === "duplicate_order")
        notes.push(`序號 ${conflict.detail} 出現 ${conflict.sequence_names.length} 次：${names}`);
      else {
        const [file, text] = conflict.detail.split(" / ");
        notes.push(`${names}：檔名是 ${file}，描述寫 ${text}`);
      }
    }
    if (summary.unreadable_raw?.length)
      notes.push(`讀不到檔頭，已排除（不分析）：${summary.unreadable_raw.join("、")}`);
    const unmatched = summary.unmatched_sequence_names?.length;
    $(`${prefix}-summary`).textContent =
      `RAW ${summary.raw_count} 次上機 · 上機資訊配對 ${summary.matched_count} 個` +
      (unmatched ? ` · ${unmatched} 筆上機資訊未配對` : "") +
      (notes.length ? ` · ${notes.length} 處要確認，注射順序與樣本名稱以 RAW 為準` : "");
    $(`${prefix}-conflicts`).replaceChildren(...notes.map((note) => el("li", note)));
    $(`${prefix}-conflicts`).hidden = !notes.length;
  }
  function pathField(container, key, label, kind, value, prefix = "u-", help = "") {
    const wrap = el("div", undefined, "path-line");
    wrap.id = `${prefix}${key}-row`;
    const title = el("label", label);
    title.htmlFor = `${prefix}${key}`;
    const input = el("input", undefined, "path");
    input.id = title.htmlFor;
    input.value = isTemplatePath(String(value || "")) ? "" : value || "";
    input.dataset.analysisIdle = "";
    const browse = el("button", "瀏覽");
    browse.type = "button";
    browse.dataset.browse = input.id;
    browse.dataset.kind = kind;
    browse.dataset.analysisIdle = "";
    wrap.append(title, input, browse);
    if (help) wrap.append(el("small", help, "field-help"));
    container.append(wrap);
    return input;
  }

  // Readiness
  function checkItem(tone, label, detail, focus, mono = false) {
    return { tone, label, detail, focus, mono };
  }
  function pathItem(label, id, missing) {
    const value = $(id)?.value.trim() || "";
    const usable = value && !isTemplatePath(value);
    return checkItem(
      usable ? "ok" : "todo",
      label,
      usable ? pathTail(value) : missing,
      id,
      usable,
    );
  }
  function checklist(kind) {
    if (!state.config) return;
    const items = [];
    if (kind === "targeted") {
      items.push(pathItem("RAW 資料夾", "t-data_dir", "尚未選擇資料夾"));
      items.push(pathItem("輸出資料夾", "t-output_dir", "尚未選擇資料夾"));
      items.push(pathItem("RawFileReader DLL 資料夾", "t-dll_dir", "尚未選擇資料夾"));
      const istd = state.targets.filter(
        (row) => row.role === "istd",
      ).length;
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
      const mode = currentMode();
      if (mode === "discovery_only")
        items.push(pathItem("單一 RAW", "u-raw_file", "尚未選擇 RAW 檔"));
      if (mode === "align_only")
        items.push(
          pathItem(
            "Discovery batch index",
            "u-discovery_batch_index",
            "尚未選擇既有 Discovery 結果",
          ),
        );
      if (mode !== "discovery_only")
        items.push(pathItem("RAW 資料夾", "u-raw_dir", "尚未選擇資料夾"));
      items.push(pathItem("輸出資料夾", "u-output_dir", "尚未選擇資料夾"));
      items.push(pathItem("RawFileReader DLL 資料夾", "u-dll_dir", "尚未選擇資料夾"));
      const preset = $("u-preset");
      items.push(
        checkItem(
          state.method ? "ok" : "todo",
          "分析方法",
          preset.selectedOptions[0]?.textContent || "尚未選擇方法",
          "u-preset",
        ),
      );
      if (mode !== "discovery_only") items.push(...sampleItems("untargeted"));
    }
    const list = $(`${kind === "targeted" ? "target" : "untargeted"}-checklist`);
    list.replaceChildren(
      ...items.map((item) => {
        const li = el("li", undefined, item.tone);
        const target = el("button", undefined, "check-item");
        target.type = "button";
        target.append(
          ui.icon(
            item.tone === "ok" ? "check-circle" : item.tone === "todo" ? "dot" : "info",
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
  // Counts come from the server's group owner; this only words them.
  function sampleItems(page) {
    const set = state.sampleSets[page];
    const refresh = sampleId(page, "samples-refresh");
    const edit = sampleId(page, "samples-edit");
    if (!set.rows.length) {
      const order = $(samplePages[page].sequence)?.value.trim();
      return [
        checkItem(
          "info",
          "樣本與上機資訊（選用）",
          order
            ? `上機資訊 ${pathTail(order)}；可讀取 RAW 後確認角色、組別與排除`
            : "尚未讀取；可讀取 RAW 後確認角色、組別與排除",
          refresh,
        ),
      ];
    }
    const items = [
      checkItem("ok", "樣本與上機資訊", `${set.rows.length} 個樣本已讀取`, edit),
    ];
    const overview = set.overview;
    if (!overview) return items;
    items.push(
      checkItem(
        "info",
        "各組樣本數",
        overview.groups.map((g) => `${g.group} ${g.count}`).join(" · ") ||
          "沒有要分析的樣本",
        edit,
      ),
    );
    if (overview.unassigned_study_samples)
      items.push(
        checkItem(
          "todo",
          "未標組別的研究樣本",
          `${overview.unassigned_study_samples} 個，會歸到 Other`,
          edit,
        ),
      );
    if (overview.single_sample_groups.length)
      items.push(
        checkItem(
          "todo",
          "只有 1 個樣本的組別",
          overview.single_sample_groups.join("、"),
          edit,
        ),
      );
    if (overview.excluded.length) {
      const reasons = new Map();
      for (const row of overview.excluded) {
        const reason = row.reason || "未填原因";
        reasons.set(reason, (reasons.get(reason) || 0) + 1);
      }
      items.push(
        checkItem(
          "info",
          "排除的樣本",
          `${overview.excluded.length} 個：` +
            [...reasons].map(([reason, n]) => `${reason} ×${n}`).join("、"),
          edit,
        ),
      );
    }
    return items;
  }
  function focusField(id) {
    const node = $(id);
    if (!node) return;
    for (
      let parent = node.parentElement;
      parent;
      parent = parent.parentElement
    )
      if (parent.tagName === "DETAILS") parent.open = true;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    if (node.tagName !== "DETAILS") node.focus({ preventScroll: true });
  }

  // Targeted
  function settingsValues() {
    const values = { ...state.config.settings };
    for (const spec of state.config.targeted_form.settings) {
      const input = $(`t-${spec.key}`);
      if (!input) continue;
      if (spec.kind === "checkbox") values[spec.key] = String(input.checked);
      // A field left blank over a template path keeps saving the template.
      else if (input.value || !isTemplatePath(String(values[spec.key] ?? "")))
        values[spec.key] = input.value;
    }
    return values;
  }
  function renderSettings(values) {
    const data = $("targeted-data");
    const method = $("targeted-settings");
    const execution = $("targeted-execution");
    const outputDir = $("t-output_dir")?.value ?? state.config.targeted_output_dir;
    for (const panel of [data, method, execution]) panel.replaceChildren();
    const note = el("div", undefined, "method-note");
    note.id = "target-method-note";
    method.append(el("p", "目標清單決定名稱、m/z、預期 RT、CID-NL tag 與 ISTD 配對。平台共用候選與證據策略。", "settings-notice"), note);
    const grid = el("div", undefined, "settings-grid");
    execution.append(grid);
    // Paths use the same rows as Untargeted: RAW, run inputs, output, machine.
    for (const spec of state.config.targeted_form.settings) {
      if (spec.kind === "readonly" || spec.key === "signal_processing_method" || spec.key === "count_no_ms2_as_detected") continue;
      if (!["folder", "file"].includes(spec.kind)) {
        grid.append(field(spec, values[spec.key], "t-"));
        continue;
      }
      if (spec.scope === "environment")
        pathField(data, "output_dir", "輸出資料夾", "folder", outputDir, "t-");
      if (spec.key === "injection_order_source") {
        const value = String(values[spec.key] || "");
        $(samplePages.targeted.sequence).value = isTemplatePath(value) ? "" : value;
        continue;
      }
      pathField(data, spec.key, spec.label, spec.browse || spec.kind, values[spec.key], "t-", spec.help);
    }
    setMethodState(state.config.targeted_method);
    $("t-data_dir").dataset.batchPath = values.data_dir || "";
    lock();
  }
  function applyTargetedDraft(result) {
    state.config.settings = result.settings;
    renderSettings(result.settings);
    setMethodState(result.summary);
    dirty("targeted");
  }
  function renderTargetedMethods(methods, selected = "") {
    const select = $("target-method-select");
    select.replaceChildren(new Option("目前工作區設定", ""));
    for (const method of methods) select.add(new Option(method.name, method.path));
    select.value = selected;
  }
  async function loadTargetedMethod(path) {
    if (!path) throw new Error("請先選擇清單。");
    const result = await api("/api/targeted/method/load", { path, settings: settingsValues() });
    state.targets = result.targets;
    applyTargetedDraft(result);
    targetCount();
    done("已載入目標清單；批次及本機設定保持不變。儲存或開始分析後套用。");
  }
  function bindListUpload(prefix, receive) {
    $(`${prefix}-import`).onclick = () => $(`${prefix}-import-file`).click();
    $(`${prefix}-import-file`).onchange = (event) => {
      receive(event.target.files);
      event.target.value = "";
    };
    const zone = $(`${prefix}-dropzone`);
    for (const type of ["dragenter", "dragover", "dragleave", "drop"]) {
      zone.addEventListener(type, (event) => {
        event.preventDefault();
        zone.classList.toggle("drag-over", !isBusy() && ["dragenter", "dragover"].includes(type));
        if (type === "drop") receive(event.dataTransfer.files);
      });
    }
  }
  async function readListFile(files) {
    if (files.length !== 1) throw new Error("請一次選擇一個方法檔或目標表。");
    const file = files[0];
    if (!/\.(xlsx|csv|tsv)$/i.test(file.name)) throw new Error("請選擇 XLSX、CSV 或 TSV 檔案；不支援 .xls。");
    if (!file.size || file.size > 1024 * 1024) throw new Error("請選擇非空白且不超過 1 MiB 的檔案。");
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = () => reject(new Error("無法讀取檔案，請重新選擇。"));
      reader.readAsDataURL(file);
    });
    return { name: file.name, content };
  }
  let pendingTargetImport = null;
  async function previewTargetFiles(files) {
    if (isBusy()) return;
    const message = $("target-import-status");
    message.textContent = "";
    message.className = "";
    pendingTargetImport = null;
    try {
      state.importing = true;
      lock();
      message.textContent = "正在讀取檔案…";
      const upload = await readListFile(files);
      const result = await api("/api/targets/upload", { ...upload, settings: settingsValues() });
      pendingTargetImport = result;
      const istdCount = result.targets.filter((row) => row.role === "istd").length;
      $("target-import-summary").textContent = `${upload.name} · ${result.kind === "method" ? "方法檔（Method + Targets）" : "目標表"} · ${result.targets.length} 個目標（ISTD ${istdCount} 個）`;
      const body = $("target-import-rows");
      body.replaceChildren();
      for (const row of result.targets) {
        const tr = document.createElement("tr");
        for (const value of [row.label, row.mz, row.expected_rt_min, row.role === "istd" ? "ISTD" : "Analyte", row.istd_pair || "—"]) {
          const td = document.createElement("td");
          td.textContent = value || "";
          tr.append(td);
        }
        body.append(tr);
      }
      message.textContent = "已辨識檔案，請確認預覽；取消不會變更目前清單。";
      $("target-import-preview").showModal();
    } catch (error) {
      message.textContent = error.message;
      message.className = "error";
    } finally {
      state.importing = false;
      lock();
    }
  }
  async function saveTargeted(quiet = false) {
    const settings = settingsValues();
    const output_dir = $("t-output_dir").value;
    const receipt = await api("/api/targeted/save", { settings, targets: state.targets, output_dir });
    state.config.settings = settings;
    state.config.targeted_output_dir = output_dir;
    state.config.settings_source = "workspace.json";
    state.config.targeted_method = receipt.summary;
    setMethodState(receipt.summary);
    dirty("targeted", false);
    if (!quiet) done("Targeted 設定與目標已儲存。");
  }
  const isIstd = (row) => row.role === "istd";
  function targetCount() {
    const istd = state.targets.filter(isIstd).length;
    $("target-count").textContent = state.targets.length
      ? `${state.targets.length} 個目標 · ${istd} 個 ISTD`
      : "尚未載入目標";
    renderTargetPreview();
    checklist("targeted");
  }
  function renderTargetPreview() {
    const preview = $("target-preview");
    if (!state.targets.length) {
      preview.replaceChildren(
        el(
          "div",
          "目前沒有目標。匯入 CSV／TSV／Excel，或按「檢視與編輯」新增。",
          "target-preview-empty",
        ),
      );
      return;
    }
    const table = el("table");
    const head = el("tr");
    for (const [label, cls] of [
      ["名稱", ""],
      ["m/z", "num"],
      ["預期 RT (min)", "num"],
      ["CID-NL tag", "num"],
      ["ISTD 配對", ""],
    ])
      head.append(el("th", label, cls));
    const thead = el("thead");
    thead.append(head);
    const body = el("tbody");
    for (const row of state.targets.slice(0, 6)) {
      const tr = el("tr");
      const name = el("td");
      name.append(el("span", row.label || "（未命名）"));
      if (isIstd(row)) name.append(" ", el("span", "ISTD", "istd-flag"));
      tr.append(
        name,
        el("td", row.mz || "—", "num"),
        el(
          "td",
          row.expected_rt_min || "—",
          "num",
        ),
        el("td", row.cid_nl_tag || "—", "num"),
        el("td", row.istd_pair || "—"),
      );
      body.append(tr);
    }
    table.append(thead, body);
    const scroll = el("div", undefined, "table-scroll");
    scroll.append(table);
    const nodes = [scroll];
    if (state.targets.length > 6)
      nodes.push(
        el(
          "p",
          `另外 ${state.targets.length - 6} 個目標，按「檢視與編輯」查看完整清單。`,
          "target-preview-more",
        ),
      );
    preview.replaceChildren(...nodes);
  }
  const targetColumns = [
    ["label", "名稱"],
    ["pairing", "角色／ISTD 配對"],
    ["mz", "m/z"],
    ["expected_rt_min", "預期 RT（min）"],
    ["cid_nl_tag", "CID-NL tag（dR / R / MeR）"],
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
      for (const [key, label] of targetColumns) {
        const td = el("td");
        const input = el(key === "pairing" ? "select" : "input");
        input.setAttribute("aria-label", `${label}（第 ${index + 1} 列）`);
        if (key === "pairing") {
          const choices = [["", "未配對"], ["role:istd", "此列是 ISTD"]];
          for (const standard of state.targets.filter(isIstd)) {
            if (standard !== row) choices.push([`pair:${standard.label}`, standard.label]);
          }
          const value = isIstd(row) ? "role:istd" : row.istd_pair ? `pair:${row.istd_pair}` : "";
          if (!choices.some(([key]) => key === value))
            choices.push([value, `${row.istd_pair}（請重新選擇）`]);
          for (const [value, label] of choices) {
            const option = el("option", label);
            option.value = value;
            input.append(option);
          }
          input.value = value;
        } else {
          input.value = row[key] || "";
          if (
            ["mz", "expected_rt_min"].includes(
              key,
            )
          )
            input.inputMode = "decimal";
        }
        input.addEventListener("input", () => {
          if (key === "pairing") {
            row.role = input.value === "role:istd" ? "istd" : "analyte";
            row.istd_pair = input.value.startsWith("pair:") ? input.value.slice(5) : "";
            renderTargets();
          } else row[key] = input.value;
          dirty("targeted");
          targetCount();
        });
        td.append(input);
        tr.append(td);
      }
      const td = el("td");
      const remove = button(
        "",
        () => {
          state.targets.splice(index, 1);
          dirty("targeted");
          renderTargets();
        },
        "icon-button",
        "x",
      );
      remove.setAttribute("aria-label", `刪除第 ${index + 1} 列`);
      remove.title = "刪除這一列";
      td.append(remove);
      tr.append(td);
      body.append(tr);
    });
    targetCount();
  }

  // Untargeted
  function currentMode() {
    return (
      document.querySelector('input[name="u-mode"]:checked')?.value || "full"
    );
  }
  function setMode(value) {
    const input = document.querySelector(
      `input[name="u-mode"][value="${value}"]`,
    );
    (input || document.querySelector('input[name="u-mode"]')).checked = true;
  }
  function modeFields() {
    const mode = currentMode();
    $("u-raw_dir-row").hidden = mode === "discovery_only";
    $("u-raw_file-row").hidden = mode !== "discovery_only";
    $("u-discovery_batch_index-row").hidden = mode !== "align_only";
    $("sample-section").hidden = mode === "discovery_only";
    $("istd-path-field").parentElement.hidden = mode === "discovery_only";
    checklist("untargeted");
  }
  function untargetedValues() {
    const values = {
      ...state.config.untargeted,
      mode: currentMode(),
      preset: $("u-preset").value,
      sequence_source: $("sequence-source").value,
      overrides: {},
    };
    for (const [key] of pathFields) values[key] = $(`u-${key}`).value;
    values.targets_csv = $("u-targets_csv").value;
    for (const control of state.method?.tuning_controls || []) {
      const input = $(`tune-${control.key}`);
      if (["rt_min", "rt_max"].includes(control.key) &&
          (!$("u-rt-crop").checked || input.value.trim() === "")) continue;
      values.overrides[control.key] =
        input.type === "number" ? Number(input.value) : input.value;
    }
    const rows = state.sampleSets.untargeted.rows;
    values.samples = rows.length ? rows : null;
    if (state.method) {
      values.preset_snapshot = structuredClone(state.method.draft);
      const snapshotOverrides = values.preset_snapshot.overrides;
      for (const key of ["rt_min", "rt_max"]) {
        delete snapshotOverrides[key];
        if (Object.hasOwn(values.overrides, key)) snapshotOverrides[key] = values.overrides[key];
      }
    }
    return values;
  }
  async function saveUntargeted(quiet = false) {
    const values = untargetedValues();
    await api("/api/untargeted/save", values);
    state.config.untargeted = values;
    dirty("untargeted", false);
    if (!quiet) done("Untargeted 批次設定已儲存。");
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
    const tuning = $("method-tuning");
    tuning.replaceChildren();
    const cropFields = el("div", undefined, "settings-grid span-all");
    cropFields.id = "method-rt-crop-fields";
    let hasCrop = false;
    for (const control of data.tuning_controls) {
      const value = Object.hasOwn(overrides, control.key)
        ? overrides[control.key] : data.effective_values[control.key];
      const node = field(control, value, "tune-");
      if (["rt_min", "rt_max"].includes(control.key)) {
        hasCrop ||= value !== null && value !== undefined && value !== "";
        const input = node.querySelector("input");
        input.dataset.rtBound = "true";
        input.placeholder = control.key === "rt_min" ? "RAW 採集起點（自動）" : "RAW 採集終點（自動）";
        cropFields.append(node);
      } else tuning.append(node);
    }
    const cropLabel = el("label", undefined, "field checkbox span-all");
    const crop = el("input");
    crop.type = "checkbox";
    crop.id = "u-rt-crop";
    crop.dataset.analysisIdle = "";
    crop.checked = hasCrop;
    cropLabel.append(crop, el("span", "限制分析 RT 範圍（選用）"));
    const cropNote = el("p", "預設分析完整 RAW 採集範圍（自動）。需要裁切時才啟用；任一端留空會使用該 RAW 的採集起點／終點。", "settings-notice span-all");
    cropFields.hidden = !hasCrop;
    crop.addEventListener("change", () => {
      cropFields.hidden = !crop.checked;
      lock();
      updateMethodPreview();
    });
    tuning.append(cropLabel, cropNote, cropFields);
    lock();
    updateMethodPreview();
    if (state.booted) await act(reloadIstd);
  }
  function updateMethodPreview() {
    if (!state.method) return;
    const preview = state.method.preview;
    const chips = preview.tags.map((tag) =>
      el("span", `${tag.name} · ${tag.neutral_loss_da} Da`, "chip accent"),
    );
    chips.push(el("span", preview.combine_mode, "chip"));
    const crop = $("u-rt-crop")?.checked;
    const lower = crop ? $("tune-rt_min")?.value.trim() : "";
    const upper = crop ? $("tune-rt_max")?.value.trim() : "";
    chips.push(el("span", !lower && !upper
      ? "完整 RAW 採集範圍（自動）"
      : `RT ${lower || "RAW 起點"}–${upper || "RAW 終點"} min`, "chip"));
    $("method-preview").replaceChildren(...chips);
    checklist("untargeted");
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
        input.setAttribute(
          "aria-label",
          `${{ enabled: "啟用", name: "標籤名稱", neutral_loss_da: "Neutral loss" }[key]}（第 ${index + 1} 列）`,
        );
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
      const remove = button(
        "",
        () => {
          state.draft.tags.splice(index, 1);
          renderMethodEditor();
        },
        "icon-button",
        "x",
      );
      remove.setAttribute("aria-label", `移除第 ${index + 1} 個標籤`);
      td.append(remove);
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
        done("方法已套用至本次執行。");
      } else done(`方法已儲存：${result.path}。目前執行方法維持原設定。`);
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
  function mountSampleSteps() {
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
  function renderRoleSummary(page) {
    const set = state.sampleSets[page];
    const counts = new Map();
    for (const sample of set.rows)
      counts.set(sample.sample_role, (counts.get(sample.sample_role) || 0) + 1);
    const excluded = set.rows.filter((row) => row.excluded).length;
    $(sampleId(page, "role-summary")).replaceChildren(
      ...(set.rows.length
        ? []
        : [el("span", "讀取 RAW 後可檢視樣本，並指定角色、組別與排除。", "subtle")]),
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
    renderEarlierNote(page);
    checklist(page);
  }
  function applySampleResult(page, result) {
    state.sampleSets[page] = {
      rows: result.samples,
      overview: result.overview || null,
      suggestions: result.group_suggestions || {},
      source: result.summary?.annotation_source || "",
      earlier: result.summary?.earlier_annotations || [],
    };
    renderSampleSummary(result.summary, sampleId(page, "samples"));
    renderRoleSummary(page);
    if (state.editing === page && $("sample-editor").open) renderSamples();
  }
  function clearSamples(page) {
    state.sampleSets[page] = emptySamples();
    $(sampleId(page, "samples-summary")).textContent = "";
    $(sampleId(page, "samples-conflicts")).hidden = true;
    renderRoleSummary(page);
    if (state.editing === page && $("sample-editor").open) renderSamples();
  }
  // Naming-pattern cards group the server's suggestions; a card is a group
  // name until applied, and applying writes that name to its members.
  function suggestionCards(set) {
    if (set.cards) return set.cards;
    const cards = new Map();
    for (const row of set.rows) {
      const label = set.suggestions[row.raw_stem];
      if (!label) continue;
      if (!cards.has(label)) cards.set(label, { label, members: [] });
      cards.get(label).members.push(row.raw_stem);
    }
    set.cards = [...cards.values()].sort(
      (a, b) => b.members.length - a.members.length,
    );
    return set.cards;
  }
  function cardRows(set, card) {
    const members = new Set(card.members);
    return set.rows.filter((row) => members.has(row.raw_stem));
  }
  // Rows a card wrote keep following it; rows labelled by hand stay as they are.
  function relabelCard(page, card, label) {
    const set = state.sampleSets[page];
    let changed = false;
    for (const row of cardRows(set, card))
      if (row.group.trim() === card.label) {
        row.group = label;
        changed = true;
      }
    return changed;
  }
  async function renameCard(page, card, label) {
    const set = state.sampleSets[page];
    if (!label || label === card.label) return renderGroupCards(page);
    const changed = relabelCard(page, card, label);
    card.label = label;
    const same = set.cards.find((other) => other !== card && other.label === label);
    if (same) {
      same.members.push(...card.members);
      set.cards = set.cards.filter((other) => other !== card);
    }
    if (changed) await persistSamples(page);
    else renderGroupCards(page);
  }
  async function mergeCard(page, card, target) {
    const set = state.sampleSets[page];
    const changed = relabelCard(page, card, target.label);
    target.members.push(...card.members);
    set.cards = set.cards.filter((other) => other !== card);
    if (changed) await persistSamples(page);
    else renderGroupCards(page);
  }
  async function applyCard(page, card, overwrite) {
    const set = state.sampleSets[page];
    for (const row of cardRows(set, card))
      if (overwrite || !row.group.trim()) row.group = card.label;
    await persistSamples(page);
  }
  function renderGroupCards(page) {
    const set = state.sampleSets[page];
    const box = $(sampleId(page, "group-cards"));
    const cards = set.rows.length ? suggestionCards(set) : [];
    box.hidden = !cards.length;
    if (!cards.length) return box.replaceChildren();
    const list = el("div", undefined, "group-card-list");
    for (const card of cards) {
      const rows = cardRows(set, card);
      const open = rows.filter((row) => !row.group.trim()).length;
      const applied = rows.filter((row) => row.group.trim() === card.label).length;
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
      const apply = button(`套用到 ${open} 個`, () => applyCard(page, card, false));
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
        overwrite.setAttribute("aria-label", `以 ${card.label} 覆蓋已標組別的樣本`);
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
      list.append(node);
    }
    box.replaceChildren(
      el(
        "p",
        "依命名樣式建議的組別：確認名稱後套用。已有組別的樣本只在按「覆蓋」時才會改。",
        "subtle",
      ),
      list,
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
    const set = state.sampleSets[state.editing];
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
      help: "選先前匯出的樣品資訊，或工作區中已確認的批次；依樣品名稱對應到這批 RAW。",
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
      ui.toast("請先讀取 RAW 樣本。");
      return;
    }
    state.annotation = { page, mode, preview: null };
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
    const { page } = state.annotation;
    const raw_stems = state.sampleSets[page].rows.map((row) => row.raw_stem);
    const values = {
      raw_stems,
      path: $("annotation-path").value.trim(),
      text: state.annotation.mode === "table" ? $("annotation-text").value : "",
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
    state.annotation.preview = result;
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
            row.excluded ? `排除${row.exclusion_reason ? `：${row.exclusion_reason}` : ""}` : "—",
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
    const { page, preview } = state.annotation;
    const overwrite = $("annotation-overwrite").checked;
    const byStem = new Map(state.sampleSets[page].rows.map((row) => [row.raw_stem, row]));
    for (const match of preview.matched) {
      const row = byStem.get(match.raw_stem);
      if (!row) continue;
      if (match.group && (overwrite || !row.group.trim())) row.group = match.group;
      if (match.excluded) {
        row.excluded = true;
        row.exclusion_reason = match.exclusion_reason;
      }
    }
    $("annotation-dialog").close();
    await persistSamples(page);
    done(`已套用 ${preview.matched.length} 個樣本的組別與排除。`);
  }
  function renderEarlierNote(page) {
    const set = state.sampleSets[page];
    const note = $(sampleId(page, "samples-earlier"));
    const earlier = set.source !== "confirmed" ? set.earlier[0] : null;
    note.hidden = !earlier;
    if (!earlier) return note.replaceChildren();
    note.replaceChildren(
      ui.icon("info"),
      el(
        "span",
        `這個資料夾還沒有確認過的樣品資訊，但找到一份先前確認的（${earlier.matched}/${earlier.total} 個 RAW 對得上），可能是資料夾搬過或改過名。`,
      ),
      button("預覽並沿用", () => openAnnotations(page, "carry", earlier.path), "ghost"),
    );
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
    const page = state.editing;
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
        row.sample_role = select.value;
        act(() => persistSamples(page));
      });
      tr.append(sampleCell(select));
      const group = el("input");
      group.value = row.group || "";
      group.setAttribute("list", "sample-group-options");
      group.setAttribute("aria-label", `${row.raw_stem} 組別`);
      group.placeholder = row.sample_role === "study_sample" ? "Other" : "";
      group.addEventListener("change", () => {
        row.group = group.value.trim();
        act(() => persistSamples(page));
      });
      const groupCell = el("div", undefined, "group-cell");
      groupCell.append(
        groupSwatch(set.overview?.row_groups?.[row.raw_stem]?.color),
        group,
      );
      tr.append(sampleCell(groupCell));
      const excluded = el("input");
      excluded.type = "checkbox";
      excluded.checked = Boolean(row.excluded);
      excluded.setAttribute("aria-label", `${row.raw_stem} 排除`);
      const reason = el("input");
      reason.value = row.exclusion_reason || "";
      reason.disabled = !row.excluded;
      reason.placeholder = row.excluded ? "選填" : "";
      reason.setAttribute("aria-label", `${row.raw_stem} 排除原因`);
      excluded.addEventListener("change", () => {
        row.excluded = excluded.checked;
        if (!row.excluded) row.exclusion_reason = "";
        renderSamples();
        act(() => persistSamples(page));
      });
      reason.addEventListener("change", () => {
        row.exclusion_reason = reason.value.trim();
        act(() => persistSamples(page));
      });
      tr.append(sampleCell(excluded), sampleCell(reason));
      body.append(tr);
    }
    if (!set.rows.length) {
      const tr = el("tr");
      const td = el("td", "尚未讀取樣本。關閉此視窗後按「讀取 RAW 樣本」。");
      td.colSpan = 9;
      td.className = "subtle";
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
    return state.sampleSets[state.editing].rows.filter((row) =>
      selected.has(row.raw_stem),
    );
  }
  function applyToSelected(change) {
    const rows = selectedSamples();
    if (!rows.length) return;
    rows.forEach(change);
    renderSamples();
    act(() => persistSamples(state.editing));
  }
  // Edits confirm the folder's sample metadata at once; both flows read it.
  async function persistSamples(page) {
    const set = state.sampleSets[page];
    if (!set.rows.length) return;
    const rawDir = $(samplePages[page].rawDir).value;
    const saved = await api("/api/samples/save", {
      raw_dir: rawDir,
      samples: set.rows,
    });
    set.overview = saved.overview;
    set.source = "confirmed";
    // The other flow on the same folder shows the same confirmed rows.
    for (const [other, spec] of Object.entries(samplePages))
      if (other !== page && $(spec.rawDir).value === rawDir) {
        state.sampleSets[other] = set;
        renderRoleSummary(other);
      }
    renderRoleSummary(page);
    if (state.editing === page && $("sample-editor").open) renderSamples();
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
  async function startJob(kind, values) {
    if (isBusy()) throw new Error("請等待目前工作完成或先停止。");
    if (kind === "untargeted") await saveUntargeted(true);
    try {
      state.analysis = await api("/api/analysis/start", { kind, values });
    } catch (error) {
      if (
        kind === "targeted" &&
        values.targets.length === 0 &&
        error.message.includes("at least one target row is required")
      ) {
        throw new Error(
          `請先在 Targeted 清單新增至少一個 target：按「檢視與編輯」新增，或匯入目標清單。\n詳細資訊：${error.message}`,
        );
      }
      throw error;
    }
    if (kind === "targeted") {
      state.config.settings = { ...values.settings };
      state.config.targeted_method = await api("/api/targeted/method", {});
      setMethodState(state.config.targeted_method);
      dirty("targeted", false);
    }
    state.seenJob = null;
    renderJob();
    status("工作已開始。關閉分頁不會停止分析。");
  }
  function renderActivity() {
    const job = state.analysis.job;
    const pill = $("activity");
    const page = pageOf(job);
    const recentlyFinished =
      state.seenAt && Date.now() - state.seenAt < 20000;
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
  function renderJob() {
    const job = state.analysis.job;
    const sampleSummary = job?.kind === "targeted"
      ? job.sample_summary
      : state.analysis.results.targeted?.sample_summary;
    $("target-samples-review").hidden = !sampleSummary;
    if (sampleSummary) renderSampleSummary(sampleSummary, "target-samples");
    const card = $("analysis-job");
    card.hidden = !job || pageOf(job) !== state.view;
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
    const progressText = job.total
      ? `${job.completed} / ${job.total} · `
      : "";
    $("analysis-job-time").textContent =
      `${progressText}${isRunning() ? "已經過" : "耗時"} ${ui.formatDuration(job.elapsed_seconds)}`;
    const analysisDone =
      job.status === "succeeded" && ["targeted", "untargeted"].includes(job.kind);
    $("analysis-job-output").hidden = !job.output_dir || analysisDone;
    $("analysis-job-output").textContent = job.output_dir || "";
    if (job.total > 0) {
      $("analysis-progress").max = job.total;
      $("analysis-progress").value = job.completed;
    } else $("analysis-progress").removeAttribute("value");
    $("analysis-stop").hidden = !isRunning();
    $("analysis-stop").disabled =
      job.status === "cancelling" || !job.cancellable;
    $("analysis-job-outputs").replaceChildren();
    if (analysisDone)
      $("analysis-job-outputs").append(
        button(
          "查看結果",
          () =>
            $(`${job.kind === "targeted" ? "target" : "untargeted"}-result`)
              .scrollIntoView({ behavior: "smooth", block: "start" }),
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
    state.seenJob = job.job_id;
    if (!state.booted) {
      renderResults();
      return;
    }
    state.seenAt = Date.now();
    if (job.status === "succeeded") {
      if (job.kind === "samples") {
        const result = state.analysis.results.samples;
        const page = result.page || "untargeted";
        applySampleResult(page, result);
        if (page === "untargeted") dirty("untargeted");
        done(`已讀取 ${result.samples.length} 個樣本。`);
      } else if (job.kind === "quality")
        done(
          `Feature Quality 匯出完成：${state.analysis.results.quality.output_dir}`,
        );
      else if (job.kind === "inspector") {
        done(`Inspector 匯出完成：${state.analysis.results.inspector.output}`);
        const artifact = state.analysis.results.inspector.artifacts?.[0];
        if (artifact) showArtifact(artifact);
      } else {
        dirty(job.kind, false);
        done(`${jobNames[job.kind]}完成，結果已列在下方。`);
      }
    } else if (card.hidden)
      status(
        `${jobNames[job.kind]}${jobLabels[job.status]}：${job.error || "先前的結果仍可檢閱。"}`,
        job.status === "failed",
      );
    renderResults();
  }
  function artifactLink(artifact, label = artifact.name) {
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
  function showArtifact(artifact) {
    $("artifact-title").textContent = artifact.name;
    $("artifact-frame").src = artifact.url;
    $("artifact-open").href = artifact.url;
    $("artifact-dialog").showModal();
  }
  function formatNumber(value) {
    const number = Number(value);
    if (value === null || value === undefined || value === "" || !Number.isFinite(number))
      return value;
    return number.toLocaleString("zh-TW", {
      maximumFractionDigits: Math.abs(number) >= 100 ? 0 : 3,
    });
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
    const head = el("div", undefined, "result-head");
    const title = el("h2");
    title.append(
      ui.icon("check-circle"),
      `${kind === "targeted" ? "Targeted" : "Untargeted"} 分析結果`,
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
      const label = artifact.path === result.excel_path ? "下載結果 XLSX"
        : artifact.path === result.matrix_tsv ? "下載矩陣 TSV" : artifact.name;
      artifacts.append(artifactLink(artifact, label));
    }
    return { container, artifacts };
  }
  function renderResults() {
    const targeted = state.analysis.results.targeted;
    if (targeted) {
      const { container, artifacts } = resultBase("targeted", targeted);
      const metrics = el("div", undefined, "result-metrics");
      metric(metrics, targeted.total_files, "RAW 檔案");
      metric(metrics, targeted.targets?.length, "目標");
      container.append(metrics);
      if (targeted.excel_path) container.append(button("在結果檢閱開啟", () => openReview(targeted.excel_path, targeted.review_paths), "primary", "review"));
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
      container.append(el("p", "樣本量值與需檢閱項目集中在結果檢閱；XLSX 保留同一份結果。", "subtle"));
    }
    const result = state.analysis.results.untargeted;
    if (result) {
      const { container, artifacts } = resultBase("untargeted", result);
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
          button("在結果檢閱開啟", () => openReview(evidence, result.review_paths), "primary", "review"),
        );
        actions.append(
          button(
            "自訂 Inspector 匯出",
            () => openInspectorExport(evidence),
            "",
            "download",
          ),
        );
      } else
        container.append(
          el(
            "p",
            "Discovery-only 會輸出候選與檢閱表；跨樣本檢閱需完成 Alignment。",
            "subtle",
          ),
        );
      if (result.matrix_tsv && result.alignment_outputs?.matrix_identity_tsv)
        actions.append(
          button(
            "Feature Quality 篩選匯出",
            () => openQuality(result),
            "",
            "sliders",
          ),
        );
      container.append(actions, artifacts);
    }
  }
  function openReview(source, reviewPaths) {
    selectView("review");
    document.dispatchEvent(
      new CustomEvent("xic:open-review", { detail: { source, reviewPaths } }),
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
  function requestPath(title, value = "", label = "完整路徑") {
    $("path-dialog-title").textContent = title;
    $("path-dialog-label").textContent = label;
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
  function renderIstd(result) {
    const selection = result.selection;
    $("istd-summary").textContent = result.message ||
      `${selection.targets.length} 個適用內標；${selection.method_skipped.length} 個不適用此方法；略過 ${selection.skipped_count} 個一般目標。${selection.targets.length ? "" : " 本次不使用內標參考。"}`;
    const preview = $("istd-preview");
    preview.replaceChildren();
    if (!selection) return;
    const table = el("table");
    const head = el("tr");
    for (const label of ["內標名稱", "CID-NL tag", "m/z", "預期 RT (min)", "本方法使用狀態"]) head.append(el("th", label));
    table.append(head);
    for (const [targets, status] of [[selection.targets, "適用"], [selection.method_skipped, "不適用此方法（略過）"]]) {
      for (const target of targets) {
        const row = el("tr");
        for (const value of [target.label, target.cid_nl_tag, target.mz, target.expected_rt_min, status]) row.append(el("td", String(value ?? "")));
        table.append(row);
      }
    }
    preview.append(table);
  }
  async function reloadIstd() {
    if (!state.method) {
      renderIstd({ message: "請先選擇有效方法，才能判定適用內標。" });
      return;
    }
    $("istd-preview").replaceChildren();
    $("istd-summary").textContent = "正在讀取清單…";
    try {
      renderIstd(await api("/api/istd", { path: $("u-targets_csv").value, preset_snapshot: state.method.draft }));
    } catch (error) {
      $("istd-summary").textContent = "清單未載入，請修正檔案或清空路徑停用。";
      throw error;
    }
  }
  async function importIstdFiles(files) {
    if (isBusy()) return;
    const message = $("istd-import-status");
    message.className = "";
    try {
      state.importing = true;
      lock();
      message.textContent = "正在讀取檔案…";
      const upload = await readListFile(files);
      if (!state.method) throw new Error("請先選擇有效方法，再匯入內標清單。");
      const result = await api("/api/istd/upload", { ...upload, preset_snapshot: state.method.draft });
      $("u-targets_csv").value = result.path;
      renderIstd(result);
      dirty("untargeted");
      message.textContent = `已匯入 ${upload.name}；儲存或開始分析後套用。`;
    } catch (error) {
      message.textContent = error.message;
      message.className = "error";
    } finally {
      state.importing = false;
      lock();
    }
  }
  async function copyIstd() {
    const destination = await requestPath(
      "另存可編輯內標副本",
      `${state.config.config_dir}/my-targets.${$("u-targets_csv").value.split(".").pop() || "xlsx"}`,
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
  async function stopJob() {
    if (!isRunning() || !state.analysis.job.cancellable) return;
    const accepted = await ui.confirm({
      title: `停止${jobNames[state.analysis.job.kind]}？`,
      message:
        "已發布的結果會保留；正在執行的步驟會先完成清理再停止。先前的可用結果不受影響。",
      accept: "停止工作",
      danger: true,
    });
    if (!accepted) return;
    state.analysis = await api("/api/analysis/cancel", {
      job_id: state.analysis.job.job_id,
    });
    renderJob();
  }
  function applyThemeButton() {
    const dark = document.documentElement.dataset.theme === "dark";
    const toggle = $("theme-toggle");
    toggle.replaceChildren(ui.icon(dark ? "sun" : "moon"));
    toggle.title = dark ? "切換淺色模式" : "切換深色模式";
    toggle.setAttribute("aria-label", toggle.title);
  }
  async function shutdown() {
    if (isBusy())
      throw new Error("請先停止或等待目前工作完成，再結束工作區。");
    const accepted = await ui.confirm({
      title: "結束工作區？",
      message:
        "本機工作區會停止，這個分頁之後無法再操作。已儲存的設定與結果都會保留。",
      accept: "結束工作區",
      danger: true,
    });
    if (!accepted) return;
    await api("/api/shutdown", {});
    state.closed = true;
    const screen = el("div", undefined, "closed-screen");
    const inner = el("div");
    const glyph = el("span", undefined, "empty-glyph");
    glyph.append(ui.icon("power"));
    inner.append(
      glyph,
      el("h1", "工作區已結束"),
      el("p", "可以關閉這個分頁。需要時重新啟動 XIC Extractor。", "subtle"),
    );
    screen.append(inner);
    document.body.append(screen);
  }
  function bind() {
    document.addEventListener("input", (event) => {
      if (!["t-dll_dir", "u-dll_dir", "dll-dir"].includes(event.target.id)) return;
      const value = event.target.value;
      state.config.settings.dll_dir = value;
      state.config.untargeted.dll_dir = value;
      for (const id of ["t-dll_dir", "u-dll_dir", "dll-dir"]) {
        if ($(id)) $(id).value = value;
      }
      dirty("targeted");
      dirty("untargeted");
    });
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
    $("targeted-form").addEventListener("input", () => dirty("targeted"));
    $("untargeted-form").addEventListener("input", () => dirty("untargeted"));
    $("targeted-form").addEventListener("submit", (event) => {
      event.preventDefault();
      act(() =>
        startJob("targeted", {
          settings: settingsValues(),
          targets: state.targets,
          output_dir: $("t-output_dir").value,
        }),
      );
    });
    $("untargeted-form").addEventListener("submit", (event) => {
      event.preventDefault();
      act(() => startJob("untargeted", untargetedValues()));
    });
    $("target-save").onclick = () => act(() => saveTargeted());
    $("target-method-load").onclick = () => act(() => loadTargetedMethod($("target-method-select").value));
    $("target-method-save").onclick = () => act(async () => {
      const name = await requestPath("另存目標清單", "", "清單名稱（不含 .xlsx）");
      if (!name) return;
      const result = await api("/api/targeted/method/save", { name, settings: settingsValues(), targets: state.targets });
      state.config.targeted_methods = result.methods;
      renderTargetedMethods(result.methods, result.methods.find((method) => method.name === result.name)?.path);
      done(`已另存目標清單：${result.name}`);
    });
    $("untargeted-save").onclick = () => act(() => saveUntargeted());
    $("target-new").onclick = () => {
      $("t-data_dir").value = "";
      $("t-injection_order_source").value = "";
      state.config.settings.data_dir = "";
      state.config.settings.injection_order_source = "";
      clearSamples("targeted");
      $("target-result").hidden = true;
      dirty("targeted");
      focusField("t-data_dir");
    };
    $("targeted-data").addEventListener("input", (event) => {
      if (event.target.id === "t-data_dir" && event.target.value !== event.target.dataset.batchPath) {
        $("t-injection_order_source").value = "";
        event.target.dataset.batchPath = event.target.value;
        clearSamples("targeted");
      }
    });
    $("target-edit").onclick = () => {
      renderTargets();
      $("target-editor").showModal();
    };
    $("target-add").onclick = () => {
      state.targets.push({
        label: "",
        mz: "",
        expected_rt_min: "",
        cid_nl_tag: "dR",
        role: "analyte",
        istd_pair: "",
      });
      dirty("targeted");
      renderTargets();
      $("target-rows").lastElementChild?.querySelector("input")?.focus();
    };
    $("target-editor-save").onclick = () =>
      act(async () => {
        await saveTargeted();
        $("target-editor").close();
      });
    $("target-export").onclick = () => act(async () => {
      const path = await requestPath("匯出目標清單（.xlsx）");
      if (!path) return;
      const result = await api("/api/targets/export", { path, targets: state.targets });
      done(result.message);
    });
    bindListUpload("target", previewTargetFiles);
    bindListUpload("istd", importIstdFiles);
    $("target-import-preview").addEventListener("close", () => {
      if (pendingTargetImport) $("target-import-status").textContent = "已取消匯入，目前清單未變更。";
      pendingTargetImport = null;
    });
    $("target-import-confirm").onclick = () => {
      if (!pendingTargetImport || isBusy()) return;
      const result = pendingTargetImport;
      pendingTargetImport = null;
      state.targets = result.targets;
      if (result.kind === "method") applyTargetedDraft(result);
      $("target-method-select").value = "";
      targetCount();
      dirty("targeted");
      $("target-import-preview").close();
      $("target-import-status").textContent = `已匯入 ${state.targets.length} 個目標；儲存或開始分析後套用。`;
    };
    $("u-mode").addEventListener("change", modeFields);
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
        overrides: {},
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
    for (const [page, spec] of Object.entries(samplePages)) {
      $(sampleId(page, "samples-refresh")).onclick = () =>
        act(() => scanSamples(page));
      $(sampleId(page, "samples-import")).onclick = () =>
        act(() => scanSamples(page, true));
      $(sampleId(page, "samples-edit")).onclick = () => {
        state.editing = page;
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
      $(spec.sequence).addEventListener("change", () => clearSamples(page));
    }
    $("sample-filter").oninput = renderSamples;
    $("sample-select-all").onchange = () =>
      $("sample-rows")
        .querySelectorAll("input[type=checkbox]")
        .forEach((node) => (node.checked = $("sample-select-all").checked));
    roleOptions($("sample-bulk-role"), "study_sample");
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
      applyToSelected((row) => (row.sample_role = $("sample-bulk-role").value));
    $("sample-apply-group").onclick = () =>
      applyToSelected((row) => (row.group = $("sample-bulk-group").value.trim()));
    $("sample-exclude").onclick = () =>
      applyToSelected((row) => {
        row.excluded = true;
        row.exclusion_reason = $("sample-bulk-reason").value.trim();
      });
    $("sample-include").onclick = () =>
      applyToSelected((row) => {
        row.excluded = false;
        row.exclusion_reason = "";
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
          sampleRawDir = "";
          $("u-output_dir").value = "";
          $("sequence-source").value = "";
          clearSamples("untargeted");
          $("untargeted-result").hidden = true;
          if (id === "untargeted-clear") {
            $("u-targets_csv").value = "";
            await reloadIstd();
            await loadMethod("dna_dr");
          }
          dirty("untargeted");
        });
    let sampleRawDir = $("u-raw_dir").value;
    $("u-raw_dir").addEventListener("change", () => {
      const nextRawDir = $("u-raw_dir").value;
      if (nextRawDir === sampleRawDir) return;
      sampleRawDir = nextRawDir;
      $("sequence-source").value = "";
      state.config.untargeted.sequence_source = "";
      clearSamples("untargeted");
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
    $("sequence-source").addEventListener("change", () => dirty("untargeted"));
    $("analysis-stop").onclick = () => act(stopJob);
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
    $("artifact-dialog").addEventListener(
      "close",
      () => ($("artifact-frame").src = "about:blank"),
    );
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
    document.addEventListener("xic:review-busy", (event) => {
      state.reviewBusy = event.detail;
      lock();
    });
    document.addEventListener("keydown", (event) => {
      if (!(event.ctrlKey || event.metaKey) || document.querySelector("dialog[open]"))
        return;
      if (["1", "2", "3"].includes(event.key)) {
        event.preventDefault();
        selectView({ 1: "targeted", 2: "untargeted", 3: "review" }[event.key]);
      } else if (event.key === ".") {
        event.preventDefault();
        act(stopJob);
      } else if (event.key === "Enter" && state.view !== "review") {
        event.preventDefault();
        if (!isBusy()) $(`${state.view}-form`).requestSubmit();
      } else if (event.key.toLowerCase() === "s" && state.view !== "review") {
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
  function setOffline(offline) {
    document.body.classList.toggle("offline", offline);
    $("offline-banner").hidden = !offline;
  }
  try {
    const response = await fetch("/assets/analysis.html");
    if (!response.ok) throw new Error("分析頁面無法載入。");
    $("analysis-root").innerHTML = await response.text();
    mountSampleSteps();
    await api("/api/session");
    state.config = await api("/api/config");
    state.targets = state.config.targets;
    ui.setTheme(state.config.preferences.theme || "light");
    $("dll-dir").value = state.config.settings.dll_dir || "";
    applyThemeButton();
    renderSettings(state.config.settings);
    renderTargetedMethods(state.config.targeted_methods || []);
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
      "內標清單 XLSX／CSV／TSV（留空停用）",
      "targets",
      state.config.untargeted.targets_csv,
    );
    setMode(state.config.untargeted.mode);
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
    targetCount();
    renderRoleSummary("targeted");
    renderRoleSummary("untargeted");
    modeFields();
    state.analysis = state.config.analysis;
    const loadedSamples = state.analysis.results.samples;
    const loadedPage = loadedSamples?.page || "untargeted";
    const loadedSpec = samplePages[loadedPage];
    if (loadedSamples?.input_paths &&
        loadedSamples.input_paths.raw_dir === $(loadedSpec.rawDir).value &&
        loadedSamples.input_paths.dll_dir === $(loadedSpec.dll).value &&
        loadedSamples.input_paths.sequence_source === $(loadedSpec.sequence).value)
      applySampleResult(loadedPage, loadedSamples);
    renderResults();
    selectView(
      state.config.preferences.view ||
        (state.config.review_source ? "review" : "targeted"),
      false,
    );
    renderJob();
    state.booted = true;
    checklist("targeted");
    checklist("untargeted");
    document.body.classList.remove("booting");
    await act(reloadIstd);
    let tick = 0;
    let polling = false;
    setInterval(async () => {
      tick += 1;
      if (polling || state.closed || (state.offlineCount >= 3 && tick % 6)) return;
      // Slow responses must not fill the browser's connection pool with status
      // requests and starve the user's query, report, and curve requests.
      polling = true;
      try {
        state.analysis = await api("/api/analysis");
        state.offlineCount = 0;
        setOffline(false);
        renderJob();
      } catch (_) {
        state.offlineCount += 1;
        if (state.offlineCount >= 3) setOffline(true);
      } finally {
        polling = false;
      }
    }, 750);
  } catch (error) {
    document.body.classList.remove("booting");
    ui.toast(error.message, "error");
  }
})();
