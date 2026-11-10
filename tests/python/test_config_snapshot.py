"""WP5b: moving every default into registry.py changed no value, and a project written by 2.6 opens and behaves as before."""
import json
import os
import shutil
import subprocess
import sys

from research import providers, registry, strategy as S, supervisor as SV
from research.store import DEFAULT_CONFIG, Project

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
# Keys that did not exist when the snapshot was taken. Anything else appearing or changing is a regression.
INTENDED_NEW = {
    "agents.profiles.codex-fast", "agents.profiles.codex-balanced", "agents.profiles.codex-deep",         # generated from the provider registry
    "agents.profiles.agy-fast", "agents.profiles.agy-balanced", "agents.profiles.agy-deep",
    "agents.profiles.opencode", "agents.profiles.opencode-fast", "agents.profiles.opencode-balanced", "agents.profiles.opencode-deep",   # WP13
    "orchestration.strategies.balanced_first", "orchestration.strategies.deep_first",                       # provider-neutral names (old ones kept)
    "orchestration.strategies.fast_max", "orchestration.strategies.balanced_heavy",
    "supervisor.launch_grace_seconds", "supervisor.escalation",                                             # already effective via supervisor.DEFAULTS
    "auto_promotion.test_command", "auto_promotion.review_timeout_minutes", "auto_promotion.max_file_mb",   # already effective via autopromote.DEFAULTS
    "auto_promotion.protected_test_globs",
    "notifications.alerts.on", "notifications.alerts.cooldown_minutes", "notifications.alerts.escalate_after",       # WP14a chat gateway
    "notifications.alerts.approval_expiry_hours", "notifications.digest.at", "notifications.digest.silent_if_unchanged",
    "notifications.discord", "notifications.level", "notifications.summary", "ask", "guide.site.auto_include",                                          # WP15; 3.2.2 push level; 3.3.0 site
    "governance.approval.chat", "governance.approval.chat_allow", "supervisor.investigator", "supervisor.usage_limit", "supervisor.recovery", "supervisor.commit_keep", "supervisor.commit_max_file_mb", "git.preflight",                                            # WP14b: approval from chat (TOTP)
}


def _flat(d, path=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and v:
            out.update(_flat(v, f"{path}{k}."))
        else:
            out[f"{path}{k}"] = v
    return out


def _frozen():
    old = _flat(json.load(open(os.path.join(FIX, "default_config_pre_wp5.json"))))
    old["agents.handoff.fallback_order"] = list(providers.KNOWN)   # derived from the provider registry (a new provider extends it)
    return old


def _is_new(key):
    return any(key == n or key.startswith(n + ".") for n in INTENDED_NEW)


def test_defaults_equal_the_frozen_pre_wp5_snapshot_modulo_intended_new_keys():
    old = _frozen()
    new = _flat(json.loads(json.dumps(registry.defaults(), default=str)))
    assert {k: v for k, v in new.items() if k in old} == old, "a default moved"
    assert {k for k in new if k not in old and not _is_new(k)} == set()
    assert all(any(k == n or k.startswith(n + ".") for k in new) for n in INTENDED_NEW)   # each intended key really exists
    assert json.loads(json.dumps(DEFAULT_CONFIG, default=str)) == json.loads(json.dumps(registry.defaults(), default=str))


def test_a_fresh_project_merges_to_the_snapshot_defaults(proj):
    old = _frozen()
    got = _flat(json.loads(json.dumps(proj.config, default=str)))
    diff = {k: (old[k], got.get(k)) for k in old if got.get(k) != old[k] and k not in ("project.name", "project.created_at", "project.git_repo", "project.goal")}
    assert diff == {}


def test_registry_sections_merge_partial_configs(proj):
    proj.config["supervisor"] = {"max_parallel_agents": 4}
    c = registry.section(proj, "supervisor")
    assert c["max_parallel_agents"] == 4 and c["poll_seconds"] == 5 and SV.cfg(proj)["session_resume"] == "auto"
    assert registry.default("context.max_chars") == 12000


def test_a_2_6_era_project_opens_and_behaves_unchanged(tmp_path):
    root = tmp_path / "legacy"
    shutil.copytree(os.path.join(FIX, "proj_2_6"), root)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, env=env)
    with Project(str(root)) as p:
        assert p.config["agents"]["default_profile"] == "codex" and p.config["supervisor"]["max_parallel_agents"] == 2
        assert [q["id"] for q in p.list("question")] == ["Q-001"] and p.get("experiment", "EXP-001")["title"] == "exp one"
        # the old strategy name and a user strategy extending an old name still resolve to the same policy as before
        assert S.active_name(p) == "sonnet_first" and S.validate(p, "sonnet_first")["ok"] and S.validate(p, "mine")["ok"]
        assert S.definition(p, "mine")["route"]["planning"] == "deep"
        assert S.definition(p, "sonnet_first") == S.definition(p, "balanced_first")
        assert SV.cfg(p)["max_parallel_agents"] == 2
    r = subprocess.run([sys.executable, "-m", "research_cli", "status", "--json"], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
