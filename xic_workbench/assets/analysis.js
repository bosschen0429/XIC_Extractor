"use strict";
(async () => {
  const $ = (id) => document.getElementById(id);
  const ui = window.XicUi;
  const state = {
    config: null,
    targets: [],
    samples: [],
    method: null,
    draft: null,
    analysis: { job: null, results: {} },
    reviewBusy: false,
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
    ["dll_dir", "RawFileReader DLL", "folder"],
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
    return state.reviewBusy || isRunning();
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
    notes.append(el("p", `設定檔：${state.config.settings_source || "settings.csv"}`, "settings-source"));
    for (const warning of summary?.warnings || []) {
      notes.append(el("p", warning, "settings-warning"));
    }
    checklist("targeted");
  }
  function selectView(view, persist = true) {
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

  // Form fields
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
  function pathField(container, key, label, kind, value, prefix = "u-") {
    const wrap = el("div", undefined, "path-line");
    wrap.id = `${prefix}${key}-row`;
    const title = el("label", label);
    title.htmlFor = `${prefix}${key}`;
    const input = el("input", undefined, "path");
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

  // Readiness
  function checkItem(tone, label, detail, focus, mono = false) {
    return { tone, label, detail, focus, mono };
  }
  function pathItem(label, id, missing) {
    const value = $(id)?.value.trim() || "";
    // Example templates ship placeholder paths that are not real inputs.
    const usable =
      value && !value.startsWith("<") && !/[\\/]your[\\/]/i.test(value);
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
          "target-import-path",
        ),
      );
      items.push(pathItem("RawFileReader DLL", "t-dll_dir", "尚未設定 DLL 資料夾"));
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
      items.push(pathItem("輸出資料夾", "u-output_dir", "尚未選擇輸出位置"));
      items.push(pathItem("RawFileReader DLL", "u-dll_dir", "尚未設定 DLL 資料夾"));
      const preset = $("u-preset");
      items.push(
        checkItem(
          state.method ? "ok" : "todo",
          "分析方法",
          preset.selectedOptions[0]?.textContent || "尚未選擇方法",
          "u-preset",
        ),
      );
      if (mode !== "discovery_only")
        items.push(
          checkItem(
            "info",
            "樣本資訊（選用）",
            state.samples.length
              ? `${state.samples.length} 個樣本已讀取`
              : "尚未讀取；可在步驟 4 讀取並指定角色",
            "samples-refresh",
          ),
        );
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
      if (input) values[spec.key] =
        spec.kind === "checkbox" ? String(input.checked) : input.value;
    }
    return values;
  }
  function renderSettings(values) {
    const data = $("targeted-data");
    const method = $("targeted-settings");
    const execution = $("targeted-execution");
    for (const panel of [data, method, execution]) panel.replaceChildren();
    const note = el("div", undefined, "method-note");
    note.id = "target-method-note";
    method.append(el("p", "目標清單決定名稱、m/z、預期 RT、CID-NL tag 與 ISTD 配對。平台共用候選與證據策略。", "settings-notice"), note);
    const panels = { batch: data, environment: execution };
    const groups = new Map();
    for (const spec of state.config.targeted_form.settings) {
      const panel = panels[spec.scope];
      if (!panel || spec.kind === "readonly" || spec.key === "signal_processing_method" || spec.key === "count_no_ms2_as_detected") continue;
      const groupKey = spec.scope;
      if (!groups.has(groupKey)) {
        const section = el("section", undefined, "settings-group");
        const scopeHeading = { batch: "批次輸入", environment: "本機環境" };
        section.append(el("h3", scopeHeading[spec.scope] || spec.group));
        const grid = el("div", undefined, "settings-grid");
        section.append(grid);
        panel.append(section);
        groups.set(groupKey, grid);
      }
      groups.get(groupKey).append(field(spec, values[spec.key], "t-"));
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
  async function saveTargeted(quiet = false) {
    const settings = settingsValues();
    const receipt = await api("/api/targeted/save", { settings, targets: state.targets });
    state.config.settings = settings;
    state.config.settings_source = "settings.csv";
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
      custom_roles: { ...state.config.untargeted.custom_roles },
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
    for (const sample of state.samples)
      values.custom_roles[sample.raw_stem] = sample.sample_role;
    values.samples = state.samples.length ? state.samples : null;
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
  function renderRoleSummary() {
    const counts = new Map();
    for (const sample of state.samples)
      counts.set(sample.sample_role, (counts.get(sample.sample_role) || 0) + 1);
    $("role-summary").replaceChildren(
      ...(state.samples.length
        ? []
        : [el("span", "讀取 RAW 後可檢視樣本並指定角色。", "subtle")]),
      ...roles
        .filter((role) => counts.has(role))
        .map((role) =>
          el(
            "span",
            `${roleLabels[role]} ${counts.get(role)}`,
            `chip${role === "study_sample" ? " accent" : ""}`,
          ),
        ),
    );
    $("samples-count").textContent = state.samples.length
      ? `${state.samples.length} 個樣本`
      : "尚未讀取樣本";
    checklist("untargeted");
  }
  function renderSamples() {
    renderRoleSummary();
    const filter = $("sample-filter").value.trim().toLowerCase();
    const body = $("sample-rows");
    body.replaceChildren();
    let shown = 0;
    for (const row of state.samples) {
      if (
        filter &&
        ![row.raw_stem, row.sample_name, row.batch_id]
          .join(" ")
          .toLowerCase()
          .includes(filter)
      )
        continue;
      shown += 1;
      const tr = el("tr");
      const selected = el("input");
      selected.type = "checkbox";
      selected.dataset.sample = row.raw_stem;
      selected.setAttribute("aria-label", `選取 ${row.raw_stem}`);
      const check = el("td");
      check.append(selected);
      tr.append(check);
      tr.append(el("td", row.raw_stem ?? "—", "mono"));
      tr.append(el("td", row.batch_id ?? "—"));
      tr.append(el("td", row.injection_order ?? "—", "num"));
      tr.append(el("td", row.sample_name ?? "—"));
      const select = el("select");
      select.setAttribute("aria-label", `${row.raw_stem} 角色`);
      roleOptions(select, row.sample_role);
      select.addEventListener("change", () => {
        row.sample_role = select.value;
        dirty("untargeted");
        renderRoleSummary();
      });
      const td = el("td");
      td.append(select);
      tr.append(td);
      body.append(tr);
    }
    if (!state.samples.length) {
      const tr = el("tr");
      const td = el("td", "尚未讀取樣本。關閉此視窗後按「讀取 RAW 樣本」。");
      td.colSpan = 6;
      td.className = "subtle";
      tr.append(td);
      body.append(tr);
    }
    $("sample-select-all").checked = false;
    $("sample-editor-count").textContent = filter
      ? `顯示 ${shown} ／ 共 ${state.samples.length} 個樣本`
      : `共 ${state.samples.length} 個樣本`;
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

  // Jobs
  async function startJob(kind, values) {
    if (isBusy()) throw new Error("請等待目前工作完成或先停止。");
    if (kind === "untargeted") await saveUntargeted(true);
    state.analysis = await api("/api/analysis/start", { kind, values });
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
    const page = jobPage[job?.kind] || "untargeted";
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
    const card = $("analysis-job");
    card.hidden = !job || (jobPage[job.kind] || "untargeted") !== state.view;
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
      if (job.kind === "samples" && job.status === "succeeded") {
        state.samples = state.analysis.results.samples.samples;
        renderSamples();
      }
      return;
    }
    state.seenAt = Date.now();
    if (job.status === "succeeded") {
      if (job.kind === "samples") {
        const result = state.analysis.results.samples;
        state.samples = result.samples;
        renderSamples();
        $("samples-summary").textContent =
          `RAW ${result.summary.raw_count} 個 · 上機資訊配對 ${result.summary.matched_count} 個${result.summary.unmatched_sequence_names?.length ? ` · ${result.summary.unmatched_sequence_names.length} 筆上機資訊未配對` : ""}`;
        dirty("untargeted");
        done(`已讀取 ${state.samples.length} 個樣本。`);
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
  function artifactLink(artifact) {
    const link = el("a");
    const html = artifact.name.toLowerCase().endsWith(".html");
    link.append(ui.icon(html ? "external" : "file"), artifact.name);
    link.href = artifact.url;
    link.download = artifact.name;
    link.title = html ? "在工作區開啟報告" : "下載檔案";
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
    for (const artifact of result.artifacts || [])
      artifacts.append(artifactLink(artifact));
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
      if (targeted.excel_path) container.append(button("在結果檢閱開啟", () => openReview(targeted.excel_path), "primary", "review"));
      container.append(artifacts);
      const warnings = el("div");
      for (const warning of targeted.istd_warnings || []) {
        const line = el("p", undefined, "result-warning");
        line.append(
          ui.icon("alert"),
          `ISTD ${warning.label}：只有 ${warning.detected} / ${warning.total} 個樣本偵測到。`,
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
          button("在結果檢閱開啟", () => openReview(evidence), "primary", "review"),
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
        }),
      );
    });
    $("untargeted-form").addEventListener("submit", (event) => {
      event.preventDefault();
      act(() => startJob("untargeted", untargetedValues()));
    });
    $("target-save").onclick = () => act(() => saveTargeted());
    $("target-method-load").onclick = () => act(() => loadTargetedMethod($("target-method-select").value));
    $("target-method-import").onclick = () => act(async () => {
      const path = await requestPath("載入清單資料夾（settings.csv 與 targets.csv）");
      if (!path) return;
      await loadTargetedMethod(path);
      $("target-method-select").value = "";
    });
    $("target-method-save").onclick = () => act(async () => {
      const name = await requestPath("另存目標清單", "", "清單名稱");
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
      $("target-result").hidden = true;
      dirty("targeted");
      focusField("t-data_dir");
    };
    $("targeted-data").addEventListener("input", (event) => {
      if (event.target.id === "t-data_dir" && event.target.value !== event.target.dataset.batchPath) {
        $("t-injection_order_source").value = "";
        event.target.dataset.batchPath = event.target.value;
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
    $("target-import").onclick = () =>
      act(async () => {
        const result = await api("/api/targets/import", {
          path: $("target-import-path").value,
        });
        state.targets = result.targets;
        targetCount();
        dirty("targeted");
        done(`已匯入 ${state.targets.length} 個目標；儲存或開始分析後套用。`);
      });
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
    $("samples-refresh").onclick = () => act(scanSamples);
    $("samples-import").onclick = () => act(scanSamples);
    $("samples-edit").onclick = () => {
      renderSamples();
      $("sample-editor").showModal();
    };
    $("sample-filter").oninput = renderSamples;
    $("sample-select-all").onchange = () =>
      $("sample-rows")
        .querySelectorAll("input[type=checkbox]")
        .forEach((node) => (node.checked = $("sample-select-all").checked));
    roleOptions($("sample-bulk-role"), "study_sample");
    $("sample-apply-role").onclick = () => {
      const selected = new Set(
        [...$("sample-rows").querySelectorAll("input:checked")].map(
          (node) => node.dataset.sample,
        ),
      );
      if (!selected.size) {
        ui.toast("請先勾選要套用角色的樣本。");
        return;
      }
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
          done(`樣本資料已匯出：${path}`);
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
          sampleRawDir = "";
          $("u-output_dir").value = "";
          $("sequence-source").value = "";
          state.samples = [];
          state.config.untargeted.custom_roles = {};
          renderSamples();
          $("samples-summary").textContent = "";
          $("untargeted-result").hidden = true;
          if (id === "untargeted-clear") {
            $("u-targets_csv").value = "";
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
      state.config.untargeted.custom_roles = {};
      state.samples = [];
      $("samples-summary").textContent = "";
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
      "內標 CSV（留空停用）",
      "csv",
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
    renderRoleSummary();
    modeFields();
    state.analysis = state.config.analysis;
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
    setInterval(async () => {
      tick += 1;
      if (state.closed || (state.offlineCount >= 3 && tick % 6)) return;
      try {
        state.analysis = await api("/api/analysis");
        state.offlineCount = 0;
        setOffline(false);
        renderJob();
      } catch (_) {
        state.offlineCount += 1;
        if (state.offlineCount >= 3) setOffline(true);
      }
    }, 750);
  } catch (error) {
    document.body.classList.remove("booting");
    ui.toast(error.message, "error");
  }
})();
