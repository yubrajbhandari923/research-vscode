"""What an agent is doing, readable while it runs and after it finished (`research logs`).

A headless agent's stdout is the wrong place to look: Claude Code prints one JSON object when it exits, so a two-hour attempt showed nothing for two hours.
The provider keeps a live record of its own, though. This module reads, in order of preference:

* Claude Code's session transcript (`~/.claude/projects/<cwd>/<session>.jsonl`), appended as the agent thinks, calls tools and gets results;
* the execution's captured stdout when it is a JSON event stream (Codex `exec --json`, any provider that streams);
* the captured stdout and stderr as plain text.

and turns them into one line per step: what the agent said, which tool it called with what, what came back (shortened), errors in full. Nothing here
writes anything or talks to a provider; it only reads files that already exist.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import execution as E

KEYS = ("command", "file_path", "path", "pattern", "query", "url", "description", "prompt", "notebook_path")


def _ts(iso: Any) -> Optional[float]:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def _one(s: Any, n: int) -> str:
    s = " ".join(str(s if s is not None else "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def claude_dir(cwd: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd or "")))


def transcript(rec: Dict[str, Any], session_id: Optional[str] = None) -> Optional[str]:
    """The Claude Code transcript of this execution: the named session if it was written during the run, else the newest one written since it started."""
    cmd = os.path.basename((rec.get("provider_argv") or rec.get("argv") or [""])[0] or "")
    argv = " ".join(str(x) for x in (rec.get("argv") or []))
    if "claude" not in cmd and "claude" not in argv:
        return None
    d = claude_dir(rec.get("cwd") or "")
    start = (_ts(rec.get("launched_at")) or _ts(rec.get("prepared_at")) or 0.0) - 5
    end = _ts(rec.get("ended_at"))
    files = []
    for f in glob.glob(os.path.join(d, "*.jsonl")):
        try:
            m = os.path.getmtime(f)
        except OSError:
            continue
        if m >= start and (end is None or m <= end + 120):
            files.append((m, f))
    if not files:
        return None
    if session_id:
        named = os.path.join(d, session_id + ".jsonl")
        if any(f == named for _, f in files):
            return named
    return max(files)[1]


def _tool(name: str, inp: Any) -> str:
    if not isinstance(inp, dict):
        return name
    for k in KEYS:
        if inp.get(k):
            return f"{name}  {_one(inp[k], 200)}"
    v = next((v for v in inp.values() if isinstance(v, str) and v), "")
    return f"{name}  {_one(v, 200)}" if v else name


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(x.get("text") or x.get("content") or "") if isinstance(x, dict) else str(x) for x in content)
    return "" if content is None else str(content)


def _claude(d: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    ts = _ts(d.get("timestamp"))
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    content = msg.get("content")
    t = d.get("type")
    if t == "assistant":
        if d.get("isApiErrorMessage"):
            yield {"ts": ts, "kind": "error", "text": _text_of(content)}
            return
        for c in content if isinstance(content, list) else [{"type": "text", "text": content}]:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and str(c.get("text") or "").strip():
                yield {"ts": ts, "kind": "say", "text": str(c["text"]).strip()}
            elif c.get("type") == "tool_use":
                yield {"ts": ts, "kind": "tool", "text": _tool(str(c.get("name") or "tool"), c.get("input"))}
    elif t == "user":
        if isinstance(content, str):
            if content.strip():
                yield {"ts": ts, "kind": "prompt", "text": content.strip()}
            return
        for c in content if isinstance(content, list) else []:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                yield {"ts": ts, "kind": "error" if c.get("is_error") else "result", "text": _text_of(c.get("content")).strip()}
            elif isinstance(c, dict) and c.get("type") == "text" and str(c.get("text") or "").strip():
                yield {"ts": ts, "kind": "prompt", "text": str(c["text"]).strip()}
    elif t == "result":
        cost = d.get("total_cost_usd")
        tail = f"  ({d.get('num_turns')} turns" + (f", ${cost:.2f}" if isinstance(cost, (int, float)) else "") + ")" if d.get("num_turns") else ""
        yield {"ts": ts, "kind": "error" if d.get("is_error") else "done", "text": (str(d.get("result") or d.get("error") or "finished")).strip() + tail}
    elif t == "system" and d.get("subtype") not in (None, "init"):
        yield {"ts": ts, "kind": "meta", "text": _one(d.get("content") or d.get("subtype"), 300)}


def _stream(d: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """A JSON event that is not a Claude transcript line (Codex `exec --json` and similar): show what it carries without knowing every schema."""
    if d.get("type") in ("assistant", "user", "result", "system") and ("message" in d or d.get("type") in ("result", "system")):
        yield from _claude(d)
        return
    item = d.get("item") if isinstance(d.get("item"), dict) else d.get("msg") if isinstance(d.get("msg"), dict) else d
    kind = str(item.get("type") or d.get("type") or "")
    if item.get("command"):
        out = item.get("aggregated_output") or item.get("output")
        cmd = item["command"] if isinstance(item["command"], str) else " ".join(str(x) for x in item["command"])
        if not out or "started" in str(d.get("type")):
            yield {"ts": None, "kind": "tool", "text": "shell  " + _one(cmd, 200)}
        if out:
            yield {"ts": None, "kind": "error" if item.get("exit_code") not in (None, 0) else "result", "text": str(out).strip()}
    elif item.get("text") or item.get("message"):
        yield {"ts": None, "kind": "error" if "error" in kind else "say", "text": str(item.get("text") or item.get("message")).strip()}
    elif "error" in kind or d.get("error"):
        yield {"ts": None, "kind": "error", "text": _one(d.get("error") or json.dumps(d), 600)}


def _read_lines(path: str, offset: int = 0) -> Tuple[List[str], int]:
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    cut = data.rfind(b"\n") + 1          # only whole lines: a half-written one is read next time
    return data[:cut].decode("utf-8", errors="replace").splitlines(), offset + cut


def _parse(lines: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s[0] == "{":
            try:
                d = json.loads(s)
                if isinstance(d, dict):
                    out += list(_stream(d))
                    continue
            except ValueError:
                pass
        out.append({"ts": None, "kind": "raw", "text": line.rstrip()})
    return out


class Reader:
    """Incremental: `more()` returns the events that appeared since the last call (for `--follow`)."""

    def __init__(self, p, execution_id: str, session_id: Optional[str] = None):
        self.p, self.id, self.sid = p, execution_id, session_id
        self.offsets: Dict[str, int] = {}
        self.source: Optional[str] = None

    def record(self) -> Dict[str, Any]:
        return E.show(self.p, self.id)

    def more(self) -> List[Dict[str, Any]]:
        rec = self.record()
        base = E.path(self.p, self.id)[:-5]
        t = transcript(rec, self.sid)
        ev: List[Dict[str, Any]] = []
        if t:
            if self.source not in (None, t):       # the provider started a new session file (a resume): follow it
                self.offsets.pop(self.source, None)
            self.source = t
            lines, self.offsets[t] = _read_lines(t, self.offsets.get(t, 0))
            ev += _parse(lines)
        out_path = base + ".out"
        lines, self.offsets[out_path] = _read_lines(out_path, self.offsets.get(out_path, 0))
        parsed = _parse(lines)
        ev += [e for e in parsed if not t or e["kind"] in ("done", "error", "raw")]     # with a transcript, stdout only adds the final result
        if not t:
            self.source = out_path
        err_path = base + ".err"
        lines, self.offsets[err_path] = _read_lines(err_path, self.offsets.get(err_path, 0))
        ev += [{"ts": None, "kind": "stderr", "text": l} for l in lines if l.strip()]
        if rec.get("error") and not self.offsets.get("_err"):
            self.offsets["_err"] = 1
            ev.append({"ts": None, "kind": "error", "text": str(rec["error"])})
        return ev


def events(p, execution_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    r = Reader(p, execution_id, session_id)
    ev = r.more()
    rec = r.record()
    return {"execution": execution_id, "state": rec["state"], "alive": rec.get("alive"), "exit_code": rec.get("exit_code"), "source": r.source,
            "cwd": rec.get("cwd"), "started": rec.get("launched_at"), "ended": rec.get("ended_at"), "events": ev}


def last_activity(p, execution_id: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The agent's latest step and how long ago, for the board. Reads only the tail of the transcript."""
    try:
        rec = E.show(p, execution_id)
    except (OSError, ValueError):
        return None
    path = transcript(rec, session_id) or (E.path(p, execution_id)[:-5] + ".out")
    try:
        size, mtime = os.path.getsize(path), os.path.getmtime(path)
    except OSError:
        return None
    lines, _ = _read_lines(path, max(0, size - 60_000))
    ev = [e for e in _parse(lines[1:] if size > 60_000 else lines) if e["kind"] in ("say", "tool", "error", "done")]
    if not ev:
        return None
    e = ev[-1]
    return {"kind": e["kind"], "text": _one(e["text"], 110), "seconds_ago": round(max(0.0, time.time() - (e["ts"] or mtime)), 1)}


MARKS = {"say": "", "tool": "▸ ", "result": "  ", "error": "✗ ", "done": "■ ", "prompt": "» ", "meta": "· ", "raw": "", "stderr": "! "}


def render(ev: List[Dict[str, Any]], full: bool = False, show_prompt: bool = False) -> List[str]:
    """One step per line. Tool results are shortened to their first lines unless `full`; errors and what the agent says are never cut."""
    out: List[str] = []
    for e in ev:
        k, text = e["kind"], e["text"]
        if k == "prompt" and not show_prompt:
            out.append(f"         » (task prompt, {len(text)} characters; --prompt shows it)") if len(text) > 400 else out.append("         » " + _one(text, 400))
            continue
        clock = time.strftime("%H:%M:%S", time.localtime(e["ts"])) if e.get("ts") else "        "
        body = text.splitlines() or [""]
        if k == "result" and not full:
            more = len(body) - 3
            body = [_one(l, 200) for l in body[:3]] + ([f"… {more} more line(s)"] if more > 0 else [])
        out.append(f"{clock} {MARKS.get(k, '')}{body[0]}")
        out += [f"         {'  ' if k in ('result', 'tool') else ''}{l}" for l in body[1:]]
    return out
