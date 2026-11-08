"""Exercise the shipped sample owner's intention/snapshot Interface."""

import base64
import json
import shutil
import subprocess
from importlib.resources import files


def _run_metadata_case(case):
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the shipped JavaScript"
    source = (
        files("xic_workbench")
        .joinpath("assets/analysis_sample_metadata.js")
        .read_bytes()
    )
    module = "data:text/javascript;base64," + base64.b64encode(source).decode()
    harness = """
import assert from 'node:assert/strict';
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
const locations = {
  targeted: {raw_dir: '/batch', dll_dir: '/dll', sequence_source: ''},
  untargeted: {raw_dir: '/batch', dll_dir: '/dll', sequence_source: ''},
};
const inputs = page => ({...locations[page]});
function result(role = 'study_sample', page = 'targeted') {
  return {
    page, input_paths: inputs(page),
    samples: [
      {raw_stem: 'A', sample_role: role, excluded: false},
      {raw_stem: 'B', sample_role: 'qc', excluded: false},
    ],
    overview: {excluded: []},
    summary: {confirmed: true, annotation_source: 'confirmed'},
  };
}
const initial = samples => ({job: null, results: {samples}});
const job = (id = 'read', page = 'targeted') => ({
  job_id: id, kind: 'samples', page, status: 'succeeded',
});
"""
    completed = subprocess.run(
        [node, "--input-type=module"],
        input=(
            f"import {{createSampleMetadata}} from {json.dumps(module)};\n"
            + harness
            + case
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_retained_samples_restore_independently_of_the_latest_job_kind():
    _run_metadata_case(
        """
const metadata = createSampleMetadata({request: async () => {}, inputs});
const retained = result();
assert.equal(metadata.restore({
  job: {kind: 'untargeted', status: 'succeeded', job_id: 'latest'},
  results: {samples: retained},
}), true);
retained.samples[0].sample_role = 'blank';
retained.overview.excluded.push({raw_stem: 'A', reason: ''});
for (const page of ['targeted', 'untargeted']) {
  assert.deepEqual(metadata.forPage(page).rows.map(row => row.sample_role),
    ['study_sample', 'qc']);
  assert.deepEqual(metadata.forPage(page).overview.excluded, []);
  assert.throws(() => {
    metadata.forPage(page).rows[0].sample_role = 'blank';
  }, TypeError);
}
"""
    )


def test_a_manual_read_supersedes_validation_before_preview_transport():
    _run_metadata_case(
        """
const validation = deferred();
let requests = 0;
const metadata = createSampleMetadata({
  request: async () => { requests++; return result(); }, inputs,
});
const preview = metadata.preview('targeted', () => validation.promise);
const accept = metadata.prepareJob({page: 'targeted', ...inputs('targeted')});
accept(job());
validation.resolve(true);
assert.equal(await preview, false);
assert.equal(requests, 0);
assert.equal(metadata.completeJob(job(), result('blank')), true);
assert.equal(metadata.forPage('targeted').rows[0].sample_role, 'blank');
"""
    )


def test_returning_to_a_previous_folder_does_not_revive_its_old_preview():
    _run_metadata_case(
        """
const response = deferred();
const old = result();
const metadata = createSampleMetadata({request: () => response.promise, inputs});
const preview = metadata.preview('targeted');
await flush();
locations.targeted.raw_dir = '/different';
metadata.clear('targeted');
locations.targeted.raw_dir = '/batch';
metadata.clear('targeted');
response.resolve(old);
assert.equal(await preview, false);
assert.deepEqual(metadata.forPage('targeted').rows, []);
"""
    )


def test_shared_edits_invalidate_reads_on_both_flows_without_mutable_rows():
    _run_metadata_case(
        """
const previewResponse = deferred(), saveResponse = deferred();
const writes = [];
const metadata = createSampleMetadata({
  request: (url, values) => {
    if (url.endsWith('/preview')) return previewResponse.promise;
    writes.push(values);
    return saveResponse.promise;
  }, inputs,
});
metadata.restore(initial(result()));
const old = result('study_sample', 'untargeted');
const preview = metadata.preview('untargeted');
await flush();
const edit = metadata.edit('targeted', ['A'], {excluded: true});
await flush();
previewResponse.resolve(old);
assert.equal(await preview, false);
for (const page of ['targeted', 'untargeted']) {
  assert.deepEqual(metadata.forPage(page).rows.map(row => row.excluded),
    [true, false]);
}
assert.equal(writes[0].raw_dir, '/batch');
assert.deepEqual(writes[0].samples.map(row => row.excluded), [true, false]);
assert.equal(metadata.saving, true);
saveResponse.resolve({overview: {excluded: [{raw_stem: 'A', reason: ''}]}});
await edit;
assert.equal(metadata.saving, false);
assert.deepEqual(metadata.forPage('untargeted').overview.excluded,
  [{raw_stem: 'A', reason: ''}]);
"""
    )


def test_confirmed_mirroring_cannot_replace_a_newer_other_flow_intention():
    _run_metadata_case(
        """
const response = deferred();
const metadata = createSampleMetadata({request: () => response.promise, inputs});
const preview = metadata.preview('targeted');
await flush();
const accepted = metadata.prepareJob({
  page: 'untargeted', ...inputs('untargeted'),
});
accepted(job('manual', 'untargeted'));
response.resolve(result());
assert.equal(await preview, true);
assert.deepEqual(metadata.forPage('untargeted').rows, []);
assert.equal(metadata.completeJob(job('manual', 'untargeted'),
  result('blank', 'untargeted')), true);
assert.equal(metadata.forPage('targeted').rows[0].sample_role, 'blank');
assert.equal(metadata.forPage('untargeted').rows[0].sample_role, 'blank');
"""
    )


def test_failed_save_reports_its_error_without_blocking_the_newer_edit():
    _run_metadata_case(
        """
const firstResponse = deferred(), secondResponse = deferred();
const writes = [];
const metadata = createSampleMetadata({
  request: (url, values) => {
    writes.push(values);
    return writes.length === 1 ? firstResponse.promise : secondResponse.promise;
  }, inputs,
});
metadata.restore(initial(result()));
const first = metadata.edit('targeted', ['A'], {sample_role: 'qc'});
const failure = assert.rejects(first, /disk full/);
const second = metadata.edit('untargeted', ['A'], {sample_role: 'blank'});
await flush();
assert.equal(writes.length, 1);
assert.equal(writes[0].samples[0].sample_role, 'qc');
firstResponse.reject(new Error('disk full'));
await failure;
await flush();
assert.equal(writes.length, 2);
assert.equal(writes[1].samples[0].sample_role, 'blank');
assert.equal(metadata.saving, true);
secondResponse.resolve({overview: {excluded: []}});
await second;
assert.equal(metadata.saving, false);
for (const page of ['targeted', 'untargeted']) {
  assert.deepEqual(metadata.forPage(page).rows.map(row => row.sample_role),
    ['blank', 'qc']);
  assert.equal(metadata.forPage(page).source, 'confirmed');
}
"""
    )


def test_review_exclusion_supersedes_reads_without_touching_another_folder():
    _run_metadata_case(
        """
locations.untargeted.raw_dir = '/another-batch';
const late = deferred();
let confirmed;
const metadata = createSampleMetadata({
  request: (url, values) => {
    if (url.endsWith('/exclude')) return Promise.resolve(confirmed);
    return values.raw_dir === '/another-batch'
      ? Promise.resolve(result('blank', 'untargeted')) : late.promise;
  },
  inputs,
});
metadata.restore(initial(result()));
await metadata.preview('untargeted');
const old = result();
const preview = metadata.preview('targeted');
await flush();
confirmed = result('pooled_qc');
confirmed.samples[0].excluded = true;
confirmed.overview = {excluded: [{raw_stem: 'A', reason: ''}]};
await metadata.exclude({raw_dir: '/batch', raw_stem: 'A'});
confirmed.samples[0].excluded = false;
late.resolve(old);
assert.equal(await preview, false);
assert.equal(metadata.forPage('targeted').rows[0].excluded, true);
assert.equal(metadata.forPage('targeted').rows[0].sample_role, 'pooled_qc');
assert.equal(metadata.forPage('untargeted').rows[0].sample_role, 'blank');
assert.deepEqual(metadata.forPage('untargeted').overview.excluded, []);
"""
    )


def test_a_restored_running_read_cannot_apply_results_for_different_inputs():
    _run_metadata_case(
        """
const old = result();
locations.targeted.raw_dir = '/new-batch';
const metadata = createSampleMetadata({request: async () => {}, inputs});
const running = {...job(), status: 'running'};
metadata.restore({job: running, results: {}});
assert.equal(metadata.completeJob(job(), old), false);
assert.deepEqual(metadata.forPage('targeted').rows, []);
"""
    )


def test_review_exclusion_supersedes_previews_even_when_the_folder_is_not_loaded():
    _run_metadata_case(
        """
const late = deferred(), old = result(), confirmed = result();
confirmed.samples[0].excluded = true;
const metadata = createSampleMetadata({
  request: url => url.endsWith('/exclude')
    ? Promise.resolve(confirmed) : late.promise,
  inputs,
});
const preview = metadata.preview('targeted');
await flush();
await metadata.exclude({raw_dir: '/batch', raw_stem: 'A'});
late.resolve(old);
assert.equal(await preview, false);
for (const page of ['targeted', 'untargeted']) {
  assert.deepEqual(metadata.forPage(page).rows.map(row => row.excluded),
    [true, false]);
}
"""
    )


def test_review_exclusion_waits_for_older_saves_and_keeps_the_latest_role():
    _run_metadata_case(
        """
let disk = result().samples;
const releaseSave = deferred(), calls = [];
const metadata = createSampleMetadata({
  request: async (url, values) => {
    calls.push(url);
    if (url.endsWith('/save')) {
      await releaseSave.promise;
      disk = structuredClone(values.samples);
      return {overview: {excluded: []}};
    }
    disk = disk.map(row => row.raw_stem === values.raw_stem
      ? {...row, excluded: true} : row);
    return {samples: disk, overview: {excluded: [{raw_stem: 'A', reason: ''}]}};
  }, inputs,
});
metadata.restore(initial(result()));
const saving = metadata.edit('targeted', ['A'], {sample_role: 'blank'});
const excluding = metadata.exclude({raw_dir: '/batch', raw_stem: 'A'});
await flush();
assert.deepEqual(calls, ['/api/samples/save']);
releaseSave.resolve();
await saving;
await excluding;
assert.deepEqual(calls, ['/api/samples/save', '/api/samples/exclude']);
assert.deepEqual(disk.map(row => [row.sample_role, row.excluded]),
  [['blank', true], ['qc', false]]);
for (const page of ['targeted', 'untargeted']) {
  assert.deepEqual(metadata.forPage(page).rows.map(row =>
    [row.sample_role, row.excluded]), [['blank', true], ['qc', false]]);
}
"""
    )


def test_canonical_writes_cannot_overlap_an_analysis_job():
    _run_metadata_case(
        """
let busy = true, requests = 0;
const metadata = createSampleMetadata({
  inputs, jobBusy: () => busy,
  request: async () => { requests++; return {overview: {excluded: []}}; },
});
metadata.restore(initial(result()));
assert.equal(metadata.canEdit, false);
await assert.rejects(metadata.edit('targeted', ['A'], {excluded: true}),
  /目前工作/);
await assert.rejects(metadata.exclude({raw_dir: '/batch', raw_stem: 'A'}),
  /目前工作/);
assert.equal(requests, 0);
assert.equal(metadata.forPage('targeted').rows[0].excluded, false);
busy = false;
assert.equal(metadata.canEdit, true);
await metadata.edit('targeted', ['A'], {excluded: true});
assert.equal(requests, 1);
assert.equal(metadata.forPage('targeted').rows[0].excluded, true);
"""
    )
