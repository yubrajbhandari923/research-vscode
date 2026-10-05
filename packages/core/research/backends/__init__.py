"""Execution backends. The experiment/run model does not care where a run executes."""
from __future__ import annotations

from typing import Dict

from .base import Backend
from .local import LocalBackend
from .slurm import SlurmBackend

BACKENDS: Dict[str, type] = {"local": LocalBackend, "slurm": SlurmBackend}


def get_backend(name: str) -> Backend:
    from ..util import ResearchError
    cls = BACKENDS.get(name)
    if not cls:
        raise ResearchError(f"Unknown backend {name!r} (available: {', '.join(BACKENDS)})")
    return cls()
