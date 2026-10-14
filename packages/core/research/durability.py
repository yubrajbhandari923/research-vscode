"""Local advisory locks and an undo journal for canonical/index transactions.

The journal is fsynced before each canonical write. A commit marker follows the
SQLite commit. Recovery rolls back an unmarked transaction, then rebuilds the
index; it never guesses which partial records should be kept. Supported writers
share the project lock. Raw filesystem edits and broken network-FS lock/fsync
implementations are outside this protocol.
"""
from __future__ import annotations

import base64
import contextlib
import contextvars
import functools
import json
import os
import tempfile
import threading
from typing import Dict

from .util import ResearchError

_active = contextvars.ContextVar('research_transaction', default=None)
_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}
_local = threading.local()


def fsync_dir(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def replace_bytes(path, data, mode=None):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path) + '.', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
        fsync_dir(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def lock(path, blocking=True):
    """Thread/process exclusion, reentrant only within the owning thread."""
    path = os.path.realpath(path)
    with _guard:
        mutex = _locks.setdefault(path, threading.RLock())
    acquired = mutex.acquire(blocking=blocking)
    if not acquired:
        raise ResearchError('Operation already in progress: ' + path)
    held = getattr(_local, 'held', {})
    _local.held = held
    try:
        if path in held:
            yield held[path]
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a+b') as f:
            try:
                if os.name == 'posix':
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                else:
                    import msvcrt
                    if not os.path.getsize(path):
                        f.write(b'0'); f.flush()
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            except OSError as e:
                raise ResearchError('Operation already in progress: ' + path) from e
            held[path] = f.fileno()
            try:
                yield f.fileno()
            finally:
                del held[path]
                if os.name != 'posix':
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        mutex.release()


def project_dir(path):
    parent = os.path.abspath(path)
    while parent != os.path.dirname(parent):
        if os.path.basename(parent) == '.research':
            return parent
        parent = os.path.dirname(parent)
    return None


@contextlib.contextmanager
def writing(path, append=False):
    root = project_dir(path)
    with lock(os.path.join(root, '.write.lock')) if root else contextlib.nullcontext():
        journal = _active.get()
        if journal and root and os.path.realpath(root) == journal.root:
            journal.before(path, append)
        yield


class Journal:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.path = os.path.join(root, '.transaction.json')
        self.entries = {}
        self.committed = False
        self.token = _active.set(self)

    def save(self):
        replace_bytes(self.path, json.dumps({'version': 1, 'committed': self.committed,
                      'entries': self.entries}, sort_keys=True).encode(), 0o600)

    def before(self, path, append=False):
        rel = os.path.relpath(path, self.root)
        if rel in self.entries:
            return
        if not os.path.realpath(path).startswith(self.root + os.sep):
            raise ResearchError('Canonical transaction refuses an external symlink: ' + path)
        exists = os.path.exists(path)
        entry = {'exists': exists, 'mode': os.stat(path).st_mode & 0o777 if exists else None}
        if exists:
            if append:
                entry['length'] = os.path.getsize(path)
            else:
                with open(path, 'rb') as f:
                    entry['data'] = base64.b64encode(f.read()).decode('ascii')
        self.entries[rel] = entry
        self.save()

    def commit(self):
        if self.entries:
            self.committed = True
            self.save()

    def close(self, remove=True):
        _active.reset(self.token)
        if remove and os.path.exists(self.path):
            os.unlink(self.path)
            fsync_dir(self.root)

    def rollback(self):
        if self.entries:
            restore(self.root, self.entries)


def restore(root, entries):
    for rel, entry in reversed(list(entries.items())):
        path = os.path.join(root, rel)
        if os.path.isabs(rel) or '..' in rel.split(os.sep) or not os.path.realpath(path).startswith(os.path.realpath(root) + os.sep):
            raise ResearchError('Unsafe path in recovery journal: ' + rel)
        if not entry['exists']:
            if os.path.exists(path):
                os.unlink(path)
                fsync_dir(os.path.dirname(path))
        elif 'length' in entry:
            with open(path, 'r+b') as f:
                f.truncate(entry['length']); f.flush(); os.fsync(f.fileno())
        else:
            replace_bytes(path, base64.b64decode(entry['data']), entry['mode'])


def recover(root):
    """Called under the write lock. Returns whether the index needs rebuilding."""
    path = os.path.join(root, '.transaction.json')
    marker = os.path.join(root, '.recovery-needed')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf8') as f:
                rec = json.load(f)
            if rec['version'] != 1:
                raise ValueError('unsupported journal version')
            if not rec['committed']:
                replace_bytes(marker, b'rebuild required\n', 0o600)
                restore(root, rec['entries'])
            os.unlink(path)
            fsync_dir(root)
        except (ValueError, KeyError, TypeError, OSError) as e:
            raise ResearchError('Cannot recover canonical transaction; preserve .transaction.json: ' + str(e)) from e
    return os.path.exists(marker)


def atomic(fn):
    """Group a short record-only service mutation with its audit writes."""
    @functools.wraps(fn)
    def wrapped(project, *args, **kwargs):
        with project.tx():
            return fn(project, *args, **kwargs)
    return wrapped
