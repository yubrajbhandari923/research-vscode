"""Configuration: layering, sparse files, surgical edits, validation, migration.

The contract under test: the effective settings every consumer reads are unchanged by any of this, a
project's file holds only what it changes, and an edit never destroys a comment or reorders a key.
"""
import json
import os
import subprocess
import sys

import pytest

from research import configfile as CF
from research import yamlio
from research.store import DEFAULT_CONFIG, Project
from research.util import ResearchError


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """An optional machine-level config layer."""
    d = tmp_path / "xdg"
    (d / "research-os").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(d))
    monkeypatch.delenv("RESEARCH_CONFIG", raising=False)
    path = d / "research-os" / "config.yaml"

    def write(text):
        path.write_text(text, encoding="utf-8")
        return str(path)
    return write


@pytest.fixture(autouse=True)
def _no_machine_layer(monkeypatch, tmp_path):
    """Isolate every test from the developer's real ~/.config, unless it asks for a machine layer."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-xdg"))
    monkeypatch.delenv("RESEARCH_CONFIG", raising=False)


# --------------------------------------------------------------------------- primitives

def test_flatten_unflatten_roundtrip():
    d = {"a": {"b": 1, "c": {}}, "d": [1, 2], "e": None}
    flat = dict(CF.flatten(d))
    assert flat == {"a.b": 1, "a.c": {}, "d": [1, 2], "e": None}
    assert CF.unflatten(flat) == d


@pytest.mark.parametrize("value", [
    "plain", "has: colon", "yes", "no", "#hash", "", "12000", 5, 0.5, True, False, None,
    [], ["a", "b"], {}, "trailing ", "ünïcode ✓", "a | pipe",
])
def test_scalar_rendering_roundtrips(value):
    """A value written inline must read back as itself — YAML has many ways to get this wrong."""
    rendered = CF._render_value(value)
    assert "\n" not in rendered
    assert yamlio.load(f"k: {rendered}\n")["k"] == value


def test_dump_block_style_is_hand_editable():
    """Flow style breaks when a human adds an inline comment; block style does not."""
    d = {"project": {"name": "x", "goal": "y"}}
    assert "{name:" in yamlio.dump(d)                       # the old default, kept for front-matter
    block = yamlio.dump(d, block=True)
    assert "{" not in block
    edited = block.replace("goal: y", "goal: y   # my note")
    assert yamlio.load(edited)["project"]["goal"] == "y"    # still parses with a comment


# --------------------------------------------------------------------------- layering

def test_effective_matches_the_old_deep_merge(proj):
    """No consumer may see a different value than before this refactor."""
    from research.store import _deep_merge
    raw = proj.raw_config
    assert CF.effective(raw, DEFAULT_CONFIG) == _deep_merge(DEFAULT_CONFIG, raw)


def test_absent_key_is_inherited_not_missing(proj):
    raw = {"project": {"name": "x"}}
    eff = CF.effective(raw, DEFAULT_CONFIG)
    assert eff["resume"] == {"stale_checkpoint_days": 14}
    assert eff["agent_policy"]["max_runs_without_synthesis"] == 5


def test_precedence_default_then_machine_then_project(machine, proj):
    machine("context: {max_chars: 20000}\ncoordination: {stale_task_hours: 9}\n")
    raw = {"context": {"max_chars": 30000}}
    eff = CF.effective(raw, DEFAULT_CONFIG)
    assert eff["context"]["max_chars"] == 30000            # project wins over machine
    assert eff["coordination"]["stale_task_hours"] == 9    # machine wins over default
    assert eff["resume"]["stale_checkpoint_days"] == 14    # default where neither speaks


def test_sources_reports_the_winning_layer(machine, proj):
    machine("context: {max_chars: 20000}\n")
    src = CF.sources({"project": {"name": "mine"}}, DEFAULT_CONFIG)
    assert src["context.max_chars"] == {"value": 20000, "source": "machine", "defined_in": []}
    assert src["project.name"]["source"] == "project"
    assert src["resume.stale_checkpoint_days"]["source"] == "default"


def test_machine_layer_cannot_set_project_identity(machine):
    machine("project: {name: STOLEN, goal: STOLEN}\ncontext: {max_chars: 1}\n")
    settings, path = CF.machine_config()
    assert "project" not in settings and settings["context"]["max_chars"] == 1
    assert path and os.path.isfile(path)


def test_research_config_env_overrides_the_xdg_location(tmp_path, monkeypatch):
    explicit = tmp_path / "elsewhere.yaml"
    explicit.write_text("context: {max_chars: 777}\n", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_CONFIG", str(explicit))
    settings, path = CF.machine_config()
    assert settings["context"]["max_chars"] == 777 and path == str(explicit)


def test_absent_machine_layer_is_the_old_behaviour(proj):
    settings, path = CF.machine_config()
    assert settings == {} and (path is None or not os.path.isfile(path))
    assert proj.config["context"]["max_chars"] == 12000


def test_broken_machine_layer_is_reported_not_swallowed(machine):
    machine("context: {max_chars: [unclosed\n")
    with pytest.raises(ResearchError) as e:
        CF.machine_config()
    assert "machine config" in str(e.value) and "unaffected" in (e.value.hint or "")


# --------------------------------------------------------------------------- sparse files

def test_init_writes_a_sparse_block_style_file(proj):
    text = open(proj.config_path, encoding="utf-8").read()
    live = [l for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
    assert len(live) <= 8, live                  # identity only
    assert "{" not in "\n".join(live)            # block style
    assert CF.REFERENCE_BANNER in text           # defaults still discoverable
    assert proj.config["agent_policy"]["max_runs_without_synthesis"] == 5   # and still in force


def test_sparse_keeps_identity_even_when_it_equals_a_default(proj):
    """Dropping project.description because it happens to be "" would be a silent data change."""
    raw = {"project": {"name": "p", "description": "", "goal": "", "created_at": "t", "git_repo": None}}
    keep = CF.overrides(raw, DEFAULT_CONFIG)
    for k in ("project.name", "project.description", "project.created_at", "project.git_repo"):
        assert k in keep, k


def test_unknown_keys_are_never_dropped(proj):
    raw = {"project": {"name": "p"}, "my_lab": {"grid": "0.25deg"}, "future_feature": True}
    assert CF.overrides(raw, DEFAULT_CONFIG)["my_lab.grid"] == "0.25deg"
    assert CF.overrides(raw, DEFAULT_CONFIG)["future_feature"] is True
    assert CF.effective(raw, DEFAULT_CONFIG)["my_lab"] == {"grid": "0.25deg"}
    text = CF.render(CF.sparse(raw, DEFAULT_CONFIG), DEFAULT_CONFIG)
    assert yamlio.load(text)["my_lab"] == {"grid": "0.25deg"}


def test_reference_block_shows_the_value_in_force_not_the_builtin_default(machine, proj):
    """A commented reference line must not invite you to undo a machine-level setting.

    Spotted in a real file: the block printed `# default_cluster: null` while the machine layer had set it
    to `duke`, so uncommenting that line would have silently disabled HPC for the project.
    """
    mpath = machine("hpc:\n  default_cluster: duke\n  clusters:\n    duke:\n      ssh_host: login\n")
    text = CF.render({"project": {"name": "p"}}, DEFAULT_CONFIG)
    assert "default_cluster: duke" in text
    assert "default_cluster: null" not in text
    assert "← set for every project in your machine config" in text
    assert mpath in text                                 # and it says where to look
    # the commented block is still only a comment: it changes nothing
    assert (yamlio.load(text).get("hpc") or {}).get("default_cluster") is None


def test_reference_block_has_no_machine_annotations_without_a_machine_layer(proj):
    text = CF.render({"project": {"name": "p"}}, DEFAULT_CONFIG)
    assert "←" not in text and "default_cluster: null" in text


def test_sparse_file_round_trips_the_effective_config(proj):
    raw = {"project": {"name": "p"}, "context": {"max_chars": 999}, "unknown_thing": [1, 2]}
    before = CF.effective(raw, DEFAULT_CONFIG)
    text = CF.render(CF.sparse(raw, DEFAULT_CONFIG), DEFAULT_CONFIG)
    assert CF.effective(yamlio.load(text), DEFAULT_CONFIG) == before


def test_machine_layer_shrinks_the_project_file(machine, proj):
    """A cluster defined once on the machine should not be repeated per project."""
    machine("hpc:\n  default_cluster: duke\n  clusters:\n    duke:\n      ssh_host: login\n")
    raw = {"project": {"name": "p"},
           "hpc": {"default_cluster": "duke",
                   "clusters": {"duke": {"ssh_host": "login", "project": {"slug": "p"}}}}}
    keep = CF.overrides(raw, DEFAULT_CONFIG)
    assert "hpc.default_cluster" not in keep                       # inherited from the machine
    assert "hpc.clusters.duke.ssh_host" not in keep                # ditto
    assert keep["hpc.clusters.duke.project.slug"] == "p"           # only what is project-specific


# --------------------------------------------------------------------------- surgical edits

LEGACY_WITH_COMMENTS = """# Research project configuration (human-editable).
# Our lab added this note and expects it to survive.

project:
  name: pravah
  goal: old goal

# IMPORTANT: lab rule — three runs minimum
agent_policy:
  max_runs_without_synthesis: 3   # do not raise without asking
  enforce_for_humans: false

hpc:
  clusters: {}
"""


def test_editing_a_key_preserves_every_comment():
    out = CF.set_keys(LEGACY_WITH_COMMENTS, {"project.goal": "new goal"})
    assert yamlio.load(out)["project"]["goal"] == "new goal"
    assert "# Our lab added this note and expects it to survive." in out
    assert "# IMPORTANT: lab rule — three runs minimum" in out
    assert "max_runs_without_synthesis: 3   # do not raise without asking" in out


def test_editing_a_commented_key_keeps_its_comment():
    out = CF.set_keys(LEGACY_WITH_COMMENTS, {"agent_policy.max_runs_without_synthesis": 8})
    assert yamlio.load(out)["agent_policy"]["max_runs_without_synthesis"] == 8
    assert "# do not raise without asking" in out


def test_editing_preserves_ordering_and_untouched_lines():
    out = CF.set_keys(LEGACY_WITH_COMMENTS, {"project.goal": "new goal"})
    before = LEGACY_WITH_COMMENTS.split("\n")
    after = out.split("\n")
    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1 and "goal" in after[differing[0]]


def test_adding_a_new_key_to_an_existing_block():
    out = CF.set_keys(LEGACY_WITH_COMMENTS, {"project.status": "active"})
    d = yamlio.load(out)
    assert d["project"] == {"name": "pravah", "goal": "old goal", "status": "active"}
    assert "# IMPORTANT: lab rule — three runs minimum" in out


def test_adding_a_nested_block_under_an_empty_mapping():
    out = CF.set_keys(LEGACY_WITH_COMMENTS, {
        "hpc.clusters.duke.ssh_host": "plp-drcc-login2",
        "hpc.clusters.duke.paths.workspace_root": "/approved/ws"})
    d = yamlio.load(out)
    assert d["hpc"]["clusters"]["duke"]["ssh_host"] == "plp-drcc-login2"
    assert d["hpc"]["clusters"]["duke"]["paths"]["workspace_root"] == "/approved/ws"
    assert "{" not in out.split("# ──")[0].replace("clusters: {}", "")   # grew into a real block


def test_adding_a_brand_new_top_level_block():
    out = CF.set_keys(LEGACY_WITH_COMMENTS, {"context.max_chars": 20000})
    assert yamlio.load(out)["context"]["max_chars"] == 20000
    assert out.startswith("# Research project configuration")


def test_repeated_edits_are_stable():
    text = LEGACY_WITH_COMMENTS
    for i in range(5):
        text = CF.set_keys(text, {"project.goal": f"goal {i}"})
    assert yamlio.load(text)["project"]["goal"] == "goal 4"
    assert text.count("goal:") == 1                       # no duplicated keys accumulated
    assert "# IMPORTANT: lab rule — three runs minimum" in text
    once = CF.set_keys(text, {"project.goal": "goal 4"})
    assert once == text                                   # writing the same value is a no-op


def test_crlf_line_endings_survive_an_edit():
    text = LEGACY_WITH_COMMENTS.replace("\n", "\r\n")
    out = CF.set_keys(text, {"project.goal": "new goal"})
    assert "\r\n" in out and "\n\n" not in out.replace("\r\n", "")
    assert yamlio.load(out.replace("\r\n", "\n"))["project"]["goal"] == "new goal"


def test_flow_style_is_detected_as_not_surgically_editable():
    assert CF.editable(LEGACY_WITH_COMMENTS)
    assert not CF.editable("project: {name: x, goal: y}\n")
    assert CF.editable("hpc:\n  clusters: {}\n")          # an empty mapping is fine


# --------------------------------------------------------------------------- validation

def test_check_accepts_a_fresh_project(proj):
    text = open(proj.config_path, encoding="utf-8").read()
    r = CF.check(proj.raw_config, DEFAULT_CONFIG, text)
    assert r["ok"] and not r["errors"] and not r["unknown"]


def test_check_reports_unknown_keys_without_erroring(proj):
    r = CF.check({"project": {"name": "p"}, "my_lab": {"grid": 1}}, DEFAULT_CONFIG)
    assert r["ok"]                                        # unknown is not invalid
    assert [u["key"] for u in r["unknown"]] == ["my_lab.grid"]


def test_check_reports_redundant_keys(proj):
    r = CF.check({"context": {"max_chars": 12000}}, DEFAULT_CONFIG)
    assert "context.max_chars" in r["redundant"] and r["ok"]


def test_check_catches_a_type_mistake(proj):
    r = CF.check({"context": {"max_chars": "lots"}}, DEFAULT_CONFIG)
    assert not r["ok"]
    assert any(e["key"] == "context.max_chars" for e in r["errors"])


def test_check_warns_when_a_commented_reference_line_was_edited(proj):
    """Editing `#   max_runs_without_synthesis: 2` does nothing; saying so is the whole point."""
    with open(proj.config_path, encoding="utf-8") as f:
        text = f.read()
    assert "#   max_runs_without_synthesis: 5" in text
    edited = text.replace("max_runs_without_synthesis: 5", "max_runs_without_synthesis: 2")
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(edited)
    r = CF.check(yamlio.load(edited) or {}, DEFAULT_CONFIG, edited)
    assert r["ok"]                                   # it is not invalid, just ineffective
    assert any("has no effect" in w for w in r["warnings"])
    hit = r["edited_comments"][0]
    assert hit["key"] == "agent_policy.max_runs_without_synthesis"
    assert hit["value"] == "2" and hit["default"] == 5
    with Project(proj.root, sync=False) as p:
        assert p.config["agent_policy"]["max_runs_without_synthesis"] == 5   # genuinely unchanged


def test_check_does_not_flag_a_reference_line_that_shows_a_machine_value(machine, proj):
    """The reference block prints the value in force; the comment detector must agree with it.

    These two features contradicted each other at first: the block showed `default_cluster: duke` from the
    machine layer, and the detector — comparing against the built-in `null` — called it an edited comment.
    """
    machine("hpc:\n  default_cluster: duke\n  clusters:\n    duke:\n      ssh_host: login\n"
            "      paths:\n        workspace_root: /approved/ws\n")
    text = CF.render({"project": {"name": "p"}}, DEFAULT_CONFIG)
    assert "default_cluster: duke" in text
    r = CF.check(yamlio.load(text) or {}, DEFAULT_CONFIG, text)
    assert not r["edited_comments"], r["edited_comments"]
    assert r["ok"] and not r["warnings"]


def test_check_still_flags_a_genuinely_edited_reference_line(machine, proj):
    machine("hpc:\n  default_cluster: duke\n  clusters:\n    duke:\n      ssh_host: login\n"
            "      paths:\n        workspace_root: /approved/ws\n")
    text = CF.render({"project": {"name": "p"}}, DEFAULT_CONFIG)
    text = text.replace("#   max_chars: 12000", "#   max_chars: 99")
    r = CF.check(yamlio.load(text) or {}, DEFAULT_CONFIG, text)
    assert any(e["key"] == "context.max_chars" for e in r["edited_comments"])


def test_check_is_quiet_about_an_untouched_reference_block(proj):
    with open(proj.config_path, encoding="utf-8") as f:
        text = f.read()
    r = CF.check(proj.raw_config, DEFAULT_CONFIG, text)
    assert not r["edited_comments"] and not r["warnings"]


def test_uncommenting_a_reference_line_actually_applies_it(proj):
    with open(proj.config_path, encoding="utf-8") as f:
        text = f.read()
    text = text.replace("# agent_policy:\n#   max_runs_without_synthesis: 5",
                        "agent_policy:\n  max_runs_without_synthesis: 2")
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(text)
    with Project(proj.root, sync=False) as p:
        assert p.config["agent_policy"]["max_runs_without_synthesis"] == 2
        assert p.config["agent_policy"]["max_failed_runs_without_review"] == 3   # siblings inherited


def test_check_catches_duplicate_keys():
    text = "context:\n  max_chars: 1\ncoordination:\n  stale_task_hours: 2\ncontext:\n  max_chars: 3\n"
    dups = {d["key"]: d for d in CF.duplicate_keys(text)}
    # both the duplicated block and the leaf inside it are reported: either alone would under-explain it
    assert dups["context"]["lines"] == [1, 5]
    assert dups["context.max_chars"]["lines"] == [2, 6]
    r = CF.check(yamlio.load(text), DEFAULT_CONFIG, text)
    assert not r["ok"] and any("defined 2 times" in e["problem"] for e in r["errors"])


def test_duplicate_keys_ignores_comments_and_distinct_paths():
    text = ("# context:\n"
            "a:\n  x: 1\n"
            "b:\n  x: 2\n")
    assert CF.duplicate_keys(text) == []


def test_check_validates_hpc_profiles_structurally(proj):
    bad = {"hpc": {"default_cluster": "ghost",
                   "clusters": {"duke": {"transport": "ssh"}}}}
    r = CF.check(bad, DEFAULT_CONFIG)
    keys = {e["key"] for e in r["errors"]}
    assert "hpc.clusters.duke.ssh_host" in keys
    assert "hpc.clusters.duke.paths.workspace_root" in keys
    assert "hpc.default_cluster" in keys
    assert not r["ok"]


def test_check_accepts_a_complete_hpc_profile(proj):
    good = {"hpc": {"default_cluster": "duke", "clusters": {"duke": {
        "ssh_host": "login", "paths": {"workspace_root": "/approved/ws"},
        "site_specific_knob": 7}}}}
    r = CF.check(good, DEFAULT_CONFIG)
    assert r["ok"], r["errors"]
    assert not r["unknown"]            # user-named cluster sub-keys are not "unknown"


def test_check_validates_the_effective_cluster_not_just_the_project_block(machine, proj):
    """A project that inherits its cluster has almost nothing of its own — that is correct, not invalid.

    Found on a real project: after moving the duke profile to the machine layer, `config check` wrongly
    reported a missing ssh_host because it was validating the project's raw block.
    """
    machine("hpc:\n  default_cluster: duke\n  clusters:\n    duke:\n"
            "      ssh_host: login\n      paths:\n        workspace_root: /approved/ws\n")
    raw = {"hpc": {"clusters": {"duke": {"project": {"slug": "mine"}}}}}
    r = CF.check(raw, DEFAULT_CONFIG)
    assert r["ok"], r["errors"]
    eff = CF.effective(raw, DEFAULT_CONFIG)
    assert eff["hpc"]["clusters"]["duke"]["ssh_host"] == "login"
    assert eff["hpc"]["clusters"]["duke"]["project"]["slug"] == "mine"


def test_check_resolves_a_default_cluster_defined_on_the_machine(machine, proj):
    machine("hpc:\n  clusters:\n    duke:\n      ssh_host: login\n")
    r = CF.check({"hpc": {"default_cluster": "duke"}}, DEFAULT_CONFIG)
    assert not any(e["key"] == "hpc.default_cluster" for e in r["errors"])


# --------------------------------------------------------------------------- malformed input

def test_unparseable_config_gives_a_useful_error(proj):
    """The old failure mode was a raw ParserError traceback from every single command."""
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write("project: {name: x   # a comment inside braces\n")
    with pytest.raises(ResearchError) as e:
        proj.load_config()
    assert "could not be parsed" in str(e.value)
    assert "config check" in (e.value.hint or "")


def test_non_mapping_config_is_refused(proj):
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write("- a\n- b\n")
    with pytest.raises(ResearchError) as e:
        proj.load_config()
    assert "not a mapping" in str(e.value)


# --------------------------------------------------------------------------- migration

def test_migrate_preview_is_safe_and_changes_nothing(proj):
    legacy = yamlio.dump(proj.config)            # flow style, every effective key, as 0.2.0 wrote it
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(legacy)
    before = open(proj.config_path, "rb").read()
    prev = CF.migrate_preview(proj.config_path, DEFAULT_CONFIG)
    assert prev["safe"] and prev["effective_identical"]
    assert not prev["dropped_keys"] and not prev["changed_values"]
    assert prev["lines_after"] < prev["lines_before"] or "{" not in prev["new"]
    assert prev["diff"]
    assert open(proj.config_path, "rb").read() == before      # a preview writes nothing


def test_migration_preserves_the_effective_config_exactly(proj):
    proj.config["my_lab"] = {"grid": "0.25deg"}
    proj.config["context"]["max_chars"] = 44
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(yamlio.dump(proj.config))
    with Project(proj.root, sync=False) as p1:
        before = dict(p1.config)
    prev = CF.migrate_preview(proj.config_path, DEFAULT_CONFIG)
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(prev["new"])
    with Project(proj.root, sync=False) as p2:
        assert p2.config == before
        assert p2.config["my_lab"] == {"grid": "0.25deg"}
        assert p2.config["context"]["max_chars"] == 44


def test_migrate_is_idempotent(proj):
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(yamlio.dump(proj.config))
    first = CF.migrate_preview(proj.config_path, DEFAULT_CONFIG)
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(first["new"])
    second = CF.migrate_preview(proj.config_path, DEFAULT_CONFIG)
    assert not second["changed"]


# --------------------------------------------------------------------------- write paths

def test_project_set_preserves_comments(proj):
    """`research project set --goal` must not destroy the user's notes — the original bug."""
    with open(proj.config_path, encoding="utf-8") as f:
        text = f.read()
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(text.replace("project:", "# MY NOTE: do not reformat this file\nproject:"))
    p2 = Project(proj.root, sync=False)
    try:
        p2.update_project(goal="a brand new goal")
    finally:
        p2.close()
    after = open(proj.config_path, encoding="utf-8").read()
    assert "# MY NOTE: do not reformat this file" in after
    assert yamlio.load(after)["project"]["goal"] == "a brand new goal"


def test_in_place_config_mutation_still_works(proj):
    """The `p.config[...] = x; p.save_config()` style used across the codebase."""
    proj.config["orchestration"]["enabled"] = True
    proj.config["my_new_thing"] = {"a": 1}
    proj.save_config()
    with Project(proj.root, sync=False) as p2:
        assert p2.config["orchestration"]["enabled"] is True
        assert p2.config["my_new_thing"] == {"a": 1}
        assert p2.config["agent_policy"]["max_runs_without_synthesis"] == 5   # untouched default


def test_save_config_writes_only_what_changed(proj):
    before = open(proj.config_path, encoding="utf-8").read()
    proj.config["context"]["max_chars"] = 31337
    proj.save_config()
    after = open(proj.config_path, encoding="utf-8").read()
    added = [l for l in after.split("\n") if l not in before.split("\n") and l.strip()]
    assert any("31337" in l for l in added)
    assert len(added) <= 2                       # the key and at most its parent block


def test_save_config_on_a_legacy_flow_file_does_not_corrupt_it(proj):
    """A flow-style file cannot be edited surgically, so it is rewritten sparsely rather than mangled."""
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(yamlio.dump(proj.config))
    p2 = Project(proj.root, sync=False)
    try:
        p2.update_project(goal="set on a legacy file")
    finally:
        p2.close()
    with Project(proj.root, sync=False) as p3:
        assert p3.config["project"]["goal"] == "set on a legacy file"
        assert p3.config["agent_policy"]["max_runs_without_synthesis"] == 5
    assert CF.editable(open(proj.config_path, encoding="utf-8").read())   # now block style


def test_repeated_project_set_does_not_grow_the_file(proj):
    p2 = Project(proj.root, sync=False)
    try:
        for i in range(5):
            p2.update_project(goal=f"goal {i}")
    finally:
        p2.close()
    text = open(proj.config_path, encoding="utf-8").read()
    assert text.count("goal:") == 1
    assert yamlio.load(text)["project"]["goal"] == "goal 4"


# --------------------------------------------------------------------------- CLI / RPC round trips

def _cli(cwd, *args, env=None):
    return subprocess.run([sys.executable, "-m", "research_cli", *args], cwd=cwd,
                          capture_output=True, text=True, env={**os.environ, **(env or {})})


def test_cli_config_surface(proj):
    root = proj.root
    proj.close()
    r = _cli(root, "config", "show", "--json")
    assert r.returncode == 0 and "project" in json.loads(r.stdout)
    r = _cli(root, "config", "show", "--effective", "--json")
    assert json.loads(r.stdout)["agent_policy"]["max_runs_without_synthesis"] == 5
    r = _cli(root, "config", "show", "--sources", "--json")
    assert json.loads(r.stdout)["project.name"]["source"] == "project"
    r = _cli(root, "config", "check", "--json")
    assert r.returncode == 0 and json.loads(r.stdout)["ok"]
    r = _cli(root, "config", "migrate")
    assert r.returncode == 0 and "already sparse" in r.stdout


def test_cli_config_check_exits_nonzero_on_a_real_error(proj):
    root = proj.root
    with open(proj.config_path, "a", encoding="utf-8") as f:
        f.write("\ncontext:\n  max_chars: not-a-number\n")
    proj.close()
    r = _cli(root, "config", "check")
    assert r.returncode == 1 and "max_chars" in r.stdout


def test_cli_config_migrate_dry_run_then_apply(proj):
    root = proj.root
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write(yamlio.dump(proj.config))
    proj.close()
    before = open(os.path.join(root, ".research", "config.yaml"), "rb").read()
    r = _cli(root, "config", "migrate")
    assert r.returncode == 0 and "--- a/config.yaml" in r.stdout
    assert open(os.path.join(root, ".research", "config.yaml"), "rb").read() == before
    r = _cli(root, "config", "migrate", "--apply")
    assert r.returncode == 0, r.stderr
    assert os.path.exists(os.path.join(root, ".research", "config.yaml.before-migrate"))
    with Project(root, sync=False) as p:
        assert p.config["agent_policy"]["max_runs_without_synthesis"] == 5


def test_rpc_update_project_preserves_comments(proj, repo):
    import io
    from research.rpc import serve
    with open(proj.config_path, encoding="utf-8") as f:
        text = f.read()
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write("# EXTENSION TEST NOTE\n" + text)
    proj.close()
    out = io.StringIO()
    serve(str(repo), io.StringIO(json.dumps(
        {"id": 1, "method": "update_project", "params": {"goal": "set over rpc"}}) + "\n"), out)
    assert "error" not in out.getvalue().splitlines()[-1]
    after = open(os.path.join(str(repo), ".research", "config.yaml"), encoding="utf-8").read()
    assert "# EXTENSION TEST NOTE" in after
    assert yamlio.load(after)["project"]["goal"] == "set over rpc"


def test_init_is_idempotent_on_a_customized_config(proj):
    with open(proj.config_path, encoding="utf-8") as f:
        text = f.read()
    with open(proj.config_path, "w", encoding="utf-8") as f:
        f.write("# KEEP ME\n" + text.replace("name: ", "name: "))
    before = open(proj.config_path, "rb").read()
    root = proj.root
    proj.close()
    p2 = Project.init(root)
    try:
        assert open(proj.config_path, "rb").read() == before
        assert p2.config["context"]["max_chars"] == 12000
    finally:
        p2.close()
