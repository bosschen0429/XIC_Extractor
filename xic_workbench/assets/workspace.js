"use strict";
// Shared UI primitives for the workspace shell. analysis.js loads after this.
window.XicUi = (() => {
  const $ = (id) => document.getElementById(id);
  function icon(name, cls = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", `icon${cls ? ` ${cls}` : ""}`);
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }
  function toast(message, kind = "info", timeout) {
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.setAttribute("role", kind === "error" ? "alert" : "status");
    const text = document.createElement("p");
    text.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "關閉訊息");
    close.append(icon("x"));
    close.addEventListener("click", () => node.remove());
    node.append(
      icon(kind === "error" ? "alert" : kind === "ok" ? "check-circle" : "info"),
      text,
      close,
    );
    const host = $("toasts");
    host.append(node);
    while (host.children.length > 4) host.firstElementChild.remove();
    const delay = timeout ?? (kind === "error" ? 0 : 5000);
    if (delay) setTimeout(() => node.remove(), delay);
    return node;
  }
  function confirm({ title, message, accept = "確定", danger = false }) {
    const dialog = $("confirm-dialog");
    $("confirm-title").textContent = title;
    $("confirm-message").textContent = message;
    $("confirm-accept").textContent = accept;
    $("confirm-accept").className = danger ? "danger-button" : "primary";
    dialog.returnValue = "cancel";
    dialog.showModal();
    (danger
      ? dialog.querySelector('button[value="cancel"]')
      : $("confirm-accept")
    ).focus();
    return new Promise((resolve) =>
      dialog.addEventListener(
        "close",
        () => resolve(dialog.returnValue === "ok"),
        { once: true },
      ),
    );
  }
  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("xic-theme", theme);
    } catch (_) {}
    for (const frame of document.querySelectorAll("iframe")) {
      try {
        if (frame.contentDocument?.documentElement)
          frame.contentDocument.documentElement.dataset.theme = theme;
      } catch (_) {}
    }
  }
  function formatDuration(seconds) {
    const total = Math.max(0, Math.floor(seconds || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return `${h} 小時 ${m} 分`;
    if (m) return `${m} 分 ${String(s).padStart(2, "0")} 秒`;
    return `${s} 秒`;
  }
  return { icon, toast, confirm, setTheme, formatDuration };
})();

(() => {
  const $ = (id) => document.getElementById(id);
  const { icon } = window.XicUi;
  const state = {
    page: null,
    item: null,
    recorded: null,
    curves: null,
    curveLabels: [],
    tab: "recorded",
    busy: false,
    job: null,
    samples: new Set(),
    filters: {},
    browser: null,
    analysisBusy: false,
  };
  const fmt = (value, digits) =>
    Number.isFinite(value) ? value.toFixed(digits) : "—";
  const count = (value) => Number(value).toLocaleString();
  // Intensities span decades, so they read as Excel-style scientific notation.
  const scientific = (value) => {
    const [mantissa, exponent] = value.toExponential(2).split("e");
    const power = Number(exponent);
    return `${mantissa}E${power < 0 ? "-" : "+"}${String(Math.abs(power)).padStart(2, "0")}`;
  };
  const badgeTone = {
    需留意: "warn",
    診斷曲線: "accent",
  };
  function status(message, kind = "") {
    $("status").textContent = message;
    $("status").className = `inline-status ${kind}`;
  }
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
  function locked() {
    return state.busy || !!state.job;
  }
  function controls() {
    const busy = locked();
    document
      .querySelectorAll("[data-idle]")
      .forEach((element) => (element.disabled = busy));
    document
      .querySelectorAll(".item")
      .forEach((element) => (element.disabled = busy));
    document
      .querySelectorAll("#sample-list input")
      .forEach(
        (element) =>
          (element.disabled = busy || element.dataset.available !== "true"),
      );
    $("load-curves").disabled =
      busy || state.analysisBusy || !state.item || state.samples.size === 0;
    const supported = document.querySelectorAll(
      '#sample-list input[data-available="true"]',
    ).length;
    $("acquisition-hint").textContent = !state.item
      ? ""
      : !supported
        ? "這個項目沒有任何樣本保留觀測座標，無法自動取得 XIC。"
        : state.analysisBusy
          ? "分析執行中；完成後才能讀取 RAW。"
          : state.samples.size === 0
            ? "勾選至少一個樣本後即可載入。"
            : "";
    $("sample-count").textContent = `已選 ${state.samples.size} 個樣本`;
    $("previous").disabled = busy || !state.page || state.page.offset === 0;
    $("next").disabled =
      busy ||
      !state.page ||
      state.page.offset + state.page.limit >= state.page.matched_count;
    $("cancel").hidden = !state.job;
    $("cancel").disabled = state.job?.status === "cancelling";
    $("tab-curves").disabled = !state.curves;
    document.dispatchEvent(
      new CustomEvent("xic:review-busy", { detail: !!state.job }),
    );
  }
  async function action(work, message) {
    if (locked()) return;
    state.busy = true;
    controls();
    if (message) status(message, "working");
    try {
      await work();
    } catch (error) {
      status(error.message, "error");
    } finally {
      state.busy = false;
      controls();
    }
  }
  function clearSelection() {
    state.item = null;
    state.recorded = null;
    state.curves = null;
    state.curveLabels = [];
    $("selection").hidden = true;
    $("selection-empty").hidden = false;
    $("inspector").src = "about:blank";
    $("job-progress").hidden = true;
  }
  function showTargetedOverview() {
    $("targeted-overview").hidden = false;
    $("targeted-review-nav").hidden = true;
    $("review").hidden = true;
  }
  async function openTargetedResults(target = "", attentionOnly = false) {
    $("query-form").reset();
    $("target-id").value = target;
    $("target-attention").checked = attentionOnly;
    await query(0, queryFields());
    $("targeted-review-nav").scrollIntoView({ block: "start", behavior: "smooth" });
  }
  function renderTargetedOverview(summary) {
    const host = $("targeted-overview");
    host.replaceChildren();
    if (!summary) return;
    const node = (tag, text, className = "") => {
      const element = document.createElement(tag);
      if (text !== undefined) element.textContent = text;
      element.className = className;
      return element;
    };
    const resultButton = (text, target = "", attentionOnly = false, className = "") => {
      const button = node("button", text, className);
      button.type = "button";
      button.dataset.idle = "";
      button.addEventListener("click", () => action(
        () => openTargetedResults(target, attentionOnly), "正在開啟樣本結果…",
      ));
      return button;
    };
    const heading = node("div", undefined, "overview-heading");
    heading.append(node("span", "整批結果總覽", "eyebrow"));
    const conclusion = !summary.result_count ? "尚無已保存結果"
      : summary.focus_count ? `先看 ${count(summary.focus_count)} 筆與多數不同的結果`
        : "各目標結果一致，沒有需要先看的少數結果";
    heading.append(node("h2", conclusion));
    const scope = `${count(summary.sample_count)} 個樣本 · ${count(summary.targets.length)} 個目標`
      + (summary.focus_count ? ` · 少數結果分布在 ${count(summary.focus_target_count)} 個目標` : "");
    heading.append(node("p", scope, "subtle"));
    host.append(heading);
    const metrics = node("div", undefined, "overview-metrics");
    const ratio = (counted, total) => `${count(counted)} / ${count(total)}`;
    for (const [value, label] of [
      [count(summary.focus_count), "需先看"],
      [ratio(summary.analyte_counted, summary.analyte_total), "Analyte 計入"],
      [ratio(summary.istd_counted, summary.istd_total), "ISTD 計入"],
    ]) {
      if (label === "ISTD 計入" && !summary.istd_total) continue;
      const metric = node("div");
      metric.append(node("strong", value), node("span", label));
      metrics.append(metric);
    }
    host.append(metrics);
    if (summary.count_unknown) host.append(node("p", `${count(summary.count_unknown)} 筆未記錄偵測判定，未推測為未偵測。`, "overview-unknown"));
    const actions = node("div", undefined, "overview-actions");
    if (summary.focus_count) actions.append(resultButton(`先看這 ${count(summary.focus_count)} 筆 →`, "", true, "primary"));
    actions.append(resultButton("查看所有樣本結果 →"));
    host.append(actions);
    host.append(node("h3", "各目標結果"));
    const table = node("table", undefined, "overview-table");
    const head = node("thead");
    const header = node("tr");
    for (const text of ["目標", "類型", "計入 / 全部", "需先看", "要確認"]) header.append(node("th", text));
    head.append(header);
    const body = node("tbody");
    // Counts sit in their own columns; this column says only what to check.
    const focusText = target => {
      switch (target.focus_kind) {
        case "minority_counted": return "是否抓錯峰";
        case "minority_not_counted": return "是否漏抓";
        case "mixed": return target.focus_samples.length ? "沒有明顯多數，看檢閱註記" : "";
        default:
          // Quality notes on counted peaks affect quantitation; they stay one level down.
          return target.counted && target.review ? `${target.review} 筆檢閱註記` : "";
      }
    };
    for (const target of summary.targets) {
      const row = node("tr");
      const name = node("td");
      name.append(resultButton(target.label, target.label, false, "text-button"));
      const counted = node("td", `${target.counted} / ${target.total}${target.count_unknown ? `（${target.count_unknown} 未記錄）` : ""}`);
      const focus = node("td");
      if (target.focus_samples.length) focus.append(resultButton(`${target.focus_samples.length} 筆 →`, target.label, true, "text-button focus-link"));
      else focus.textContent = "—";
      row.append(name, node("td", target.role || "—"), counted, focus, node("td", focusText(target)));
      body.append(row);
    }
    table.append(head, body);
    const scroll = node("div", undefined, "overview-table-scroll");
    scroll.append(table);
    host.append(scroll);
    const quantities = node("details", undefined, "overview-quantities");
    quantities.append(node("summary", "量值摘要（僅計入偵測的結果）"));
    const values = node("table", undefined, "overview-table");
    const valueHeader = node("tr");
    for (const text of ["目標", "RT 範圍 (min)", "Area 中位數"]) valueHeader.append(node("th", text));
    const valueHead = node("thead");
    valueHead.append(valueHeader);
    const valueBody = node("tbody");
    for (const target of summary.targets) {
      const row = node("tr");
      row.append(node("td", target.label), node("td", target.rt_min === null ? "—" : `${fmt(target.rt_min, 4)}–${fmt(target.rt_max, 4)}`), node("td", Number.isFinite(target.median_area) ? scientific(target.median_area) : "—"));
      valueBody.append(row);
    }
    values.append(valueHead, valueBody);
    quantities.append(values);
    host.append(quantities, node("p", `需先看：各目標中占 ${Math.round(summary.minority_share * 100)}% 以下的一方；只決定閱讀順序，不改變已保存的判定。`, "overview-footnote"));
  }
  function renderPage(page) {
    state.page = page;
    const targeted = page.source_kind === "targeted_workbook";
    $("targeted-overview").hidden = true;
    $("targeted-review-nav").hidden = !targeted;
    renderTargetedOverview(page.overview);
    $("targeted-filters").hidden = !targeted;
    $("group-filter").hidden = targeted;
    $("review-range-filters").open = !targeted;
    const selectedTarget = $("target-id").value;
    document.querySelector(".review-grid").classList.toggle("single-target", targeted && Boolean(selectedTarget) && page.matched_count === 1);
    $("target-id").replaceChildren(new Option("全部目標", ""), ...(page.target_names || []).map(name => new Option(name, name)));
    $("target-id").value = selectedTarget;
    document.querySelector(".acquisition-panel").hidden = targeted;
    $("tab-curves").hidden = targeted;
    const selectedSample = $("target-sample").value;
    $("target-sample").replaceChildren(new Option("全部樣本", ""), ...(page.all_samples || page.samples).map(s => new Option(s.label, s.id)));
    $("target-sample").value = selectedSample;
    clearSelection();
    $("review").hidden = false;
    $("review-empty").hidden = true;
    $("batch-title").textContent = targeted ? "Targeted 結果" : "批次結果已開啟";
    $("batch-description").textContent = targeted ? page.source.split(/[\\/]/).pop() : page.source;
    $("raw-dir").closest(".path-line").hidden = targeted;
    $("dll-dir").closest(".path-line").hidden = targeted;
    $("source-settings").querySelector("summary").textContent = targeted ? "切換結果來源" : "結果與 RAW 位置";
    $("batch-description").className = "mono";
    $("batch-description").title = page.source;
    $("batch-count").hidden = false;
    $("batch-count").textContent =
      `${count(page.samples.length)} 個樣本 · ${count(page.run_total)} 個檢閱項目`;
    const filtered = page.matched_count !== page.run_total;
    $("list-summary").replaceChildren(
      Object.assign(document.createElement("span"), {
        textContent: filtered
          ? `符合 ${count(page.matched_count)} ／ 全批 ${count(page.run_total)}`
          : `全批 ${count(page.run_total)} 個項目`,
      }),
      Object.assign(document.createElement("span"), {
        textContent: page.selected_count
          ? `第 ${count(page.offset + 1)}–${count(page.offset + page.selected_count)} 筆`
          : "",
      }),
    );
    const list = $("item-list");
    list.replaceChildren();
    for (const item of page.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "item";
      button.dataset.itemId = item.id;
      button.setAttribute("aria-current", "false");
      const top = document.createElement("span");
      top.className = "item-top";
      const title = document.createElement("strong");
      title.textContent = targeted ? item.title : `m/z ${fmt(item.precursor_mz, 4)}`;
      const rt = document.createElement("span");
      rt.className = "item-rt";
      rt.textContent = targeted ? `m/z ${fmt(item.precursor_mz, 4)}` : `RT ${fmt(item.rt_apex_min, 2)} min`;
      top.append(title, rt);
      const bottom = document.createElement("span");
      bottom.className = "item-bottom";
      const summary = document.createElement("span");
      summary.textContent = targeted
        ? `${item.review_summary.scope_sample_count} 個樣本的已保存結果`
        : `${item.title} · ${item.review_summary.observed_sample_count || 0} 個樣本`;
      const badge = document.createElement("span");
      const label = item.badge_label || "已記錄";
      badge.className = `chip ${badgeTone[label] || ""}`;
      badge.textContent = label;
      bottom.append(summary, badge);
      button.append(top, bottom);
      button.addEventListener("click", () =>
        action(() => selectItem(item), "正在準備檢閱內容…"),
      );
      list.append(button);
    }
    if (!page.items.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "沒有符合條件的項目。調整範圍或清除條件後再搜尋。";
      list.append(empty);
    }
    list.scrollTop = 0;
    $("page-label").textContent = page.matched_count
      ? `${count(Math.floor(page.offset / page.limit) + 1)} / ${count(Math.max(1, Math.ceil(page.matched_count / page.limit)))} 頁`
      : "0 筆";
    status(
      filtered
        ? `找到 ${count(page.matched_count)} 個符合條件的項目（搜尋範圍是全批已儲存結果）。`
        : `已載入全批 ${count(page.run_total)} 個項目。`,
    );
  }
  async function selectItem(item) {
    const detail = await api("/api/detail", { item_id: item.id });
    restoreDetail(detail);
  }
  function restoreDetail(detail) {
    const item = detail.item;
    state.item = detail.item;
    state.recorded = detail.view_id;
    state.curves = null;
    state.curveLabels = [];
    $("selection").hidden = false;
    $("selection-empty").hidden = true;
    $("job-progress").hidden = true;
    const targeted = state.page.source_kind === "targeted_workbook";
    $("item-kind").textContent = targeted ? "Targeted 已保存結果" : item.title;
    $("item-title").textContent = targeted
      ? `${item.title} · m/z ${fmt(item.precursor_mz, 4)}`
      : `m/z ${fmt(item.precursor_mz, 4)} · RT ${fmt(item.rt_apex_min, 2)} min`;
    $("item-identity").textContent =
      item.group_id || item.candidate_id || item.id;
    document
      .querySelectorAll(".item")
      .forEach((button) =>
        button.setAttribute(
          "aria-current",
          String(button.dataset.itemId === item.id),
        ),
      );
    const available = new Set(detail.item.observed_sample_ids);
    state.samples = new Set(
      [...state.samples].filter((id) => available.has(id)),
    );
    if (!state.samples.size)
      state.samples = new Set(
        state.page.samples
          .filter((sample) => available.has(sample.id))
          .slice(0, 2)
          .map((sample) => sample.id),
      );
    const list = $("sample-list");
    list.replaceChildren();
    for (const sample of state.page.samples) {
      const supported = available.has(sample.id);
      const label = document.createElement("label");
      label.className = `sample-option${supported ? "" : " unavailable"}`;
      if (!supported) label.title = "此樣本缺少觀測座標，無法自動擷取";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = sample.id;
      checkbox.dataset.available = String(supported);
      checkbox.checked = state.samples.has(sample.id);
      checkbox.addEventListener("change", () => {
        checkbox.checked
          ? state.samples.add(sample.id)
          : state.samples.delete(sample.id);
        controls();
      });
      const text = document.createElement("span");
      text.textContent = sample.label;
      if (!supported) {
        const note = document.createElement("small");
        note.textContent = "無觀測座標";
        text.append(note);
      }
      label.append(checkbox, text);
      list.append(label);
    }
    showTab("recorded");
    status(state.page.source_kind === "targeted_workbook" ? "唯讀檢閱已保存的 Targeted 結果，不需要 RAW。" : "可查看當次分析紀錄，或為勾選的樣本載入 XIC。");
  }
  function showTab(tab) {
    const view = tab === "curves" ? state.curves : state.recorded;
    if (!view) return;
    state.tab = tab;
    $("tab-recorded").setAttribute("aria-selected", String(tab === "recorded"));
    $("tab-curves").setAttribute("aria-selected", String(tab === "curves"));
    $("view-note").className = `view-note${tab === "curves" ? " curves" : ""}`;
    $("view-note").textContent =
      tab === "curves"
        ? `新取得的診斷曲線 · ${state.curveLabels.join("、")}。依報告樣本檔名取檔，已核對 RAW 內容屬於此批次；來源未保留獨立的樣本與 RAW 綁定。這些曲線不改寫當次定量值。`
        : state.page.source_kind === "targeted_workbook"
          ? "唯讀 XLSX · 只顯示已保存結果，不重新計算。"
          : "這是當次分析保存的證據與量值。若未保留曲線，可在上方按需取得 XIC。";
    $("inspector").src = `/reports/${view}.html?embed=1`;
    $("download").href = `/reports/${view}.html?download=1`;
    $("download").hidden = state.page.source_kind === "targeted_workbook";
  }
  async function openBatch() {
    const page = await api("/api/open", { source: $("source").value });
    $("query-form").reset();
    state.filters = {};
    state.samples = new Set();
    renderPage(page);
    $("source-settings").open = false;
    if (page.source_kind === "targeted_workbook") showTargetedOverview();
    else if (page.items.length) await selectItem(page.items[0]);
  }
  function queryFields() {
    const filters = state.page?.source_kind === "targeted_workbook"
      ? { target_id: $("target-id").value.trim(), sample_id: $("target-sample").value, attention_only: $("target-attention").checked }
      : { group_id: $("group-id").value.trim() };
    for (const [id, key] of [
      ["mz-min", "mz_min"],
      ["mz-max", "mz_max"],
      ["rt-min", "rt_min"],
      ["rt-max", "rt_max"],
    ])
      if ($(id).value.trim()) filters[key] = Number($(id).value);
    return filters;
  }
  async function query(offset, filters = state.filters) {
    const page = await api("/api/query", { ...filters, offset, limit: 25 });
    state.filters = filters;
    renderPage(page);
    if (page.items.length) await selectItem(page.items[0]);
  }
  async function startCurves() {
    if (!$("raw-dir").value.trim() || !$("dll-dir").value.trim()) {
      $("source-settings").open = true;
      throw new Error("請先在「結果與 RAW 位置」選擇 RAW 與 DLL 資料夾。");
    }
    const job = await api("/api/curves", {
      item_id: state.item.id,
      sample_ids: [...state.samples],
      raw_dir: $("raw-dir").value,
      dll_dir: $("dll-dir").value,
      ppm: Number($("ppm").value),
      padding_min: Number($("padding").value),
    });
    state.job = job;
    $("job-progress").hidden = false;
    updateProgress(job);
    controls();
    setTimeout(pollJob, 400);
  }
  function updateProgress(job) {
    const progress = $("progress");
    if (job.total) {
      progress.max = job.total;
      progress.value = job.completed;
    } else progress.removeAttribute("value");
    $("job-message").textContent =
      `${job.phase}${job.total ? ` · ${job.completed}/${job.total}` : ""} · ${job.message}`;
    status(job.message, job.status === "failed" ? "error" : "working");
  }
  async function pollJob() {
    if (!state.job) return;
    try {
      const job = await api(`/api/jobs/${state.job.id}`);
      state.job = job;
      updateProgress(job);
      if (["running", "cancelling"].includes(job.status)) {
        controls();
        setTimeout(pollJob, 500);
        return;
      }
      if (job.status === "succeeded") {
        state.curves = job.view_id;
        state.curveLabels = state.page.samples
          .filter((sample) => job.sample_ids.includes(sample.id))
          .map((sample) => sample.label);
        showTab("curves");
      }
      state.job = null;
      status(job.message, job.status === "failed" ? "error" : "");
      controls();
    } catch (error) {
      status(`無法讀取工作進度：${error.message}。正在重新連線…`, "error");
      setTimeout(pollJob, 2000);
    }
  }

  // The embedded Inspector grows with its content so the page keeps one
  // scroll; its dialogs are placed inside the part of the frame on screen.
  const frame = $("inspector");
  let frameObserver = null;
  function frameDocument() {
    try {
      return frame.contentDocument;
    } catch (_) {
      return null;
    }
  }
  function placeFrameDialogs() {
    const doc = frameDocument();
    if (!doc?.documentElement || frame.offsetParent === null) return;
    const topbar = document.querySelector(".topbar")?.offsetHeight || 0;
    const rect = frame.getBoundingClientRect();
    doc.documentElement.style.setProperty(
      "--host-top",
      `${Math.max(0, topbar - rect.top)}px`,
    );
    doc.documentElement.style.setProperty(
      "--host-height",
      `${window.innerHeight - topbar}px`,
    );
  }
  function fitFrame() {
    const doc = frameDocument();
    if (!doc?.body) return;
    const height = Math.ceil(doc.body.getBoundingClientRect().height);
    frame.style.height = `${Math.max(height + 8, 480)}px`;
    placeFrameDialogs();
  }
  frame.addEventListener("load", () => {
    frameObserver?.disconnect();
    frameObserver = null;
    const doc = frameDocument();
    if (!doc?.body || frame.getAttribute("src") === "about:blank") {
      frame.style.height = "";
      return;
    }
    doc.documentElement.dataset.theme =
      document.documentElement.dataset.theme || "light";
    const Observer = frame.contentWindow.ResizeObserver || ResizeObserver;
    frameObserver = new Observer(fitFrame);
    frameObserver.observe(doc.body);
    fitFrame();
  });
  let placing = false;
  const schedulePlacement = () => {
    if (placing) return;
    placing = true;
    requestAnimationFrame(() => {
      placing = false;
      placeFrameDialogs();
    });
  };
  window.addEventListener("scroll", schedulePlacement, { passive: true });
  window.addEventListener("resize", () => {
    schedulePlacement();
    fitFrame();
  });

  async function browse(path) {
    try {
      const data = await api("/api/browse", { path, kind: state.browser.kind });
      state.browser.data = data;
      $("browse-path").value = data.path;
      $("browse-parent").disabled = data.parent === null;
      $("browse-use").disabled = !data.path;
      const files = data.entries.filter((entry) => !entry.directory).length;
      $("browse-note").textContent = data.truncated
        ? "此處僅顯示前 1,000 個位置；可直接輸入完整路徑。"
        : !data.path
          ? "選擇一個磁碟開始。"
          : files
            ? "點選檔案即可使用；或進入資料夾後按「使用此資料夾」。"
            : "進入資料夾，或按「使用此資料夾」選擇目前位置。";
      const list = $("browse-entries");
      list.replaceChildren();
      for (const entry of data.entries) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = entry.directory ? "is-dir" : "is-file";
        button.append(
          icon(!data.path ? "drive" : entry.directory ? "folder" : "file"),
          document.createTextNode(entry.name),
        );
        button.addEventListener("click", () =>
          entry.directory ? browse(entry.path) : useLocation(entry.path),
        );
        list.append(button);
      }
      if (!data.entries.length) {
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent =
          state.browser.kind === "folder"
            ? "這裡沒有子資料夾。若這就是要用的位置，按「使用此資料夾」。"
            : "這裡沒有符合類型的檔案或子資料夾。";
        list.append(empty);
      }
      list.scrollTop = 0;
    } catch (error) {
      $("browse-note").textContent = error.message;
    }
  }
  function useLocation(path) {
    $(state.browser.target).value = path;
    $(state.browser.target).dispatchEvent(
      new Event("change", { bubbles: true }),
    );
    $(state.browser.target).dispatchEvent(
      new Event("input", { bubbles: true }),
    );
    $("browser-dialog").close();
  }
  $("open-form").addEventListener("submit", (event) => {
    event.preventDefault();
    action(openBatch, "正在讀取批次結果…");
  });
  $("query-form").addEventListener("submit", (event) => {
    event.preventDefault();
    action(() => query(0, queryFields()), "正在搜尋全批結果…");
  });
  // Targeted filters are single choices, so each change applies immediately.
  for (const id of ["target-id", "target-sample", "target-attention"]) {
    $(id).addEventListener("change", () =>
      action(() => query(0, queryFields()), "正在更新結果…"),
    );
  }
  $("back-to-overview").addEventListener("click", showTargetedOverview);
  $("reset-query").addEventListener("click", () =>
    action(async () => {
      $("query-form").reset();
      await query(0, {});
    }, "正在讀取全批結果…"),
  );
  $("previous").addEventListener("click", () =>
    action(() => query(Math.max(0, state.page.offset - 25)), "正在載入上一頁…"),
  );
  $("next").addEventListener("click", () =>
    action(() => query(state.page.offset + 25), "正在載入下一頁…"),
  );
  $("load-curves").addEventListener("click", () =>
    action(startCurves, "正在準備所選樣本 XIC…"),
  );
  $("cancel").addEventListener("click", async () => {
    if (!state.job) return;
    try {
      state.job = await api(`/api/jobs/${state.job.id}/cancel`, {});
      updateProgress(state.job);
      controls();
    } catch (error) {
      status(error.message, "error");
    }
  });
  $("tab-recorded").addEventListener("click", () => showTab("recorded"));
  $("tab-curves").addEventListener("click", () => showTab("curves"));
  $("item-list").addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const items = [...document.querySelectorAll(".item")];
    if (!items.length) return;
    event.preventDefault();
    const current = items.findIndex(
      (item) => item.getAttribute("aria-current") === "true",
    );
    const next =
      items[
        Math.max(
          0,
          Math.min(items.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)),
        )
      ];
    if (next === items[current]) return;
    next.focus();
    next.scrollIntoView({ block: "nearest" });
    next.click();
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-browse]");
    if (!button) return;
    state.browser = {
      target: button.dataset.browse,
      kind: button.dataset.kind,
    };
    $("browse-title").textContent =
      ["folder"].includes(button.dataset.kind) || !button.dataset.kind
        ? "選擇資料夾"
        : "選擇檔案或資料夾";
    $("browse-use").textContent = "使用此資料夾";
    $("browser-dialog").showModal();
    $("browse-path").focus();
    browse($(state.browser.target).value);
  });
  document.addEventListener("xic:open-review", (event) => {
    $("source").value = event.detail;
    action(openBatch, "正在讀取分析結果…");
  });
  document.addEventListener("xic:analysis-busy", (event) => {
    if (state.analysisBusy === event.detail) return;
    state.analysisBusy = event.detail;
    controls();
  });
  $("browse-close").addEventListener("click", () =>
    $("browser-dialog").close(),
  );
  $("browse-form").addEventListener("submit", (event) => {
    event.preventDefault();
    browse($("browse-path").value);
  });
  $("browse-parent").addEventListener("click", () =>
    browse(state.browser.data.parent),
  );
  $("browse-roots").addEventListener("click", () => browse(""));
  $("browse-use").addEventListener("click", () =>
    useLocation(state.browser.data.path),
  );
  action(async () => {
    const session = await api("/api/session");
    $("source").value = session.defaults.source || "";
    $("raw-dir").value = session.defaults.raw_dir || "";
    $("dll-dir").value = session.defaults.dll_dir || "";
    const review = await api("/api/review");
    if (review.page) {
      $("source").value = review.page.source;
      renderPage(review.page);
      if (review.job) state.samples = new Set(review.job.sample_ids);
      if (review.page.source_kind === "targeted_workbook") showTargetedOverview();
      else if (review.detail) restoreDetail(review.detail);
      if (review.job) {
        state.job = review.job;
        $("job-progress").hidden = false;
        await pollJob();
      }
    } else if ($("source").value) await openBatch();
    else status("");
  }, "正在連接本機工作區…");
})();
