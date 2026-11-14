"""Exercise curation draft/preview/export through the shipped Interface."""

import json

import pytest

from tests.javascript_fixtures import run_asset_case


def _run_curation_case(case):
    harness = """
import assert from 'node:assert/strict';
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function fakeClock() {
  let next = 0;
  const callbacks = new Map();
  return {
    setTimeout(fn) { callbacks.set(++next, fn); return next; },
    clearTimeout(id) { callbacks.delete(id); },
    get pending() { return callbacks.size; },
    async run(count = 1) {
      for (let i = 0; i < count; i++) {
        const first = callbacks.entries().next().value;
        if (!first) return;
        callbacks.delete(first[0]);
        first[1]();
        await flush();
      }
    },
  };
}
const samples = [
  {raw_stem: 'S1', sample_role: 'sample', excluded: false},
  {raw_stem: 'QC', sample_role: 'qc', excluded: false},
];
const detection = source => ({source_dir: source, steps: [{
  key: 'finite_fraction', label: '有效值比例',
  parameters: [{key: 'min_finite_fraction', label: '最低比例'}],
}]});
const preview = kept => ({feature_count: 4, kept, removed: 4 - kept, steps: []});
const step = value => ({enabled: true, parameters: {min_finite_fraction: value}});
function setup(overrides = {}) {
  const clock = fakeClock(), requests = [], started = [], adopted = [];
  const session = createCurationSession({
    preferences: {finite_fraction: {enabled: true,
      parameters: {min_finite_fraction: 0.6}}},
    request: async (path, values) => {
      requests.push({path, values});
      if (path.endsWith('/source')) return detection(values.source_dir);
      if (path.endsWith('/preview')) return preview(2);
      return {matrix_curation: values.matrix_curation, theme: 'dark'};
    },
    startJob: async (kind, values) => {
      started.push({kind, values}); return {job_id: 'curated'};
    },
    adoptPreferences: values => adopted.push(values),
    ...overrides,
  }, clock);
  return {session, clock, requests, started, adopted};
}
async function ready(fixture) {
  fixture.session.load('/run', samples);
  await fixture.clock.run(2);
  assert.equal(fixture.session.snapshot.canExport, true);
}
"""
    run_asset_case(
        "analysis_curation_session.js", "createCurationSession", harness, case
    )


def test_export_uses_the_previewed_draft_and_owns_readonly_snapshots():
    _run_curation_case(
        """
const fixture = setup();
const input = structuredClone(samples);
fixture.session.load('/run', input);
input[0].excluded = true;
await fixture.clock.run(2);
assert.equal(fixture.session.snapshot.canExport, true);
assert.throws(() => {
  fixture.session.snapshot.settings.finite_fraction.parameters
    .min_finite_fraction = '1';
}, TypeError);
const expected = {
  source_dir: '/run', sample_metadata_path: '', istd_path: '',
  samples: [
    {raw_stem: 'S1', sample_role: 'sample', excluded: false},
    {raw_stem: 'QC', sample_role: 'qc', excluded: false},
  ],
  steps: {finite_fraction: {min_finite_fraction: 0.6}},
};
assert.deepEqual(fixture.requests.find(r => r.path.endsWith('/preview')).values,
  expected);
assert.deepEqual(await fixture.session.export(), {job_id: 'curated'});
assert.deepEqual(fixture.started, [{kind: 'curation', values: expected}]);
assert.deepEqual(fixture.adopted, [{theme: 'dark', matrix_curation: {steps: {
  finite_fraction: {enabled: true, parameters: {min_finite_fraction: 0.6}},
}}}]);
"""
    )


def test_a_new_source_immediately_retires_detection_preview_and_run_samples():
    _run_curation_case(
        """
const fixture = setup();
await ready(fixture);
fixture.session.setSources({
  source_dir: '/another', sample_metadata_path: '/samples.tsv', istd_path: '',
});
assert.equal(fixture.session.snapshot.detection, null);
assert.equal(fixture.session.snapshot.preview, null);
assert.equal(fixture.session.snapshot.canExport, false);
await assert.rejects(fixture.session.export(), /預覽更新中/);
await fixture.clock.run(2);
const requested = fixture.requests.filter(r => r.path.endsWith('/preview')).at(-1);
assert.equal(requested.values.source_dir, '/another');
assert.equal(requested.values.sample_metadata_path, '/samples.tsv');
assert.equal(requested.values.samples, null);
assert.equal(fixture.session.snapshot.canExport, true);
"""
    )


def test_pending_preview_coalesces_edits_and_cannot_approve_an_older_draft():
    _run_curation_case(
        """
const held = deferred(), thresholds = [];
const fixture = setup({request: async (path, values) => {
  if (path.endsWith('/source')) return detection(values.source_dir);
  const threshold = values.steps.finite_fraction.min_finite_fraction;
  thresholds.push(threshold);
  return threshold === 0.7 ? held.promise : preview(threshold === 0.9 ? 1 : 2);
}});
await ready(fixture);
fixture.session.setStep('finite_fraction', step('0.7'));
await fixture.clock.run();
fixture.session.setStep('finite_fraction', step('0.8'));
await fixture.clock.run();
fixture.session.setStep('finite_fraction', step('0.9'));
await fixture.clock.run();
assert.deepEqual(thresholds, [0.6, 0.7]);
held.resolve(preview(3));
await flush();
assert.equal(fixture.session.snapshot.preview, null);
assert.equal(fixture.session.snapshot.canExport, false);
await fixture.clock.run();
assert.deepEqual(thresholds, [0.6, 0.7, 0.9]);
assert.equal(fixture.session.snapshot.preview.kept, 1);
assert.equal(fixture.session.snapshot.canExport, true);
"""
    )


def test_an_old_source_error_cannot_replace_the_new_sources_detection():
    _run_curation_case(
        """
const old = deferred();
const fixture = setup({request: async (path, values) => {
  if (path.endsWith('/source'))
    return values.source_dir === '/old' ? old.promise : detection(values.source_dir);
  return preview(1);
}});
fixture.session.load('/old', samples);
await fixture.clock.run();
fixture.session.load('/new', samples);
await fixture.clock.run();
old.reject(new Error('old folder cannot be read'));
await flush();
await fixture.clock.run(2);
assert.equal(fixture.session.snapshot.sourceError, '');
assert.equal(fixture.session.snapshot.detection.source_dir, '/new');
assert.equal(fixture.session.snapshot.preview.kept, 1);
assert.equal(fixture.session.snapshot.canExport, true);
"""
    )


def test_reselecting_an_unreadable_source_retries_without_changing_the_path():
    _run_curation_case(
        """
let attempts = 0;
const fixture = setup({request: async (path, values) => {
  if (path.endsWith('/source')) {
    if (++attempts === 1) throw new Error('source unavailable');
    return detection(values.source_dir);
  }
  return preview(2);
}});
fixture.session.load('/run', samples);
await fixture.clock.run();
assert.equal(fixture.session.snapshot.sourceError, 'source unavailable');
fixture.session.setSources({source_dir: '/run', sample_metadata_path: '',
  istd_path: ''});
await fixture.clock.run(2);
assert.equal(attempts, 2);
assert.equal(fixture.session.snapshot.sourceError, '');
assert.equal(fixture.session.snapshot.canExport, true);
"""
    )


def test_missing_parameters_and_disabled_steps_do_not_approve_an_export():
    _run_curation_case(
        """
const fixture = setup();
await ready(fixture);
fixture.session.setStep('finite_fraction', {enabled: false,
  parameters: {min_finite_fraction: '0.6'}});
await fixture.clock.run();
assert.match(fixture.session.snapshot.message, /至少啟用一個/);
fixture.session.setStep('finite_fraction', step(''));
await fixture.clock.run();
assert.match(fixture.session.snapshot.message, /最低比例/);
assert.equal(fixture.session.snapshot.canExport, false);
assert.equal(fixture.requests.filter(r => r.path.endsWith('/preview')).length, 1);
fixture.session.setStep('finite_fraction', step('0.8'));
await fixture.clock.run();
assert.equal(fixture.session.snapshot.canExport, true);
"""
    )


def test_editing_during_preference_save_cancels_export_without_a_second_submit():
    _run_curation_case(
        """
const preferences = deferred();
let preferenceRequests = 0;
const fixture = setup({request: async (path, values) => {
  if (path.endsWith('/source')) return detection(values.source_dir);
  if (path.endsWith('/preview')) return preview(2);
  preferenceRequests++;
  return preferences.promise;
}});
await ready(fixture);
const exporting = fixture.session.export();
assert.equal(fixture.session.snapshot.exporting, true);
await assert.rejects(fixture.session.export(), /預覽更新中/);
assert.equal(preferenceRequests, 1);
fixture.session.setStep('finite_fraction', step('0.9'));
preferences.resolve({matrix_curation: {steps: {}}});
await assert.rejects(exporting, /設定已變更/);
assert.deepEqual(fixture.started, []);
assert.equal(fixture.session.snapshot.exporting, false);
assert.equal(fixture.session.snapshot.canExport, false);
await fixture.clock.run();
assert.equal(fixture.session.snapshot.canExport, true);
"""
    )


@pytest.mark.parametrize("failure_stage", ("preferences", "start"))
def test_failed_export_preserves_the_preview_and_permits_retry(failure_stage):
    _run_curation_case(
        """
const stage = FAILURE_STAGE;
let failed = false, accepted = 0;
const fixture = setup({
  request: async (path, values) => {
    if (path.endsWith('/source')) return detection(values.source_dir);
    if (path.endsWith('/preview')) return preview(2);
    if (stage === 'preferences' && !failed) {
      failed = true; throw new Error('preferences unavailable');
    }
    return {matrix_curation: values.matrix_curation};
  },
  startJob: async () => {
    if (stage === 'start' && !failed) {
      failed = true; throw new Error('another job is running');
    }
    accepted++; return {job_id: 'accepted'};
  },
});
await ready(fixture);
await assert.rejects(fixture.session.export(),
  stage === 'preferences' ? /preferences unavailable/ : /another job/);
assert.equal(fixture.session.snapshot.exporting, false);
assert.equal(fixture.session.snapshot.canExport, true);
assert.equal(accepted, 0);
assert.deepEqual(await fixture.session.export(), {job_id: 'accepted'});
assert.equal(accepted, 1);
""".replace("FAILURE_STAGE", json.dumps(failure_stage))
    )


@pytest.mark.parametrize("phase", ("source", "preview", "export"))
def test_dispose_ignores_late_reads_and_cannot_launch_an_export(phase):
    _run_curation_case(
        """
const phase = PHASE, held = deferred();
let hold = false, notifications = 0;
const fixture = setup({request: async (path, values) => {
  if (hold) return held.promise;
  if (path.endsWith('/source')) return detection(values.source_dir);
  if (path.endsWith('/preview')) return preview(2);
  return {};
}});
fixture.session.subscribe(() => notifications++);
let exporting;
if (phase === 'source') {
  hold = true;
  fixture.session.load('/run', samples);
  await fixture.clock.run();
} else {
  await ready(fixture);
  hold = true;
  if (phase === 'preview') {
    fixture.session.setStep('finite_fraction', step('0.8'));
    await fixture.clock.run();
  } else exporting = fixture.session.export();
}
fixture.session.dispose();
const before = notifications;
held.resolve(phase === 'source' ? detection('/run') : preview(1));
if (exporting) await assert.rejects(exporting, /設定已變更/);
await flush();
assert.equal(notifications, before);
assert.equal(fixture.clock.pending, 0);
assert.equal(fixture.session.snapshot.canExport, false);
assert.deepEqual(fixture.started, []);
""".replace("PHASE", json.dumps(phase))
    )
