// Curation draft, source discovery and preview-backed export admission.
// Presentation submits edits and reads snapshots; it never compares requests.
const browserClock = {
  setTimeout: (callback, delay) => setTimeout(callback, delay),
  clearTimeout: (timer) => clearTimeout(timer),
};
function freeze(value) {
  if (value && typeof value === "object") {
    Object.freeze(value);
    for (const child of Object.values(value)) freeze(child);
  }
  return value;
}
const owned = (value) => freeze(structuredClone(value));

export function createCurationSession(
  { request, startJob, preferences = {}, adoptPreferences = () => {} },
  clock = browserClock,
) {
  let sources = owned({ source_dir: "", sample_metadata_path: "", istd_path: "" });
  let runSource = "";
  let samples = null;
  let detection = null;
  let settings = owned({});
  let remembered = owned(preferences);
  let approved = null;
  let preview = null;
  let sourceError = "";
  let message = "請選擇 Alignment 輸出資料夾。";
  let revision = 0;
  let requestPending = false;
  let timer = null;
  let exporting = false;
  let disposed = false;
  const listeners = new Set();

  function snapshot() {
    return Object.freeze({
      sources,
      runSource,
      detection,
      settings,
      preview,
      sourceError,
      message,
      exporting,
      canExport: !disposed && !exporting && approved?.revision === revision,
    });
  }
  let current = snapshot();
  function publish() {
    current = snapshot();
    for (const listener of listeners) listener(current);
  }
  function schedule(delay = 300) {
    if (timer !== null) clock.clearTimeout(timer);
    timer = null;
    if (!disposed && sources.source_dir)
      timer = clock.setTimeout(refresh, delay);
  }
  function invalidate() {
    revision += 1;
    approved = null;
    preview = null;
    message = sources.source_dir
      ? "正在更新預覽…"
      : "請選擇 Alignment 輸出資料夾。";
  }
  function replaceSources(values) {
    sources = owned(values);
    detection = null;
    settings = owned({});
    sourceError = "";
    invalidate();
    publish();
    schedule();
  }
  function stepSettingsFor(steps) {
    const draft = {};
    for (const step of steps) {
      const saved = remembered[step.key];
      const parameters = {};
      for (const parameter of step.parameters)
        parameters[parameter.key] = String(
          saved?.parameters?.[parameter.key] ?? "",
        );
      draft[step.key] = {
        enabled: !step.unavailable_reason && (saved?.enabled ?? true),
        parameters,
      };
    }
    return owned(draft);
  }
  function requestDraft() {
    const steps = {};
    const saved = structuredClone(remembered);
    for (const step of detection.steps) {
      if (step.unavailable_reason) continue;
      const { enabled, parameters: draft } = settings[step.key];
      const parameters = {};
      for (const parameter of step.parameters) {
        const value = draft[parameter.key].trim();
        if (enabled && value === "")
          throw new Error(`請輸入「${step.label}」的${parameter.label}。`);
        if (value !== "") parameters[parameter.key] = Number(value);
      }
      saved[step.key] = { enabled, parameters };
      if (enabled) steps[step.key] = parameters;
    }
    if (!Object.keys(steps).length)
      throw new Error("請至少啟用一個可用的整理步驟。");
    return owned({
      revision,
      remembered: saved,
      values: {
        ...sources,
        source_dir: detection.source_dir,
        samples: sources.source_dir === runSource ? samples : null,
        steps,
      },
    });
  }
  async function refresh() {
    timer = null;
    if (disposed || requestPending || !sources.source_dir) return;
    const kind = detection ? "preview" : "source";
    let draft;
    if (kind === "preview") {
      try {
        draft = requestDraft();
      } catch (error) {
        message = error.message;
        publish();
        return;
      }
    }
    const requestRevision = revision;
    requestPending = true;
    message =
      kind === "source"
        ? "正在讀取整理來源…"
        : "正在計算預覽；仍可修改設定或檢閱結果。";
    publish();
    let response;
    let failure;
    try {
      response = await request(
        `/api/matrix-curation/${kind}`,
        kind === "source"
          ? {
              ...sources,
              samples: sources.source_dir === runSource ? samples : null,
            }
          : draft.values,
      );
    } catch (error) {
      failure = error;
    }
    requestPending = false;
    if (disposed) return;
    if (requestRevision !== revision) {
      schedule();
      return;
    }
    if (failure) {
      message = failure.message;
      if (kind === "source") sourceError = message;
      publish();
      return;
    }
    if (kind === "source") {
      detection = owned(response);
      settings = stepSettingsFor(detection.steps);
      message = "正在更新預覽…";
      publish();
      schedule();
    } else {
      approved = draft;
      preview = owned(response);
      message = "";
      publish();
    }
  }

  return Object.freeze({
    get snapshot() {
      return current;
    },
    subscribe(listener) {
      listeners.add(listener);
      listener(current);
      return () => listeners.delete(listener);
    },
    load(sourceDir, runSamples) {
      if (disposed) return;
      runSource = sourceDir.trim();
      samples = owned(runSamples ?? null);
      replaceSources({
        source_dir: runSource,
        sample_metadata_path: "",
        istd_path: "",
      });
      schedule(0);
    },
    setSources(values, { refresh = false } = {}) {
      if (disposed) return;
      const next = Object.fromEntries(
        Object.keys(sources).map((key) => [key, values[key].trim()]),
      );
      if (
        refresh ||
        sourceError ||
        Object.keys(next).some((key) => next[key] !== sources[key])
      )
        replaceSources(next);
    },
    setStep(key, values) {
      const step = detection?.steps.find((step) => step.key === key);
      if (disposed || !step || step.unavailable_reason) return;
      const previous = settings[key];
      if (
        previous.enabled === values.enabled &&
        step.parameters.every((parameter) =>
          previous.parameters[parameter.key] === values.parameters[parameter.key],
        )
      )
        return;
      settings = owned({ ...settings, [key]: values });
      invalidate();
      publish();
      schedule();
    },
    async export() {
      if (!current.canExport)
        throw new Error("預覽正在更新，請稍候再產生新矩陣。");
      const submitted = approved;
      exporting = true;
      publish();
      try {
        const preferences = await request("/api/preferences", {
          matrix_curation: { steps: submitted.remembered },
        });
        remembered = submitted.remembered;
        adoptPreferences(preferences);
        if (disposed || submitted.revision !== revision)
          throw new Error("設定已變更，請等候新的預覽再匯出。");
        return await startJob("curation", submitted.values);
      } finally {
        exporting = false;
        publish();
      }
    },
    dispose() {
      disposed = true;
      revision += 1;
      if (timer !== null) clock.clearTimeout(timer);
      timer = null;
      listeners.clear();
      current = snapshot();
    },
  });
}
