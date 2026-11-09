"""Research website operations (see site.py): the same functions the `research site` commands call. Nothing here pushes or commits."""
from __future__ import annotations

from typing import Any, Dict

from . import op, ro


@op(needs_project=False)
def site_status(s) -> Dict[str, Any]:
    """Quarto, workflow, remote, Pages URL, published experiments and the exact next commands. Read-only."""
    from .. import site
    p = ro(s)
    try:
        return site.status(p)
    finally:
        p.close()


@op(mutates=True)
def site_init(p, apply: bool = False) -> Dict[str, Any]:
    from .. import site
    return site.init(p, apply=apply)


@op(mutates=True)
def site_add(p, experiment: str, at: str = "verified", dry_run: bool = False) -> Dict[str, Any]:
    from .. import site
    return site.add(p, experiment, at=at, dry_run=dry_run)


@op(mutates=True)
def site_remove(p, experiment: str, apply: bool = False) -> Dict[str, Any]:
    """Unpublish: previews unless `apply`. Moves the folder to _unpublished/ and deletes nothing."""
    from .. import site
    return site.remove(p, experiment, apply=apply)
