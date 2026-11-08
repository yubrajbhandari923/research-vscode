"""WP5b: a read-only open never takes the write lock when nothing changed; `dirty` and `refresh_context` follow commits."""
import threading

import pytest

from research import context as C, durability, services
from research.store import Project


def _count_locks(monkeypatch):
    taken = []
    real = durability.lock

    def counting(path, *a, **kw):
        taken.append(str(path))
        return real(path, *a, **kw)
    monkeypatch.setattr(durability, "lock", counting)
    return taken


def test_twenty_concurrent_status_calls_never_take_the_write_lock(proj, repo, monkeypatch):
    services.create_question(proj, "q")
    proj.close()
    Project(str(repo)).close()   # settle: the first open after a write may ingest/export once
    taken = _count_locks(monkeypatch)
    errs = []

    def status():
        try:
            with Project.find(str(repo)) as p:
                C.resume_context(p)
        except Exception as e:   # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=status) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and [t for t in taken if t.endswith(".write.lock")] == []


def test_a_hand_edit_is_still_ingested_and_takes_the_lock(proj, repo, monkeypatch):
    q = services.create_question(proj, "q")["id"]
    path = proj.mirror_path("question", q)
    proj.close()
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(text.replace("q\n", "edited title\n", 1))
    taken = _count_locks(monkeypatch)
    with Project.find(str(repo)) as p:
        assert p.get("question", q)["title"] == "edited title"
    assert [t for t in taken if t.endswith(".write.lock")]


def test_dirty_is_set_by_a_commit_and_cleared_by_refresh_context(proj):
    proj.refresh_context()
    assert proj.dirty is False
    services.create_question(proj, "another")
    assert proj.dirty is True
    proj.refresh_context()
    assert proj.dirty is False
