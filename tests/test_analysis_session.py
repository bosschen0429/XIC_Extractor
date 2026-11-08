"""Exercise the shipped Analysis Session through its intention/observation seam."""

import json

import pytest

from tests.javascript_fixtures import run_asset_case


def _run_session_case(case):
    harness = """
import assert from 'node:assert/strict';
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function fakeClock() {
  let callback = null, elapsed = 0;
  return {
    now: () => elapsed,
    setInterval(fn) { callback = fn; return 1; },
    clearInterval() { callback = null; },
    async tick(count = 1) {
      for (let i = 0; i < count; i++) {
        elapsed += 750;
        callback?.();
        await flush();
      }
    },
  };
}
const previous = {targeted: {total_files: 2, samples: [{group: 'original'}]}};
const idle = () => ({job: null, results: structuredClone(previous)});
const progress = (status, id = 'job', results = previous) => structuredClone({
  job: {job_id: id, kind: 'targeted', status, cancellable: true}, results,
});
"""
    run_asset_case("analysis_session.js", "createAnalysisSession", harness, case)


def test_restore_is_silent_and_each_new_completion_is_delivered_once():
    _run_session_case(
        """
const clock = fakeClock();
let response = progress('succeeded', 'restored');
const session = createAnalysisSession(response, {
  request: async () => structuredClone(response),
}, clock);
const completions = [];
session.subscribe((snapshot, completed) => {
  if (completed)
    completions.push([completed.job_id, snapshot.results.targeted.total_files]);
});
await clock.tick(2);
assert.deepEqual(completions, []);
response = progress('running', 'new');
await clock.tick();
response = progress('succeeded', 'new', {targeted: {total_files: 3}});
await clock.tick(3);
assert.deepEqual(completions, [['new', 3]]);
response = progress('failed', 'failed');
await clock.tick(2);
assert.deepEqual(completions, [['new', 3], ['failed', 2]]);
assert.equal(session.snapshot.lastCompletionAt, 5250);
"""
    )


def test_pending_start_captures_inputs_blocks_duplicates_and_can_retry_failure():
    _run_session_case(
        """
const clock = fakeClock(), first = deferred();
let calls = 0, submitted;
const session = createAnalysisSession(idle(), {
  request: async (path, values) => {
    assert.equal(path, '/api/analysis/start');
    calls++;
    submitted = values;
    return calls === 1 ? first.promise : progress('running', 'retry');
  },
}, clock);
session.subscribe(() => {});
const inputs = {settings: {value: 5}};
const starting = session.start('targeted', inputs);
assert.equal(session.snapshot.busy, true);
inputs.settings.value = 99;
await assert.rejects(session.start('targeted', inputs));
await clock.tick(3);
assert.equal(calls, 1);
assert.equal(submitted.values.settings.value, 5);
first.reject(new Error('start failed'));
await assert.rejects(starting, /start failed/);
assert.equal(session.snapshot.canStart, true);
assert.deepEqual(session.snapshot.results, previous);
await session.start('targeted', inputs);
assert.equal(session.snapshot.job.job_id, 'retry');
assert.equal(calls, 2);
"""
    )


@pytest.mark.parametrize("action", ["start", "cancel"])
def test_poll_is_single_flight_and_cannot_undo_a_command(action):
    _run_session_case(
        f"const action = {json.dumps(action)};\n"
        + """
const clock = fakeClock(), old = deferred();
const initial = action === 'start' ? idle() : progress('running');
let reads = 0;
const session = createAnalysisSession(initial, {
  request: async path => {
    if (path === '/api/analysis') { reads++; return old.promise; }
    if (path.endsWith('/start')) return progress('running');
    if (path.endsWith('/cancel')) return progress('cancelling');
    assert.fail(path);
  },
}, clock);
session.subscribe(() => {});
await clock.tick(5);
assert.equal(reads, 1);
if (action === 'start') await session.start('targeted', {});
else await session.cancel();
old.resolve(initial);
await flush();
assert.equal(session.snapshot.job.status,
  action === 'start' ? 'running' : 'cancelling');
assert.equal(session.snapshot.busy, true);
"""
    )


def test_application_adoption_precedes_observation_and_failure_keeps_accepted_job():
    _run_session_case(
        """
const clock = fakeClock(), adoption = deferred();
let bound = false, reads = 0;
const context = {sampleRevision: 7};
const session = createAnalysisSession(idle(), {
  request: async path => {
    if (path === '/api/analysis') { reads++; return progress('succeeded'); }
    return progress('running');
  },
  prepareStart: () => context,
  acceptedStart: async (kind, inputs, job, received) => {
    assert.equal(received, context);
    assert.equal(job.job_id, 'job');
    await adoption.promise;
    bound = true;
    throw new Error('method summary unavailable');
  },
}, clock);
let completed = 0;
session.subscribe((snapshot, terminal) => {
  if (terminal) { assert.equal(bound, true); completed++; }
});
const starting = session.start('targeted', {});
await flush();
await clock.tick(2);
assert.equal(reads, 0);
adoption.resolve();
await assert.rejects(starting, /method summary unavailable/);
assert.equal(session.snapshot.job.status, 'running');
assert.equal(session.snapshot.busy, true);
await clock.tick(2);
assert.equal(completed, 1);
"""
    )


def test_cancel_confirmation_does_not_switch_to_another_tabs_new_job():
    _run_session_case(
        """
const clock = fakeClock(), confirmation = deferred();
let confirms = 0, cancellations = 0;
const session = createAnalysisSession(progress('running', 'intended'), {
  request: async path => {
    if (path === '/api/analysis') return progress('running', 'other-tab-job');
    cancellations++;
    assert.fail('must not cancel a substituted job');
  },
  confirmCancel: job => {
    assert.equal(job.job_id, 'intended');
    confirms++;
    return confirmation.promise;
  },
}, clock);
session.subscribe(() => {});
const cancelling = session.cancel();
await session.cancel();
assert.equal(confirms, 1);
await clock.tick();
confirmation.resolve(true);
await cancelling;
assert.equal(cancellations, 0);
assert.equal(session.snapshot.job.job_id, 'other-tab-job');
assert.equal(session.snapshot.canCancel, true);
"""
    )


def test_offline_backoff_recovers_and_unsubscribe_only_stops_observation():
    _run_session_case(
        """
const clock = fakeClock(), late = deferred();
let calls = 0, observed = 0;
const session = createAnalysisSession(idle(), {
  request: async path => {
    assert.equal(path, '/api/analysis');
    calls++;
    if (calls <= 3) throw new Error('offline');
    if (calls === 4) return progress('running');
    return late.promise;
  },
}, clock);
const unsubscribe = session.subscribe(() => { observed++; });
await clock.tick(3);
assert.equal(session.snapshot.connection, 'offline');
await clock.tick(2);
assert.equal(calls, 3);
await clock.tick();
assert.equal(calls, 4);
assert.equal(session.snapshot.connection, 'online');
await clock.tick();
unsubscribe();
const before = observed;
late.resolve(progress('cancelled'));
await flush();
await clock.tick(10);
assert.equal(calls, 5);
assert.equal(observed, before);
assert.equal(session.snapshot.job.status, 'running');
"""
    )


def test_snapshots_are_readonly_without_taking_ownership_of_bootstrap_input():
    _run_session_case(
        """
const initial = idle();
const session = createAnalysisSession(initial, {request: async () => idle()});
initial.results.targeted.samples[0].group = 'changed elsewhere';
assert.equal(session.snapshot.results.targeted.samples[0].group, 'original');
assert.throws(() => {
  session.snapshot.results.targeted.samples[0].group = 'edit';
}, TypeError);
assert.throws(() => { session.snapshot.job = {}; }, TypeError);
assert.equal(session.snapshot.results.targeted.samples[0].group, 'original');
"""
    )
