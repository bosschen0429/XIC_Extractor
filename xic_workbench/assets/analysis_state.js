// Shared analysis data and its only write owner. Views read state and submit
// transitions here; DOM controls and uncommitted editor drafts stay with views.
const emptySamples = () => ({
  rows: [],
  overview: null,
  suggestions: {},
  source: "",
});
export const state = {
  config: null,
  targets: [],
  identificationMethods: {},
  sampleSets: { targeted: emptySamples(), untargeted: emptySamples() },
  sampleRevisions: { targeted: 0, untargeted: 0 },
  sampleJobAction: null,
  method: null,
  analysis: { job: null, results: {} },
  importing: false,
  view: "targeted",
  dirty: { targeted: false, untargeted: false },
  seenJob: null,
  offlineCount: 0,
  curation: { runSource: "", samples: null, detection: null, request: 0 },
};
export function isRunning() {
  return ["running", "cancelling"].includes(state.analysis.job?.status);
}
export function setAnalysisState(analysis) {
  const wasBusy = isRunning();
  state.analysis = analysis;
  if (wasBusy !== isRunning())
    document.dispatchEvent(new CustomEvent("xic:analysis-busy"));
}
export function initializeConfig(config) {
  state.config = config;
  state.targets = config.targets;
  state.identificationMethods = config.identification_methods || {};
}
export function updateConfig(values) {
  Object.assign(state.config, values);
}
export function updateTargetedSettings(values) {
  Object.assign(state.config.settings, values);
}
export function updateUntargetedSettings(values) {
  Object.assign(state.config.untargeted, values);
}
export function setDirty(kind, value) {
  state.dirty[kind] = value;
}
export function setView(view) {
  state.view = view;
}
export function setImporting(value) {
  state.importing = value;
}
export function setMethod(data, preset) {
  state.method = { ...data, key: preset };
}
export function setTargets(targets) {
  state.targets = targets;
}
export function updateTarget(row, values) {
  Object.assign(row, values);
}
export function removeTarget(index) {
  state.targets.splice(index, 1);
}
export function addTarget(row) {
  state.targets.push(row);
}
export function setIdentificationMethods(methods, oldName, newName) {
  state.identificationMethods = methods;
  if (oldName && oldName !== newName)
    for (const target of state.targets)
      if (target.method === oldName) target.method = newName;
}
export function setSampleJobAction(jobId, action) {
  state.sampleJobAction = { jobId, action };
}
export function resetSeenJob() {
  state.seenJob = null;
}
export function markJobSeen(jobId) {
  state.seenJob = jobId;
}
export function markJobFinished() {
  state.seenAt = Date.now();
}
export function markClosed() {
  state.closed = true;
}
export function markBooted() {
  state.booted = true;
}
export function recordOnline() {
  state.offlineCount = 0;
}
export function recordOffline() {
  state.offlineCount += 1;
}
export function updateCuration(values) {
  Object.assign(state.curation, values);
}
export function beginCurationDetection() {
  return ++state.curation.request;
}
export function updateSample(row, values) {
  Object.assign(row, values);
}
export function replaceSamples(page, result) {
  state.sampleSets[page] = {
    rows: result.samples,
    overview: result.overview || null,
    suggestions: result.group_suggestions || {},
    source: result.summary?.annotation_source || "",
  };
}
export function mirrorSampleSet(page, other) {
  state.sampleRevisions[other] += 1;
  state.sampleSets[other] = state.sampleSets[page];
}
export function clearSampleSet(page) {
  state.sampleSets[page] = emptySamples();
}
export function confirmSampleSet(page, overview) {
  state.sampleSets[page].overview = overview;
  state.sampleSets[page].source = "confirmed";
}
export function receiveConfirmedSamples(page, samples, overview) {
  sampleAction(page);
  state.sampleSets[page] = {
    ...state.sampleSets[page],
    rows: samples,
    overview,
    source: "confirmed",
    cards: null,
  };
}
// Revisions belong to user actions, including validation before a preview.
// Completed requests may update only the display they still own.
export function sampleAction(page, advance = true) {
  if (advance) state.sampleRevisions[page] += 1;
  return { page, revisions: { ...state.sampleRevisions } };
}
export function currentSampleAction(action) {
  return (
    action &&
    state.sampleRevisions[action.page] === action.revisions[action.page]
  );
}
export function suggestionCards(set) {
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
export function cardRows(set, card) {
  const members = new Set(card.members);
  return set.rows.filter((row) => members.has(row.raw_stem));
}
// Rows a card wrote keep following it; hand-labelled rows stay as they are.
function relabelCard(page, card, label) {
  let changed = false;
  for (const row of cardRows(state.sampleSets[page], card))
    if (row.group.trim() === card.label) {
      row.group = label;
      changed = true;
    }
  return changed;
}
export function renameSampleCard(page, card, label) {
  const set = state.sampleSets[page];
  const changed = relabelCard(page, card, label);
  card.label = label;
  const same = set.cards.find(
    (other) => other !== card && other.label === label,
  );
  if (same) {
    same.members.push(...card.members);
    set.cards = set.cards.filter((other) => other !== card);
  }
  return changed;
}
export function mergeSampleCard(page, card, target) {
  const set = state.sampleSets[page];
  const changed = relabelCard(page, card, target.label);
  target.members.push(...card.members);
  set.cards = set.cards.filter((other) => other !== card);
  return changed;
}
export function applySampleCard(page, card, overwrite) {
  for (const row of cardRows(state.sampleSets[page], card))
    if (overwrite || !row.group.trim()) row.group = card.label;
}
export function applySampleAnnotations(page, preview, overwrite, reason) {
  const byStem = new Map(
    state.sampleSets[page].rows.map((row) => [row.raw_stem, row]),
  );
  for (const match of preview.matched) {
    const row = byStem.get(match.raw_stem);
    if (!row) continue;
    if (match.group && (overwrite || !row.group.trim()))
      row.group = match.group;
    if (match.excluded) {
      row.excluded = true;
      row.exclusion_reason = match.exclusion_reason || reason;
    }
  }
}
