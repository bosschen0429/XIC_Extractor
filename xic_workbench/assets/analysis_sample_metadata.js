// Editable sample metadata, read recency and folder-scoped persistence.
// Consumers read immutable page snapshots and submit intentions, not revisions.
const pages = ["targeted", "untargeted"];
const empty = () =>
  Object.freeze({
    rows: Object.freeze([]),
    overview: null,
    source: "",
    summary: null,
  });
function freeze(value) {
  if (value && typeof value === "object") {
    Object.freeze(value);
    for (const child of Object.values(value)) freeze(child);
  }
  return value;
}
const owned = (value) => freeze(structuredClone(value));

export function createSampleMetadata({ request, inputs, jobBusy = () => false }) {
  const sets = Object.fromEntries(pages.map((page) => [page, empty()]));
  const revisions = { targeted: 0, untargeted: 0 };
  const saves = new Map();
  const listeners = new Set();
  let pendingWrites = 0;
  let jobRead = null;

  function publish(changed, replaced = false) {
    for (const listener of listeners) listener({ pages: changed, replaced });
  }
  function captureRead(page, paths = inputs(page)) {
    return { page, paths: { ...paths }, revisions: { ...revisions } };
  }
  function beginRead(page, paths) {
    revisions[page] += 1;
    return captureRead(page, paths);
  }
  const matching = (page, paths) =>
    Object.entries(paths).every(([key, value]) => inputs(page)[key] === value);
  const current = (read) =>
    revisions[read.page] === read.revisions[read.page] &&
    matching(read.page, read.paths);

  function mirror(read) {
    const changed = [read.page];
    for (const other of pages) {
      if (
        other !== read.page &&
        inputs(other).raw_dir === read.paths.raw_dir &&
        revisions[other] === read.revisions[other]
      ) {
        revisions[other] += 1;
        // Metadata is shared; each flow retains its own read summary.
        sets[other] = Object.freeze({
          ...sets[read.page],
          summary: sets[other].summary,
        });
        changed.push(other);
      }
    }
    return changed;
  }
  function adopt(read, result) {
    if (
      !current(read) ||
      (result.input_paths && !matching(read.page, result.input_paths))
    )
      return false;
    sets[read.page] = owned({
      rows: result.samples,
      overview: result.overview || null,
      source: result.summary?.annotation_source || "",
      summary: result.summary || null,
    });
    publish(result.summary?.confirmed ? mirror(read) : [read.page], true);
    return true;
  }

  function requireWritable() {
    if (jobBusy()) throw new Error("請等待目前工作完成或先停止。");
  }
  async function writeFolder(rawDir, send, accept) {
    pendingWrites += 1;
    publish([]);
    // Continue after a predecessor fails; its caller still receives that error.
    // Save and Review exclusion share this queue, not just their UI revisions.
    const saving = (saves.get(rawDir) || Promise.resolve()).then(send, send);
    saves.set(rawDir, saving);
    try {
      return accept(await saving);
    } finally {
      pendingWrites -= 1;
      if (saves.get(rawDir) === saving) saves.delete(rawDir);
      publish([]);
    }
  }

  return Object.freeze({
    forPage: (page) => sets[page],
    get saving() {
      return pendingWrites > 0;
    },
    get canEdit() {
      return !jobBusy();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    // Reserve the manual action before pointer-triggered blur validation.
    interrupt(page) {
      revisions[page] += 1;
    },
    clear(page) {
      revisions[page] += 1;
      sets[page] = empty();
      publish([page], true);
    },
    async preview(page, validate = () => true) {
      const { raw_dir, sequence_source } = inputs(page);
      const read = beginRead(page, { raw_dir, sequence_source });
      if (!(await validate()) || !current(read)) return false;
      const result = await request("/api/samples/preview", read.paths);
      return adopt(read, result);
    },
    prepareJob({ page, raw_dir, dll_dir, sequence_source }) {
      const read = beginRead(page, { raw_dir, dll_dir, sequence_source });
      return (job) => {
        jobRead = { id: job.job_id, read };
      };
    },
    completeJob(job, result) {
      const read = jobRead?.id === job.job_id ? jobRead.read : null;
      jobRead = null;
      return Boolean(read && job.status === "succeeded" && adopt(read, result));
    },
    restore(initial) {
      if (initial.job?.kind === "samples") {
        const page = initial.job.page || "untargeted";
        jobRead = {
          id: initial.job.job_id,
          read: captureRead(page),
        };
      }
      const result = initial.results.samples;
      if (!result?.input_paths) return false;
      const page = result.page || "untargeted";
      return adopt(captureRead(page, result.input_paths), result);
    },
    async exclude(values) {
      requireWritable();
      const submitted = structuredClone(values);
      const rawDir = submitted.raw_dir;
      // A confirmed exclusion supersedes reads even on a newly selected folder
      // whose preview has not populated any rows yet.
      const reads = pages
        .filter((page) => inputs(page).raw_dir === rawDir)
        .map((page) => beginRead(page, { raw_dir: rawDir }));
      return writeFolder(
        rawDir,
        () => request("/api/samples/exclude", submitted),
        (result) => {
          const rows = owned(result.samples);
          const overview = owned(result.overview);
          const changed = [];
          for (const read of reads) {
            if (!current(read)) continue;
            sets[read.page] = Object.freeze({
              ...sets[read.page],
              rows,
              overview,
              source: "confirmed",
            });
            changed.push(read.page);
          }
          if (changed.length) publish(changed, true);
          return result;
        },
      );
    },
    async edit(page, stems, changes) {
      requireWritable();
      const set = sets[page];
      if (!stems.length || !set.rows.length) return;
      const selected = new Set(stems);
      const patch = owned(changes);
      const rows = Object.freeze(
        set.rows.map((row) =>
          selected.has(row.raw_stem) ? Object.freeze({ ...row, ...patch }) : row,
        ),
      );
      const changed = pages.filter((other) => sets[other].rows === set.rows);
      for (const other of changed) {
        revisions[other] += 1;
        sets[other] = Object.freeze({ ...sets[other], rows });
      }
      const read = captureRead(page);
      const rawDir = read.paths.raw_dir;
      publish(changed);
      return writeFolder(
        rawDir,
        () => request("/api/samples/save", { raw_dir: rawDir, samples: rows }),
        (saved) => {
          if (!current(read)) return;
          sets[page] = Object.freeze({
            ...sets[page],
            overview: owned(saved.overview),
            source: "confirmed",
          });
          publish(mirror(read));
        },
      );
    },
  });
}
