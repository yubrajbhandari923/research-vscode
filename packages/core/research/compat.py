"""Version compatibility between the VS Code extension and the core (see docs/INSTALL_UPDATE.md)."""
from __future__ import annotations


def _nums(v: str):
    return [int(x) for x in str(v).split("+")[0].split("-")[0].split(".")[:3] if x.isdigit()]


def core_satisfies(core_version: str, required: str) -> bool:
    """`required` like "3.0": same major as the core, and the core's minor >= required minor."""
    c, r = _nums(core_version) + [0, 0], _nums(required) + [0, 0]
    return c[0] == r[0] and c[1] >= r[1]
