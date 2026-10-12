"""Model usage ledger: .research/usage.jsonl, append-only. One line per measured agent session or attempt.

Only what a provider or the caller actually reported is stored. A field that is absent stays absent: summaries count
how many records reported it and never treat a missing value as zero. Cost is recorded only when the provider reports
it (Research OS keeps no price table), so it can be partial; `reported` says how partial.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .store import append_jsonl
from .util import ResearchError, now_iso

METRICS = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd"]
GROUPS = ["strategy", "model", "task", "level", "profile", "source"]


def _path(p) -> str:
    return p.path("usage.jsonl")


def parse_claude_json(text: str) -> Dict[str, Any]:
    """`claude -p --output-format json` result → usage fields. Defensive: any missing piece is simply omitted."""
    obj: Any = None
    for blob in (text, *reversed([ln for ln in (text or "").splitlines() if ln.strip().startswith("{")])):
        try:
            obj = json.loads(blob)
            break
        except ValueError:
            continue
    if isinstance(obj, list):
        obj = next((o for o in reversed(obj) if isinstance(o, dict) and o.get("type") == "result"), None)
    if not isinstance(obj, dict):
        return {}
    u = obj.get("usage") or {}
    out: Dict[str, Any] = {}
    for dst, src in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"),
                     ("cache_read_tokens", "cache_read_input_tokens"), ("cache_write_tokens", "cache_creation_input_tokens")):
        if isinstance(u.get(src), (int, float)):
            out[dst] = u[src]
    if isinstance(obj.get("total_cost_usd"), (int, float)):
        out["cost_usd"] = obj["total_cost_usd"]
    for k in ("duration_ms", "num_turns", "session_id"):
        if obj.get(k) is not None:
            out[k] = obj[k]
    out["result_text"] = obj.get("result")
    return out


PARSERS = {"claude_json": parse_claude_json}


def record(p, source: str, usage: Dict[str, Any], **ctx: Any) -> Optional[Dict[str, Any]]:
    """Append a usage record. Returns None (and writes nothing) when no measurement was supplied at all."""
    vals = {k: v for k, v in usage.items() if k in METRICS + ["duration_ms", "num_turns", "session_id"] and v is not None}
    if not any(k in vals for k in METRICS):
        return None
    rec = {"ts": now_iso(), "source": source, **{k: v for k, v in ctx.items() if v is not None}, **vals}
    append_jsonl(_path(p), rec)
    return rec


def records(p) -> List[Dict[str, Any]]:
    try:
        with open(_path(p), encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []


def summary(p, by: str = "strategy", task: Optional[str] = None) -> Dict[str, Any]:
    if by not in GROUPS:
        raise ResearchError(f"--by must be one of {', '.join(GROUPS)}")
    rows: Dict[str, Dict[str, Any]] = {}
    for r in records(p):
        if task and r.get("task") != task:
            continue
        g = rows.setdefault(str(r.get(by) or "unknown"), {"records": 0, "tasks": set(), **{m: {"total": None, "reported": 0} for m in METRICS}})
        g["records"] += 1
        if r.get("task"):
            g["tasks"].add(r["task"])
        for m in METRICS:
            if isinstance(r.get(m), (int, float)):
                g[m]["total"] = (g[m]["total"] or 0) + r[m]
                g[m]["reported"] += 1
    for g in rows.values():
        n = len(g["tasks"])
        g["tasks"] = n
        # per-task mean over the tasks that reported, so strategies with different task counts compare fairly
        g["mean_per_task"] = {m: (round(g[m]["total"] / n, 2) if g[m]["total"] is not None and n else None) for m in METRICS}
    return {"by": by, "groups": rows,
            "note": "totals sum only records that reported the field ('reported' counts them); missing is never zero; "
                    "cost is provider-reported, not calculated"}
