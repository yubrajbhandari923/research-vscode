"""YAML + front-matter I/O. Uses system PyYAML if present, else the vendored pure-Python copy."""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Tuple

try:  # pragma: no cover - depends on environment
    import yaml as _yaml  # type: ignore
except Exception:  # pragma: no cover
    from ._vendor import yaml as _yaml  # type: ignore

_Loader = getattr(_yaml, "CSafeLoader", None) or _yaml.SafeLoader
_Dumper = getattr(_yaml, "CSafeDumper", None) or _yaml.SafeDumper


class _Dumper2(_Dumper):  # type: ignore[misc, valid-type]
    pass


def _str_rep(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper2.add_representer(str, _str_rep)


def _stringify_dates(v: Any) -> Any:
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _stringify_dates(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_stringify_dates(x) for x in v]
    return v


def load(text: str) -> Any:
    return _stringify_dates(_yaml.load(text, Loader=_Loader))


def dump(data: Any) -> str:
    return _yaml.dump(data, Dumper=_Dumper2, sort_keys=False, allow_unicode=True,
                      default_flow_style=None, width=100)


def split_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """Return (front_matter_dict, body). Tolerates files without front-matter."""
    if text.startswith("﻿"):
        text = text[1:]
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            fm = load("\n".join(lines[1:i])) or {}
            if not isinstance(fm, dict):
                fm = {}
            return fm, "\n".join(lines[i + 1:]).lstrip("\n")
    return {}, text


def join_front_matter(fm: Dict[str, Any], body: str) -> str:
    return "---\n" + dump(fm) + "---\n\n" + body.rstrip() + "\n"
