"""Entity definitions + SQLite schema/migrations. Single source of truth for fields."""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .util import ResearchError

SCHEMA_VERSION = 3

AUTHOR_COLS = [("author_type", "text"), ("author_name", "text"), ("author_model", "text")]
TIME_COLS = [("created_at", "text"), ("updated_at", "text")]

STATUSES = {
    "question": ["open", "investigating", "answered", "blocked", "abandoned"],
    "experiment": ["proposed", "ready", "running", "needs_review", "completed", "failed", "abandoned"],
    "run": ["queued", "running", "completed", "failed", "cancelled", "unknown"],
    "finding": ["preliminary", "supported", "contradicted", "superseded"],
    "decision": ["active", "reversed", "superseded"],
    "plan": ["active", "completed", "blocked", "abandoned"],
    "task": ["todo", "running", "verify", "done", "blocked"],  # 'ready' is derived, not stored
}
FINDING_KINDS = ["result", "failure"]
# Verdicts: task checks pass/fail; finding reviews supported/contradicted/needs_work
REVIEW_VERDICTS = {"check": ["pass", "fail"], "review": ["supported", "contradicted", "needs_work"]}
CONFIDENCE = ["low", "medium", "high"]
RUN_TERMINAL = {"completed", "failed", "cancelled", "unknown"}

# Task types: recommended canonical values (extensible - custom values allowed)
TASK_TYPES = ["research", "implementation", "experiment", "analysis", "verification", "synthesis", "debug", "data"]
# Task roles: defaults for agent routing (extensible)
TASK_ROLES = ["planner", "implementer", "verifier", "analyst", "human"]

# Entities that are mirrored as Markdown files.
#   columns: (column, kind)          kind ∈ text | json | bool | int
#   fm:      column -> front-matter key (if different from column)
#   sections: (column, heading) rendered as '## heading' blocks in the body
#   links:   (front-matter key, relation) stored in `links` table (src = this entity)
ENTITIES: Dict[str, dict] = {
    "question": {
        "table": "questions", "prefix": "Q", "width": 3, "dir": "questions",
        "columns": [("title", "text"), ("status", "text"), ("parent_id", "text"), ("tags", "json")]
        + AUTHOR_COLS + TIME_COLS + [("description", "text")],
        "fm": {"parent_id": "parent"},
        "sections": [("description", "Description")],
        "links": [],
    },
    "experiment": {
        "table": "experiments", "prefix": "EXP", "width": 3, "dir": "experiments",
        "columns": [
            ("title", "text"), ("status", "text"), ("question_id", "text"), ("parent_id", "text"),
            ("is_baseline", "bool"), ("parameters", "json"), ("param_delta", "json"),
            ("metrics_requested", "json"), ("expected_artifacts", "json"), ("planned_runs", "int"),
            ("tags", "json"), ("git_branch", "text"), ("git_commit", "text"), ("git_dirty", "bool"),
        ] + AUTHOR_COLS + TIME_COLS + [
            ("started_at", "text"), ("completed_at", "text"),
            ("hypothesis", "text"), ("motivation", "text"), ("method", "text"),
            ("expected_outcome", "text"), ("success_criteria", "text"), ("stop_conditions", "text"),
            ("limitations", "text"), ("notes", "text"),
        ],
        "fm": {"question_id": "question", "parent_id": "derived_from", "metrics_requested": "metrics",
               "is_baseline": "baseline"},
        "sections": [
            ("hypothesis", "Hypothesis"), ("motivation", "Motivation"), ("method", "Method"),
            ("expected_outcome", "Expected outcome"), ("success_criteria", "Success criteria"),
            ("stop_conditions", "Stop conditions"), ("limitations", "Known limitations"),
            ("notes", "Notes"),
        ],
        "links": [],
    },
    "finding": {
        "table": "findings", "prefix": "F", "width": 3, "dir": "findings",
        "columns": [("title", "text"), ("kind", "text"), ("status", "text"), ("confidence", "text"),
                    ("superseded_by", "text"), ("tags", "json")]
        + AUTHOR_COLS + TIME_COLS
        + [("statement", "text"), ("limitations", "text"), ("contradicting_evidence", "text")],
        "fm": {},
        "sections": [("statement", "Statement"), ("limitations", "Limitations"),
                     ("contradicting_evidence", "Contradicting evidence")],
        "links": [("supports", "supports"), ("contradicts", "contradicts"),
                  ("related", "related"), ("questions", "addresses")],
    },
    "decision": {
        "table": "decisions", "prefix": "D", "width": 3, "dir": "decisions",
        "columns": [("title", "text"), ("status", "text"), ("date", "text"), ("superseded_by", "text"),
                    ("tags", "json")]
        + AUTHOR_COLS + TIME_COLS + [("statement", "text"), ("reason", "text")],
        "fm": {},
        "sections": [("statement", "Decision"), ("reason", "Reason")],
        "links": [("supporting_findings", "based_on"), ("experiments", "related")],
    },
    "checkpoint": {
        "table": "checkpoints", "prefix": "CP", "width": 3, "dir": "checkpoints",
        "columns": [("title", "text"), ("baseline_experiment_id", "text"),
                    ("finding_ids", "json"), ("failure_ids", "json"), ("question_ids", "json"),
                    ("experiment_ids", "json"),
                    ("git_branch", "text"), ("git_commit", "text"), ("git_dirty", "bool")]
        + AUTHOR_COLS + TIME_COLS
        + [("goal", "text"), ("understanding", "text"), ("baseline", "text"),
           ("current_problem", "text"), ("next_experiment", "text"), ("notes", "text")],
        "fm": {"baseline_experiment_id": "baseline_experiment", "finding_ids": "important_findings",
               "failure_ids": "known_failures", "question_ids": "open_questions",
               "experiment_ids": "active_experiments"},
        "sections": [("goal", "Current goal"), ("understanding", "Current understanding"),
                     ("baseline", "Baseline"), ("current_problem", "Current problems / blockers"),
                     ("next_experiment", "Next step"), ("notes", "Notes")],
        "links": [],
    },
    "plan": {
        "table": "plans", "prefix": "PLAN", "width": 3, "dir": "plans",
        "columns": [
            ("title", "text"), ("status", "text"), ("root_question_id", "text"), ("skills", "json"),
        ] + AUTHOR_COLS + TIME_COLS + [
            ("completed_at", "text"),
            ("objective", "text"), ("success_criteria", "text"), ("context", "text"),
        ],
        "fm": {"root_question_id": "question"},
        "sections": [("objective", "Objective"), ("success_criteria", "Success Criteria"), ("context", "Context")],
        "links": [],
    },
    "task": {
        "table": "tasks", "prefix": "T", "width": 3, "dir": "tasks",
        "columns": [
            ("plan_id", "text"), ("title", "text"), ("task_type", "text"), ("status", "text"),
            ("assigned_role", "text"), ("depends_on", "json"),
            ("related_question_id", "text"), ("related_experiment_id", "text"),
            ("artifacts", "json"), ("skills", "json"), ("checks", "json"),
            ("claimed_by", "text"), ("claimed_at", "text"),
        ] + AUTHOR_COLS + TIME_COLS + [
            ("completed_by", "text"),  # who completed this task (agent name or human)
            ("started_at", "text"), ("completed_at", "text"),
            ("goal", "text"), ("inputs", "text"), ("expected_outputs", "text"),
            ("acceptance_criteria", "text"), ("verification", "text"),
            ("result", "text"), ("blockers", "text"), ("notes", "text"),
        ],
        "fm": {"plan_id": "plan", "related_question_id": "question", "related_experiment_id": "experiment"},
        "sections": [
            ("goal", "Goal"), ("inputs", "Inputs"), ("expected_outputs", "Expected Outputs"),
            ("acceptance_criteria", "Acceptance Criteria"), ("verification", "Verification"),
            ("result", "Result"), ("blockers", "Blockers"), ("notes", "Notes"),
        ],
        "links": [],
    },
}

PREFIX_TO_TYPE = {"Q": "question", "EXP": "experiment", "RUN": "run", "F": "finding",
                  "D": "decision", "CP": "checkpoint", "A": "artifact", "PLAN": "plan", "T": "task"}
TYPE_TO_PREFIX = {v: k for k, v in PREFIX_TO_TYPE.items()}
WIDTH = {"Q": 3, "EXP": 3, "RUN": 4, "F": 3, "D": 3, "CP": 3, "A": 4, "PLAN": 3, "T": 3}

_ID_RE = re.compile(r"^\s*(EXP|RUN|CP|Q|F|D|A|PLAN|T)[-_ ]?0*(\d+)\s*$", re.I)


def format_id(prefix: str, n: int) -> str:
    return f"{prefix}-{n:0{WIDTH[prefix]}d}"


def parse_id(s: str) -> Optional[Tuple[str, int]]:
    m = _ID_RE.match(str(s))
    if not m:
        return None
    return m.group(1).upper(), int(m.group(2))


def normalize_id(s: Optional[str], expect: Optional[str] = None) -> Optional[str]:
    """'exp-1', 'EXP1', 'EXP-001' → 'EXP-001'. Bare numbers accepted when `expect` given."""
    if s is None or str(s).strip() == "":
        return None
    s = str(s).strip()
    if expect and re.fullmatch(r"\d+", s):
        return format_id(expect, int(s))
    p = parse_id(s)
    if not p:
        if s.startswith("note:"):
            return s
        raise ResearchError(f"Not a valid research id: {s!r}")
    if expect and p[0] != expect:
        raise ResearchError(f"Expected a {PREFIX_TO_TYPE[expect]} id ({expect}-…), got {s!r}")
    return format_id(p[0], p[1])


def type_of(id_: str) -> str:
    if id_.startswith("note:"):
        return "note"
    p = parse_id(id_)
    if not p:
        raise ResearchError(f"Not a valid research id: {id_!r}")
    return PREFIX_TO_TYPE[p[0]]


def _coltype(kind: str) -> str:
    return {"text": "TEXT", "json": "TEXT", "bool": "INTEGER", "int": "INTEGER"}[kind]


def _entity_ddl() -> List[str]:
    out = []
    for spec in ENTITIES.values():
        cols = ",\n  ".join(f"{c} {_coltype(k)}" for c, k in spec["columns"])
        out.append(f"CREATE TABLE IF NOT EXISTS {spec['table']} (\n  id TEXT PRIMARY KEY,\n  {cols}\n)")
    return out


DDL_V1 = _entity_ddl() + [
    """CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE IF NOT EXISTS counters (prefix TEXT PRIMARY KEY, next INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS syntheses (
      id TEXT PRIMARY KEY,
      experiment_id TEXT NOT NULL REFERENCES experiments(id),
      what_happened TEXT, what_worked TEXT, what_failed TEXT, interpretation TEXT,
      limitations TEXT, unresolved TEXT, next_experiment TEXT,
      runs_covered TEXT, author_type TEXT, author_name TEXT, author_model TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS runs (
      id TEXT PRIMARY KEY,
      experiment_id TEXT NOT NULL REFERENCES experiments(id),
      label TEXT, command TEXT, working_dir TEXT, parameters TEXT, env TEXT,
      backend TEXT, hostname TEXT, pid INTEGER, slurm_job_id TEXT, slurm TEXT,
      run_dir TEXT, stdout_path TEXT, stderr_path TEXT,
      git_branch TEXT, git_commit TEXT, git_dirty INTEGER,
      status TEXT, exit_code INTEGER, reviewed INTEGER DEFAULT 0, override_reason TEXT,
      created_at TEXT, started_at TEXT, ended_at TEXT, updated_at TEXT,
      author_type TEXT, author_name TEXT, author_model TEXT, notes TEXT,
      metrics_offset INTEGER DEFAULT 0, artifacts_offset INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS metrics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT REFERENCES runs(id), experiment_id TEXT REFERENCES experiments(id),
      name TEXT NOT NULL, value REAL, value_text TEXT, step INTEGER, unit TEXT, timestamp TEXT,
      author_type TEXT)""",
    """CREATE TABLE IF NOT EXISTS artifacts (
      id TEXT PRIMARY KEY, run_id TEXT, experiment_id TEXT,
      name TEXT, type TEXT, path TEXT NOT NULL, external INTEGER DEFAULT 0, description TEXT,
      size INTEGER, mtime TEXT, hash TEXT, tags TEXT, preview TEXT,
      author_type TEXT, author_name TEXT, author_model TEXT, created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS links (
      src_type TEXT NOT NULL, src_id TEXT NOT NULL, dst_type TEXT NOT NULL, dst_id TEXT NOT NULL,
      relation TEXT NOT NULL, note TEXT,
      PRIMARY KEY (src_id, dst_id, relation))""",
    """CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, entity_type TEXT, entity_id TEXT,
      action TEXT, summary TEXT, author_type TEXT, author_name TEXT)""",
    """CREATE TABLE IF NOT EXISTS mirrors (
      path TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT, hash TEXT, mtime REAL, size INTEGER)""",
    "CREATE INDEX IF NOT EXISTS ix_runs_exp ON runs(experiment_id)",
    "CREATE INDEX IF NOT EXISTS ix_metrics_run ON metrics(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_metrics_exp ON metrics(experiment_id)",
    "CREATE INDEX IF NOT EXISTS ix_art_run ON artifacts(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_art_exp ON artifacts(experiment_id)",
    "CREATE INDEX IF NOT EXISTS ix_links_dst ON links(dst_id)",
    "CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts)",
]

# Ordered migrations: version -> list of statements. Add new versions at the end only.
DDL_V2 = [
    # Add Plan table
    """CREATE TABLE IF NOT EXISTS plans (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        root_question_id TEXT,
        author_type TEXT,
        author_name TEXT,
        author_model TEXT,
        created_at TEXT,
        updated_at TEXT,
        completed_at TEXT,
        objective TEXT,
        success_criteria TEXT,
        context TEXT,
        FOREIGN KEY (root_question_id) REFERENCES questions(id)
    )""",
    # Add Task table (simplified: removed redundant created_by, removed ready status)
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        title TEXT NOT NULL,
        task_type TEXT,
        status TEXT DEFAULT 'todo',
        assigned_role TEXT,
        depends_on TEXT,
        related_question_id TEXT,
        related_experiment_id TEXT,
        artifacts TEXT,
        author_type TEXT,
        author_name TEXT,
        author_model TEXT,
        created_at TEXT,
        updated_at TEXT,
        completed_by TEXT,
        started_at TEXT,
        completed_at TEXT,
        goal TEXT,
        inputs TEXT,
        expected_outputs TEXT,
        acceptance_criteria TEXT,
        verification TEXT,
        result TEXT,
        blockers TEXT,
        notes TEXT,
        FOREIGN KEY (plan_id) REFERENCES plans(id),
        FOREIGN KEY (related_question_id) REFERENCES questions(id),
        FOREIGN KEY (related_experiment_id) REFERENCES experiments(id)
    )""",
    # Indices
    "CREATE INDEX IF NOT EXISTS idx_tasks_plan ON tasks(plan_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status)",
    # Counters for new prefixes
    "INSERT OR IGNORE INTO counters (prefix, next) VALUES ('PLAN', 1)",
    "INSERT OR IGNORE INTO counters (prefix, next) VALUES ('T', 1)",
]

def _add_column(table: str, col: str, kind: str):
    """Migration step: add a column unless it exists (fresh DBs already get every column from ENTITIES)."""
    def step(conn: sqlite3.Connection) -> None:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    return step


DDL_V3 = [
    _add_column("plans", "skills", "TEXT"),
    _add_column("tasks", "skills", "TEXT"),
    _add_column("tasks", "checks", "TEXT"),
    _add_column("tasks", "claimed_by", "TEXT"),
    _add_column("tasks", "claimed_at", "TEXT"),
    # verification records: task check runs and finding reviews (canonical: .research/reviews.jsonl)
    """CREATE TABLE IF NOT EXISTS reviews (
      id TEXT PRIMARY KEY, target_id TEXT NOT NULL, target_type TEXT, kind TEXT, verdict TEXT,
      summary TEXT, details TEXT, author_type TEXT, author_name TEXT, author_model TEXT, created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_reviews_target ON reviews(target_id)",
]

MIGRATIONS: Dict[int, List[Any]] = {1: DDL_V1, 2: DDL_V2, 3: DDL_V3}


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15.0, isolation_level=None)  # autocommit; explicit txns
    conn.row_factory = sqlite3.Row
    # WAL is unsafe on NFS/Lustre (needs shared memory); classic rollback journal is portable.
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=OFF")  # referential integrity is enforced in services
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(r[0]) if r else 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn: sqlite3.Connection) -> int:
    v = current_version(conn)
    if v > SCHEMA_VERSION:
        raise ResearchError(
            f"This .research database uses schema v{v}, newer than this tool (v{SCHEMA_VERSION}).",
            hint="Upgrade the research package / VS Code extension.")
    for target in sorted(MIGRATIONS):
        if target <= v:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in MIGRATIONS[target]:
                stmt(conn) if callable(stmt) else conn.execute(stmt)
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(target),))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        v = target
    return v
