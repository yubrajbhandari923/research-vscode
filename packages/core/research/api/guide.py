"""Scientist Guide operations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import op, ro


@op(needs_project=False, mcp="guide_status")
def guide_status(s) -> Dict[str, Any]:
    """Important results, chapters' freshness, citations, last draft check, review state, Quarto availability. Read-only."""
    from .. import guide as G
    p = ro(s)
    try:
        return G.overview(p)
    finally:
        p.close()


@op(mutates=True)
def guide_init(p, workflow: Optional[bool] = None) -> Dict[str, Any]:
    from .. import guide as G
    return G.init(p, workflow=workflow)


@op(mutates=True)
def guide_build(p, edition: str = "scientific", pdf: bool = False, allow_invalid: bool = False) -> Dict[str, Any]:
    from .. import guide_quarto as GQ
    return GQ.build(p, edition, pdf, allow_invalid)


@op()
def guide_preview(p, edition: str = "scientific") -> Dict[str, Any]:
    """The command that serves the book with live reload (the extension runs it in a terminal). Starts nothing itself."""
    from .. import guide_quarto as GQ
    from .. import provenance as PV
    return {"argv": GQ.preview_argv(p, edition), "cwd": PV.main_root(p)}


@op(mutates=True)
def guide_highlight(p, id: str, on: bool = True) -> Dict[str, Any]:
    from .. import guide as G
    return G.star(p, id, on)


@op(mutates=True)
def guide_log(p, ids: Optional[List[str]] = None) -> Dict[str, Any]:
    from .. import guide as G
    return G.log(p, ids)


@op(mutates=True)
def guide_draft(p, results: Optional[List[str]] = None, section: Optional[str] = None, profile: Optional[str] = None,
                model: Optional[str] = None) -> Dict[str, Any]:
    """Prepare (never start) a bounded agent draft. Returns the dispatch (argv/env/brief) the extension then previews and starts."""
    from .. import guide as G
    d = G.draft(p, profile, model, None, results, [section] if section else None)
    d["shell"] = None   # never ship a copy-pasteable string built from record text
    return d


@op(mutates=True, mcp="guide_draft_done")
def guide_draft_done(p, ids: Optional[List[str]] = None) -> Dict[str, Any]:
    from .. import guide as G
    return G.draft_done(p, ids)


@op(needs_project=False)
def guide_cite_list(s) -> Dict[str, Any]:
    from .. import guide as G
    p = ro(s)
    try:
        return G.cite_list(p)
    finally:
        p.close()


@op(mutates=True)
def guide_cite_add(p, bibtex: Optional[str] = None, key: Optional[str] = None, title: Optional[str] = None,
                   authors: Optional[str] = None, year: Optional[str] = None, doi: Optional[str] = None,
                   journal: Optional[str] = None, url: Optional[str] = None) -> Dict[str, Any]:
    from .. import guide as G
    return G.cite_add(p, bibtex, key, title, authors, year, doi, journal, url)


@op(needs_project=False, mcp="guide_validate")
def guide_validate(s) -> Dict[str, Any]:
    """Deterministic claim/figure/section provenance validation. Read-only; not a scientific review."""
    from .. import provenance as PV
    p = ro(s)
    try:
        return PV.validate(p)
    finally:
        p.close()


@op(needs_project=False, mcp="guide_highlights")
def guide_highlights(s) -> Any:
    from .. import guide as G
    p = ro(s)
    try:
        return G.highlights(p)
    finally:
        p.close()


@op(mcp="guide_draft_check")
def guide_draft_check(p) -> Dict[str, Any]:
    from .. import guide as G
    return G.draft_check(p)
