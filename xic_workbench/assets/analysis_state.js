// Application settings. Analysis Session and sampleMetadata own jobs and samples;
// DOM controls and uncommitted editor drafts stay with views.
export const state = {
  config: null,
  targets: [],
  identificationMethods: {},
  method: null,
  importing: false,
  view: "targeted",
  dirty: { targeted: false, untargeted: false },
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
