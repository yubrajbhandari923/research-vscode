"""Store durability: atomic writes, rebuild, JSONL ledgers, schema migration, concurrent writers."""
import json
import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor

import pytest
from research import orchestrate as O
from research import plans
from research import runs
from research import runs as R
from research import schema
from research import services
from research import services as S
from research import verification as V
from research.store import Project
from research.store import _atomic_write
from research.util import PolicyBlocked
from research.util import ResearchError


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


ENV = {**os.environ, 'PYTHONPATH': os.path.join(ROOT, 'packages', 'core') + os.pathsep + os.path.join(ROOT, 'packages', 'cli')}


@pytest.fixture
def task(proj):
    plan = plans.create_plan(proj, 'p')
    return plans.create_task(proj, plan['id'], 't', goal='g', acceptance_criteria='a', checks=['cmd:true'])


def py(code, root, *args, check=False):
    return subprocess.run([sys.executable, '-c', textwrap.dedent(code), root, *args], env=ENV, capture_output=True, text=True, check=check)


def services_ok(p):
    from research import services
    return services.create_question(p, 'after recovery')['id'].startswith('Q-')


def _exp_with_runs(p, repo, n=2):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "alpha sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]}, planned_runs=6)
    (repo / "sim.py").write_text(
        "import research,sys\na=float(sys.argv[1])\nresearch.log_metric('err', abs(a-0.5))\n"
        "open(f'out_{a}.png','wb').write(b'\\x89PNG\\r\\n')\nresearch.register_artifact(f'out_{a}.png', description='plot')\n")
    runs = [R.exec_run(p, e["id"], f"{sys.executable} sim.py {a}", parameters={"alpha": a}, tee=False)
            for a in (0.1, 0.5)[:n]]
    return q, e, runs


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


def test_failed_recovery_preserves_the_journal(proj, repo):
    (repo / '.research' / '.transaction.json').write_text('{"version": 1, "committed": false, "entries": {"../escape": {"exists": false}}}')
    with pytest.raises(ResearchError, match='Unsafe path in recovery journal'):
        Project(str(repo))
    assert (repo / '.research' / '.transaction.json').exists()


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


def test_migration_v2_to_v3_adds_columns_and_reviews(tmp_path):
    db = str(tmp_path / "old.db")
    conn = schema.connect(db)
    for stmt in schema.MIGRATIONS[1] + schema.MIGRATIONS[2]:
        stmt(conn) if callable(stmt) else conn.execute(stmt)
    for col in ("skills", "checks", "claimed_by", "claimed_at"):
        conn.execute(f"ALTER TABLE tasks DROP COLUMN {col}")
    conn.execute("ALTER TABLE plans DROP COLUMN skills")
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','2')")
    assert schema.migrate(conn) == 3
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert {"skills", "checks", "claimed_by", "claimed_at"} <= cols
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='reviews'").fetchone()
    assert schema.migrate(conn) == 3  # idempotent


def test_reviews_survive_rebuild(proj, repo, monkeypatch):
    q, e, runs = _exp_with_runs(proj, repo)
    f = S.create_finding(proj, "x", supports=[runs[0]["id"]])
    V.review_finding(proj, f["id"], "supported", "ok")
    proj.rebuild_index(backup=True)
    assert V.latest(proj, f["id"], "review")["summary"] == "ok"
