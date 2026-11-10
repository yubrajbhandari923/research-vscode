"""The built-in guide: what you are looking at and how to proceed. Plain text, one entry per section so a screen can open at the right place."""
from __future__ import annotations

from typing import Dict, List, Tuple

SECTIONS: List[Tuple[str, str]] = [("start", "Start here"), ("status", "Statuses"), ("who", "Who does what"), ("review", "Reviews and approvals"),
                                   ("keys", "Keys"), ("trouble", "Troubleshooting")]

START = """RESEARCH LOOP
  Question -> Experiment -> Run -> Evidence -> Synthesis -> Finding -> Decision
  A question is what you want to know. An experiment is one planned test of a hypothesis. A run is one execution (local, Slurm...). Evidence is
  what a run produced (metrics, artifacts, logs). A synthesis is the written interpretation of the runs against the hypothesis. A finding is a
  claim the evidence supports, reviewed by someone other than its author. A decision records what you chose to do next.

AUTOMATION LOOP
  Plan -> Task -> Agent -> Verification -> Promotion
  A plan lists tasks. The supervisor starts an agent per task, then VERIFIES the result (programs, not opinions). Verified code can be promoted
  to main, which stays a separate, human-gated step.

THIS SCREEN
  The Inbox tab (key i) lists everything that needs you, in three kinds that are never mixed:
    Action              something is waiting for you, or evidence cannot be trusted
    Recommended         a scientific step is due but nothing is stuck (a synthesis, a review)
    Informational       nothing to do (a planned experiment)
  An experiment saying needs_review is a recommended follow-up about interpretation. It is NOT a decision request, so the Decisions tab can be empty
  while the Inbox shows it.
"""
STATUS = """EXPERIMENT   proposed -> ready -> running -> needs_review -> completed   (or failed / abandoned)
  proposed      planned or proposed; no run yet. Normal. Needs nothing unless an AGENT proposed it (then accept or abandon it).
  ready         accepted, waiting for its first run.
  running       a run is queued or running.
  needs_review  every run finished. Waiting for a synthesis and for you to accept it.
  completed     you closed it after reading the synthesis. Only a person does this.

RUN           queued -> running -> completed | failed | cancelled
  completed     the job exited 0. That says nothing about whether its files are where the project expects them.
  For Slurm/HPC the run record can be 'collected' (logs and metadata synced) while artifacts remain on the cluster.

ARTIFACT      local | collected | inaccessible | missing | modified
  local / collected   the file is here and matches what was registered
  inaccessible        registered on external storage this machine cannot reach (not mounted, wrong host). Not verified, not rewritten.
  missing             the path is reachable but the file is gone
  modified            the content differs from the registered hash: do not cite it until you know why

FINDING       preliminary -> supported | contradicted | superseded
PROPOSAL      pending -> approved | rejected         DECISION  open -> answered | expired
"""
WHO = """AUTOMATIC (the program does it, no agent, no cost)
  detects finished runs - checks evidence completeness (exit codes, metrics, artifacts, hooks) - notices a synthesis is due - prepares a grounded draft
  brief (recorded facts only) - verifies agent work
AGENT-ASSISTED (only when you start it)
  an analyst reads the runs and writes an independent interpretation against the hypothesis, criteria and limitations. 'Prepare' writes the brief and
  starts nothing; 'Launch' starts a real agent and may cost money. A prepared session is not a running one.
RESEARCHER-CONTROLLED (never automatic)
  accepting or editing an interpretation - recording a synthesis - closing an experiment - establishing a finding - scientific decisions - promotion
  to main - anything protected by your PIN
"""
REVIEW = """Six different things, easy to confuse:
  Engineering verification   programs check that a task's output exists and passes its checks (supervisor). About code, not truth.
  Independent code review    another agent or person reviews a code change before it is promoted. About code quality.
  Experiment synthesis       the interpretation of runs against the hypothesis. Written by an analyst or you; recorded by you.
  Finding review             a reviewer other than the author marks a finding supported or contradicted. About a claim.
  Governance approval        you approve or reject a proposal or intervention that changes plans, resources or scientific content (PIN).
  Decision request           a structured question with options that something is waiting on. Answer it to unblock.
Nothing here bypasses your PIN or an approval gate: the TUI calls the same operations as the CLI and asks for the PIN when they need it.
"""
KEYS = [("i", "open the Inbox"), ("Enter / x", "actions for the selected item"), ("w", "what does this mean? (selected status or item)"), ("?", "this guide (any screen)"),
        ("h", "Git health: uncommitted files, worktrees, reviewed commit"), ("1-8", "switch tab (not on Decisions)"), ("1-9", "Decisions: pick that option"), ("[ ]", "previous / next tab"), ("r", "refresh now (also every 5 s)"), ("/", "filter rows"),
        ("k", "cancel the selected run"), ("a / d", "approve / reject a proposal (PIN)"), ("t", "Decisions: answer in your own words"),
        ("ctrl+t", "dark / light theme"), ("q", "quit")]
TROUBLE = """The Decisions tab is empty but an experiment says needs_review.   That is a status, not a question: see the Inbox (i).
An artifact is 'inaccessible'.   Its registered path is on storage this machine cannot reach. Reconnect it and choose Re-check, run Sync HPC, or collect the
  files and register them. A finished Slurm job does not prove its files exist.
'Prepare analyst draft' did nothing visible.   It wrote a brief only. Choose Launch analyst to start the agent.
An action says it needs a PIN.   Set one: `research approval set-pin`. Agents and chat cannot approve protected changes.
Data looks stale.   The TUI refreshes every 5 s; r refreshes now. A row in the Inbox says when a source could not be read.
More: docs/QUICKSTART.md, docs/GOVERNANCE.md, docs/SUPERVISOR.md.
"""
BODY: Dict[str, str] = {"start": START, "status": STATUS, "who": WHO, "review": REVIEW, "trouble": TROUBLE}

# what a status or item kind means, for the contextual `w` key: topic -> section
TOPIC = {"proposed": "status", "ready": "status", "running": "status", "needs_review": "status", "completed": "status", "failed": "status", "preliminary": "status",
         "inaccessible": "status", "missing": "status", "modified": "status", "synthesis": "who", "artifacts": "status", "decision": "review", "proposal": "review",
         "intervention": "review", "finding": "review", "promotion": "review", "blocked_task": "review", "experiment": "status", "failed_runs": "status"}


def section_for(topic: str) -> str:
    return TOPIC.get(topic, "start")


def body(section: str) -> str:
    if section == "keys":
        return "\n".join(f"  {k:10} {d}" for k, d in KEYS)
    return BODY.get(section, START)
