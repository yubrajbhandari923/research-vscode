"""One command router for every platform: auth by user id, roles, project selection, op policy, button clicks, replies as `Message`.

Platform adapters hand it an `Incoming`; it returns a `Reply`. Everything a command does goes through `api.call(surface="chat")`, so
`Op.chat` (None | read | decide | approve) is the policy: chat may say no to anything and yes only to what has an op here.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import api, machine, registry
from ..util import PolicyBlocked, ResearchError
from . import Button, Incoming, Message, Reply, actions, gdir, pairing, read_json, txn, write_json

NEED = {"read": "viewer", "decide": "operator", "approve": "approver"}
# name -> (op, how to turn the argument text into params, help line)
COMMANDS: Dict[str, Tuple[str, Any, str]] = {
    "status": ("chat_cmd_status", lambda a: {}, "all plans: health, progress, blocked"),
    "blocked": ("chat_cmd_blocked", lambda a: {}, "what is stuck and why"),
    "task": ("chat_cmd_task", lambda a: {"task": a}, "T-12: one task"),
    "logs": ("chat_cmd_logs", lambda a: {"task": a.split()[0] if a else "", "stream": a.split()[1] if len(a.split()) > 1 else ""}, "T-12 [stderr]: last log lines"),
    "report": ("chat_cmd_report", lambda a: {"scope": a}, "[scope]: the report as a file"),
    "finding": ("chat_cmd_finding", lambda a: {"id": a}, "F-3: claim, evidence, status"),
    "usage": ("chat_cmd_usage", lambda a: {"period": a or "today"}, "[today|week]: usage by provider and model"),
    "proposals": ("chat_cmd_proposals", lambda a: {}, "pending proposals"),
    "interventions": ("chat_cmd_interventions", lambda a: {}, "recent interventions"),
    "digest": ("chat_cmd_digest", lambda a: {}, "what changed since yesterday"),
    "decisions": ("chat_cmd_decisions", lambda a: {}, "open decision requests, one button per option"),
    "options": ("chat_cmd_options", lambda a: {}, "show the latest diagnosis"),
    "debug": ("chat_cmd_debug", lambda a: {}, "diagnose the halt now"),
    "tasks": ("chat_cmd_tasks", lambda a: {}, "what is running, what waits for you, with the command for each"),
    "say": ("chat_cmd_say", lambda a: {"text": a}, "[T-12] text: instruct the supervisor"),
    "choose": ("chat_cmd_choose", lambda a: {"n": a}, "N: pick a diagnosis option"),
    "pause": ("chat_cmd_pause", lambda a: {"plan": a or None}, "[plan]"),
    "resume": ("chat_cmd_resume", lambda a: {"plan": a or None}, "[plan]"),
    "stop": ("chat_cmd_stop", lambda a: {"task": a}, "T-12: stop the current attempt"),
    "continue": ("chat_cmd_continue", lambda a: {"task": a.split()[0] if a else "", "profile": a.split()[1] if len(a.split()) > 1 else ""}, "T-12 PROFILE: hand to another agent"),
    "approve-start": ("chat_cmd_approve_start", lambda a: {"task": a}, "T-12: let the task start"),
}
LOCAL = {"whoami": "your role and the projects you can see", "help": "this list", "project": "use NAME | list: choose the project for this chat",
         "sethome": "send digests and machine alerts to this chat", "health": "is chat set up and running? (what `research chat doctor` checks)",
         "pair": "get a pairing code (groups)"}
ALIASES = {"start": "help", "approve": "REFUSE", "accept": "REFUSE", "void": "REFUSE", "promote": "REFUSE", "restart": "REFUSE", "cancel": "REFUSE"}
_now = time.time


def command_list() -> List[Tuple[str, str]]:
    return [(n, v[2].split(":")[-1].strip()[:200] or n) for n, v in COMMANDS.items()] + list(LOCAL.items())


# ----------------------------------------------------------------------------------------------- projects
def enabled_projects(platform: str) -> Dict[str, str]:
    """label -> root of the known projects whose config enables `platform` (label: the folder name, `-2` for a clash)."""
    from ..store import Project
    out: Dict[str, str] = {}
    for root in machine.known_projects():
        try:
            with Project(root, sync=False) as p:
                if not registry.section(p, "notifications")[platform].get("enabled"):
                    continue
        except Exception:       # noqa: BLE001 - a broken project must not stop the others
            continue
        label, n = os.path.basename(root.rstrip("/")) or root, 2
        while label in out:
            label, n = f"{os.path.basename(root.rstrip('/'))}-{n}", n + 1
        out[label] = root
    return out


def card_actions(p, kind: str, subject: str) -> List[Dict[str, Any]]:
    """H.9: the extra Approve button on an approval card, only when approval from chat is enabled, an authenticator is enrolled and not
    disabled, and the proposal's action class is one chat may approve (`approval.CHAT_FORBIDDEN` is a code constant). Otherwise nothing:
    the card already carries 'Approve on my computer'. The button asks the approver for an authenticator code; the PIN never enters chat."""
    if kind != "proposal":
        return []
    try:
        from .. import approval, governance as G, supervisor as SV
        ap = G.cfg(p)["approval"]
        st = approval.chat_status()
        pr = SV._prop_load(p, subject)
        tag = approval.chat_action(pr)
        allowed = ap.get("chat_allow") or []
        if (not (ap.get("chat") or {}).get("enabled") or not st["chat_enrolled"] or st["chat_disabled"] or tag in approval.CHAT_FORBIDDEN
                or tag not in approval.CHAT_ACTIONS or (allowed and tag not in allowed)):
            return []
        return [{"label": "Approve (authenticator code)", "kind": "ask_code", "op": "chat_approve_proposal", "min_role": "approver",
                 "params": {"id": subject, "content_hash": approval.proposal_hash(pr)}}]
    except Exception:           # noqa: BLE001 - a card without the button is the safe fallback
        return []


def decision_actions(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One button per option of a decision request (H.7). Protected options go through the same op: it refuses to run them from chat and
    flags the request for the PIN flow, so the button is labelled for what it does."""
    out = []
    for o in d.get("options") or []:
        out.append({"label": (f"{o['key']}. {o['label']}" + (" (on my computer)" if o.get("protected") else ""))[:40], "op": "decision_answer",
                    "params": {"id": d["id"], "key": o["key"]}})
    return out


def via_for(op: str, platform: str) -> str:
    return f"chat:{platform}" if op.startswith("decision_") else platform


def buttons_for(root: str, specs: List[Dict[str, Any]], alert_id: Optional[str] = None) -> List[Button]:
    out = []
    for a in specs or []:
        kind = a.get("kind") or ("local" if a.get("text") else "ask_code" if a.get("ask_code") else "op")
        need = a.get("min_role") or ("approver" if kind == "ask_code" else "operator")
        tok = actions.issue(root, kind, a.get("op", ""), a.get("params"), need, alert_id, a.get("text", ""))
        out.append(Button(str(a.get("label", "?"))[:40], tok, a.get("style", "default")))
    return out


# ----------------------------------------------------------------------------------------------- the router
class Inbox:
    """What adapters call. Blocking; adapters on an event loop run these in an executor (the Discord 3 s deadline: defer first)."""

    def __init__(self, platform: str):
        self.platform = platform

    def command(self, inc: Incoming) -> Reply:
        try:
            return self._command(inc)
        except (ResearchError, PolicyBlocked) as ex:
            return Reply([Message(_err(ex), color="error", chat=inc.chat, ephemeral=True)])
        except Exception as ex:     # noqa: BLE001 - never leak internals to chat
            return Reply([Message(f"Error: {type(ex).__name__}", color="error", chat=inc.chat, ephemeral=True)])

    def click(self, inc: Incoming) -> Reply:
        try:
            return self._click(inc)
        except (ResearchError, PolicyBlocked) as ex:
            return Reply(toast=_err(ex)[:190])
        except Exception as ex:     # noqa: BLE001
            return Reply(toast=f"Error: {type(ex).__name__}")

    # --- state
    def _chats(self) -> Dict[str, Any]:
        return read_json(gdir("chats.json"))

    def _set_chat(self, key: str, value: Any) -> None:
        with txn("chats"):
            d = self._chats()
            d.setdefault(key.split("|")[0], {})[key.split("|")[1]] = value
            write_json(gdir("chats.json"), d)

    def _project(self, inc: Incoming, explicit: Optional[str] = None) -> Tuple[str, str]:
        projs = enabled_projects(self.platform)
        if explicit and explicit in projs:
            return explicit, projs[explicit]
        if not projs:
            raise ResearchError("No project has chat enabled", hint="Run `research chat setup` in a project.")
        if len(projs) == 1:
            k = next(iter(projs))
            return k, projs[k]
        sel = (self._chats().get("selected") or {}).get(f"{self.platform}:{inc.chat}")
        for k, r in projs.items():
            if r == sel:
                return k, r
        raise ResearchError("Several projects use this bot: choose one with /project use NAME. Projects: " + ", ".join(projs))

    # --- authorization
    def _denied(self, inc: Incoming) -> Reply:
        """Unknown user: groups get nothing except /pair; a private chat gets a pairing code (rate-limited) or one polite no per hour."""
        text = inc.text.lstrip()
        word = text.split()[0].lower().split("@")[0] if text else ""
        if inc.group and word != "/pair":
            return Reply()
        code = pairing.request_code(self.platform, inc.user_id, inc.user_name)
        if code:
            return Reply([Message(f"You are not paired yet. Your pairing code is {code} (valid for 1 hour).\nOn the computer that runs Research OS, run:\n"
                                  f"  research chat pair {code}", chat=inc.chat, ephemeral=True)])
        with txn("chats"):
            d = self._chats()
            den = d.setdefault("denied", {})
            k = f"{self.platform}:{inc.user_id}"
            if _now() - den.get(k, 0) < 3600:
                return Reply()
            den[k] = _now()
            write_json(gdir("chats.json"), d)
        return Reply([Message("Not authorized. If a pairing code was sent to you, the owner approves it with `research chat pair CODE`.", chat=inc.chat, ephemeral=True)])

    # --- commands
    def _command(self, inc: Incoming) -> Reply:
        if inc.is_bot or not inc.text:
            return Reply()
        role = pairing.role_of(self.platform, inc.user_id)
        if role is None:
            return self._denied(inc)
        text = inc.text.strip()
        done = self._pending_code(inc, role)
        if done is not None:
            return done
        if text.startswith("/"):
            cmd, _, rest = text.partition(" ")
            cmd = cmd.lower().split("@")[0][1:].replace("_", "-")
            rest = rest.strip()
        else:
            cmd, rest = ("say", text)
            ref = _refs().get(inc.reply_to or "")
            if ref and ref.get("decision"):
                return self._answer_decision(inc, role, ref, text)
        cmd = ALIASES.get(cmd, cmd)
        if cmd == "REFUSE" or cmd in ("approve-proposal", "approve-intervention"):
            return self._msg(inc, "That needs your computer: it is PIN-protected or bypasses verification. Use the terminal, VS Code → Plans → Approvals, "
                                  "or `research tui`. (A card with an Approve button appears here only when authenticator approval is set up.)", "warn")
        if cmd in LOCAL:
            return self._local(inc, role, cmd, rest)
        spec = COMMANDS.get(cmd)
        if spec is None:
            return self._msg(inc, "Unknown command. /help", "warn")
        o = api.OPS.get(spec[0])
        need = NEED.get(o.chat if o else None, "operator")
        if not pairing.at_least(role, need):
            return self._msg(inc, f"/{cmd} needs the {need} role; you are {role}. Ask the owner (research chat pair --role).", "warn")
        first = rest.split()[0] if rest.split() else ""
        explicit = first if first in enabled_projects(self.platform) else None      # `/status alpha`
        if explicit:
            rest = rest[len(first):].strip()
        label, root = self._project(inc, explicit)
        params = spec[1](rest)
        if o and "via" in o.params:
            params["via"] = via_for(spec[0], self.platform)
        res = self._run(root, spec[0], params, inc)
        return self._render(inc, label, root, res)

    def _local(self, inc: Incoming, role: str, cmd: str, rest: str) -> Reply:
        if cmd == "help":
            lines = [f"/{n} {v[2]}" for n, v in COMMANDS.items() if pairing.at_least(role, NEED.get(getattr(api.OPS.get(v[0]), "chat", None), "operator"))]
            lines += [f"/{n} {d}" for n, d in LOCAL.items() if n != "pair"]
            return self._msg(inc, "\n".join(lines) + "\nPlain text is sent as /say. Protected changes (science, resources, frozen tests, promotion) need your computer.")
        projs = enabled_projects(self.platform)
        if cmd == "whoami":
            return self._msg(inc, f"{inc.user_name or inc.user_id}: {role} on {self.platform} (id {inc.user_id})\nProjects: {', '.join(projs) or 'none'}")
        if cmd == "pair":
            return self._msg(inc, f"You are already paired as {role}.")
        if cmd == "health":
            from . import manage
            from ..store import Project
            first = rest.split()[0] if rest.split() else ""
            label, root = self._project(inc, first if first in projs else None)
            with Project(root, sync=False) as p:
                d = manage.doctor(p)
            mark = {"ok": "✓", "info": "·", "warn": "!", "error": "✗"}
            lines = [f"Chat health for {label}:"]
            for c in d["checks"]:
                lines.append(f"{mark[c['level']]} {c['message']}" + (f"\n    on your computer: {c['fix']}" if c["fix"] and c["level"] in ("warn", "error") else ""))
            return self._msg(inc, "\n".join(lines), "ok" if d["ok"] else "warn")
        if cmd == "project":
            sub, _, name = rest.partition(" ")
            if sub == "use" and name.strip() in projs:
                self._set_chat(f"selected|{self.platform}:{inc.chat}", projs[name.strip()])
                return self._msg(inc, f"Using {name.strip()} for this chat.", "ok")
            return self._msg(inc, "Projects: " + (", ".join(projs) or "none") + "\n/project use NAME")
        if cmd == "sethome":
            if not pairing.at_least(role, "operator"):
                return self._msg(inc, "/sethome needs the operator role.", "warn")
            if self.platform == "discord":
                from . import discord_bot
                discord_bot.set_home(inc.chat)           # discord.json is the one place status, doctor, test and alerts read
            else:
                self._set_chat(f"home|{self.platform}", inc.chat)
            return self._msg(inc, "This chat now receives digests and machine alerts. Saved on this machine; `research chat test" + (" discord" if self.platform == "discord" else "")
                             + "` posts here." + (" (Run /sethome in a channel, not in a thread.)" if self.platform == "discord" else ""), "ok")
        return self._msg(inc, "Unknown command. /help", "warn")

    def _run(self, root: str, op: str, params: Dict[str, Any], inc: Incoming) -> Any:
        s = api.Session(root, author={"author_type": "human", "author_name": f"{self.platform}:{inc.user_name or inc.user_id}", "author_model": None})
        try:
            return api.call(op, s, params, surface="chat")
        finally:
            if s.p is not None:
                s.p.close()

    def _render(self, inc: Incoming, label: str, root: str, res: Any) -> Reply:
        res = res if isinstance(res, dict) else {"text": str(res)}
        m = Message(str(res.get("text") or "Done."), files=list(res.get("files") or []), chat=inc.chat, project=label,
                    buttons=buttons_for(root, res.get("actions") or []))
        return Reply([m])

    def _msg(self, inc: Incoming, text: str, color: str = "info") -> Reply:
        return Reply([Message(text, color=color, chat=inc.chat, ephemeral=True)])

    # --- decisions (H.7): a free-text reply to a decision card is the answer
    def _answer_decision(self, inc: Incoming, role: str, ref: Dict[str, Any], text: str) -> Reply:
        if not pairing.at_least(role, "operator"):
            return self._msg(inc, "Answering needs the operator role.", "warn")
        op = "decision_answer" if "decision_answer" in api.OPS else "chat_cmd_say"
        params = {"id": ref["decision"], "text": text, "via": via_for(op, self.platform)} if op == "decision_answer" else {"text": text, "via": self.platform}
        res = self._run(ref["project"], op, params, inc)
        if isinstance(res, dict) and "outcome" in res and "text" not in res:
            res = dict(res, text=res["outcome"])
        return self._render(inc, ref.get("label", ""), ref["project"], res)

    # --- buttons
    def _click(self, inc: Incoming) -> Reply:
        role = pairing.role_of(self.platform, inc.user_id)
        if role is None or inc.is_bot:
            return Reply(toast="Not authorized")
        rec = actions.take(inc.token or "", by=inc.user_name or inc.user_id)      # unknown / used / expired raise with the reason
        if not pairing.at_least(role, rec["min_role"]):
            actions.release(inc.token or "")
            return Reply(toast=f"This needs the {rec['min_role']} role")
        card = _refs().get(inc.ref or "") or {}
        done = lambda note: Message((card.get("text") or "")[:1500] + f"\n\n{note}", chat=inc.chat, color="ok")        # noqa: E731
        if rec["kind"] == "local":
            return Reply([Message(rec["text"] or "Do this on your computer.", chat=inc.chat, ephemeral=True)], toast="See the message", edit=done(f"→ {inc.user_name or inc.user_id}: continue on your computer"))
        if rec["kind"] == "ask_code":
            _pending_set(self.platform, inc.user_id, {**rec, "token": inc.token, "card": inc.ref, "chat": inc.chat, "expires": _now() + 300})
            return Reply([Message("Reply with your current 6-digit authenticator code. I delete your message after use.", chat=inc.chat, ephemeral=True)], toast="Waiting for your code")
        params = dict(rec["params"])
        o = api.OPS.get(rec["op"])
        if o and "via" in o.params:
            params.setdefault("via", via_for(rec["op"], self.platform))
        try:
            res = self._run(rec["project"], rec["op"], params, inc)
        except Exception:
            actions.release(inc.token or "")
            raise
        res = res if isinstance(res, dict) else {"text": str(res)}
        text = res.get("text") or res.get("outcome") or "done"
        if res.get("already"):
            text = "Already answered from another surface: " + str(text)
        if res.get("needs_local") or res.get("already") or res.get("needs_text"):
            actions.release(inc.token or "")                  # nothing was decided here: the buttons stay usable
            return Reply([Message(str(text), chat=inc.chat, ephemeral=True)], toast=str(text)[:190])
        return Reply([Message(str(text), chat=inc.chat)], toast="Done", edit=done(f"✓ {rec['op'].replace('chat_cmd_', '').replace('decision_answer', 'answered')} by {inc.user_name or inc.user_id}"))

    def _pending_code(self, inc: Incoming, role: str) -> Optional[Reply]:
        """H.9: after an Approve click, a 6-digit message from the same user is the authenticator code. It goes only to the op, is never
        stored or logged here, and the message is deleted afterwards."""
        if not re.fullmatch(r"\d{6}", inc.text.strip()):
            return None
        rec = _pending_get(self.platform, inc.user_id)
        if not rec or rec["expires"] < _now():
            return None
        try:
            res = self._run(rec["project"], rec["op"], {**rec["params"], "code": inc.text.strip(), "platform": self.platform, "user_id": inc.user_id, "role": role}, inc)
        except (ResearchError, PolicyBlocked) as ex:
            return Reply([Message(_err(ex), color="error", chat=inc.chat, ephemeral=True)], delete=[inc.ref] if inc.ref else [])
        _pending_set(self.platform, inc.user_id, None)
        card = _refs().get(rec.get("card") or "") or {}
        txt = (res.get("text") if isinstance(res, dict) else None) or "Approved."
        return Reply([Message(str(txt), chat=inc.chat, color="ok")], delete=[inc.ref] if inc.ref else [],
                     edit=Message((card.get("text") or "")[:1500] + f"\n\n✓ approved by {inc.user_name or inc.user_id} via {self.platform}", chat=inc.chat, color="ok"))


def _err(ex: Exception) -> str:
    return "Not done: " + str(ex)[:600] + ((" " + ex.hint) if getattr(ex, "hint", None) else "")


def _refs() -> Dict[str, Any]:
    return read_json(gdir("refs.json"))


def _pending_set(platform: str, uid: str, rec: Optional[Dict[str, Any]]) -> None:
    with txn("pending"):
        d = read_json(gdir("pending.json"))
        d = {k: v for k, v in d.items() if v.get("expires", 0) > _now()}
        if rec is None:
            d.pop(f"{platform}:{uid}", None)
        else:
            d[f"{platform}:{uid}"] = rec
        write_json(gdir("pending.json"), d)


def _pending_get(platform: str, uid: str) -> Optional[Dict[str, Any]]:
    return read_json(gdir("pending.json")).get(f"{platform}:{uid}")
