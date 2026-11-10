"""Hardcoded provenance in generated reports: the same generator run against two different fixture datasets. A report that is read from the data is clean on both;
one with typed-in run ids, experiment ids, numbers or run history is caught on the dataset it was not written for, and before a promotion integrates it."""
import os
import subprocess

import pytest

import research
from conftest import git
from research import guide_quarto as GQ, promotion as PR, provenance as PV, runs as R, services, workspace as W
from research.store import Project
from research.util import PolicyBlocked

CHAPTER = "investigations/results.qmd"


def dataset(path, name, losses):
    """A project with one experiment whose runs recorded the given final losses."""
    p = research.init(str(path), name=name)
    exp = services.create_experiment(p, f"{name} experiment", hypothesis="h")["id"]
    ids = []
    for v in losses:
        r = R.create_run(p, exp, command="train", backend="local", status="completed", exit_code=0)["id"]
        R.log_metric(p, "loss", v, run=r)
        ids.append(r)
    return p, exp, ids


@pytest.fixture
def two(tmp_path):
    a, ea, ra = dataset(tmp_path / "a", "A", [0.9, 0.5, 0.12])          # three runs
    b, eb, rb = dataset(tmp_path / "b", "B", [0.77])                    # one run, another value
    yield (a, ea, ra), (b, eb, rb)
    a.close()
    b.close()


def good_report(p, exp, runs):
    """Reads everything from the project: the number from the metric, the attribution from the records it used. Ids only in a researcher-only block."""
    final = R.summary_metrics(p, runs[-1])["loss"]["value"]
    body = "\n".join(f"- {r}: {R.summary_metrics(p, r)['loss']['value']:.3g}" for r in runs)
    return (f"# Results\n\nThe final training loss was {final:.3g} after {len(runs)} run(s).\n\n"
            f"::: {{.content-visible when-profile=\"researcher\"}}\nGenerated from {exp}:\n{body}\n:::\n")


def hardcoded_report(*_):
    """What PRAVAH's generator did: the run, experiment and numbers are typed in, plus a claim about another run."""
    return ("# Results\n\nThe final training loss was 0.12 after 3 runs.\n\n"
            "::: {.content-visible when-profile=\"researcher\"}\nGenerated from EXP-001:\n- RUN-0003: 0.12\nRUN-0002 was unstable and diverged.\n:::\n")


def leaky_report(p, exp, runs):
    return good_report(p, exp, runs).replace("The final training loss", f"Run {runs[0]} shows that the final training loss", 1)


def errors(p, text):
    return {i["code"] for i in GQ.text_issues(p, CHAPTER, text) if i["severity"] == "error"}


def test_a_data_driven_report_is_clean_on_both_datasets(two):
    for p, exp, runs in two:
        assert errors(p, good_report(p, exp, runs)) == set()


def test_hardcoded_attribution_is_stale_on_the_dataset_it_was_not_written_for(two):
    (a, ea, ra), (b, eb, rb) = two
    assert errors(a, hardcoded_report()) == set()                       # on A the typed-in ids happen to exist: it looks fine, which is the trap
    assert errors(b, hardcoded_report()) == {"stale_attribution"}       # on B RUN-0003 / RUN-0002 do not exist: invented history
    msg = next(i["message"] for i in GQ.text_issues(b, CHAPTER, hardcoded_report()) if i["code"] == "stale_attribution")
    assert "RUN-0003" in msg and "RUN-0002" in msg and "never typed in" in msg


def test_internal_identifiers_in_scientific_prose_stay_an_error_on_both(two):
    for p, exp, runs in two:
        assert "identifier_in_scientific_text" in errors(p, leaky_report(p, exp, runs))


def claim_codes(p, runs, statement):
    """Register an observation citing the last run's loss and return the codes of the issues validation reports on THAT claim."""
    GQ.init_quarto(p)
    if not os.path.exists(os.path.join(PV.guide_dir(p), CHAPTER)):
        GQ.chapter_add(p, CHAPTER, "Results")
    cid = PV.claim_add(p, statement, "observation", CHAPTER, [f"{runs[-1]}#loss@last"])["id"]
    return {i["code"] for i in PV.validate(p)["claims"][cid]["issues"]}


def test_a_fixed_number_in_a_claim_is_caught_when_the_data_differ(two):
    (a, ea, ra), (b, eb, rb) = two
    typed = "The final loss reached 0.12 in the last run."
    assert "stated_value_mismatch" not in claim_codes(a, ra, typed)                          # true for A (0.12) ...
    assert "stated_value_mismatch" in claim_codes(b, rb, typed)                              # ... a fixed value on B (0.77)
    assert "stated_value_mismatch" not in claim_codes(b, rb, "The final loss reached 0.77 in the last run.")      # read from B's data: fine
    assert "stated_value_mismatch" not in claim_codes(b, rb, "The loss decreased over training.")                  # no number quoted: not judged


# ------------------------------------------------------------------------------------------------ promotion gate
@pytest.fixture
def world(repo, tmp_path):
    Project.init(str(repo), name="d").close()
    W.enable(str(repo), str(tmp_path / "ledger"))
    p = Project(str(repo))
    p.author = {"author_type": "human", "author_name": "alice"}
    p.save_config({"governance.approval.require_pin": False, "promotion.require_supported_finding": False, "promotion.test_command": "true"})
    p.config = p.load_config()
    e = services.create_experiment(p, "x", question=services.create_question(p, "q")["id"], hypothesis="h")
    ws = W.create(p, e["id"], name="rep")
    r = R.create_run(p, e["id"], command="t", backend="local", status="completed", exit_code=0)["id"]
    yield p, ws, e["id"], r
    p.close()


def review(p, pid):
    me, p.author = p.author, {"author_type": "human", "author_name": "bob"}          # an independent reviewer, as the gates require
    try:
        PR.review(p, pid, "approve", "reviewed")
    finally:
        p.author = me


def commit(path, name, text):
    full = os.path.join(path, name)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(text)
    git(path, "add", name)
    git(path, "commit", "-qm", f"add {name}")


def candidate(p, ws, **files):
    for name, text in files.items():
        commit(ws["path"], name.replace("__", "/"), text)
    return PR.prepare(p, ws["id"], paths=[n.replace("__", "/") for n in files])["id"]


def test_a_promotion_with_hardcoded_scientific_content_is_refused_before_integration(world):
    p, ws, exp, run = world
    pid = candidate(p, ws, **{"reports__results.qmd": hardcoded_report()})
    g = PR.show(p, pid)["gates"]["scientific_content"]
    assert g["state"] == "failed" and "stale" not in g["detail"] and "RUN-0003" in g["detail"]
    PR.verify(p, pid)
    review(p, pid)
    with pytest.raises(PolicyBlocked) as e:
        PR.approve(p, pid, "ship")
    assert "scientific content provenance" in str(e.value)
    assert not PR.show(p, pid)["classification"]["integration_ready"]


def test_a_data_driven_chapter_and_code_only_promotions_are_not_blocked(world):
    p, ws, exp, run = world
    ok = f"# Results\n\nThe loss was recorded.\n\n::: {{.content-visible when-profile=\"researcher\"}}\nFrom {run}.\n:::\n"
    pid = candidate(p, ws, **{"reports__results.qmd": ok})
    assert PR.show(p, pid)["gates"]["scientific_content"]["state"] == "passed"
    commit(ws["path"], "solver.py", "x = 1\n")
    code = PR.prepare(p, ws["id"], paths=["solver.py"])["id"]
    assert PR.show(p, code)["gates"]["scientific_content"]["state"] == "not_applicable"      # unrelated code is never held up
    PR.verify(p, code)
    review(p, code)
    PR.approve(p, code, "ship")


def test_agents_are_told_to_generate_results_from_data_and_never_hardcode_provenance():
    from research import agentsmd
    block = agentsmd.block_text() if hasattr(agentsmd, "block_text") else open(os.path.join(os.path.dirname(research.__file__), "templates", "AGENTS.block.md")).read()
    for needle in ("generated from the recorded data", "Never hardcode a result", "researcher-only block", "research guide validate"):
        assert needle in block
