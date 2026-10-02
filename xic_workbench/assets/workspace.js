"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
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
  function status(message, kind = "") {
    $("status").textContent = message;
    $("status").className = `status ${kind}`;
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
  function renderPage(page) {
    state.page = page;
    clearSelection();
    $("review").hidden = false;
    $("batch-title").textContent = "批次結果已開啟";
    $("batch-description").textContent = page.source;
    $("batch-count").textContent =
      `${count(page.samples.length)} 個樣本 · ${count(page.run_total)} 個檢閱項目`;
    $("list-summary").textContent =
      `符合 ${count(page.matched_count)} ／全批 ${count(page.run_total)} 個項目`;
    const list = $("item-list");
    list.replaceChildren();
    for (const item of page.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "item";
      button.dataset.itemId = item.id;
      button.setAttribute("aria-current", "false");
      const title = document.createElement("strong");
      title.textContent = `m/z ${fmt(item.precursor_mz, 4)}`;
      const metadata = document.createElement("span");
      metadata.className = "item-meta";
      const rt = document.createElement("span");
      rt.textContent = `RT ${fmt(item.rt_apex_min, 2)} min`;
      const type = document.createElement("span");
      type.textContent = item.title;
      metadata.append(rt, type);
      const samples = document.createElement("span");
      samples.className = "item-samples";
      samples.textContent = `${item.review_summary.observed_sample_count || 0} 個樣本有觀測 · ${item.badge_label || "已記錄"}`;
      button.append(title, metadata, samples);
      button.addEventListener("click", () =>
        action(() => selectItem(item), "正在準備檢閱內容…"),
      );
      list.append(button);
    }
    if (!page.items.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "沒有符合條件的項目";
      list.append(empty);
    }
    $("page-label").textContent = page.selected_count
      ? `${count(page.offset + 1)}–${count(page.offset + page.selected_count)}`
      : "0 筆";
    status(
      `已載入 ${count(page.selected_count)} 個項目；搜尋範圍涵蓋全批已儲存結果。`,
    );
  }
  async function selectItem(item) {
    const detail = await api("/api/detail", { item_id: item.id });
    state.item = detail.item;
    state.recorded = detail.view_id;
    state.curves = null;
    state.curveLabels = [];
    $("selection").hidden = false;
    $("selection-empty").hidden = true;
    $("job-progress").hidden = true;
    $("item-kind").textContent = item.title;
    $("item-title").textContent =
      `m/z ${fmt(item.precursor_mz, 4)} · RT ${fmt(item.rt_apex_min, 2)} min`;
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
        note.textContent = "缺少觀測座標";
        text.append(note);
      }
      label.append(checkbox, text);
      list.append(label);
    }
    showTab("recorded");
    status("可查看當次分析紀錄，或載入所選樣本的 XIC。");
  }
  function showTab(tab) {
    const view = tab === "curves" ? state.curves : state.recorded;
    if (!view) return;
    state.tab = tab;
    $("tab-recorded").setAttribute("aria-selected", String(tab === "recorded"));
    $("tab-curves").setAttribute("aria-selected", String(tab === "curves"));
    $("view-note").textContent =
      tab === "curves"
        ? `新取得的診斷曲線 · ${state.curveLabels.join("、")}。依報告樣本檔名取檔，已核對 RAW 內容屬於此批次；來源未保留獨立的樣本與 RAW 綁定。這些曲線不改寫當次定量值。`
        : "這是當次分析保存的證據與量值。若未保留曲線，可在上方按需取得 XIC。";
    $("inspector").src = `/reports/${view}.html?embed=1`;
    $("download").href = `/reports/${view}.html?download=1`;
    $("download").hidden = false;
  }
  async function openBatch() {
    const page = await api("/api/open", { source: $("source").value });
    $("query-form").reset();
    state.filters = {};
    state.samples = new Set();
    renderPage(page);
    $("source-settings").open = false;
    if (page.items.length) await selectItem(page.items[0]);
  }
  function queryFields() {
    const filters = { group_id: $("group-id").value.trim() };
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
  async function browse(path) {
    try {
      const data = await api("/api/browse", { path, kind: state.browser.kind });
      state.browser.data = data;
      $("browse-path").value = data.path;
      $("browse-parent").disabled = data.parent === null;
      $("browse-use").disabled = !data.path;
      $("browse-note").textContent = data.truncated
        ? "此處僅顯示前 1,000 個位置；可直接輸入完整路徑。"
        : "選擇資料夾，或點選 SQLite 結果檔案。";
      const list = $("browse-entries");
      list.replaceChildren();
      for (const entry of data.entries) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = `${entry.directory ? "▸" : "□"} ${entry.name}`;
        button.addEventListener("click", () =>
          entry.directory ? browse(entry.path) : useLocation(entry.path),
        );
        list.append(button);
      }
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
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-browse]");
    if (!button) return;
    state.browser = {
      target: button.dataset.browse,
      kind: button.dataset.kind,
    };
    $("browser-dialog").showModal();
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
  $("browse-use").addEventListener("click", () =>
    useLocation(state.browser.data.path),
  );
  action(async () => {
    const session = await api("/api/session");
    $("source").value = session.defaults.source || "";
    $("raw-dir").value = session.defaults.raw_dir || "";
    $("dll-dir").value = session.defaults.dll_dir || "";
    if ($("source").value) await openBatch();
    else status("請開啟一個批次。");
  }, "正在連接本機工作區…");
})();
