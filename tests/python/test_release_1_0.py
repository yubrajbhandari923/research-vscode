"""1.0.0 acceptance scenarios: real processes, real files, simulated provider (never a live model)."""
import io
import json
import os
import subprocess
import sys
import textwrap

import pytest

from research import agents, hooks, mcp, orchestrate as O, plans, rpc, verification
from research.execution import launch
from research.store import Project
from research.util import PolicyBlocked, ResearchError

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV = {**os.environ, 'PYTHONPATH': os.path.join(ROOT, 'packages', 'core') + os.pathsep + os.path.join(ROOT, 'packages', 'cli')}


def py(code, root, *args, check=False):
    return subprocess.run([sys.executable, '-c', textwrap.dedent(code), root, *args], env=ENV, capture_output=True, text=True, check=check)


def fake_provider(proj, tmp_path, body):
    script = tmp_path / 'provider.py'
    script.write_text(body)
    proj.config['agents']['profiles']['fake'] = {'command': sys.executable, 'args': [str(script)]}
    proj.save_config()


@pytest.fixture
def task(proj):
    plan = plans.create_plan(proj, 'p')
    return plans.create_task(proj, plan['id'], 't', goal='g', acceptance_criteria='a', checks=['cmd:true'])


# ---- 4: exclusive execution -------------------------------------------------------------------------------
def test_only_one_process_executes_a_task(proj, repo, tmp_path, task):
    fake_provider(proj, tmp_path, f"import time; open({str(tmp_path / 'ran')!r}, 'a').write('x'); time.sleep(1.5)\n")
    a = agents.dispatch(proj, 'implementer', task['id'], profile='fake', allow_unfrozen='test')
    b = agents.dispatch(proj, 'implementer', task['id'], profile='fake', allow_unfrozen='test')
    assert a['state'] == 'prepared' and a['execution_id'] != b['execution_id']
    procs = [subprocess.Popen(d['argv'], cwd=repo, env={**ENV, **d['env']}, stderr=subprocess.PIPE, text=True) for d in (a, b)]
    results = [p.wait(timeout=30) for p in procs]
    assert sorted(results) == [0, 3]                                   # one ran; the other refused, not queued
    assert (tmp_path / 'ran').read_text() == 'x'
    winner = (a, b)[results.index(0)]
    again = subprocess.run(winner['argv'], cwd=repo, env={**ENV, **winner['env']}, capture_output=True, text=True)
    assert again.returncode == 3 and 'already' in again.stderr   # a prepared command is single-use


def test_execution_states_are_recorded(proj, repo, tmp_path, task):
    fake_provider(proj, tmp_path, 'import sys; sys.exit(7)\n')
    d = agents.dispatch(proj, 'implementer', task['id'], profile='fake', allow_unfrozen='test')
    rec = tmp_path.parent  # noqa: F841
    assert launch(repo, d['execution_id']) == 7
    with open(os.path.join(repo, '.research', 'orchestration', 'executions', d['execution_id'] + '.json')) as f:
        r = json.load(f)
    assert r['state'] == 'failed' and r['exit_code'] == 7


def test_parallel_ledger_updates_are_not_lost(proj, repo, task):
    code = '''
        import sys
        from research.store import Project
        from research import orchestrate as O
        with Project(sys.argv[1]) as p:
            for i in range(8):
                O.record_attempt(p, sys.argv[2], 'sonnet', 'fail', 'incorrect_implementation', note=sys.argv[3] + str(i))
    '''
    procs = [subprocess.Popen([sys.executable, '-c', textwrap.dedent(code), str(repo), task['id'], n], env=ENV) for n in 'ab']
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    with Project(str(repo)) as p:
        assert len(O.ledger(p, task['id'])['attempts']) == 16


# ---- 5: crash consistency ---------------------------------------------------------------------------------
@pytest.mark.parametrize('where', ['inside', 'after_sqlite_commit'])
def test_killed_writer_recovers_to_one_coherent_state(proj, repo, where):
    code = '''
        import os, sys
        from research.store import Project
        from research import services, durability
        where = sys.argv[2]
        with Project(sys.argv[1]) as p:
            if where == 'after_sqlite_commit':
                durability.Journal.commit = lambda self: os._exit(9)
            with p.tx():
                services.create_question(p, 'crash victim')
                if where == 'inside':
                    os._exit(9)
    '''
    assert py(code, str(repo), where).returncode == 9
    assert os.path.exists(repo / '.research' / '.transaction.json')
    for _ in range(2):                                         # reopening repeatedly is idempotent
        with Project(str(repo)) as p:
            assert [q['title'] for q in p.list('question')] == []
    assert not os.path.exists(repo / '.research' / '.transaction.json')
    assert not list((repo / '.research' / 'questions').glob('Q-*.md'))
    with Project(str(repo)) as p:
        assert 'crash victim' not in json.dumps(p.q('SELECT summary FROM events'))
        assert services_ok(p)


def services_ok(p):
    from research import services
    return services.create_question(p, 'after recovery')['id'].startswith('Q-')


def test_failed_recovery_preserves_the_journal(proj, repo):
    (repo / '.research' / '.transaction.json').write_text('{"version": 1, "committed": false, "entries": {"../escape": {"exists": false}}}')
    with pytest.raises(ResearchError, match='Unsafe path in recovery journal'):
        Project(str(repo))
    assert (repo / '.research' / '.transaction.json').exists()


# ---- 2/3: one effective task specification and policy across interfaces ----------------------------------
def test_same_obligations_through_every_interface(proj, repo, task):
    O.set_contract(proj, task['id'], objective='g', constraints=['no new deps'], allowed_files=['src/*.py'])
    hooks.add(proj, 'gate', 'post_implementation', 'true', required=True)      # unapproved, required
    core = plans.task_context(proj, task['id'])['effective_spec']
    out = io.StringIO()
    req = {'id': 1, 'method': 'task_context', 'params': {'id': task['id']}}
    rpc.serve(str(repo), io.StringIO(json.dumps(req) + '\n'), out)
    rows = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r for r in rows if r.get('id') == 1][0]['result']['effective_spec'] == core
    out = io.StringIO()
    msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'task_context', 'arguments': {'id': task['id']}}}
    mcp.serve(str(repo), io.StringIO(json.dumps(msg) + '\n'), out)
    text = json.dumps(json.loads(out.getvalue()))
    for needle in ('no new deps', 'src/*.py', 'HUMAN APPROVAL REQUIRED'):
        assert needle in text
    cli = subprocess.run([sys.executable, '-m', 'research_cli', 'task', 'context', task['id'], '--json'], cwd=repo, env=ENV,
                         capture_output=True, text=True)
    assert cli.returncode == 0 and json.loads(cli.stdout)['effective_spec'] == core
    brief = (repo / agents.dispatch(proj, 'implementer', task['id'], allow_unfrozen='x')['brief']).read_text()
    assert 'no new deps' in brief and 'HUMAN APPROVAL REQUIRED' in brief


def test_conflicting_legacy_fields_block_dispatch_and_completion(proj, task):
    O.set_contract(proj, task['id'], objective='something else')
    spec = plans.task_context(proj, task['id'])['effective_spec']
    assert [c['field'] for c in spec['conflicts']] == ['objective']
    with pytest.raises(PolicyBlocked, match='Conflicting'):
        agents.dispatch(proj, 'implementer', task['id'])
    assert not O_passed(proj, task['id'])


def O_passed(proj, tid):
    return O.verify_work(proj, tid)['passed']


def test_required_unapproved_hook_blocks_every_completion_path(proj):
    hooks.add(proj, 'gate', 'post_implementation', 'true', required=True)
    plan = plans.create_plan(proj, 'p')
    from research.util import detect_author
    proj.author = detect_author(agent='claude')
    with pytest.raises(PolicyBlocked):
        plans.create_task(proj, plan['id'], 'born done', checks=['cmd:true'], status='done')
    t = plans.create_task(proj, plan['id'], 'x', checks=['cmd:true'])
    plans.start_task(proj, t['id'])
    for finish in (lambda: plans.update_task(proj, t['id'], status='done'), lambda: plans.complete_task(proj, t['id']),
                   lambda: verification.verify_task(proj, t['id'])):
        try:
            res = finish()
        except PolicyBlocked:
            continue
        assert res.get('passed') is False          # verification reports; it never confers completion
    assert proj.get('task', t['id'])['status'] != 'done'


def test_no_verifier_is_unverified_not_passed(proj):
    plan = plans.create_plan(proj, 'p')
    t = plans.create_task(proj, plan['id'], 'x')
    r = O.verify_work(proj, t['id'])
    assert not r['passed'] and r['verification_state'] == 'unverified'


def test_cli_exit_code_reflects_failed_verification(proj, repo):
    plan = plans.create_plan(proj, 'p')
    t = plans.create_task(proj, plan['id'], 'x', checks=['cmd:false'])
    run = subprocess.run([sys.executable, '-m', 'research_cli', 'task', 'verify', t['id'], '--json'], cwd=repo, env=ENV,
                         capture_output=True, text=True)
    assert run.returncode != 0 and json.loads(run.stdout)['passed'] is False


# ---- 6: project-wide blockers ----------------------------------------------------------------------------
def test_blocked_task_in_another_active_plan_is_listed(proj):
    from research import context
    a, b = plans.create_plan(proj, 'a'), plans.create_plan(proj, 'b')
    for pl in (a, b):
        plans.update_plan(proj, pl['id'], status='active')
    t = plans.create_task(proj, b['id'], 'stuck')
    plans.update_task(proj, t['id'], status='blocked', blockers='need data')
    assert [x['id'] for x in context.research_home(proj)['blocked_tasks']] == [t['id']]


@pytest.mark.skipif(os.name != 'posix', reason='descriptor inheritance')
def test_killed_launcher_cannot_let_a_survivor_race_a_replacement(proj, repo, tmp_path, task):
    import signal
    import time
    fake_provider(proj, tmp_path, 'import time; time.sleep(4)\n')
    a = agents.dispatch(proj, 'implementer', task['id'], profile='fake', allow_unfrozen='test')
    b = agents.dispatch(proj, 'implementer', task['id'], profile='fake', allow_unfrozen='test')
    wrapper = subprocess.Popen(a['argv'], cwd=repo, env={**ENV, **a['env']})
    path = repo / '.research' / 'orchestration' / 'executions' / (a['execution_id'] + '.json')
    for _ in range(100):
        if json.loads(path.read_text())['state'] == 'running':
            break
        time.sleep(0.1)
    pid = json.loads(path.read_text())['pid']
    wrapper.send_signal(signal.SIGKILL); wrapper.wait()
    try:
        replacement = subprocess.run(b['argv'], cwd=repo, env={**ENV, **b['env']}, capture_output=True, text=True)
        assert replacement.returncode == 3                      # the surviving provider still owns the task
    finally:
        os.kill(pid, signal.SIGKILL)
