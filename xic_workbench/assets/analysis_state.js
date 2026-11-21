export let analysis = null;
export function adoptAnalysis(session) {
  analysis = session;
}
export const state = {
  config: null,
  targets: [],
  deltaMass: { settings: {}, ions: new Map() },
  method: null,
  importing: false,
  view: "targeted",
  dirty: { targeted: false, untargeted: false },
};
export function initializeConfig(config) {
  state.config = config;
  state.targets = config.targets;
  setDeltaMass(config.delta_mass);
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
export function setDeltaMass(payload) {
  const byLabel = payload?.ions || {};
  state.deltaMass = {
    settings: {
      ...state.config.targeted_specificity.delta_mass_settings,
      ...(payload?.settings || {}),
    },
    ions: new Map(
      state.targets
        .filter((row) => Object.hasOwn(byLabel, row.label))
        .map((row) => [row, structuredClone(byLabel[row.label])]),
    ),
  };
}
export function markClosed() {
  state.closed = true;
}
export function markBooted() {
  state.booted = true;
}
