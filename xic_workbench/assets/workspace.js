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
  // History owns reading locations, not view mounts or filter edits. Snapshots
  // remain in this document so refresh starts from the server's current batch.
  const locations = new Map();
  const readers = new Map();
  const session = `${Date.now()}-${Math.random()}`;
  let position = 0;
  let revision = 0;
  let restoring = false;
  const capture = () => ({
    parts: [...readers].map(([name, reader]) => [name, reader.capture()]),
    scroll: window.scrollY,
  });
  const mark = () => ({ xicNavigation: session, position });
  history.replaceState(mark(), "");
  history.scrollRestoration = "manual";
  const navigation = {
    get revision() { return revision; },
    register(name, reader) { readers.set(name, reader); },
    push() {
      if (restoring) return;
      locations.set(position, capture());
      for (const key of locations.keys()) if (key > position) locations.delete(key);
      position += 1;
      history.pushState(mark(), "");
    },
    back(fallback, matches = () => true) {
      for (let target = position - 1; target >= 0; target -= 1) {
        const location = locations.get(target);
        if (location && matches(Object.fromEntries(location.parts))) {
          history.go(target - position);
          return;
        }
      }
      fallback();
    },
  };
  window.addEventListener("popstate", (event) => {
    if (event.state?.xicNavigation !== session) return;
    revision += 1;
    locations.set(position, capture());
    position = event.state.position;
    const location = locations.get(position);
    if (!location) return;
    const currentRevision = revision;
    // Restore readers after the browser completes the history transition.
    setTimeout(() => {
      if (revision !== currentRevision) return;
      restoring = true;
      try {
        for (const [name, value] of location.parts) readers.get(name)?.restore(value);
        requestAnimationFrame(() => window.scrollTo(0, location.scroll));
      } finally { restoring = false; }
    }, 0);
  });
  return { icon, toast, confirm, setTheme, formatDuration, navigation };
})();

(() => {
  const $ = (id) => document.getElementById(id);
  const { icon, navigation } = window.XicUi;
  const state = {
    page: null,
    item: null,
    detail: null,
    recorded: null,
    curveView: null,
    curveCache: new Map(),
    curveRequestKey: null,
    curveOpener: null,
    busy: false,
    restoring: false,
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
  let statusOwner = "review";
  function status(message, kind = "", owner = "review") {
    statusOwner = owner;
    $("status").textContent = message;
    $("status").className = `inline-status ${kind}`;
  }
  function curveStatus(message, kind = "") {
    $("xic-status").textContent = message;
    $("inline-curve-status").textContent = message;
    // Curve completion may replace its own feedback, not a still-valid query
    // or saved-result error. The drawer retains the curve's local feedback.
    if (statusOwner === "curves" || !$("status").classList.contains("error"))
      status(message, kind, "curves");
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
  const reviewSlots = new Map();
  function clearReview(id) {
    const slot = reviewSlots.get(id);
    if (slot) {
      reviewSlots.delete(id);
      slot.handle?.destroy();
    }
    $(id).replaceChildren();
    delete $(id).dataset.viewId;
  }
  async function mountReview(id, view, mode) {
    const previous = reviewSlots.get(id);
    if (previous?.view === view && previous.handle) return true;
    clearReview(id);
    const slot = {view, detail: state.detail, handle: null};
    reviewSlots.set(id, slot);
    try {
      const payload = await api(`/api/views/${view}`);
      if (reviewSlots.get(id) !== slot || state.detail !== slot.detail) return;
      if (id === "xic-review" && !$("xic-dialog").open) {
        clearReview(id);
        return;
      }
      slot.handle = window.XicInspector.mount($(id), payload, {
        mode,
        onRequestCurves: request => {
          if (reviewSlots.get(id) === slot && state.detail === slot.detail)
            requestCurves(request);
        },
        ...(mode === "result" && state.page?.source_kind === "targeted_workbook"
          ? { onRequestExclusion: requestExclusion }
          : {}),
      });
      $(id).dataset.viewId = view;
      return true;
    } catch (error) {
      if (reviewSlots.get(id) !== slot || state.detail !== slot.detail) return;
      clearReview(id);
      if (mode === "curves") curveMessage(error.message, true);
      else status(error.message, "error");
    }
  }
  // The run's RAW folder owns the exclusion; it applies on the next run.
  async function requestExclusion({ sample_id }) {
    const dialog = $("exclude-dialog");
    $("exclude-message").textContent =
      `把 ${sample_id} 標記為排除：之後分析這個 RAW 資料夾時不再分析它。`;
    $("exclude-reason").value = "";
    dialog.returnValue = "cancel";
    dialog.showModal();
    $("exclude-reason").focus();
    const accepted = await new Promise(resolve =>
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
    if (!accepted) return;
    try {
      const raw_dir = $("raw-dir").value;
      const result = await api("/api/samples/exclude", {
        raw_dir,
        dll_dir: $("dll-dir").value,
        raw_stem: sample_id,
        reason: $("exclude-reason").value,
      });
      document.dispatchEvent(new CustomEvent("xic:samples-confirmed", {
        detail: { raw_dir, samples: result.samples, overview: result.overview },
      }));
      window.XicUi.toast(
        `已標記排除 ${sample_id}。目前結果不變；重新執行後才會套用。`, "ok");
    } catch (error) {
      status(error.message, "error");
    }
  }
  let serverPage = null;
  let pageRequest = Promise.resolve();
  function requestPage(path, values) {
    // The server owns one active query. A history restore must run after any
    // earlier query, even when navigation discarded that query's UI response.
    const request = pageRequest.then(() => api(path, values)).then(page => {
      serverPage = page;
      return page;
    });
    pageRequest = request.catch(() => {});
    return request;
  }
  async function restoreQuery(page, filters) {
    const revision = navigation.revision;
    state.restoring = true;
    controls();
    try {
      await pageRequest;
      if (!page || state.page !== page || revision !== navigation.revision ||
          serverPage === page || state.job) return;
      status("正在還原結果查詢…", "working");
      await requestPage("/api/query", {
        ...filters, review_scope: page.review_scope, offset: page.offset, limit: page.limit,
      });
      if (revision === navigation.revision && state.page === page) {
        // The unchanged source keeps saved report/item IDs valid. Reuse its
        // reading snapshot after restoring the corresponding server query.
        serverPage = page;
        status("已還原結果查詢。");
      }
    } catch (error) {
      if (revision === navigation.revision) status(error.message, "error");
    } finally {
      if (revision === navigation.revision) {
        state.restoring = false;
        controls();
      }
    }
  }
  function locked() {
    return state.busy || state.restoring || !!state.job;
  }
  function supportsNearestSearch() {
    return state.page?.source_kind !== "targeted_workbook" &&
      ["matrix", "unresolved"].includes(state.page?.review_scope);
  }
  function nearestSearchMode() {
    return supportsNearestSearch() && $("untargeted-search-mode").value === "nearest";
  }
  function validateNearestMz() {
    const text = $("nearest-mz").value.trim();
    const value = Number(text);
    const invalid = nearestSearchMode() && text !== "" &&
      (!Number.isFinite(value) || value <= 0);
    const message = invalid ? "請輸入大於 0 的有限 m/z（例如 123.4），或留白瀏覽。" : "";
    $("nearest-mz").setCustomValidity(message);
    $("nearest-mz").setAttribute("aria-invalid", String(invalid));
    $("nearest-mz-error").textContent = message;
    $("nearest-mz-error").hidden = !invalid;
    return !invalid;
  }
  function syncQueryMode() {
    const nearest = nearestSearchMode(), busy = locked();
    $("untargeted-search").hidden = !supportsNearestSearch();
    $("untargeted-search-mode").disabled = busy || !supportsNearestSearch();
    $("nearest-mz-field").hidden = !nearest;
    $("nearest-mz").disabled = busy || !nearest;
    $("exact-mz-range").hidden = nearest;
    for (const id of ["mz-min", "mz-max"]) $(id).disabled = busy || nearest;
    $("group-filter").hidden = state.page?.source_kind === "targeted_workbook" || nearest;
    $("group-id").disabled = busy || $("group-filter").hidden;
    $("search-mode-hint").textContent = nearest
      ? "依 m/z 距離找候選，不代表化合物確認或通過質量容差。RT 可留白；精確 m/z 範圍與分組 ID 不生效。"
      : "精確範圍／分組 ID 生效；約略 m/z 不生效。";
    $("review-range-filters").querySelector("summary").textContent = nearest
      ? "RT 範圍（選填，留白不限）" : "m/z / RT 範圍";
    if (nearest) $("review-range-filters").open = true;
    validateNearestMz();
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
    const sampleIndex = state.item?.curve_sample_ids.indexOf($("xic-sample").value) ?? -1;
    $("xic-sample").disabled = busy || state.analysisBusy;
    $("xic-previous").disabled = busy || state.analysisBusy || sampleIndex <= 0;
    $("xic-next").disabled = busy || state.analysisBusy || sampleIndex < 0 || sampleIndex >= (state.item?.curve_sample_ids.length || 0) - 1;
    $("xic-cancel").hidden = !state.job;
    $("xic-cancel").disabled = state.job?.status === "cancelling";
    $("xic-retry").disabled = busy || state.analysisBusy;
    syncQueryMode();
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
    $("inline-curves").hidden = true;
    clearReview("inline-curve-review");
    state.item = null;
    state.detail = null;
    state.recorded = null;
    state.curveCache.clear();
    $("selection").hidden = true;
    $("selection-empty").hidden = false;
    renderSelectionEmpty();
    clearReview("recorded-review");
    clearReview("xic-review");
    state.curveView = null;
    if ($("xic-dialog").open) $("xic-dialog").close();
    $("job-progress").hidden = true;
  }
  function renderSelectionEmpty() {
    if (state.page?.matched_count === 0) {
      selectionEmpty("目前篩選條件沒有符合結果", emptyResultGuidance());
    } else {
      selectionEmpty("選擇一個項目開始檢閱", "查看當次分析紀錄，或為選定樣本重新取得 XIC。");
    }
  }
  function selectionEmpty(title, description) {
    $("selection-empty-title").textContent = title;
    $("selection-empty-description").textContent = description;
  }
  function emptyResultGuidance() {
    const filters = state.filters, parts = [];
    if (state.page.source_kind !== "targeted_workbook") {
      const scopes = {matrix: "矩陣群組", unresolved: "未解決定量", archive: "未採用群組", candidates: "未分組候選"};
      parts.push(`範圍：${scopes[state.page.review_scope] || "所有紀錄"}`);
    }
    if (filters.target_role) parts.push(`類別：${filters.target_role}`);
    if (filters.target_id) parts.push(`目標：${filters.target_id}`);
    if (filters.sample_id) {
      const sample = (state.page.all_samples || state.page.samples).find(s => s.id === filters.sample_id);
      parts.push(`樣本：${sample?.label || filters.sample_id}`);
    }
    if (filters.attention_only) parts.push("優先檢閱");
    if (filters.group_id) parts.push(`分組 ID：${filters.group_id}`);
    if (filters.nearest_mz != null) parts.push(`約略 m/z：${filters.nearest_mz}`);
    for (const [key, label] of [["mz_min", "m/z ≥"], ["mz_max", "m/z ≤"], ["rt_min", "RT ≥"], ["rt_max", "RT ≤"]]) {
      if (filters[key] != null) parts.push(`${label} ${filters[key]}${key.startsWith("rt_") ? " min" : ""}`);
    }
    return `目前條件：${parts.join("；") || "全批結果（未加篩選）"}。`
      + "可調整上方單一條件後重查，或按「清除條件」返回瀏覽；不會自動放寬條件。";
  }
  function showTargetedOverview() {
    $("targeted-overview").hidden = false;
    $("targeted-review-nav").hidden = true;
    $("review").hidden = true;
  }
  navigation.register("review", {
    capture: () => ({
      page: state.page, detail: state.detail, filters: state.filters,
      fields: [...$("query-form").elements].filter(input => input.id).map(input =>
        [input.id, input.value, input.checked]),
      overview: !$("targeted-overview").hidden,
      curves: $("xic-dialog").open,
      curveView: state.curveView,
      sample: $("xic-sample").value,
    }),
    restore(saved) {
      // A newly opened batch supersedes old result links in this tab.
      if (saved.page && saved.page.source === state.page?.source) {
        if (!saved.overview && (state.page !== saved.page || state.detail !== saved.detail)) {
          for (const [id, value, checked] of saved.fields) {
            $(id).value = value;
            $(id).checked = checked;
          }
          state.filters = saved.filters;
          renderPage(saved.page);
          if (saved.detail) restoreDetail(saved.detail);
        }
        if (saved.overview) showTargetedOverview();
        else {
          $("targeted-overview").hidden = true;
          $("targeted-review-nav").hidden = !saved.page.overview;
          $("review").hidden = false;
          // A discarded request can leave transient loading/error feedback
          // even when history restores the same empty page object.
          if (!state.detail) renderSelectionEmpty();
        }
      }
      if (saved.curves && saved.page?.source === state.page?.source) {
        $("xic-sample").value = saved.sample;
        if (!$("xic-dialog").open) $("xic-dialog").showModal();
        // History retains the reading's receipt even if filters replace its mount.
        // The acquisition fills in its id when it finishes while the drawer is closed.
        const view = saved.curveView || state.curveView;
        if (view?.id) showCurveView(view);
      } else if ($("xic-dialog").open) $("xic-dialog").close();
      restoreQuery(!saved.overview && saved.page === state.page ? saved.page : null, saved.filters);
    },
  });
  async function openTargetedResults(target = "", attentionOnly = false) {
    navigation.push();
    $("query-form").reset();
    $("target-attention").checked = attentionOnly;
    // The overview can open a target outside the current role's dropdown.
    if (await query(0, {target_id: target, attention_only: attentionOnly}))
      $("targeted-review-nav").scrollIntoView({ block: "start" });
  }
    const node = (tag, text, className = "") => {
      const element = document.createElement(tag);
      if (text !== undefined) element.textContent = text;
      element.className = className;
      return element;
    };
  function renderTargetedOverview(summary) {
    const host = $("targeted-overview");
    host.replaceChildren();
    if (!summary) return;
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
      : summary.focus_count ? `先看 ${count(summary.focus_count)} 筆結果`
        : "沒有需要先看的結果";
    heading.append(node("h2", conclusion));
    const scope = `${count(summary.sample_count)} 個樣本 · ${count(summary.targets.length)} 個目標`
      + (summary.focus_count ? ` · 分布在 ${count(summary.focus_target_count)} 個目標` : "");
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
  function renderUntargetedOverview(summary) {
    const host = $("targeted-overview");
    host.replaceChildren();
    host.append(node("span", "Untargeted · 整批結果總覽", "eyebrow"),
      node("h2", summary.unresolved_count ? `先看 ${count(summary.unresolved_count)} 組未解決定量` : "矩陣中沒有未解決定量"),
      node("p", "閱讀順序依已保存的定量狀態與樣本覆蓋數；不重新判定峰，也不把缺值當作異常。"));
    const metrics = node("div", undefined, "overview-metrics");
    for (const [value, label] of [[summary.matrix_count, "進入矩陣"], [summary.complete_count, "全部樣本都有值"], [summary.unresolved_count, "需先看"]]) {
      const metric = node("div"); metric.append(node("strong", count(value)), node("span", label)); metrics.append(metric);
    }
    host.append(metrics);
    const openScope = (label, scope, parent = host) => {
      const button = node("button", label); button.type = "button"; button.dataset.idle = "";
      button.addEventListener("click", () => action(async () => {
        navigation.push(); $("query-form").reset(); await query(0, {review_scope: scope});
      })); parent.append(button);
    };
    if (summary.unresolved_count) openScope("先看未解決定量", "unresolved");
    openScope("查看全部矩陣群組", "matrix");
    if (summary.uncalibrated_count) host.append(node("p", `${summary.uncalibrated_count} / ${summary.samples.length} 個樣本未建立內標 RT 校正。XIC 顯示原始時間；這不代表內標未檢出。`, "overview-unknown"));
    const table = node("table", undefined, "overview-table");
    const head = node("tr"); head.append(node("th", "樣本"), node("th", "矩陣中有定量值的群組")); table.append(head);
    for (const sample of summary.samples) {const row = node("tr"); row.append(node("td", sample.label), node("td", `${count(sample.numeric_count)} / ${count(summary.matrix_count)}`)); table.append(row);}
    host.append(table);
    const more = node("details"); more.append(node("summary", "進階：未採用群組與候選證據"), node("p", "這些不是矩陣結果，僅在追查未採用原因時檢閱。"));
    openScope(`${count(summary.archive_count)} 組未採用`, "archive", more);
    openScope(`${count(summary.candidate_count)} 筆未分組候選`, "candidates", more);
    host.append(more);
  }
  function renderPage(page) {
    state.page = page;
    const targeted = page.source_kind === "targeted_workbook";
    const nearest = Number.isFinite(page.nearest_mz);
    if (nearest) {
      $("untargeted-search-mode").value = "nearest";
      if (Number($("nearest-mz").value) !== page.nearest_mz)
        $("nearest-mz").value = String(page.nearest_mz);
      // Retain the applied nearest query for pagination and reading history.
      state.filters = {nearest_mz: page.nearest_mz, review_scope: page.review_scope};
      for (const [id, key] of [["rt-min", "rt_min"], ["rt-max", "rt_max"]]) {
        $(id).value = page[key] == null ? "" : String(page[key]);
        if (page[key] != null) state.filters[key] = page[key];
      }
    }
    // Targeted review items are targets; Untargeted items are candidates.
    const noun = targeted ? "目標" : "項目";
    $("list-title").textContent = targeted ? "目標" : "檢閱項目";
    $("targeted-overview").hidden = true;
    $("targeted-review-nav").hidden = !page.overview;
    if (page.overview?.kind === "untargeted") renderUntargetedOverview(page.overview);
    else renderTargetedOverview(page.overview);
    $("targeted-filters").hidden = !targeted;
    $("group-filter").hidden = targeted;
    $("review-range-filters").open = false;
    syncQueryMode();
    const selectedTarget = state.filters.target_id || "";
    $("target-role").value = state.filters.target_role || "";
    document.querySelector(".review-grid").classList.toggle("single-target", targeted && Boolean(selectedTarget) && page.matched_count === 1);
    $("target-id").replaceChildren(new Option("全部目標", ""), ...(page.target_names || []).map(name => new Option(name, name)));
    $("target-id").value = selectedTarget;
    document.querySelector(".acquisition-panel").hidden = targeted;
    $("acquisition-description").textContent = targeted
      ? "點結果列的「看 XIC」，或勾選要看的樣本。自動帶入目標與配對 ISTD；ND 也能看圖，不改寫結果。"
      : "勾選要看的樣本，讀取保留峰頂附近的 RAW。缺少觀測座標的樣本無法自動擷取。";
    $("ppm").closest("label").hidden = targeted;
    const selectedSample = $("target-sample").value;
    $("target-sample").replaceChildren(new Option("全部樣本", ""), ...(page.all_samples || page.samples).map(s => new Option(s.label, s.id)));
    $("target-sample").value = selectedSample;
    clearSelection();
    $("review").hidden = false;
    $("review-empty").hidden = true;
    $("batch-title").textContent = targeted ? "Targeted 結果" : "批次結果已開啟";
    $("batch-description").textContent = targeted ? page.source.split(/[\\/]/).pop() : page.source;
    $("raw-dir").closest(".path-line").hidden = false;
    $("dll-dir").closest(".path-line").hidden = false;
    $("source-settings").querySelector("summary").textContent = "結果與 RAW 位置";
    $("batch-description").className = "mono";
    $("batch-description").title = page.source;
    const download = $("result-download");
    download.hidden = !page.result_artifact;
    if (page.result_artifact) {
      download.href = page.result_artifact.url;
      download.download = page.result_artifact.name;
      download.title = page.result_artifact.name;
      download.querySelector("span").textContent = targeted ? "下載結果 XLSX" : "下載矩陣 TSV";
    } else {
      download.removeAttribute("href");
      download.removeAttribute("download");
    }
    $("batch-count").hidden = false;
    $("batch-count").textContent = page.overview?.kind === "untargeted"
      ? `${count(page.samples.length)} 個樣本 · ${count(page.overview.matrix_count)} 組矩陣結果`
      : `${count(page.samples.length)} 個樣本 · ${count(page.run_total)} 個${targeted ? "目標" : "檢閱項目"}`;
    const filtered = page.matched_count !== page.run_total;
    $("list-summary").replaceChildren(
      Object.assign(document.createElement("span"), {
        textContent: page.overview?.kind === "untargeted" ? `${({matrix:"矩陣群組",unresolved:"未解決定量",archive:"未採用群組",candidates:"未分組候選"})[page.review_scope] || "所有紀錄"} · ${count(page.matched_count)} 組` : filtered
          ? `符合 ${count(page.matched_count)} ／ 全批 ${count(page.run_total)}`
          : `全批 ${count(page.run_total)} 個${noun}`,
      }),
      Object.assign(document.createElement("span"), {
        textContent: page.selected_count
          ? `第 ${count(page.offset + 1)}–${count(page.offset + page.selected_count)} 筆`
          : "",
      }),
    );
    $("nearest-results-note").hidden = !nearest;
    $("nearest-results-note").textContent = nearest
      ? `約略 m/z ${page.nearest_mz} 的候選 · 依接近程度排序（全批排序後，每頁 25 筆）。`
        + (page.missing_mz_count ? `另有 ${count(page.missing_mz_count)} 組缺少有效 m/z，未列入排序。` : "")
      : "";
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
      title.textContent = targeted ? item.title
        : `m/z ${nearest ? String(item.precursor_mz) : fmt(item.precursor_mz, 4)}`;
      const rt = document.createElement("span");
      rt.className = "item-rt";
      rt.textContent = targeted ? `m/z ${fmt(item.precursor_mz, 4)}` : `RT ${fmt(item.rt_apex_min, 2)} min`;
      top.append(title, rt);
      const bottom = document.createElement("span");
      bottom.className = "item-bottom";
      const summary = document.createElement("span");
      summary.textContent = targeted
        ? `${item.review_summary.scope_sample_count} 個樣本的已保存結果`
        : `${item.numeric_count} / ${page.samples.length} 個樣本有值`;
      const badge = document.createElement("span");
      const label = targeted ? item.badge_label || "已記錄" : item.unresolved_count ? `${item.unresolved_count} 個未解決定量` : "已保存結果";
      badge.className = `chip ${badgeTone[label] || ""}`;
      badge.textContent = label;
      bottom.append(summary, badge);
      button.append(top);
      if (nearest) {
        button.append(node("span", `Δm/z ${item.mz_distance}`, "item-distance"));
      }
      button.append(bottom);
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
      nearest ? `已載入 ${count(page.matched_count)} 組候選，依 m/z 接近程度排序；不是科學容差判定。`
        : filtered
        ? `找到 ${count(page.matched_count)} 個符合條件的${noun}（搜尋範圍是全批已儲存結果）。`
        : `已載入全批 ${count(page.run_total)} 個${noun}。`,
    );
  }
  async function selectItem(item) {
    const revision = navigation.revision;
    const detail = await api("/api/detail", { item_id: item.id });
    if (revision === navigation.revision) {
      restoreDetail(detail);
      if (state.page.source_kind !== "targeted_workbook") {
        $("inline-curves").hidden = false;
        if (!state.samples.size) $("inline-curve-status").textContent = "這組沒有可用觀測座標，無法自動取得 XIC。已保存的結果仍可查看。";
        else if (!$("raw-dir").value.trim() || !$("dll-dir").value.trim()) $("inline-curve-status").textContent = "尚未設定 RAW 或 DLL 位置。設定「結果與 RAW 位置」後，按「載入所選樣本 XIC」。";
        else await startCurves();
      }
    }
  }
  function restoreDetail(detail) {
    clearReview("inline-curve-review");
    clearReview("xic-review");
    state.curveView = null;
    state.detail = detail;
    const item = detail.item;
    state.item = detail.item;
    state.recorded = detail.view_id;
    state.curveCache.clear();
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
    const available = new Set(detail.item.curve_sample_ids);
    state.samples = targeted ? new Set(
      [...state.samples].filter((id) => available.has(id)),
    ) : new Set(available);
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
    $("xic-sample").replaceChildren(...detail.item.curve_sample_ids.map(id => new Option(
      state.page.samples.find(sample => sample.id === id)?.label || id, id,
    )));
    if (!targeted) {
      const missing = state.page.samples.filter(sample => !available.has(sample.id));
      const numericMissing = missing.filter(sample => item.numeric_sample_ids.includes(sample.id));
      $("inline-curve-coverage").textContent = `${item.numeric_count} 個樣本有定量值 · ${available.size} 個樣本可讀取 XIC（預設全部載入）`
        + (numericMissing.length ? `。有定量值，但目前檢閱資料未提供可擷取座標：${numericMissing.map(sample => sample.label).join("、")}。` : "。")
        + (missing.length ? `本次無法取得曲線的樣本：${missing.map(sample => sample.label).join("、")}。` : "");
    }
    showRecorded();
    status("可查看已保存結果，或按「看 XIC」讀取 RAW；看圖不改寫結果。");
  }
  function showRecorded() {
    $("view-note").hidden = state.page.source_kind === "targeted_workbook";
    $("view-note").textContent = state.page.source_kind === "targeted_workbook"
      ? ""
      : "先看 XIC 與下方保存的 Area；可展開「查看樣本 XIC」更換樣本。內部證據收在進階內容。";
    mountReview("recorded-review", state.recorded, "result");
    $("download").href = `/reports/${state.recorded}.html?download=1`;
    $("download").hidden = state.page.source_kind === "targeted_workbook";
  }
  async function showCurveView(view) {
    state.curveView = view;
    const targeted = state.page.source_kind === "targeted_workbook";
    if (targeted) $("xic-download").href = `/reports/${view.id}.html?download=1`;
    if (targeted && !$("xic-dialog").open) {
      curveStatus("診斷曲線已載入；重新開啟曲線視窗即可檢閱。");
      return;
    }
    curveStatus("正在顯示所選樣本的診斷曲線…", "working");
    $(targeted ? "xic-review" : "inline-curve-review").hidden = true;
    if (targeted) {
      $("xic-loading").hidden = false;
      $("xic-retry").hidden = true;
      $("xic-download").hidden = true;
    }
    const mounted = await mountReview(targeted ? "xic-review" : "inline-curve-review", view.id, "curves");
    if (!mounted || state.curveView !== view) return;
    const samples = targeted ? $("xic-sample").selectedOptions[0].textContent : `${state.samples.size} 個樣本`;
    curveStatus(`${samples} 的診斷曲線已載入；不改寫已保存結果。`);
    if (!targeted) {
      $("inline-curve-review").hidden = false;
      return;
    }
    $("xic-review").hidden = false;
    $("xic-loading").hidden = true;
    $("xic-retry").hidden = true;
    $("xic-download").hidden = false;
  }
  function curveMessage(message, retry = false, kind = retry ? "error" : "working") {
    curveStatus(message, kind);
    if (state.page.source_kind !== "targeted_workbook") {
      $("inline-curves").hidden = false;
      $("inline-curve-review").hidden = true;
    }
    state.curveView = null;
    $("xic-loading").hidden = false;
    $("xic-review").hidden = true;
    $("xic-download").hidden = true;
    $("xic-retry").hidden = !retry;
  }
  function selectCurveSample(id) {
    state.samples = new Set([id]);
    document.querySelectorAll("#sample-list input").forEach(input => {
      input.checked = input.value === id;
    });
    return startCurves();
  }
  async function openBatch() {
    const page = await requestPage("/api/open", { source: $("source").value });
    $("query-form").reset();
    state.filters = {};
    state.samples = new Set();
    renderPage(page);
    $("source-settings").open = false;
    if (page.overview) showTargetedOverview();
    else if (page.items.length) await selectItem(page.items[0]);
  }
  function queryFields() {
    const nearest = nearestSearchMode();
    const filters = state.page?.source_kind === "targeted_workbook"
      ? { target_role: $("target-role").value, target_id: $("target-id").value.trim(), sample_id: $("target-sample").value, attention_only: $("target-attention").checked }
      : { review_scope: state.page.review_scope };
    if (nearest) {
      if (!validateNearestMz()) throw new Error($("nearest-mz-error").textContent);
      if ($("nearest-mz").value.trim()) filters.nearest_mz = Number($("nearest-mz").value);
    } else {
      if (state.page?.source_kind !== "targeted_workbook") filters.group_id = $("group-id").value.trim();
      for (const [id, key] of [["mz-min", "mz_min"], ["mz-max", "mz_max"]])
        if ($(id).value.trim()) filters[key] = Number($(id).value);
    }
    for (const [id, key] of [["rt-min", "rt_min"], ["rt-max", "rt_max"]])
      if ($(id).value.trim()) filters[key] = Number($(id).value);
    return filters;
  }
  async function query(offset, filters = state.filters) {
    const revision = navigation.revision;
    if (!$("selection-empty").hidden)
      selectionEmpty("正在查詢結果…", "正在依目前條件查詢；尚未取得結果。");
    let page;
    try {
      page = await requestPage("/api/query", { ...filters, offset, limit: 25 });
    } catch (error) {
      if (revision !== navigation.revision) return false;
      if (!$("selection-empty").hidden)
        selectionEmpty("查詢未完成", `${error.message}。請重試或調整條件；這不代表零筆結果。`);
      throw error;
    }
    if (revision !== navigation.revision) return false;
    state.filters = filters;
    renderPage(page);
    if (page.items.length) await selectItem(page.items[0]);
    return revision === navigation.revision;
  }
  async function startCurves() {
    if (!$("raw-dir").value.trim() || !$("dll-dir").value.trim()) {
      $("source-settings").open = true;
      const rawMissing = !$("raw-dir").value.trim();
      const missing = $(rawMissing ? "raw-dir" : "dll-dir");
      // The click may come from far down the page; bring the field and the
      // status line below it on screen. action() disables inputs while it
      // runs, so focus waits until they are enabled again.
      missing.scrollIntoView({ block: "center" });
      setTimeout(() => missing.focus({ preventScroll: true }));
      curveStatus(`看 XIC 需要讀取 RAW。請先選擇${rawMissing ? " RAW 資料夾" : " RawFileReader DLL 資料夾"}。`, "error");
      return;
    }
    const targeted = state.page.source_kind === "targeted_workbook";
    $("xic-title").textContent = targeted ? state.item.title : `XIC · m/z ${fmt(state.item.precursor_mz, 4)}`;
    $("xic-navigation").hidden = !targeted;
    $("xic-source-note").textContent = targeted
      ? "陰影為已保存峰界；看圖不會重新積分或改變判定。"
      : "RAW 已核對批次內容；樣本依檔名對應，未保存獨立綁定。看圖不改寫結果。";
    if (targeted) {
      $("xic-sample").value = [...state.samples][0];
      const index = state.item.curve_sample_ids.indexOf($("xic-sample").value);
      $("xic-position").textContent = `${index + 1} / ${state.item.curve_sample_ids.length}`;
    }
    if (targeted && !$("xic-dialog").open) {
      navigation.push();
      $("xic-dialog").showModal();
    }
    const request = {
      item_id: state.item.id,
      sample_ids: [...state.samples],
      raw_dir: $("raw-dir").value,
      dll_dir: $("dll-dir").value,
      ppm: Number($("ppm").value),
      padding_min: Number($("padding").value),
    };
    const key = JSON.stringify([state.recorded, request]);
    state.curveRequestKey = key;
    if (state.curveCache.has(key)) {
      showCurveView({id: state.curveCache.get(key)});
      return;
    }
    curveMessage("正在讀取所選樣本的 RAW；不會重跑整批分析。 ");
    clearReview("xic-review");
    const view = state.curveView = {id: null};
    try {
      state.job = await api("/api/curves", request);
      $("job-progress").hidden = false;
      updateProgress(state.job);
      controls();
      setTimeout(() => pollJob(view), 400);
    } catch (error) {
      curveMessage(error.message, true);
    }
  }
  function updateProgress(job) {
    const progress = $("progress");
    if (job.total) {
      progress.max = job.total;
      progress.value = job.completed;
    } else progress.removeAttribute("value");
    $("job-message").textContent =
      `${job.phase}${job.total ? ` · ${job.completed}/${job.total}` : ""} · ${job.message}`;
    curveStatus(job.message, job.status === "failed" ? "error" : "working");
  }
  async function pollJob(view) {
    if (!state.job) return;
    try {
      const job = await api(`/api/jobs/${state.job.id}`);
      state.job = job;
      updateProgress(job);
      if (["running", "cancelling"].includes(job.status)) {
        controls();
        setTimeout(() => pollJob(view), 500);
        return;
      }
      if (job.status === "succeeded") {
        state.curveCache.set(state.curveRequestKey, job.view_id);
        if (state.curveCache.size > 8) state.curveCache.delete(state.curveCache.keys().next().value);
        view.id = job.view_id;
        showCurveView(view);
      } else {
        curveMessage(job.message, true, job.status === "failed" ? "error" : "");
      }
      state.job = null;
      if (!state.restoring && serverPage !== state.page) restoreQuery(state.page, state.filters);
      else controls();
    } catch (error) {
      curveStatus(`無法讀取工作進度：${error.message}。正在重新連線…`, "error");
      setTimeout(() => pollJob(view), 2000);
    }
  }

  function requestCurves({item_id, sample_id, opener}) {
    if (state.page?.source_kind !== "targeted_workbook" ||
        item_id !== state.item?.id ||
        !state.item.curve_sample_ids.includes(sample_id)) return;
    state.curveOpener = opener;
    action(() => selectCurveSample(sample_id));
  }
  const closeCurves = () => navigation.back(() => $("xic-dialog").close());
  $("xic-close").addEventListener("click", closeCurves);
  $("xic-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeCurves();
  });
  $("xic-dialog").addEventListener("close", () => {
    if ($("xic-dialog").open) return;
    // Keep a completed reading view, including its RT range, across Back/Forward.
    // Pending payloads are not reading state and must not mount after closing.
    if (!reviewSlots.get("xic-review")?.handle) {
      clearReview("xic-review");
      if (state.page?.source_kind === "targeted_workbook" && state.curveView?.id)
        showCurveView(state.curveView);
    }
    if (state.curveOpener?.isConnected) state.curveOpener.focus({preventScroll: true});
  });
  $("xic-sample").addEventListener("change", () => action(() => selectCurveSample($("xic-sample").value)));
  for (const [id, step] of [["xic-previous", -1], ["xic-next", 1]]) {
    $(id).addEventListener("click", () => {
      const ids = state.item.curve_sample_ids, index = ids.indexOf($("xic-sample").value);
      if (ids[index + step]) action(() => selectCurveSample(ids[index + step]));
    });
  }
  $("xic-retry").addEventListener("click", () => action(startCurves));
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
  $("untargeted-search-mode").addEventListener("change", syncQueryMode);
  $("nearest-mz").addEventListener("input", validateNearestMz);
  $("query-form").addEventListener("submit", (event) => {
    event.preventDefault();
    action(() => query(0, queryFields()), "正在搜尋全批結果…");
  });
  // Targeted filters are single choices, so each change applies immediately.
  $("target-role").addEventListener("change", () => {
    // Choosing a category starts at all its targets, keeping the other filters.
    $("target-id").value = "";
    action(() => query(0, queryFields()), "正在更新類別結果…");
  });
  for (const id of ["target-id", "target-sample", "target-attention"]) {
    $(id).addEventListener("change", () =>
      action(() => query(0, queryFields()), "正在更新結果…"),
    );
  }
  $("back-to-overview").addEventListener("click", () => navigation.back(
    showTargetedOverview,
    saved => saved.workspace === "review" && saved.review?.overview &&
      saved.review.page?.source === state.page?.source,
  ));
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
  async function cancelCurves() {
    if (!state.job) return;
    try {
      state.job = await api(`/api/jobs/${state.job.id}/cancel`, {});
      updateProgress(state.job);
      controls();
    } catch (error) {
      status(error.message, "error");
    }
  }
  $("cancel").addEventListener("click", cancelCurves);
  $("xic-cancel").addEventListener("click", cancelCurves);
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
    $("source").value = event.detail.source;
    if (event.detail.reviewPaths) {
      $("raw-dir").value = event.detail.reviewPaths.raw_dir;
      $("dll-dir").value = event.detail.reviewPaths.dll_dir;
    }
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
      if (review.page.overview) showTargetedOverview();
      else if (review.detail) restoreDetail(review.detail);
      if (review.job) {
        state.job = review.job;
        state.curveView = {id: null};
        $("job-progress").hidden = false;
        await pollJob(state.curveView);
      }
    } else if ($("source").value) await openBatch();
    else status("");
  }, "正在連接本機工作區…");
})();
