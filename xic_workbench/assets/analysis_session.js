// Analysis lifecycle. Views submit intentions and observe immutable snapshots;
// transport, application adoption and confirmation are supplied at construction.
const browserClock = {
  now: () => Date.now(),
  setInterval: (callback, delay) => setInterval(callback, delay),
  clearInterval: (timer) => clearInterval(timer),
};

function freezeSnapshot(value) {
  if (value && typeof value === "object") {
    Object.freeze(value);
    for (const child of Object.values(value)) freezeSnapshot(child);
  }
  return value;
}

const running = (job) => ["running", "cancelling"].includes(job?.status);
const terminal = (job) => job && !running(job);

export function createAnalysisSession(
  initial,
  {
    request,
    prepareStart = () => null,
    acceptedStart = () => {},
    confirmCancel = () => true,
  },
  clock = browserClock,
) {
  let data = freezeSnapshot(structuredClone(initial));
  let seenCompletion = terminal(data.job) ? data.job.job_id : null;
  let lastCompletionAt = null;
  let pending = null;
  let confirming = false;
  let revision = 0;
  let poll = null;
  let timer = null;
  let tick = 0;
  let failures = 0;
  const listeners = new Set();

  function snapshot() {
    const busy = running(data.job) || pending !== null || confirming;
    return Object.freeze({
      job: data.job,
      results: data.results,
      running: running(data.job),
      busy,
      canStart: !busy,
      canCancel:
        data.job?.status === "running" &&
        Boolean(data.job.cancellable) &&
        pending === null &&
        !confirming,
      connection: failures >= 3 ? "offline" : "online",
      lastCompletionAt,
    });
  }

  let current = snapshot();
  function publish(completed = null) {
    current = snapshot();
    for (const listener of listeners) listener(current, completed);
  }

  function publishAccepted() {
    let completed = null;
    if (terminal(data.job) && data.job.job_id !== seenCompletion) {
      seenCompletion = data.job.job_id;
      lastCompletionAt = clock.now();
      completed = data.job;
    }
    publish(completed);
  }

  async function readProgress() {
    tick += 1;
    if (poll || pending !== null || (failures >= 3 && tick % 6)) return;
    const reading = { revision };
    poll = reading;
    let response;
    try {
      response = await request("/api/analysis");
    } catch {
      if (poll === reading && revision === reading.revision) {
        failures += 1;
        publish();
      }
      return;
    } finally {
      if (poll === reading) poll = null;
    }
    if (!listeners.size || revision !== reading.revision) return;
    data = freezeSnapshot(response);
    failures = 0;
    publishAccepted();
  }

  return Object.freeze({
    get snapshot() {
      return current;
    },

    subscribe(listener) {
      listeners.add(listener);
      listener(current, null);
      if (timer === null) timer = clock.setInterval(readProgress, 750);
      return () => {
        listeners.delete(listener);
        if (!listeners.size && timer !== null) {
          clock.clearInterval(timer);
          timer = null;
          revision += 1;
          poll = null;
        }
      };
    },

    async start(kind, inputs) {
      if (!current.canStart) throw new Error("請等待目前工作完成或先停止。");
      const submitted = structuredClone(inputs);
      const context = prepareStart(kind, submitted);
      pending = "start";
      revision += 1;
      publish();
      let accepted = false;
      try {
        data = freezeSnapshot(
          await request("/api/analysis/start", { kind, values: submitted }),
        );
        failures = 0;
        accepted = true;
        // Bind application settings and sample action before any completion
        // can be delivered, including when that follow-up itself fails.
        await acceptedStart(kind, submitted, data.job, context);
        return data.job;
      } finally {
        pending = null;
        if (accepted) publishAccepted();
        else publish();
      }
    },

    async cancel() {
      if (!current.canCancel) return;
      const intended = data.job;
      confirming = true;
      publish();
      try {
        if (!(await confirmCancel(intended))) return;
        if (
          data.job?.job_id !== intended.job_id ||
          data.job.status !== "running" ||
          !data.job.cancellable
        )
          return;
        pending = "cancel";
        revision += 1;
        publish();
        let accepted = false;
        try {
          data = freezeSnapshot(
            await request("/api/analysis/cancel", { job_id: intended.job_id }),
          );
          failures = 0;
          accepted = true;
          return data.job;
        } finally {
          pending = null;
          if (accepted) publishAccepted();
        }
      } finally {
        confirming = false;
        publish();
      }
    },
  });
}
