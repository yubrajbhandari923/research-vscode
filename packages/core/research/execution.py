"""Task launch ownership shared by terminal, CLI and headless dispatch.

The kernel lock is held for the provider lifetime, including in the direct child
on POSIX. A prepared command is single-use. Records describe observations, not
an assurance that a provider's detached descendants have stopped.
"""
import json
import os
import subprocess
import sys
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import durability
from research.store import Project, _atomic_write
from research.util import ResearchError, now_iso


def path(p, identity):
    if not identity or any(c not in '0123456789abcdef' for c in identity):
        raise ResearchError('Invalid execution identity')
    return p.path('orchestration', 'executions', identity + '.json')


def prepare(p, task, argv, env):
    identity = uuid.uuid4().hex
    record = {'id': identity, 'task': task, 'state': 'prepared', 'prepared_at': now_iso(),
              'argv': argv, 'env': env}
    with p.tx():
        _atomic_write(path(p, identity), json.dumps(record, sort_keys=True) + '\n')
    return identity, [sys.executable, os.path.abspath(__file__), p.root, identity]


def launch(root, identity):
    with Project(root) as p:
        filename = path(p, identity)
        with open(filename, encoding='utf8') as f:
            record = json.load(f)
        task = record['task']
    # Lock ordering: task ownership before any short project transaction.
    with durability.lock(os.path.join(root, '.research', 'locks', task + '.execution'), blocking=False) as fd:
        with Project(root) as p:
            with open(filename, encoding='utf8') as f:
                record = json.load(f)
            if record['state'] != 'prepared':
                raise ResearchError('This prepared execution has already been consumed; prepare a new dispatch')
            from research.task_spec import effective, assert_consistent
            assert_consistent(effective(p, task))
            from research.orchestrate import assert_frozen_intact
            assert_frozen_intact(p, task)
            from research import hooks
            hooks.gate(p, 'preflight', task=task)
            record.update(state='launched', launched_at=now_iso(), launcher_pid=os.getpid())
            with p.tx():
                _atomic_write(filename, json.dumps(record) + '\n')
                p.event('task', task, 'execution_launched', identity)
        try:
            kwargs = {'pass_fds': (fd,)} if os.name == 'posix' else {}
            child = subprocess.Popen(record['argv'], cwd=root,
                                     env={**os.environ, **record['env']}, **kwargs)
            with Project(root) as p:
                record.update(state='running', pid=child.pid)
                _atomic_write(filename, json.dumps(record) + '\n')
            code = child.wait()
        except BaseException:
            # An interrupted wrapper cannot assert the provider has stopped.
            with Project(root) as p:
                record.update(state='unknown', observed_at=now_iso())
                _atomic_write(filename, json.dumps(record) + '\n')
            raise
        with Project(root) as p:
            record.update(state='completed' if code == 0 else 'failed', exit_code=code, ended_at=now_iso())
            with p.tx():
                _atomic_write(filename, json.dumps(record) + '\n')
                p.event('task', task, 'execution_' + record['state'], identity + ': exit ' + str(code))
        return code


if __name__ == '__main__':
    try:
        sys.exit(launch(sys.argv[1], sys.argv[2]))
    except ResearchError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(3)
