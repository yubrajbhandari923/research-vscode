"""Morning digest: a template over the events since the last digest (no model call). None when nothing changed."""
from __future__ import annotations

from typing import Optional


def build(p, since_ts: float) -> Optional[str]:
    from datetime import datetime, timezone
    since = datetime.fromtimestamp(since_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    rows = p.q("SELECT entity_type, action, COUNT(*) n FROM events WHERE ts>? GROUP BY entity_type, action ORDER BY n DESC LIMIT 12", (since,))
    lines = [f"{r['n']} {r['entity_type']} {r['action']}" for r in rows]
    waiting = []
    try:
        from .. import supervisor as SV
        waiting = [x for x in SV.list_proposals(p) if x.get("status") == "pending"]
    except Exception:       # noqa: BLE001 - a digest line is never worth an exception
        pass
    if waiting:
        lines.append(f"{len(waiting)} proposal(s) waiting for your approval (needs your computer)")
    if not lines:
        return None
    return f"Since the last digest ({p.config.get('project', {}).get('name') or 'project'}):\n- " + "\n- ".join(lines)
