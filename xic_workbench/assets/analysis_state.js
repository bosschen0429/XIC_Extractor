// Settings and editable sample data. The Analysis Session owns job lifecycle;
// DOM controls and uncommitted editor drafts stay with views.
const emptySamples = () => ({
  rows: [],
  overview: null,
  source: "",
});
export const state = {
  config: null,
  targets: [],
  identificationMethods: {},
  sampleSets: { targeted: emptySamples(), untargeted: emptySamples() },
  sampleRevisions: { targeted: 0, untargeted: 0 },
  method: null,
  importing: false,
  view: "targeted",
  dirty: { targeted: false, untargeted: false },
  curation: { runSource: "", samples: null, detection: null, request: 0 },
};
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
export function markClosed() {
  state.closed = true;
}
export function markBooted() {
  state.booted = true;
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
    rows: structuredClone(result.samples),
    overview: structuredClone(result.overview || null),
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
