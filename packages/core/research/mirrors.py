"""Markdown/YAML mirrors of high-level entities (render + parse).

File layout:
    ---
    <front-matter: ids, status, lists, parameters …>   (editable)
    ---
    # EXP-001 · Title
    ## Hypothesis
    …                                                   (editable sections)
    <!-- research:generated … -->
    …runs / synthesis / evidence (read-only, regenerated) …
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import yamlio
from .schema import ENTITIES

GENERATED_MARKER = "<!-- research:generated — everything below is regenerated; edits here are ignored -->"
_SKIP_FM = {"created_at", "updated_at"}  # still written, but not user-meaningful to edit
# JSON columns that hold lists; rendered as [] when empty (all other JSON columns as {}).
_LIST_COLS = {"tags", "metrics_requested", "expected_artifacts", "finding_ids", "failure_ids", "question_ids",
              "experiment_ids", "depends_on", "artifacts", "skills", "checks"}


def fm_key(spec: dict, col: str) -> str:
    return spec["fm"].get(col, col)


def render(etype: str, row: Dict[str, Any], links: Dict[str, List[str]], tail: str = "") -> str:
    spec = ENTITIES[etype]
    section_cols = {c for c, _ in spec["sections"]}
    fm: Dict[str, Any] = {"id": row["id"]}
    for col, kind in spec["columns"]:
        if col in section_cols:
            continue
        v = row.get(col)
        if kind == "bool":
            v = bool(v) if v is not None else None
        if kind == "json" and col in _LIST_COLS and (v is None or v == {}):
            v = []
        elif kind == "json" and v is None:
            v = {}
        if col == "param_delta" and not v:
            continue
        fm[fm_key(spec, col)] = v
    for key, _rel in spec["links"]:
        fm[key] = list(links.get(key, []))
    title = row.get("title") or ""
    parts = [f"# {row['id']} · {title}".rstrip(), ""]
    for col, heading in spec["sections"]:
        text = (row.get(col) or "").strip()
        parts += [f"## {heading}", "", text, ""] if text else [f"## {heading}", "", ""]
    body = "\n".join(parts).rstrip() + "\n"
    if tail.strip():
        body += "\n" + GENERATED_MARKER + "\n\n" + tail.strip() + "\n"
    return yamlio.join_front_matter(fm, body)


_H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$")


def parse(etype: str, text: str) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """Return (values keyed by DB column, links keyed by front-matter link key)."""
    spec = ENTITIES[etype]
    fm, body = yamlio.split_front_matter(text)
    if GENERATED_MARKER in body:
        body = body.split(GENERATED_MARKER, 1)[0]
    else:  # tolerate a mangled marker
        body = re.split(r"<!--\s*research:generated", body, maxsplit=1)[0]

    values: Dict[str, Any] = {}
    if "id" in fm:
        values["id"] = fm["id"]
    section_cols = {c for c, _ in spec["sections"]}
    rev = {fm_key(spec, c): (c, k) for c, k in spec["columns"] if c not in section_cols}
    for key, v in fm.items():
        if key in rev:
            col, kind = rev[key]
            if kind == "bool":
                v = None if v is None else bool(v)
            elif kind == "int":
                try:
                    v = None if v in (None, "") else int(v)
                except (TypeError, ValueError):
                    v = None
            elif kind == "json":
                pass
            else:
                v = None if v is None else str(v)
            values[col] = v
    links: Dict[str, List[str]] = {}
    for key, _rel in spec["links"]:
        raw = fm.get(key) or []
        if isinstance(raw, str):
            raw = [x.strip() for x in raw.split(",")]
        links[key] = [str(x).strip() for x in raw if str(x).strip()]

    # sections
    heading_to_col = {h.lower(): c for c, h in spec["sections"]}
    current: Optional[str] = None
    buf: Dict[str, List[str]] = {}
    for line in body.split("\n"):
        m = _H2.match(line)
        if m:
            col = heading_to_col.get(m.group(1).strip().lower())
            if col:
                current = col
                buf.setdefault(col, [])
                continue
            if current is None:
                continue
        if line.startswith("# ") and current is None:
            continue  # H1 title line
        if current is not None:
            buf[current].append(line)
    for col in section_cols:
        text = "\n".join(buf.get(col, [])).strip()
        values[col] = text or None
    return values, links
