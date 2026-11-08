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


def prepare(p, task, argv, env, cwd=None, headless=False, request=None, usage_parser=None, watchdog=None):
    """`request` = the dispatch arguments, so a retry (or a continuation with another provider) can rebuild the dispatch."""
    identity = uuid.uuid4().hex
    record = {'id': identity, 'task': task, 'state': 'prepared', 'prepared_at': now_iso(),
              'argv': argv, 'env': env, 'cwd': cwd, 'mode': 'headless' if headless else 'interactive',
              'request': request or {}, 'usage_parser': usage_parser, 'watch': watchdog}
    with p.tx():
        _atomic_write(path(p, identity), json.dumps(record, sort_keys=True) + '\n')
    return identity, [sys.executable, os.path.abspath(__file__), p.root, identity]


def _watch(child, out_path, wd):
    """Wait for `child` like wait(), but for providers that can hang or go silent (design D.6). Polls the captured stdout:
    - after the provider's final event (adapter.final_event) it gets `exit_grace_seconds`, then its process group is stopped and the
      run counts as exit 0 (it finished; it just did not exit);
    - no new output for `idle_timeout_seconds` stops it with exit 124;
    - exit 0 with no final event is kept as completed with a `provider_warning` (verification still decides the task).
    Returns (exit_code, extra record fields)."""
    import time
    from research import providers
    from research.processes import stop_process
    final_event = providers.ADAPTERS[wd['provider']].final_event
    grace, idle = float(wd.get('exit_grace_seconds') or 30), float(wd.get('idle_timeout_seconds') or 0)
    pos, buf, final_at, last = 0, b'', None, time.monotonic()
    while True:
        code = child.poll()   # before reading: after an exit, the read below sees everything the provider wrote
        try:
            with open(out_path, 'rb') as f:
                f.seek(pos)
                chunk = f.read()
        except OSError:
            chunk = b''
        if chunk:
            pos += len(chunk)
            last = time.monotonic()
            buf += chunk
        *lines, buf = buf.split(b'\n')
        if code is not None and buf.strip():
            lines, buf = lines + [buf], b''
        if final_at is None and any(final_event(l.decode('utf8', 'replace')) for l in lines):
            final_at = time.monotonic()
        if code is not None:
            return code, ({} if final_at or code else {'provider_warning': 'no final event; usage may be partial'})
        now = time.monotonic()
        if final_at is not None and now - final_at >= grace:
            stop_process(child)
            return 0, {'watchdog': 'stopped after final event (provider did not exit)'}
        if idle and final_at is None and now - last >= idle:
            stop_process(child)
            return 124, {'watchdog': f'no output for {int(idle)} s'}
        time.sleep(0.2)


def launch(root, identity):
    with Project(root) as p:
        filename = path(p, identity)
        with open(filename, encoding='utf8') as f:
            record = json.load(f)
        task = record['task']
    # Lock ordering: task ownership before any short project transaction.
    with Project(root, sync=False) as _p:
        lock_path = os.path.join(_p.rdir, 'locks', task + '.execution')
    with durability.lock(lock_path, blocking=False) as fd:
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
            headless = record.get('mode') == 'headless'
            if headless:  # captured, and in its own session so a cancel stops the provider's descendants too
                out, err = (open(filename[:-5] + s, 'wb') for s in ('.out', '.err'))
                kwargs.update(stdout=out, stderr=err, stdin=subprocess.DEVNULL, start_new_session=os.name == 'posix')
            try:
                child = subprocess.Popen(record['argv'], cwd=record.get('cwd') or root,
                                         env={**os.environ, **record['env']}, **kwargs)
            except OSError as e:  # missing executable, bad cwd: a failure with a reason, not a silent launcher crash
                with Project(root) as p:
                    record.update(state='failed', exit_code=127, error=f'could not start {record["argv"][0]!r}: {e}', ended_at=now_iso())
                    with p.tx():
                        _atomic_write(filename, json.dumps(record) + '\n')
                        p.event('task', task, 'execution_failed', identity + ': ' + record['error'])
                print(record['error'], file=sys.stderr)
                return 127
            with Project(root) as p:
                record.update(state='running', pid=child.pid)
                _atomic_write(filename, json.dumps(record) + '\n')
            wd = record.get('watch')
            code, note = _watch(child, filename[:-5] + '.out', wd) if headless and wd else (child.wait(), {})
            if headless:
                out.close(); err.close()
        except BaseException:
            # An interrupted wrapper cannot assert the provider has stopped.
            with Project(root) as p:
                record.update(state='unknown', observed_at=now_iso())
                _atomic_write(filename, json.dumps(record) + '\n')
            raise
        with Project(root) as p:
            with open(filename, encoding='utf8') as f:
                cancelled = json.load(f).get('cancel_requested')
            state = 'cancelled' if cancelled else 'completed' if code == 0 else 'failed'
            record.update(state=state, exit_code=code, ended_at=now_iso(), **note)
            if record.get('mode') == 'headless':
                from research import usage as U
                parse = U.PARSERS.get(record.get('usage_parser') or '')
                if parse:
                    try:
                        parsed = parse(open(filename[:-5] + '.out', encoding='utf8', errors='replace').read())
                    except OSError:
                        parsed = {}
                    req = record.get('request') or {}
                    rec = U.record(p, record['usage_parser'], parsed, task=task, role=req.get('role'), profile=req.get('profile'),
                                   model=req.get('model')) if parsed else None
                    record['reported'] = {k: v for k, v in parsed.items() if k != 'result_text'} or None
                    record['usage_recorded'] = bool(rec)
            with p.tx():
                _atomic_write(filename, json.dumps(record) + '\n')
                p.event('task', task, 'execution_' + record['state'], identity + ': exit ' + str(code))
                if note.get('watchdog'):
                    p.event('task', task, 'execution_watchdog', identity + ': ' + note['watchdog'])
        return code


# ----------------------------------------------------------------------------- observation (never launches anything)
def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def show(p, identity):
    """The record plus what is observable now. `running` is only reported while the recorded process exists."""
    with open(path(p, identity), encoding='utf8') as f:
        r = json.load(f)
    if r['state'] in ('launched', 'running') and r.get('launcher_pid') and not _alive(r['launcher_pid']):
        r['state'] = 'unknown'
        r['note'] = 'the launcher exited without recording an outcome; the provider may still be running'
    r['alive'] = bool(r.get('pid') and _alive(r['pid'])) if r['state'] == 'running' else False
    r.pop('env', None)
    return r


def list_all(p, task=None):
    d = os.path.dirname(path(p, '0'))
    out = []
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if n.endswith('.json'):
            r = show(p, n[:-5])
            if not task or r['task'] == task:
                out.append(r)
    return sorted(out, key=lambda r: r['prepared_at'], reverse=True)


def logs(p, identity, tail=4000):
    base = path(p, identity)[:-5]
    out = {}
    for s in ('out', 'err'):
        try:
            with open(f'{base}.{s}', 'rb') as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - tail))
                out[s] = f.read().decode('utf8', errors='replace')
        except OSError:
            out[s] = None   # interactive sessions write to the terminal, not to a log
    return out


def stop(p, identity):
    """Ask the running provider to stop. The launcher records the final state ('cancelled')."""
    r = show(p, identity)
    if r['state'] != 'running' or not r['alive']:
        raise ResearchError(f"Execution is {r['state']}, nothing to stop")
    with p.tx():
        raw = json.load(open(path(p, identity), encoding='utf8'))
        raw['cancel_requested'] = now_iso()
        _atomic_write(path(p, identity), json.dumps(raw) + '\n')
        p.event('task', r['task'], 'execution_cancel_requested', identity)
    import signal
    try:
        if os.name == 'posix' and r.get('mode') == 'headless':
            os.killpg(r['pid'], signal.SIGTERM)
        else:
            os.kill(r['pid'], signal.SIGTERM)
    except ProcessLookupError:
        pass
    return {'id': identity, 'signalled': r['pid']}


if __name__ == '__main__':
    try:
        sys.exit(launch(sys.argv[1], sys.argv[2]))
    except ResearchError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(3)
