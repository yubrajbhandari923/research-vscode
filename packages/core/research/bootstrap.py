"""One versioned YAML file that describes a whole project setup (`research init --from research-os.yaml`).

It is a front end for `initproject`: `to_spec` turns the file into the same spec the VS Code wizard builds, so the wizard, the CLI and
the YAML editor all go through `initproject.plan` / `initproject.apply` and produce the same project. Settings that are not wizard
fields (supervisor limits, governance, handoff) travel in `spec["config"]` as dotted keys and are validated by the settings registry.

Portable by design: no credentials, no PIN, no absolute machine paths unless you write one (python.mode: existing, hpc.ssh_host).
HPC can be a reference to a cluster named in the machine config (`hpc.cluster`), which keeps hosts out of the project file.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import providers, strategy as _strategy, yamlio
from .util import ResearchError

SCHEMA = 1

TEMPLATE = """\
# Research OS project bootstrap. Preview:  research init --from research-os.yaml --dry-run
#                                Apply:    research init --from research-os.yaml --apply
# Nothing here is a secret. The approval PIN is never part of this file: set it with `research approval set-pin`.
schema_version: 1

project:
  name: my-project              # required
  goal: ""                      # one sentence: what are you trying to find out?
  context: ""                   # optional background written into README.md

layout:                         # three sibling folders (relative to the folder you initialize)
  main_dir: main                # your code; GitHub's root
  ledger_dir: ledger            # research records (branch research/ledger)
  experiments_dir: experiments  # one git worktree per experiment

git:
  remote_url: null              # https://..., git@... or ssh://...; added as `origin`, never pushed

agents:
  enabled: [claude]             # any of: @KNOWN@
  default: claude
  strategy: balanced_first      # @STRATEGIES@ (model allocation; levels come from the default agent's level pack)
  models: {}                    # optional per-provider model, e.g. {claude: claude-sonnet-5-5}
  handoff:                      # continue a task with another provider
    enabled: false              # opt-in automatic fallback; the "Continue with another agent" action always works
    fallback_order: [@KNOWN_LIST@]
    on_provider_unavailable: pause     # pause | try_next
    on_unknown_execution_state: pause  # always pause

supervisor:                     # limits and retries
  max_parallel_agents: 1
  max_attempts_per_task: 3
  max_total_attempts: 20
  max_wall_minutes: 480
  attempt_timeout_minutes: 90
  on_failure: diagnose_and_repair      # diagnose_and_repair | pause | stop
  auto_apply_operational: true         # can only tighten governance.tasks, never loosen it
  max_auto_tasks: 5

governance:                     # agents propose, you approve
  experiments:
    agent_creation: propose_only       # propose_only | human_only
  tasks:
    agent_creation: bounded_auto       # bounded_auto | propose_only | human_only
    allowed_auto_types: [bugfix, diagnostic, verification]
    max_auto_per_experiment: 3
  scientific_changes:
    require_human_approval: true
  approval:
    require_pin: true                  # asks for your PIN once you set one
    require_rationale: true
    record_rejections: true
    confirm_human_experiment_creation: false   # true: ask for the PIN when YOU create an experiment

verification:
  independent_review: true

tools:
  packs: []                     # extra skill packs, e.g. [scientific-python, machine-learning]; Core Research is always on
  link_skills: true
  starter_hooks: true           # nine cheap, non-blocking hooks, approved for you

python:
  mode: none                    # none | create (uv) | existing
  path: null                    # create: venv folder (default .venv); existing: venv or interpreter (machine-specific)
  packages: []

hpc:                          # references only; no connection is made while initializing
  enabled: false
  cluster: null               # name of a cluster in your machine config (~/.config/research-os/config.yaml)
  # or describe one here (machine-specific):  ssh_host, workspace_root, partition

guide:
  enabled: true                 # the Scientist Guide (Quarto book)
  publish: true                 # GitHub Actions workflow that publishes it on push to main
  trigger: result               # manual | result | checkpoint
  max_words: 150
  max_sections: 3
  max_figures: 1

workspace:
  create: true                  # write <name>.code-workspace
"""

TEMPLATE = (TEMPLATE.replace("@KNOWN@", ", ".join(providers.KNOWN)).replace("@KNOWN_LIST@", ", ".join(providers.KNOWN))
            .replace("@STRATEGIES@", " | ".join(_strategy.NAMES)))

# top-level section -> allowed keys (None = free-form, validated elsewhere)
_ALLOWED = {
    "schema_version": None,
    "project": {"name", "goal", "context", "description"},
    "layout": {"main_dir", "ledger_dir", "experiments_dir"},
    "git": {"remote_url"},
    "agents": {"enabled", "default", "strategy", "models", "handoff"},
    "supervisor": None, "governance": None,
    "verification": {"independent_review"},
    "tools": {"packs", "link_skills", "starter_hooks"},
    "python": {"mode", "path", "packages"},
    "hpc": {"enabled", "cluster", "name", "ssh_host", "workspace_root", "shared_data", "projects", "scratch", "partition", "python", "required_packages", "setup"},
    "guide": {"enabled", "publish", "trigger", "max_words", "max_sections", "max_figures", "auto_log", "highlights"},
    "workspace": {"create"},
}


def parse(text: str) -> Dict[str, Any]:
    try:
        doc = yamlio.load(text)
    except Exception as e:
        line = getattr(getattr(e, "problem_mark", None), "line", None)
        raise ResearchError(f"The YAML cannot be parsed{f' (line {line + 1})' if line is not None else ''}: {getattr(e, 'problem', None) or e}")
    if not isinstance(doc, dict):
        raise ResearchError("The bootstrap file must be a YAML mapping (see `research init --template`)")
    return doc


def _flat(prefix: str, d: Any, out: Dict[str, Any]) -> None:
    for k, v in (d or {}).items():
        if isinstance(v, dict) and v:
            _flat(f"{prefix}{k}.", v, out)
        else:
            out[f"{prefix}{k}"] = v


def to_spec(doc: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """(wizard spec, errors). Errors name the YAML path, e.g. `governance.tasks.agent_creation: Choose one of …`."""
    from . import registry
    errs: List[str] = []
    if doc.get("schema_version") != SCHEMA:
        errs.append(f"schema_version: must be {SCHEMA}, got {doc.get('schema_version')!r}")
    for sec, val in doc.items():
        if sec not in _ALLOWED:
            errs.append(f"{sec}: unknown section (known: {', '.join(k for k in _ALLOWED if k != 'schema_version')})")
        elif _ALLOWED[sec] is not None and isinstance(val, dict):
            errs += [f"{sec}.{k}: unknown setting" for k in val if k not in _ALLOWED[sec]]
        elif sec != "schema_version" and not isinstance(val, dict) and val is not None:
            errs.append(f"{sec}: must be a mapping")
    g = lambda s: doc.get(s) if isinstance(doc.get(s), dict) else {}
    pr, ag, hp, tl, gd, py = g("project"), g("agents"), g("hpc"), g("tools"), g("guide"), g("python")
    spec: Dict[str, Any] = {"name": pr.get("name"), "goal": pr.get("goal") or "", "layout": g("layout"),
                            "agents": {k: ag[k] for k in ("enabled", "default", "strategy") if ag.get(k) is not None},
                            "git": {"remote_url": g("git").get("remote_url")}, "verification": g("verification"),
                            "tools": {k: v for k, v in tl.items() if v is not None}, "python": py,
                            "guide": gd, "create_workspace": g("workspace").get("create", True)}
    if pr.get("context"):
        spec["goal"] = (spec["goal"] + "\n\n" + str(pr["context"])).strip()
    if hp.get("enabled"):
        spec["hpc"] = {k: v for k, v in hp.items() if v is not None and k != "cluster"}
    cfg: Dict[str, Any] = {}
    for sec in ("supervisor", "governance"):
        _flat(sec + ".", g(sec), cfg)
    _flat("agents.handoff.", ag.get("handoff") if isinstance(ag.get("handoff"), dict) else {}, cfg)
    for prov, model in (ag.get("models") or {}).items():
        cfg[f"agents.profiles.{prov}.model"] = model
    if hp.get("cluster") and not hp.get("enabled"):
        cfg["hpc.default_cluster"] = hp["cluster"]
    for k in list(cfg):
        if k.startswith("agents.profiles."):
            continue
        try:
            ok, val, err = registry.validate(k, cfg[k])
        except ResearchError as e:
            errs.append(f"{k}: {e}")
            continue
        if ok:
            cfg[k] = val
        else:
            errs.append(f"{k}: {err}")
    spec["config"] = cfg
    return spec, errs


def check(text: str, root: str) -> Dict[str, Any]:
    """Validate and preview: {ok, errors, spec, plan}. Writes nothing."""
    from . import initproject
    try:
        spec, errs = to_spec(parse(text))
    except ResearchError as e:
        return {"ok": False, "errors": [str(e)], "plan": None, "spec": None}
    if errs:
        return {"ok": False, "errors": errs, "plan": None, "spec": spec}
    pl = initproject.plan(root, spec)
    return {"ok": pl["ok"], "errors": pl["problems"], "plan": {k: pl[k] for k in ("actions", "notes", "tree")}, "spec": spec}


def apply(text: str, root: str, author=None) -> Dict[str, Any]:
    from . import initproject
    spec, errs = to_spec(parse(text))
    if errs:
        raise ResearchError("Invalid bootstrap file: " + "; ".join(errs))
    return initproject.apply(root, spec, author=author)


def export(p) -> str:
    """The current project as a bootstrap file: portable settings only (no machine paths, no hosts, no PIN)."""
    c = p.config
    doc = yamlio.load(TEMPLATE)
    doc["project"].update(name=c["project"].get("name") or doc["project"]["name"], goal=c["project"].get("goal") or "")
    for sec in ("supervisor", "governance"):
        cur = c.get(sec) or {}
        doc[sec] = {k: cur.get(k, v) if not isinstance(v, dict) else {**v, **(cur.get(k) or {})} for k, v in doc[sec].items()}
    doc["agents"]["default"] = c["agents"].get("default_profile") or providers.KNOWN[0]
    doc["agents"]["handoff"] = {**doc["agents"]["handoff"], **(c["agents"].get("handoff") or {})}
    doc["tools"]["packs"] = [x for x in ((c.get("skills") or {}).get("packs") or []) if x != "core-research"]
    doc["verification"]["independent_review"] = bool((c.get("verification") or {}).get("independent_reviewer", True))
    return "# Exported by `research init --export`. Machine-specific settings (Python path, HPC hosts) are not included.\n" + yamlio.dump(doc, block=True)
