"""Project store: paths, config, SQLite index, mirrors sync, events, rebuild.

Canonical data lives in text files under .research/ (Markdown mirrors, run dirs, *.jsonl).
research.db is an index that can always be rebuilt from them.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import gitinfo, mirrors, schema, yamlio
from .schema import ENTITIES, format_id, parse_id
from .util import NotFound, NotInitialized, ResearchError, detect_author, jdump, jload, now_iso, sha256_bytes

RESEARCH_DIR = ".research"
SUBDIRS = ["questions", "experiments", "findings", "decisions", "checkpoints", "notes", "runs",
           "skills", "context", "prompts", "templates", "cache"]
TEMPLATES = Path(__file__).parent / "templates"

DEFAULT_CONFIG: Dict[str, Any] = {
    "project": {
        "name": "", "description": "", "goal": "", "status": "active",
        "created_at": None, "git_repo": None, "default_backend": "local",
    },
    "agent_policy": {
        "max_runs_without_synthesis": 5,
        "max_failed_runs_without_review": 3,
        "require_experiment_registration": True,
        "require_synthesis_before_new_experiment": True,
        "enforce_for_humans": False,
    },
    "backends": {
        "slurm": {
            "partition": None, "account": None, "time": "01:00:00", "cpus": None, "mem": None,
            "gpus": None, "extra_args": [],
            "setup": [],
        },
    },
    "artifacts": {"hash_max_bytes": 64 * 1024 * 1024},
    "resume": {"stale_checkpoint_days": 14},
}

CONFIG_HEADER = """# Research project configuration (human-editable).
# project.goal is shown at the top of the Resume view and in .research/context/current.md.
# agent_policy limits are enforced for agents (exit code 3); humans get warnings unless enforce_for_humans.
# backends.slurm.setup: shell lines run before every SLURM job, e.g. "module load python/3.11".
"""

GITIGNORE = """# research: the SQLite index is rebuilt automatically from the text files
research.db
research.db-journal
research.db-wal
research.db-shm
cache/
# run logs can be large; remove these lines if you want them versioned
runs/*/stdout.log
runs/*/stderr.log
"""


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def find_root(start: Optional[str] = None) -> Optional[str]:
    env = os.environ.get("RESEARCH_ROOT")
    if env and os.path.isfile(os.path.join(env, RESEARCH_DIR, "config.yaml")):
        return os.path.abspath(env)
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(cur, RESEARCH_DIR, "config.yaml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def append_jsonl(path: str, record: Dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def read_jsonl_from(path: str, offset: int):
    """Yield (record, end_offset) for complete lines after `offset`. Partial trailing lines are skipped."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return
    pos = offset
    for raw in data.split(b"\n")[:-1] if not data.endswith(b"\n") else data.split(b"\n")[:-1]:
        pos += len(raw) + 1
        s = raw.strip()
        if not s:
            continue
        try:
            yield json.loads(s.decode("utf-8")), pos
        except ValueError:
            yield None, pos


class Project:
    """Handle on one research project. Cheap to open; call close() or use as context manager."""

    def __init__(self, root: str, *, sync: bool = True, author: Optional[Dict[str, Optional[str]]] = None):
        self.root = os.path.abspath(root)
        self.rdir = os.path.join(self.root, RESEARCH_DIR)
        if not os.path.isfile(os.path.join(self.rdir, "config.yaml")):
            raise NotInitialized(f"No research project at {self.root}",
                                 hint="Run `research init` (or 'Research: Initialize Project' in VS Code).")
        self.author = author or detect_author()
        self._tx_depth = 0
        self._suppress_export = False
        self.config = self.load_config()
        for d in SUBDIRS:
            os.makedirs(os.path.join(self.rdir, d), exist_ok=True)
        self.db_path = os.path.join(self.rdir, "research.db")
        fresh = not os.path.exists(self.db_path)
        self.conn = schema.connect(self.db_path)
        schema.migrate(self.conn)
        if fresh or self.meta("built_at") is None:
            self.rebuild_index(backup=False)
        elif sync:
            self.sync()

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def find(cls, start: Optional[str] = None, **kw) -> "Project":
        root = find_root(start)
        if not root:
            raise NotInitialized("Not inside a research project (no .research/config.yaml found).",
                                 hint="Run `research init` in your project root.")
        return cls(root, **kw)

    @classmethod
    def init(cls, root: str, name: Optional[str] = None, goal: Optional[str] = None,
             description: Optional[str] = None, agents_md: bool = True) -> "Project":
        root = os.path.abspath(root)
        rdir = os.path.join(root, RESEARCH_DIR)
        os.makedirs(rdir, exist_ok=True)
        for d in SUBDIRS:
            os.makedirs(os.path.join(rdir, d), exist_ok=True)
        cfg_path = os.path.join(rdir, "config.yaml")
        created = False
        if not os.path.exists(cfg_path):
            g = gitinfo.info(root)
            cfg = _deep_merge(DEFAULT_CONFIG, {})
            cfg["project"].update({
                "name": name or os.path.basename(root), "goal": goal or "", "description": description or "",
                "created_at": now_iso(), "git_repo": g.get("remote"),
            })
            _atomic_write(cfg_path, CONFIG_HEADER + yamlio.dump(cfg))
            created = True
        gi = os.path.join(rdir, ".gitignore")
        if not os.path.exists(gi):
            _atomic_write(gi, GITIGNORE)
        # skills / prompts / templates: copy defaults without overwriting user files
        for sub in ("skills", "prompts", "templates"):
            src = TEMPLATES / sub
            for path in src.rglob("*"):
                if path.is_file():
                    dst = Path(rdir) / sub / path.relative_to(src)
                    if not dst.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, dst)
        p = cls(root)
        if created:
            p.event("project", None, "initialized", f"Initialized research project '{p.name}'")
        elif name or goal or description:
            p.update_project(name=name, goal=goal, description=description)
        if agents_md:
            p.write_agents_md()
        from . import context as _ctx
        _ctx.write_current_md(p)
        return p

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "Project":
        return self

    def __exit__(self, *a) -> None:
        self.close()

    # ------------------------------------------------------------------ config
    def load_config(self) -> Dict[str, Any]:
        path = os.path.join(self.rdir, "config.yaml")
        with open(path, encoding="utf-8") as f:
            raw = yamlio.load(f.read()) or {}
        if not isinstance(raw, dict):
            raise ResearchError(".research/config.yaml is not a mapping")
        return _deep_merge(DEFAULT_CONFIG, raw)

    def save_config(self) -> None:
        _atomic_write(os.path.join(self.rdir, "config.yaml"), CONFIG_HEADER + yamlio.dump(self.config))

    @property
    def name(self) -> str:
        return self.config["project"].get("name") or os.path.basename(self.root)

    @property
    def policy(self) -> Dict[str, Any]:
        return self.config["agent_policy"]

    def update_project(self, **fields: Any) -> Dict[str, Any]:
        changed = {k: v for k, v in fields.items() if v is not None and self.config["project"].get(k) != v}
        if changed:
            self.config["project"].update(changed)
            self.save_config()
            self.event("project", None, "updated", "Project " + ", ".join(sorted(changed)) + " updated")
        return self.config["project"]

    def write_agents_md(self) -> str:
        """Create/update AGENTS.md, touching only the text between research markers."""
        block = (TEMPLATES / "AGENTS.block.md").read_text(encoding="utf-8").strip()
        path = os.path.join(self.root, "AGENTS.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            b, e = "<!-- research:begin", "<!-- research:end -->"
            if b in text and e in text:
                start = text.index(b)
                end = text.index(e) + len(e)
                new = text[:start] + block + text[end:]
            else:
                new = text.rstrip() + "\n\n" + block + "\n"
        else:
            new = f"# Agent instructions — {self.name}\n\n" + block + "\n"
        with open(path, "a+", encoding="utf-8") as f:
            f.seek(0)
            old = f.read()
        if old != new:
            _atomic_write(path, new)
        return path

    # ------------------------------------------------------------------ paths
    def path(self, *parts: str) -> str:
        return os.path.join(self.rdir, *parts)

    def rel(self, path: str) -> str:
        """Project-relative path if inside the project, else absolute (portable storage form)."""
        ap = os.path.abspath(os.path.join(self.root, os.path.expanduser(path)))
        try:
            r = os.path.relpath(ap, self.root)
        except ValueError:
            return ap
        if r == os.pardir or r.startswith(os.pardir + os.sep):
            return ap
        return r.replace(os.sep, "/")

    def abspath(self, stored: Optional[str]) -> Optional[str]:
        if not stored:
            return None
        return stored if os.path.isabs(stored) else os.path.normpath(os.path.join(self.root, stored))

    # ------------------------------------------------------------------ db helpers
    @contextlib.contextmanager
    def tx(self) -> Iterator[None]:
        if self._tx_depth:
            self._tx_depth += 1
            try:
                yield
            finally:
                self._tx_depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self._tx_depth = 0

    def q(self, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def q1(self, sql: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
        r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def meta(self, key: str, default: Any = None) -> Any:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))

    def next_id(self, prefix: str) -> str:
        with self.tx():
            r = self.conn.execute("SELECT next FROM counters WHERE prefix=?", (prefix,)).fetchone()
            n = r[0] if r else 1
            n = max(n, self._max_existing(prefix) + 1)
            while self._id_taken_on_disk(prefix, n):
                n += 1
            self.conn.execute("INSERT OR REPLACE INTO counters(prefix,next) VALUES(?,?)", (prefix, n + 1))
        return format_id(prefix, n)

    def _max_existing(self, prefix: str) -> int:
        table = {"Q": "questions", "EXP": "experiments", "RUN": "runs", "F": "findings",
                 "D": "decisions", "CP": "checkpoints", "A": "artifacts"}[prefix]
        best = 0
        for (i,) in self.conn.execute(f"SELECT id FROM {table}"):
            p = parse_id(i)
            if p and p[1] > best:
                best = p[1]
        return best

    def _id_taken_on_disk(self, prefix: str, n: int) -> bool:
        i = format_id(prefix, n)
        if prefix == "RUN":
            return os.path.exists(self.path("runs", i))
        if prefix == "A":
            return False
        etype = schema.PREFIX_TO_TYPE[prefix]
        return os.path.exists(self.path(ENTITIES[etype]["dir"], f"{i}.md"))

    def _bump_counter(self, id_: str) -> None:
        p = parse_id(id_)
        if not p:
            return
        r = self.conn.execute("SELECT next FROM counters WHERE prefix=?", (p[0],)).fetchone()
        if not r or r[0] <= p[1]:
            self.conn.execute("INSERT OR REPLACE INTO counters(prefix,next) VALUES(?,?)", (p[0], p[1] + 1))

    # ------------------------------------------------------------------ events
    def event(self, etype: str, eid: Optional[str], action: str, summary: str,
              author: Optional[Dict[str, Optional[str]]] = None) -> None:
        a = author or self.author
        rec = {"ts": now_iso(), "entity_type": etype, "entity_id": eid, "action": action,
               "summary": summary, "author_type": a.get("author_type"), "author_name": a.get("author_name")}
        self.conn.execute(
            "INSERT INTO events(ts,entity_type,entity_id,action,summary,author_type,author_name) VALUES(?,?,?,?,?,?,?)",
            (rec["ts"], etype, eid, action, summary, rec["author_type"], rec["author_name"]))
        try:
            append_jsonl(self.path("events.jsonl"), rec)
        except OSError:
            pass

    # ------------------------------------------------------------------ generic entity ops
    def _decode(self, etype: str, row: Dict[str, Any]) -> Dict[str, Any]:
        spec = ENTITIES[etype]
        out = {"id": row["id"], "type": etype}
        for col, kind in spec["columns"]:
            v = row.get(col)
            if kind == "json":
                v = jload(v, [] if col in ("tags",) else None)
            elif kind == "bool":
                v = None if v is None else bool(v)
            out[col] = v
        return out

    def _encode(self, etype: str, values: Dict[str, Any]) -> Dict[str, Any]:
        spec = ENTITIES[etype]
        kinds = dict(spec["columns"])
        out = {}
        for k, v in values.items():
            if k not in kinds:
                continue
            kind = kinds[k]
            if kind == "json":
                v = jdump(v)
            elif kind == "bool":
                v = None if v is None else int(bool(v))
            out[k] = v
        return out

    def get(self, etype: str, id_: str) -> Dict[str, Any]:
        spec = ENTITIES[etype]
        row = self.q1(f"SELECT * FROM {spec['table']} WHERE id=?", (id_,))
        if not row:
            raise NotFound(f"{etype.capitalize()} {id_} not found")
        d = self._decode(etype, row)
        if spec["links"]:
            d["links"] = self.links_from(etype, id_)
        return d

    def exists(self, etype: str, id_: str) -> bool:
        return self.q1(f"SELECT 1 AS x FROM {ENTITIES[etype]['table']} WHERE id=?", (id_,)) is not None

    def list(self, etype: str, where: str = "", args: tuple = (), order: str = "id") -> List[Dict[str, Any]]:
        spec = ENTITIES[etype]
        sql = f"SELECT * FROM {spec['table']}" + (f" WHERE {where}" if where else "") + f" ORDER BY {order}"
        rows = [self._decode(etype, r) for r in self.q(sql, args)]
        if spec["links"]:
            for r in rows:
                r["links"] = self.links_from(etype, r["id"])
        return rows

    def links_from(self, etype: str, id_: str) -> Dict[str, List[str]]:
        spec = ENTITIES[etype]
        rel_to_key = {rel: key for key, rel in spec["links"]}
        out: Dict[str, List[str]] = {key: [] for key, _ in spec["links"]}
        for r in self.q("SELECT dst_id, relation FROM links WHERE src_id=? ORDER BY rowid", (id_,)):
            key = rel_to_key.get(r["relation"])
            if key:
                out[key].append(r["dst_id"])
        return out

    def links_to(self, id_: str) -> List[Dict[str, Any]]:
        return self.q("SELECT * FROM links WHERE dst_id=? ORDER BY rowid", (id_,))

    def set_links(self, etype: str, id_: str, links: Dict[str, List[str]]) -> None:
        spec = ENTITIES.get(etype)
        key_to_rel = {key: rel for key, rel in spec["links"]} if spec else {}
        for key, ids in links.items():
            rel = key_to_rel.get(key, key)
            self.conn.execute("DELETE FROM links WHERE src_id=? AND relation=?", (id_, rel))
            seen = set()
            for dst in ids:
                dst = str(dst).strip()
                if not dst or dst in seen:
                    continue
                seen.add(dst)
                try:
                    dtype = schema.type_of(dst)
                except ResearchError:
                    dtype = "unknown"
                self.conn.execute(
                    "INSERT OR REPLACE INTO links(src_type,src_id,dst_type,dst_id,relation) VALUES(?,?,?,?,?)",
                    (etype, id_, dtype, dst, rel))

    def insert_entity(self, etype: str, values: Dict[str, Any], links: Optional[Dict[str, List[str]]] = None,
                      id_: Optional[str] = None, summary: Optional[str] = None,
                      author: Optional[Dict[str, Optional[str]]] = None, log: bool = True) -> Dict[str, Any]:
        spec = ENTITIES[etype]
        author = author or self.author
        with self.tx():
            id_ = id_ or self.next_id(spec["prefix"])
            ts = now_iso()
            vals = dict(values)
            vals.setdefault("created_at", ts)
            vals.setdefault("updated_at", vals["created_at"])
            for k in ("author_type", "author_name", "author_model"):
                vals.setdefault(k, author.get(k))
            enc = self._encode(etype, vals)
            cols = ["id"] + list(enc)
            self.conn.execute(
                f"INSERT INTO {spec['table']} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                (id_, *enc.values()))
            self._bump_counter(id_)
            if links:
                self.set_links(etype, id_, links)
            if log:
                self.event(etype, id_, "created", summary or f"{id_} {vals.get('title') or ''}".strip(), author)
        self.export(etype, id_)
        return self.get(etype, id_)

    def update_entity(self, etype: str, id_: str, changes: Dict[str, Any],
                      links: Optional[Dict[str, List[str]]] = None, action: str = "updated",
                      summary: Optional[str] = None, log: bool = True, touch: bool = True) -> Dict[str, Any]:
        spec = ENTITIES[etype]
        cur = self.get(etype, id_)
        diff = {k: v for k, v in changes.items() if k in dict(spec["columns"]) and cur.get(k) != v}
        link_changed = False
        if links is not None:
            old = cur.get("links", {})
            link_changed = any(list(old.get(k, [])) != list(v) for k, v in links.items())
        if not diff and not link_changed:
            return cur
        with self.tx():
            if touch:
                diff["updated_at"] = now_iso()
            enc = self._encode(etype, diff)
            if enc:
                sets = ",".join(f"{k}=?" for k in enc)
                self.conn.execute(f"UPDATE {spec['table']} SET {sets} WHERE id=?", (*enc.values(), id_))
            if links is not None and link_changed:
                self.set_links(etype, id_, links)
            if log:
                fields = [k for k in diff if k != "updated_at"] + (["links"] if link_changed else [])
                self.event(etype, id_, action, summary or f"{id_}: {', '.join(fields)} changed")
        self.export(etype, id_)
        return self.get(etype, id_)

    # ------------------------------------------------------------------ mirrors
    def mirror_path(self, etype: str, id_: str) -> str:
        r = self.q1("SELECT path FROM mirrors WHERE entity_id=? AND entity_type=?", (id_, etype))
        if r and os.path.exists(self.abspath(r["path"])):
            return self.abspath(r["path"])
        return self.path(ENTITIES[etype]["dir"], f"{id_}.md")

    def _record_mirror(self, etype: str, id_: str, path: str, data: bytes) -> None:
        st = os.stat(path)
        self.conn.execute(
            "INSERT OR REPLACE INTO mirrors(path,entity_type,entity_id,hash,mtime,size) VALUES(?,?,?,?,?,?)",
            (self.rel(path), etype, id_, sha256_bytes(data), st.st_mtime, st.st_size))

    def export(self, etype: str, id_: str) -> Optional[str]:
        if self._suppress_export:
            return None
        from . import views  # local import: views depends on store
        path = self.mirror_path(etype, id_)
        # Import-before-export: never overwrite a hand edit we have not seen yet.
        rec = self.q1("SELECT hash FROM mirrors WHERE path=?", (self.rel(path),))
        if rec and os.path.exists(path):
            with open(path, "rb") as f:
                cur = f.read()
            if sha256_bytes(cur) != rec["hash"]:
                self.import_file(path, etype)
        row = self.get(etype, id_)
        text = mirrors.render(etype, row, row.get("links", {}), views.mirror_tail(self, etype, id_))
        data = text.encode("utf-8")
        old = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                old = f.read()
        if old != data:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _atomic_write(path, text)
        self._record_mirror(etype, id_, path, data)
        return path

    def import_file(self, path: str, etype: Optional[str] = None) -> Optional[str]:
        """Import a (possibly hand-edited or new) mirror file. Returns entity id."""
        if etype is None:
            d = os.path.basename(os.path.dirname(path))
            etype = next((t for t, s in ENTITIES.items() if s["dir"] == d), None)
            if not etype:
                return None
        spec = ENTITIES[etype]
        with open(path, "rb") as f:
            data = f.read()
        try:
            values, links = mirrors.parse(etype, data.decode("utf-8"))
        except Exception as e:  # malformed YAML: keep DB as-is, warn, don't crash
            self.event(etype, None, "import_error", f"Could not parse {self.rel(path)}: {e}", {"author_type": "system"})
            self._record_mirror(etype, "?", path, data)
            return None
        raw_id = values.pop("id", None) or os.path.splitext(os.path.basename(path))[0]
        try:
            id_ = schema.normalize_id(str(raw_id), spec["prefix"])
        except ResearchError:
            id_ = None
        if etype in schema.STATUSES and values.get("status") not in schema.STATUSES[etype]:
            values.pop("status", None)
        hand = {"author_type": "human", "author_name": os.environ.get("RESEARCH_AUTHOR"), "author_model": None}
        prev_suppress = self._suppress_export
        self._suppress_export = True  # avoid recursion; we record the file hash ourselves below
        try:
            if id_ and self.exists(etype, id_):
                cur = self.get(etype, id_)
                changes = {k: v for k, v in values.items() if cur.get(k) != v and k not in ("created_at",)}
                old_links = cur.get("links", {})
                lchanged = {k: v for k, v in links.items() if list(old_links.get(k, [])) != v}
                if changes or lchanged:
                    self.update_entity(etype, id_, changes, links=links if lchanged else None,
                                       action="edited", summary=f"{id_} edited by hand in {self.rel(path)}: "
                                       + ", ".join(list(changes) + list(lchanged)))
            else:
                if not id_:
                    id_ = self.next_id(spec["prefix"])
                values.setdefault("title", os.path.splitext(os.path.basename(path))[0])
                if etype in schema.STATUSES:
                    values.setdefault("status", schema.STATUSES[etype][0])
                if etype == "finding":
                    values.setdefault("kind", "result")
                auth = {k: values.get(k) or hand.get(k) for k in ("author_type", "author_name", "author_model")}
                self.insert_entity(etype, values, links, id_=id_, author=auth,
                                   summary=f"{id_} imported from {self.rel(path)}")
        finally:
            self._suppress_export = prev_suppress
        self._record_mirror(etype, id_, path, data)
        return id_

    # ------------------------------------------------------------------ sync
    def sync(self, reconcile_runs: bool = True) -> Dict[str, int]:
        """Pick up hand edits / new files, ingest job-written files, reconcile run status."""
        stats = {"imported": 0, "restored": 0}
        for etype, spec in ENTITIES.items():
            d = self.path(spec["dir"])
            seen = set()
            for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                if not fn.endswith(".md") or fn.startswith("."):
                    continue
                path = os.path.join(d, fn)
                rel = self.rel(path)
                seen.add(rel)
                st = os.stat(path)
                rec = self.q1("SELECT * FROM mirrors WHERE path=?", (rel,))
                if rec and rec["mtime"] == st.st_mtime and rec["size"] == st.st_size:
                    continue
                with open(path, "rb") as f:
                    h = sha256_bytes(f.read())
                if rec and rec["hash"] == h:
                    self.conn.execute("UPDATE mirrors SET mtime=?, size=? WHERE path=?", (st.st_mtime, st.st_size, rel))
                    continue
                if self.import_file(path, etype):
                    stats["imported"] += 1
                    # refresh generated tail/normalized form
                    eid = self.q1("SELECT entity_id FROM mirrors WHERE path=?", (rel,))
                    if eid and eid["entity_id"] != "?":
                        self.export(etype, eid["entity_id"])
            # mirror deleted? recreate it (never silently lose records)
            for row in self.q(f"SELECT id FROM {spec['table']}"):
                mp = self.mirror_path(etype, row["id"])
                if not os.path.exists(mp):
                    self.conn.execute("DELETE FROM mirrors WHERE entity_id=? AND entity_type=?", (row["id"], etype))
                    self.export(etype, row["id"])
                    self.event(etype, row["id"], "restored", f"{row['id']} mirror file was missing; regenerated",
                               {"author_type": "system", "author_name": None})
                    stats["restored"] += 1
        from . import runs as _runs
        _runs.ingest_root_files(self)
        if reconcile_runs:
            _runs.sync_runs(self)
        return stats

    # ------------------------------------------------------------------ rebuild
    def rebuild_index(self, backup: bool = True) -> Dict[str, int]:
        """Recreate research.db from the text files."""
        if backup and os.path.exists(self.db_path):
            self.conn.close()
            bak = self.path("cache", f"research.db.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.move(self.db_path, bak)
            self.conn = schema.connect(self.db_path)
            schema.migrate(self.conn)
        counts: Dict[str, int] = {}
        self._suppress_export = True
        sys_author = {"author_type": "system", "author_name": None, "author_model": None}
        saved_author = self.author
        self.author = sys_author
        try:
            with self.tx():
                for etype, spec in ENTITIES.items():
                    d = self.path(spec["dir"])
                    n = 0
                    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                        if fn.endswith(".md") and not fn.startswith("."):
                            if self._import_quiet(os.path.join(d, fn), etype):
                                n += 1
                    counts[etype] = n
                from . import runs as _runs
                counts["runs"] = _runs.import_run_dirs(self)
                _runs.ingest_root_files(self)
                # events history
                ev = self.path("events.jsonl")
                for rec, _ in read_jsonl_from(ev, 0) if os.path.exists(ev) else []:
                    if rec:
                        self.conn.execute(
                            "INSERT INTO events(ts,entity_type,entity_id,action,summary,author_type,author_name) VALUES(?,?,?,?,?,?,?)",
                            (rec.get("ts"), rec.get("entity_type"), rec.get("entity_id"), rec.get("action"),
                             rec.get("summary"), rec.get("author_type"), rec.get("author_name")))
                self.set_meta("built_at", now_iso())
        finally:
            self._suppress_export = False
            self.author = saved_author
        # regenerate mirrors (tails) now that everything is loaded
        for etype, spec in ENTITIES.items():
            for row in self.q(f"SELECT id FROM {spec['table']}"):
                self.export(etype, row["id"])
        return counts

    def _import_quiet(self, path: str, etype: str) -> Optional[str]:
        spec = ENTITIES[etype]
        with open(path, "rb") as f:
            data = f.read()
        try:
            values, links = mirrors.parse(etype, data.decode("utf-8"))
        except Exception:
            return None
        raw_id = values.pop("id", None) or os.path.splitext(os.path.basename(path))[0]
        try:
            id_ = schema.normalize_id(str(raw_id), spec["prefix"])
        except ResearchError:
            id_ = self.next_id(spec["prefix"])
        if self.exists(etype, id_):
            self.event(etype, id_, "conflict", f"Duplicate id {id_} in {self.rel(path)} — rename one of the files",
                       {"author_type": "system", "author_name": None})
            return None
        if etype in schema.STATUSES and values.get("status") not in schema.STATUSES[etype]:
            values["status"] = schema.STATUSES[etype][0]
        if etype == "finding" and not values.get("kind"):
            values["kind"] = "result"
        values.setdefault("title", id_)
        auth = {k: values.get(k) for k in ("author_type", "author_name", "author_model")}
        auth["author_type"] = auth["author_type"] or "human"
        self.insert_entity(etype, values, links, id_=id_, author=auth, log=False)
        self._record_mirror(etype, id_, path, data)
        return id_
