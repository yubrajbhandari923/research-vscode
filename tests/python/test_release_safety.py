"""Regression tests for failures found in the independent release review."""
import io
import json
import os
import shlex
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from research import expcheck, hooks, mcp, orchestrate, plans, rpc, runs, services
from research.store import _atomic_write
from research.util import PolicyBlocked, ResearchError


def test_malformed_edit_cannot_be_exported_over(proj, repo):
    q = services.create_question(proj, 'original')
    path = repo / '.research/questions' / (q['id'] + '.md')
    broken = '---\ntitle: [unclosed\n---\n'
    path.write_text(broken)
    proj.sync()
    with pytest.raises(ResearchError, match='malformed'):
        proj.export('question', q['id'])
    assert path.read_text() == broken
    assert proj.get('question', q['id'])['title'] == 'original'


@pytest.mark.parametrize('damage', ['malformed', 'duplicate'])
@pytest.mark.parametrize('backup', [False, True])
def test_rebuild_failure_preserves_index_and_files(proj, repo, damage, backup):
    q = services.create_question(proj, 'preserve')
    path = repo / '.research/questions' / (q['id'] + '.md')
    extra = path.with_name('extra.md')
    extra.write_text('---\ntitle: [broken\n---\n' if damage == 'malformed' else path.read_text())
    before = {f: f.read_bytes() for f in (path, extra)}
    with pytest.raises(ResearchError, match=damage):
        proj.rebuild_index(backup=backup)
    assert proj.get('question', q['id'])['title'] == 'preserve'
    assert all(f.read_bytes() == content for f, content in before.items())
    assert not proj._suppress_export and not proj._suppress_events


def test_rebuild_does_not_normalize_canonical_files(proj, repo):
    q = services.create_question(proj, 'q')
    path = repo / '.research/questions' / (q['id'] + '.md')
    text = path.read_text().replace('---\n', '---\ncustom_metadata: keep\n', 1)
    path.write_text(text)
    proj.rebuild_index()
    assert path.read_text() == text


def test_atomic_write_concurrent_threads_and_cleanup(tmp_path, monkeypatch):
    path = tmp_path / 'record'
    payloads = [str(n) * 10000 for n in range(20)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda text: _atomic_write(str(path), text), payloads))
    assert path.read_text() in payloads
    before = path.read_bytes()
    def fail(*args):
        raise OSError('injected replacement failure')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        _atomic_write(str(path), 'new')
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('message', [None, 1, [], 'text', {'method': 1}, {'method': 'ping', 'params': []},
                                      {'method': 'ping', 'params': {'author': []}},
                                      {'method': 'ping', 'params': {'author': {'author_type': 'invalid'}}}])
def test_rpc_survives_bad_request(repo, message):
    output = io.StringIO()
    rpc.serve(str(repo), io.StringIO(json.dumps(message) + '\n' + json.dumps({'id': 42, 'method': 'ping'}) + '\n'), output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert 'error' in rows[1]
    assert rows[-1]['id'] == 42 and 'result' in rows[-1]


@pytest.mark.parametrize('message', [None, 1, [], 'text', {'method': 'ping'},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'clientInfo': 1}},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': [], 'arguments': []}},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'params': []}])
def test_mcp_survives_bad_request(repo, message):
    output = io.StringIO()
    ping = {'jsonrpc': '2.0', 'id': 42, 'method': 'ping'}
    mcp.serve(str(repo), io.StringIO(json.dumps(message) + '\n' + json.dumps(ping) + '\n'), output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert 'error' in rows[0]
    assert rows[-1] == {**{'jsonrpc': '2.0', 'id': 42}, 'result': {}}


def test_unapproved_required_hook_blocks_gate(proj):
    hooks.add(proj, 'required', 'preflight', 'true')
    with pytest.raises(PolicyBlocked, match='required'):
        hooks.gate(proj, 'preflight')
    assert not hooks.run(proj, 'preflight')['ok']
    hooks.set_enabled(proj, 'required', False)
    assert hooks.gate(proj, 'preflight')['ok']


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf'), 'bad'])
def test_hook_timeout_validation_before_mutation(proj, timeout):
    with pytest.raises(ResearchError, match='timeout'):
        hooks.add(proj, 'invalid', 'preflight', 'true', timeout=timeout)
    assert 'invalid' not in hooks.specs(proj)


def test_advisory_hook_is_not_a_required_check(proj):
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task', checks=['cmd:true'])
    hooks.add(proj, 'advisory', 'post_implementation', 'exit 1', required=False)
    hooks.approve(proj)
    assert orchestrate.verify_work(proj, task['id'])['passed']
    plans.update_task(proj, task['id'], checks=[])
    assert not orchestrate.verify_work(proj, task['id'])['passed']


@pytest.mark.parametrize('exit_code', [None, 1])
def test_completed_label_does_not_prove_success(proj, exit_code):
    exp = services.create_experiment(proj, 'experiment')
    run = runs.attach_run(proj, exp['id'])
    runs.update_run(proj, run['id'], exit_code=exit_code)
    runs.log_metric(proj, 'loss', 1, run=run['id'])
    result = expcheck.check(proj, exp['id'])
    assert not result['deterministic']['all_passed']
    assert result['deterministic']['failed']


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process group cleanup')
def test_check_timeout_stops_descendants(proj, repo):
    from research.verification import run_check
    marker = repo / 'escaped'
    script = "import time; from pathlib import Path; time.sleep(0.8); Path('escaped').write_text('bad')"
    cmd = shlex.quote(sys.executable) + ' -c ' + shlex.quote(script)
    proj.config['verification']['check_timeout_seconds'] = 0.1
    result = run_check(proj, {'type': 'command', 'run': cmd}, {})
    assert not result['ok'] and 'timed out' in result['detail']
    time.sleep(1)
    assert not marker.exists()


def test_freeze_cannot_accept_a_changed_oracle(proj, repo):
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'implement')
    path = repo / 'acceptance.py'
    path.write_text('assert result == 42\n')
    orchestrate.freeze(proj, task['id'], ['acceptance.py'])
    before = orchestrate.ledger(proj, task['id'])['frozen']
    path.write_text('assert True\n')
    with pytest.raises(PolicyBlocked, match='already frozen'):
        orchestrate.freeze(proj, task['id'], ['acceptance.py'])
    assert orchestrate.ledger(proj, task['id'])['frozen'] == before
    assert orchestrate.tampered(proj, task['id']) == ['acceptance.py']


def test_dependency_cycles_rejected_without_mutation(proj):
    plan = plans.create_plan(proj, 'plan')
    a = plans.create_task(proj, plan['id'], 'a')
    b = plans.create_task(proj, plan['id'], 'b', depends_on=[a['id']])
    for deps in ([a['id']], [b['id']]):
        with pytest.raises(ResearchError, match='cycle'):
            plans.update_task(proj, a['id'], depends_on=deps)
        assert not proj.get('task', a['id'])['depends_on']


def test_failed_canonical_write_rolls_back_entity(proj, monkeypatch):
    import research.store as store
    q = services.create_question(proj, 'original')
    def fail(*args):
        raise OSError('injected write failure')
    monkeypatch.setattr(store, '_atomic_write', fail)
    with pytest.raises(OSError):
        proj.update_entity('question', q['id'], {'title': 'lost'})
    assert proj.get('question', q['id'])['title'] == 'original'
    with pytest.raises(OSError):
        services.create_question(proj, 'lost new record')
    assert len(proj.list('question')) == 1


def test_update_imports_hand_edit_before_applying_changes(proj, repo):
    q = services.create_question(proj, 'original')
    path = repo / '.research/questions' / (q['id'] + '.md')
    path.write_text(path.read_text().replace('title: original', 'title: edited'))
    proj.update_entity('question', q['id'], {'status': 'answered'})
    current = proj.get('question', q['id'])
    assert current['title'] == 'edited' and current['status'] == 'answered'


def test_malformed_run_rebuild_rolls_back(proj, repo):
    exp = services.create_experiment(proj, 'experiment')
    run = runs.attach_run(proj, exp['id'])
    path = repo / '.research/runs' / run['id'] / 'run.json'
    path.write_text('not json')
    with pytest.raises(ResearchError, match='run record'):
        proj.rebuild_index()
    assert runs.get_run(proj, run['id'])['experiment_id'] == exp['id']
    assert path.read_text() == 'not json'


@pytest.mark.parametrize('entry', ['null\n', '[1,2]\n', '{bad}\n'])
def test_corrupt_jsonl_cannot_silently_disappear(proj, entry):
    from pathlib import Path
    path = Path(proj.path('metrics.jsonl'))
    path.write_text(entry)
    with pytest.raises(ResearchError, match='Malformed JSONL'):
        proj.rebuild_index()
    assert path.read_text() == entry


def test_incomplete_jsonl_tail_is_preserved_and_blocks_append(tmp_path):
    from research.store import append_jsonl, read_jsonl_from
    path = tmp_path / 'metrics.jsonl'
    path.write_bytes(b'{"name":"ok"}\n{"name":')
    assert list(read_jsonl_from(str(path), 0)) == [({'name': 'ok'}, 14)]
    before = path.read_bytes()
    with pytest.raises(ResearchError, match='incomplete'):
        append_jsonl(str(path), {'name': 'next'})
    assert path.read_bytes() == before


def test_duplicate_sync_preserves_both_records(proj, repo):
    q = services.create_question(proj, 'original')
    path = repo / '.research/questions' / (q['id'] + '.md')
    original = path.read_bytes()
    duplicate = path.with_name('other.md')
    duplicate.write_text(path.read_text().replace('original', 'conflict'))
    before = duplicate.read_bytes()
    with pytest.raises(ResearchError, match='Duplicate id'):
        proj.sync()
    assert path.read_bytes() == original and duplicate.read_bytes() == before
    assert proj.get('question', q['id'])['title'] == 'original'


@pytest.mark.parametrize('entry', ['not json', '[]', '{"frozen": []}'])
def test_corrupt_ledger_never_resets_frozen_tests(proj, repo, entry):
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task')
    directory = repo / '.research/orchestration'
    directory.mkdir(exist_ok=True)
    path = directory / (task['id'] + '.json')
    path.write_text(entry)
    with pytest.raises(ResearchError, match='ledger'):
        orchestrate.assert_frozen_intact(proj, task['id'])
    assert path.read_text() == entry


@pytest.mark.parametrize('entrypoint', ['update', 'mark'])
def test_all_public_completion_paths_enforce_checks(proj, entrypoint):
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task', checks=['cmd:exit 1'])
    proj.author = {'author_type': 'agent', 'author_name': 'implementer'}
    with pytest.raises(PolicyBlocked):
        if entrypoint == 'update':
            plans.update_task(proj, task['id'], status='done')
        else:
            services.set_status(proj, task['id'], 'done')
    assert proj.get('task', task['id'])['status'] == 'todo'


def test_preflight_applies_to_plain_dispatch(proj):
    from research import agents
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task')
    hooks.add(proj, 'required', 'preflight', 'true')
    with pytest.raises(PolicyBlocked):
        agents.dispatch(proj, 'implementer', task['id'])
    hooks.approve(proj)
    result = agents.dispatch(proj, 'implementer', task['id'])
    assert result['preflight']['ran'] == ['required']


def test_headless_timeout_is_failure_and_missing_executable_is_controlled(proj):
    from research import agents
    dispatch = {'argv': [sys.executable, '-c', 'import time; time.sleep(5)'], 'env': {},
                'headless': True, 'usage_parser': None, 'policy': {}, 'role': 'implementer',
                'profile': 'test', 'model': None}
    result = agents.run_headless(proj, dispatch, timeout=0.05)
    assert result['timed_out'] and result['exit_code'] == 124
    dispatch['argv'] = ['/definitely/missing/research-agent']
    with pytest.raises(ResearchError, match='Could not start agent'):
        agents.run_headless(proj, dispatch)


def test_concurrent_claims_have_only_one_owner(proj):
    from research.store import Project
    plan = plans.create_plan(proj, 'plan')
    task = plans.create_task(proj, plan['id'], 'task')
    import threading
    barrier = threading.Barrier(2)
    def claim(name):
        with Project(proj.root, sync=False, author={'author_type': 'agent', 'author_name': name}) as p:
            barrier.wait(timeout=10)
            try:
                plans.start_task(p, task['id'])
                return True
            except PolicyBlocked:
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ['worker-a', 'worker-b']))
    assert sorted(results) == [False, True]


def test_large_concurrent_jsonl_appends_preserve_all_records(tmp_path):
    from research.store import append_jsonl, read_jsonl_from
    path = str(tmp_path / 'records.jsonl')
    def append(n):
        append_jsonl(path, {'id': n, 'payload': str(n) * 100000})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(12)))
    records = [r for r, _ in read_jsonl_from(path, 0)]
    assert sorted(r['id'] for r in records) == list(range(12))
    assert all(r['payload'] == str(r['id']) * 100000 for r in records)


def test_run_canonical_write_failure_rolls_back(proj, monkeypatch):
    exp = services.create_experiment(proj, 'experiment')
    run = runs.attach_run(proj, exp['id'])
    def fail(*args):
        raise OSError('injected canonical run write failure')
    monkeypatch.setattr(runs, '_atomic_write', fail)
    with pytest.raises(OSError):
        runs.update_run(proj, run['id'], label='unwritten')
    assert runs.get_run(proj, run['id'])['label'] is None
    with pytest.raises(OSError):
        runs.attach_run(proj, exp['id'])
    assert len(runs.list_runs(proj)) == 1
