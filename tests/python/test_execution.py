"""Task execution: one process per task, recorded states, launcher races."""
import json
import os
import subprocess
import sys

import pytest
from research import agents
from research import plans
from research.execution import launch


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


ENV = {**os.environ, 'PYTHONPATH': os.path.join(ROOT, 'packages', 'core') + os.pathsep + os.path.join(ROOT, 'packages', 'cli')}


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
