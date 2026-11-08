"""Decision requests (decisions.py): agents ask, humans answer from any surface (chat, TUI, VS Code, CLI)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import decisions as D
from . import op


@op(mutates=True, mcp="decision_request")
def decision_request(p, question: str, options: List[Dict[str, Any]], subject: Optional[str] = None, default: Optional[str] = None,
                     expires_hours: float = 72) -> Dict[str, Any]:
    """Ask the researcher a question with options (each: key, label, consequence, action). This only asks: the answer is a human's."""
    return D.request(p, question, options, subject=subject, default=default, expires_hours=expires_hours)


@op(sync=False, chat="read")
def decision_list(p, status: Optional[str] = "open", task: Optional[str] = None) -> List[Dict[str, Any]]:
    return D.list_all(p, status, task)


@op(sync=False, chat="read")
def decision_show(p, id: str) -> Dict[str, Any]:
    return D.load(p, id)


@op(mutates=True, chat="decide")
def decision_answer(p, id: str, key: Optional[str] = None, text: Optional[str] = None, via: str = "local", note: Optional[str] = None,
                    pin: Optional[str] = None) -> Dict[str, Any]:
    """Human only. An option key runs its prepared action; text goes to the supervisor. Protected options never run from chat (via="chat:...")."""
    return D.answer(p, id, key=key, text=text, via=via, note=note, pin=pin)


@op(mutates=True)
def decision_expire(p, id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Close a request without choosing (no id: all past their deadline). Never picks a default."""
    return D.expire(p, id)
