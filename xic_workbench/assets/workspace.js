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
  function trackWindow() {
    const windowId = crypto.randomUUID();
    let timer;
    const notify = (closed = false) => fetch("/api/window", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-XIC-Client": "workbench" },
      body: JSON.stringify({ window: windowId, closed }),
      keepalive: true,
    }).catch(() => {}); // Closing/crashed tabs are also covered by lease expiry.
    const resume = () => {
      clearInterval(timer);
      notify();
      timer = setInterval(() => notify(), 30000);
    };
    const leave = () => { clearInterval(timer); notify(true); };
    const visible = () => { if (!document.hidden) notify(); };
    window.addEventListener("pageshow", resume);
    window.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", visible);
    resume();
    return () => {
      clearInterval(timer);
      window.removeEventListener("pageshow", resume);
      window.removeEventListener("pagehide", leave);
      document.removeEventListener("visibilitychange", visible);
    };
  }
  return { icon, toast, confirm, setTheme, formatDuration, navigation, trackWindow };
})();

(() => {
  const $ = (id) => document.getElementById(id);
  const { navigation } = window.XicUi;
  const state = {
    runFolders: new Map(),
    runFolder: null,
    page: null,
    get item() { return this.detail?.item ?? null; },
    detail: null,
    curveView: null,
    curveCache: new Map(),
    curveOpener: null,
    busy: false,
    restoring: false,
    job: null,
    samples: new Set(),
    filters: {},
  };
  const fmt = (value, digits) =>
    Number.isFinite(value) ? value.toFixed(digits) : "—";
  const count = (value) => Number(value).toLocaleString();
  // Mass distance reads in ppm of the queried m/z, the unit used for tolerance.
  const ppm = (distance, mz) => {
    const value = (distance / mz) * 1e6;
    return value < 10 ? value.toFixed(1) : Math.round(value).toLocaleString();
  };
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
  // Headlines stay in plain language; the technical reason is one hover away.
  function status(message, kind = "", owner = "review", detail = "") {
    statusOwner = owner;
    $("status").textContent = message;
    $("status").className = `inline-status ${kind}`;
    $("status").title = detail;
  }
  function curveStatus(message, kind = "", detail = "") {
    $("xic-status").textContent = message;
    $("inline-curve-status").textContent = message;
    $("inline-curve-status").className = kind;
    $("inline-curve-status").title = detail;
    // Untargeted curves report beside their plot. Targeted curve completion may
    // replace its own page feedback, not a still-valid query or saved-result
    // error; the drawer retains the curve's local feedback.
    if (state.page?.source_kind !== "targeted_workbook") {
      if (statusOwner === "curves") status("", "", "curves");
      return;
    }
    if (statusOwner === "curves" || !$("status").classList.contains("error"))
      status(message, kind, "curves");
  }
  async function api(path, values, signal) {
    const response = await fetch(path, {
      signal,
      method: values === undefined ? "GET" : "POST",
      headers: {
        "X-XIC-Client": "workbench",
        ...(values === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(values === undefined ? {} : { body: JSON.stringify(values) }),
    });
    const data = await response.json();
    if (!response.ok)
      throw Object.assign(new Error(data.error || `要求失敗（${response.status}）`), {
        detail: data.detail || "",
      });
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
        samples: state.page.all_samples,
        sampleIds: state.page.samples.map(sample => sample.id),
        onRequestCurves: request => {
          if (reviewSlots.get(id) === slot && state.detail === slot.detail)
            requestCurves(request);
        },
        // Only a run-bound result knows which RAW folder owns the exclusion.
        ...(mode === "result" &&
        state.page?.source_kind === "targeted_workbook" &&
        state.runFolder
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
    dialog.returnValue = "cancel";
    dialog.showModal();
    $("exclude-accept").focus();
    const accepted = await new Promise(resolve =>
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
    if (!accepted) return;
    try {
      const { raw_dir, dll_dir, sequence_source } = state.runFolder;
      await window.XicAnalysis.excludeSamples({
        raw_dir,
        dll_dir,
        sequence_source,
        raw_stem: sample_id,
      });
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
    // A new page ends the running curve read on the server.
    setCurveJob(null);
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
          serverPage === page) return;
      status("正在還原結果查詢…", "working");
      await requestPage("/api/query", {
        ...filters, review_scope: page.review_scope, offset: page.offset, limit: page.limit,
      });
      if (revision === navigation.revision && state.page === page) {
        // The unchanged source keeps saved report/item IDs valid. Reuse its
        // reading snapshot after restoring the corresponding server query.
        serverPage = page;
        status("");
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
    return state.busy || state.restoring;
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
    $("review-range-filters").querySelector("summary").textContent = nearest
      ? "RT 範圍" : "m/z / RT 範圍";
    if (nearest) $("review-range-filters").open = true;
    validateNearestMz();
  }
  function controls() {
    const busy = locked(), analysisBusy = window.XicAnalysis?.busy || false;
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
      busy || analysisBusy || !state.item || state.samples.size === 0;
    const supported = document.querySelectorAll(
      '#sample-list input[data-available="true"]',
    ).length;
    $("acquisition-hint").textContent = !state.item
      ? ""
      : !supported
        ? "沒有樣本保留觀測座標，無法取得 XIC。"
        : analysisBusy
          ? "分析執行中；完成後才能讀取 RAW。"
          : "";
    $("sample-count").textContent = `${state.samples.size} / ${supported}`;
    const index = itemIndex();
    $("item-position").textContent = index < 0 ? ""
      : `${count(state.page.offset + index + 1)} / ${count(state.page.matched_count)}`;
    $("item-previous").disabled = busy || index < 0 || state.page.offset + index === 0;
    $("item-next").disabled = busy || index < 0 ||
      state.page.offset + index >= state.page.matched_count - 1;
    $("previous").disabled = busy || !state.page || state.page.offset === 0;
    $("next").disabled =
      busy ||
      !state.page ||
      state.page.offset + state.page.limit >= state.page.matched_count;
    $("cancel").hidden = !state.job;
    $("cancel").disabled = state.job?.status === "cancelling";
    const sampleIndex = state.item?.curve_sample_ids.indexOf($("xic-sample").value) ?? -1;
    $("xic-sample").disabled = busy || analysisBusy;
    $("xic-previous").disabled = busy || analysisBusy || sampleIndex <= 0;
    $("xic-next").disabled = busy || analysisBusy || sampleIndex < 0 || sampleIndex >= (state.item?.curve_sample_ids.length || 0) - 1;
    $("xic-cancel").hidden = !state.job;
    $("xic-cancel").disabled = state.job?.status === "cancelling";
    $("xic-retry").disabled = busy || analysisBusy;
    syncQueryMode();
  }
  // A request that replaces the server's curve job first ends the poll of the
  // old one, so the poll never asks for a job the server no longer has.
  let pollAbort = null;
  function setCurveJob(job) {
    if (state.job?.id !== job?.id) {
      pollAbort?.abort();
      pollAbort = null;
    }
    state.job = job;
    $("job-progress").hidden = !job;
  }
  async function action(work, message) {
    if (locked()) return;
    state.busy = true;
    controls();
    if (message) status(message, "working");
    try {
      return await work();
    } catch (error) {
      status(error.message, "error", "review", error.detail);
    } finally {
      state.busy = false;
      controls();
    }
  }
  function clearSelection() {
    setCurveJob(null);
    $("inline-curves").hidden = true;
    $("curve-source-status").textContent = "";
    clearReview("inline-curve-review");
    state.detail = null;
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
      selectionEmpty("沒有符合的結果", emptyResultGuidance());
    } else {
      selectionEmpty("從清單選一筆查看", "");
    }
  }
  function selectionEmpty(title, description) {
    $("selection-empty-title").textContent = title;
    $("selection-empty-description").textContent = description;
  }
  const reviewScopes = {matrix: "矩陣群組", unresolved: "多峰留空的群組", archive: "未採用群組", candidates: "未分組候選"};
  function emptyResultGuidance() {
    const filters = state.filters, parts = [];
    if (state.page.source_kind !== "targeted_workbook") {
      parts.push(`範圍：${reviewScopes[state.page.review_scope] || "所有紀錄"}`);
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
    return parts.join("；") || "全批結果";
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
  // A fraction reads as a filled bar beside its exact count.
  function meter(value, total, label = "") {
    const wrap = node("span", undefined, "meter");
    const bar = node("span", undefined, "meter-bar");
    const fill = node("span");
    fill.style.width = `${total ? Math.round((value / total) * 100) : 0}%`;
    bar.append(fill);
    wrap.append(bar, node("span", `${count(value)} / ${count(total)}`, "meter-text"));
    if (label) wrap.title = `${label} ${count(value)} / ${count(total)}`;
    return wrap;
  }
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
    const lead = node("div", undefined, "overview-lead");
    const heading = node("div", undefined, "overview-heading");
    const conclusion = !summary.result_count ? "尚無已保存結果"
      : summary.focus_count ? `先看 ${count(summary.focus_count)} 筆結果`
        : "沒有需要先看的結果";
    const title = node("h2", conclusion);
    // The reading rule stays one hover away; the counts below show where it applies.
    title.title = `需先看：各目標中占 ${Math.round(summary.minority_share * 100)}% 以下的一方；只決定閱讀順序，不改變已保存的判定。`;
    heading.append(title);
    const actions = node("div", undefined, "overview-actions");
    if (summary.focus_count) actions.append(resultButton(`先看這 ${count(summary.focus_count)} 筆 →`, "", true, "primary"));
    actions.append(resultButton("查看所有樣本結果 →"));
    heading.append(actions);
    const metrics = node("div", undefined, "overview-metrics");
    const ratio = (counted, total) => `${count(counted)} / ${count(total)}`;
    for (const [value, label] of [
      [ratio(summary.analyte_counted, summary.analyte_total), "Analyte 計入"],
      [ratio(summary.istd_counted, summary.istd_total), "ISTD 計入"],
    ]) {
      if (label === "ISTD 計入" && !summary.istd_total) continue;
      const metric = node("div");
      metric.append(node("strong", value), node("span", label));
      metrics.append(metric);
    }
    lead.append(heading, metrics);
    host.append(lead);
    if (summary.count_unknown) host.append(node("p", `${count(summary.count_unknown)} 筆未記錄偵測判定，未推測為未偵測`, "overview-unknown"));
    const table = node("table", undefined, "overview-table overview-targets");
    const head = node("thead");
    const header = node("tr");
    for (const text of ["目標", "類型", "計入", "需先看", "要確認"]) header.append(node("th", text));
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
      const counted = node("td");
      counted.append(meter(target.counted, target.total));
      if (target.count_unknown) counted.append(node("span", ` ${target.count_unknown} 未記錄`, "overview-unknown"));
      const focus = node("td");
      if (target.focus_samples.length) focus.append(resultButton(`${target.focus_samples.length} 筆 →`, target.label, true, "text-button focus-link"));
      row.append(name, node("td", target.role || "—"), counted, focus, node("td", focusText(target)));
      body.append(row);
    }
    table.append(head, body);
    const scroll = node("div", undefined, "overview-table-scroll");
    scroll.append(table);
    host.append(scroll);
    const quantities = node("details", undefined, "overview-quantities");
    quantities.append(node("summary", "量值摘要（僅計入偵測）"));
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
    host.append(quantities);
  }
  function renderUntargetedOverview(summary) {
    const host = $("targeted-overview");
    host.replaceChildren();
    const openScope = (label, scope, parent, className = "") => {
      const button = node("button", label, className); button.type = "button"; button.dataset.idle = "";
      button.addEventListener("click", () => action(async () => {
        navigation.push(); $("query-form").reset(); await query(0, {review_scope: scope});
      })); parent.append(button);
    };
    const lead = node("div", undefined, "overview-lead");
    const heading = node("div", undefined, "overview-heading");
    heading.append(node("h2", `${count(summary.matrix_count)} 組進入矩陣`));
    const actions = node("div", undefined, "overview-actions");
    openScope("瀏覽矩陣群組", "matrix", actions, "primary");
    heading.append(actions);
    const metrics = node("div", undefined, "overview-metrics");
    const complete = node("div"); complete.append(node("strong", count(summary.complete_count)), node("span", "全部樣本都有值")); metrics.append(complete);
    if (summary.unresolved_count) {
      // Unresolved cells stay blank in the Matrix: a count to open, not a task.
      const unresolved = node("button", undefined, "overview-metric-link"); unresolved.type = "button"; unresolved.dataset.idle = "";
      unresolved.title = "有多個可能的峰、無法判定，矩陣中留空";
      unresolved.append(node("strong", count(summary.unresolved_count)), node("span", "部分樣本多峰留空"));
      unresolved.addEventListener("click", () => action(async () => {
        navigation.push(); $("query-form").reset(); await query(0, {review_scope: "unresolved"});
      }));
      metrics.append(unresolved);
    }
    lead.append(heading, metrics);
    host.append(lead);
    const table = node("table", undefined, "overview-table overview-coverage");
    const head = node("tr"); head.append(node("th", "樣本"), node("th", "有定量值的矩陣群組")); table.append(head);
    for (const sample of summary.samples) {
      const row = node("tr"), cell = node("td");
      cell.append(meter(sample.numeric_count, summary.matrix_count));
      row.append(node("td", sample.label), cell); table.append(row);
    }
    host.append(table);
    const more = node("details", undefined, "overview-more"); more.append(node("summary", "未進入矩陣的紀錄"));
    const archived = node("div", undefined, "overview-actions");
    openScope(`${count(summary.archive_count)} 組未採用`, "archive", archived);
    openScope(`${count(summary.candidate_count)} 筆未分組候選`, "candidates", archived);
    more.append(archived);
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
    $("group-curve-settings").hidden = targeted;
    $("sample-count").hidden = targeted;
    $("targeted-curve-help").hidden = !targeted;
    const selectedSample = $("target-sample").value;
    $("target-sample").replaceChildren(new Option("全部樣本", ""), ...(page.all_samples || page.samples).map(s => new Option(s.label, s.id)));
    $("target-sample").value = selectedSample;
    clearSelection();
    $("review").hidden = false;
    $("review-empty").hidden = true;
    $("batch-title").textContent = targeted ? "Targeted 結果" : "Untargeted 結果";
    // Untargeted evidence files share one name; the run folder identifies the batch.
    const sourceParts = page.source.split(/[\\/]/);
    $("batch-description").textContent = targeted ? sourceParts.at(-1) : sourceParts.at(-2) || sourceParts.at(-1);
    $("batch-description").hidden = false;
    $("batch-description").className = "mono";
    $("batch-description").title = page.source;
    const download = $("result-download");
    download.hidden = !page.result_artifact;
    if (page.result_artifact) {
      download.href = page.result_artifact.url;
      download.download = page.result_artifact.name;
      download.title = page.result_artifact.name;
      download.querySelector("span").textContent = targeted ? "下載結果 Excel" : "下載 Alignment 矩陣";
    } else {
      download.removeAttribute("href");
      download.removeAttribute("download");
    }
    $("batch-count").hidden = false;
    $("batch-count").textContent = page.overview?.kind === "untargeted"
      ? `${count(page.samples.length)} 樣本 · ${count(page.overview.matrix_count)} 組`
      : `${count(page.samples.length)} 樣本 · ${count(page.run_total)} ${targeted ? "目標" : "項目"}`;
    const filtered = page.matched_count !== page.run_total;
    const untargetedScope = page.overview?.kind === "untargeted";
    const scopeName = reviewScopes[page.review_scope];
    $("list-summary").replaceChildren(
      node("strong", untargetedScope ? scopeName || "所有紀錄" : noun),
      node("span", untargetedScope ? `${count(page.matched_count)} 組`
        : filtered ? `${count(page.matched_count)} / ${count(page.run_total)}` : count(page.run_total)),
    );
    // The list itself shows the ordering through each row's distance.
    if (nearest) $("list-summary").append(node("span", `≈ m/z ${page.nearest_mz}`, "list-sort"));
    $("nearest-results-note").hidden = !(nearest && page.missing_mz_count);
    $("nearest-results-note").textContent = nearest && page.missing_mz_count
      ? `${count(page.missing_mz_count)} 組缺少有效 m/z，未排序`
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
        : `m/z ${fmt(item.precursor_mz, 4)}`;
      const rt = document.createElement("span");
      rt.className = "item-rt";
      rt.textContent = targeted ? `m/z ${fmt(item.precursor_mz, 4)}` : `RT ${fmt(item.rt_apex_min, 2)} min`;
      top.append(title, rt);
      const bottom = document.createElement("span");
      bottom.className = "item-bottom";
      // Coverage reads as a bar; a chip appears only when the saved result
      // carries something to check.
      if (targeted) bottom.append(node("span", `${item.review_summary.scope_sample_count} 樣本`));
      else bottom.append(meter(item.numeric_count, page.samples.length, "有值樣本"));
      const label = targeted
        ? item.badge_label && item.badge_label !== "已記錄" ? item.badge_label : ""
        : item.unresolved_count ? `${item.unresolved_count} 未解決` : "";
      if (label) bottom.append(node("span", label, `chip ${badgeTone[label] || "warn"}`));
      button.append(top);
      if (nearest) {
        button.append(node("span", `Δ ${ppm(item.mz_distance, page.nearest_mz)} ppm`, "item-distance"));
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
    status("");
  }
  async function selectItem(item) {
    const revision = navigation.revision;
    // The server ends the previous item's curve read with this selection.
    setCurveJob(null);
    const detail = await api("/api/detail", { item_id: item.id });
    if (revision === navigation.revision) {
      restoreDetail(detail);
      if (state.page.source_kind !== "targeted_workbook") {
        $("inline-curves").hidden = false;
        const status = $("inline-curve-status");
        // Saved curves show at once; RAW is read only for samples the user loads.
        if (!state.item.curve_sample_ids.length) status.textContent = "沒有觀測座標，無法取得 XIC。";
        else if (!state.samples.size) status.textContent = "此項目沒有保存的曲線；勾選樣本後按「載入 XIC」從 RAW 讀取。";
        else await startCurves();
      }
    }
  }
  // Samples without a saved curve are read from RAW.
  function needsRaw() {
    const saved = new Set(state.detail?.item.saved_curve_sample_ids || []);
    return [...state.samples].some(id => !saved.has(id));
  }
  function rawSourceSet() {
    return Boolean($("raw-dir").value.trim() && $("dll-dir").value.trim());
  }
  function restoreDetail(detail) {
    clearReview("inline-curve-review");
    clearReview("xic-review");
    state.curveView = null;
    state.detail = detail;
    const item = detail.item;
    state.curveCache.clear();
    $("selection").hidden = false;
    $("selection-empty").hidden = true;
    $("job-progress").hidden = true;
    const targeted = state.page.source_kind === "targeted_workbook";
    $("item-kind").textContent = targeted ? "" : item.title;
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
    const saved = new Set(detail.item.saved_curve_sample_ids || []);
    state.samples = targeted ? new Set(
      [...state.samples].filter((id) => available.has(id)),
    ) : saved;
    if (targeted && !state.samples.size)
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
      const noteText = !supported ? "無觀測座標"
        : !targeted && !saved.has(sample.id) ? "讀 RAW" : "";
      if (noteText) {
        const note = document.createElement("small");
        note.textContent = noteText;
        text.append(note);
      }
      label.append(checkbox, text);
      list.append(label);
    }
    $("xic-sample").replaceChildren(...detail.item.curve_sample_ids.map(id => new Option(
      state.page.samples.find(sample => sample.id === id)?.label || id, id,
    )));
    showRecorded();
    status("");
  }
  function showRecorded() {
    mountReview("recorded-review", state.detail.view_id, "result");
    $("download").href = `/reports/${state.detail.view_id}.html?download=1`;
    $("download").hidden = state.page.source_kind === "targeted_workbook";
  }
  async function showCurveView(view) {
    state.curveView = view;
    const targeted = state.page.source_kind === "targeted_workbook";
    if (targeted) $("xic-download").href = `/reports/${view.id}.html?download=1`;
    const loadedLabel = () => $("xic-sample").selectedOptions[0]?.textContent;
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
    if (!targeted) {
      // The plotted curves are their own receipt beside the saved values;
      // only samples left out of the plot need words.
      const labels = (view.unusable || []).join("、");
      curveStatus(labels ? `${labels} 在本次擷取範圍沒有可用訊號` : "");
      $("inline-curve-review").hidden = false;
      return;
    }
    curveStatus(`${loadedLabel()} 的診斷曲線已載入`);
    $("xic-review").hidden = false;
    $("xic-loading").hidden = true;
    $("xic-retry").hidden = true;
    $("xic-download").hidden = false;
  }
  function curveMessage(message, retry = false, kind = retry ? "error" : "working", detail = "") {
    curveStatus(message, kind, detail);
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
    state.runFolder = state.runFolders.get($("source").value) || null;
    const page = await requestPage("/api/open", { source: $("source").value });
    $("query-form").reset();
    state.filters = {};
    state.samples = new Set();
    renderPage(page);
    $("source-settings").open = false;
    if (page.overview) showTargetedOverview();
    else if (page.matched_count === 1) await selectItem(page.items[0]);
    document.dispatchEvent(new CustomEvent("xic:result-opened", {detail: page}));
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
  // A list opens no item unless it has exactly one result; stepping across a
  // page boundary opens the first or last item.
  async function query(offset, filters = state.filters, select = null) {
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
    if (page.items.length && (select || page.matched_count === 1))
      await selectItem(page.items[select === "last" ? page.items.length - 1 : 0]);
    return revision === navigation.revision;
  }
  // Reading continues across page boundaries of the same filtered list.
  function itemIndex() {
    return state.page && state.item
      ? state.page.items.findIndex((item) => item.id === state.item.id)
      : -1;
  }
  async function stepItem(step) {
    const page = state.page, target = itemIndex() + step;
    if (target >= 0 && target < page.items.length) {
      await selectItem(page.items[target]);
      document.querySelector(`.item[data-item-id="${CSS.escape(page.items[target].id)}"]`)
        ?.scrollIntoView({ block: "nearest" });
      return;
    }
    const offset = page.offset + step * page.limit;
    if (offset < 0 || offset >= page.matched_count) return;
    await query(offset, state.filters, step < 0 ? "last" : "first");
  }
  async function startCurves() {
    const targeted = state.page.source_kind === "targeted_workbook";
    if ((targeted || needsRaw()) && !rawSourceSet()) {
      $("curve-settings").open = true;
      const rawMissing = !$("raw-dir").value.trim();
      const missing = $(rawMissing ? "raw-dir" : "dll-dir");
      // The click may come from far down the page; bring the field and the
      // status line below it on screen. action() disables inputs while it
      // runs, so focus waits until they are enabled again.
      missing.scrollIntoView({ block: "center" });
      setTimeout(() => missing.focus({ preventScroll: true }));
      const message = `看 XIC 需要讀取 RAW。請先選擇${rawMissing ? " RAW 資料夾" : " RawFileReader DLL 資料夾"}。`;
      $("curve-source-status").textContent = message;
      curveStatus(message, "error");
      return;
    }
    $("curve-source-status").textContent = "";
    $("xic-title").textContent = targeted ? state.item.title : `XIC · m/z ${fmt(state.item.precursor_mz, 4)}`;
    $("xic-navigation").hidden = !targeted;
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
      padding_min: Number($("padding").value),
      ...(rawSourceSet() ? {raw_dir: $("raw-dir").value, dll_dir: $("dll-dir").value} : {}),
    };
    const key = JSON.stringify([state.detail.view_id, request]);
    const previousJob = state.job;
    setCurveJob(null);
    if (state.curveCache.has(key)) {
      // A cache hit sends no new /curves request to replace the server's read.
      if (previousJob) await api(`/api/jobs/${previousJob.id}/cancel`, {});
      showCurveView({...state.curveCache.get(key)});
      return;
    }
    curveMessage(targeted || needsRaw() ? "正在讀取所選樣本的 RAW；不會重跑整批分析。"
      : "正在顯示保存的曲線…");
    clearReview("xic-review");
    const view = state.curveView = {id: null, key};
    const detail = state.detail;
    try {
      const job = await api("/api/curves", request);
      if (state.detail !== detail || state.curveView !== view) return;
      if (job.status === "running") {
        setCurveJob(job);
        $("job-progress").hidden = false;
        updateProgress(job);
        controls();
        setTimeout(() => pollJob(job.id, view), 100);
      } else finishCurves(job, view);
    } catch (error) {
      if (state.curveView === view) curveMessage(error.message, true);
    }
  }
  function finishCurves(job, view) {
    if (state.curveView !== view) return;
    if (job.status === "succeeded") {
      view.id = job.view_id;
      view.unusable = job.unusable_samples || [];
      if (view.key) state.curveCache.set(view.key, {...view});
      if (state.curveCache.size > 8) state.curveCache.delete(state.curveCache.keys().next().value);
      showCurveView(view);
    } else {
      curveMessage(job.message, true, job.status === "failed" ? "error" : "", job.detail);
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
  // A poll follows one job; a newer action that replaced it ends the poll.
  async function pollJob(jobId, view) {
    if (state.job?.id !== jobId) return;
    try {
      pollAbort = new AbortController();
      const job = await api(`/api/jobs/${jobId}`, undefined, pollAbort.signal);
      if (state.job?.id !== jobId) return;
      setCurveJob(job);
      updateProgress(job);
      if (["running", "cancelling"].includes(job.status)) {
        controls();
        setTimeout(() => pollJob(jobId, view), 150);
        return;
      }
      $("job-progress").hidden = true;
      setCurveJob(null);
      finishCurves(job, view);
      if (!state.restoring && serverPage !== state.page) restoreQuery(state.page, state.filters);
      else controls();
    } catch (error) {
      if (state.job?.id !== jobId) return;
      curveStatus(`無法讀取工作進度：${error.message}。正在重新連線…`, "error");
      setTimeout(() => pollJob(jobId, view), 2000);
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
    saved => saved.review?.overview &&
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
  $("item-previous").addEventListener("click", () =>
    action(() => stepItem(-1), "正在開啟上一筆…"));
  $("item-next").addEventListener("click", () =>
    action(() => stepItem(1), "正在開啟下一筆…"));
  $("load-curves").addEventListener("click", () =>
    action(startCurves, "正在準備所選樣本 XIC…"),
  );
  async function cancelCurves() {
    if (!state.job) return;
    try {
      setCurveJob(await api(`/api/jobs/${state.job.id}/cancel`, {}));
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
  // "瀏覽" opens the operating system's own chooser on this computer.
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-browse]");
    if (!button || button.getAttribute("aria-busy") === "true") return;
    const target = $(button.dataset.browse);
    button.setAttribute("aria-busy", "true");
    try {
      const { path } = await api("/api/pick", {
        kind: button.dataset.kind || "folder",
        path: target.value,
      });
      if (!path) return;
      target.value = path;
      target.dispatchEvent(new Event("input", { bubbles: true }));
      target.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (error) {
      window.XicUi.toast(error.message, "error");
    } finally {
      button.removeAttribute("aria-busy");
    }
  });
  // A result opened from its run keeps that run's folders; the editable
  // RAW field may later point elsewhere, for example to load curves.
  document.addEventListener("xic:open-review", (event) => {
    $("source").value = event.detail.source;
    if (event.detail.reviewPaths) {
      $("raw-dir").value = event.detail.reviewPaths.raw_dir;
      $("dll-dir").value = event.detail.reviewPaths.dll_dir;
      state.runFolders.set(event.detail.source, { ...event.detail.reviewPaths });
    } else {
      $("raw-dir").value = "";
    }
    action(openBatch, "正在讀取分析結果…");
  });
  document.addEventListener("xic:analysis-busy", controls);
  document.addEventListener("xic:analysis-started", () => {
    // The accepted analysis replaces the server's curve job, including a
    // /curves response that has not reached this tab yet.
    if (state.job || (state.curveView && !state.curveView.id)) {
      setCurveJob(null);
      curveMessage("已停止曲線載入，分析工作已開始。", false, "");
      controls();
    }
  });
  window.XicReview = {
    session: api("/api/session"),
  };
  // One initial read is shared with the setup page, regardless of load order.
  window.XicReview.ready = action(async () => {
    const session = await window.XicReview.session;
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
        setCurveJob(review.job);
        state.curveView = {id: null};
        $("job-progress").hidden = false;
        pollJob(review.job.id, state.curveView);
      }
    } else if ($("source").value) await openBatch();
    else status("");
    return { page: state.page, opened: !review.page && !!session.defaults.source };
  }, "正在連接本機工作區…");
})();
