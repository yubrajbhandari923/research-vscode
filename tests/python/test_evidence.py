import json
import os

import pytest

from research import evidence as E
from research import guide as G
from research import orchestrate as O
from research import report as RP
from research import runs as R
from research import services as S
from research import verification as V
from research.util import PolicyBlocked, ResearchError
from research_cli.main import main
from test_research_os import as_agent


def _report(p):
    r = RP.write_report(p, fmt="md")
    return open(os.path.join(p.root, r["paths"][0])).read()


def mkrun(p, exp, rmse, unit="m", status="completed", seed=None):
    r = R.create_run(p, exp, command="sim", status=status, parameters={"seed": seed} if seed is not None else {})
    if rmse is not None:
        R.log_metric(p, "rmse", rmse, unit=unit, run=r["id"])
    return r["id"]


@pytest.fixture
def exp(proj):
    return S.create_experiment(proj, "e")["id"]


def finding(p, runs):
    return S.create_finding(p, "RMSE result", statement="RMSE is 12.8 m", supports=runs)["id"]


def test_numerical_mismatch_blocks_supported(proj, exp, monkeypatch):
    run = mkrun(proj, exp, 12.8)
    f = finding(proj, [run])
    b = E.bind(proj, f, run=run, metric="rmse", claim="8.2", unit="m")
    assert b["verification"]["status"] == "mismatch" and "12.8" in b["verification"]["issues"][0]
    as_agent(proj, "claude", "verifier", monkeypatch)
    with pytest.raises(PolicyBlocked, match="numerical evidence does not verify"):
        V.review_finding(proj, f, "supported", "looks plausible to me, the paper rounds it")
    assert proj.get("finding", f)["status"] == "preliminary"
    # humans are blocked too, via the direct status path
    with pytest.raises(PolicyBlocked):
        S.set_status(proj, f, "supported")
    V.review_finding(proj, f, "needs_work", "number is wrong")  # non-support verdicts still work


def test_correct_binding_then_independent_review_still_required(proj, exp, monkeypatch):
    run = mkrun(proj, exp, 12.8)
    as_agent(proj, "claude", "implementer", monkeypatch)
    f = finding(proj, [run])
    b = E.bind(proj, f, run=run, metric="rmse", claim="12.8", unit="m")
    assert b["verification"]["status"] == "verified"
    with pytest.raises(PolicyBlocked, match="independent"):
        V.review_finding(proj, f, "supported", "self review")
    as_agent(proj, "claude", "verifier", monkeypatch)
    with pytest.raises(ResearchError, match="interpretation"):
        V.review_finding(proj, f, "supported")  # verified numbers are not an interpretation review
    r = V.review_finding(proj, f, "supported", "Interpretation reasonable for synthetic data; no leakage.")
    assert r["finding"]["status"] == "supported"
    assert r["review"]["details"]["numeric_verification"]["status"] == "verified"


def test_read_binding_takes_value_from_evidence(proj, exp):
    run = mkrun(proj, exp, 12.8)
    b = E.bind(proj, finding(proj, [run]), run=run, metric="rmse")
    assert b["claim"]["source"] == "read" and b["claim"]["value"] == 12.8 and b["verification"]["status"] == "verified"


def test_derived_metric_and_wrong_arithmetic(proj, exp):
    r1, r2 = mkrun(proj, exp, 12.8), mkrun(proj, exp, 8.0)
    f = finding(proj, [r1, r2])
    good = E.bind(proj, f, derive="relative_improvement", inputs=[f"{r1}:rmse", f"{r2}:rmse"], claim="37.5", unit="%")
    assert good["verification"]["status"] == "verified"
    bad = E.bind(proj, f, derive="relative_improvement", inputs=[f"{r1}:rmse", f"{r2}:rmse"], claim="40.0", unit="%")
    assert bad["verification"]["status"] == "mismatch"
    assert E.verify_finding(proj, f)["status"] == "mismatch"
    with pytest.raises(ResearchError):
        E.bind(proj, f, derive="__import__('os')", inputs=[f"{r1}:rmse"])  # no free-form code
    # unit conflicts are explicit
    r3 = mkrun(proj, exp, 3.0, unit="km")
    v = E.bind(proj, f, derive="difference", inputs=[f"{r1}:rmse", f"{r3}:rmse"], claim="1")["verification"]
    assert v["status"] == "unverifiable" and "incompatible" in v["issues"][0]


def test_missing_failed_nan_and_ambiguous(proj, exp):
    run = mkrun(proj, exp, 12.8)
    f = finding(proj, [run])
    assert E.bind(proj, f, run=run, metric="nope", claim="1")["verification"]["status"] == "missing"
    assert E.bind(proj, f, run="RUN-9999", metric="rmse", claim="1")["verification"]["status"] == "missing"
    failed = mkrun(proj, exp, 5.0, status="failed")
    assert E.resolve_metric(proj, failed, "rmse")["status"] == "unverifiable"
    nan = mkrun(proj, exp, float("nan"))
    assert E.resolve_metric(proj, nan, "rmse")["status"] == "unverifiable"
    multi = mkrun(proj, exp, None)
    for s, v in ((1, 3.0), (2, 2.0)):
        R.log_metric(proj, "loss", v, step=s, run=multi)
    assert E.resolve_metric(proj, multi, "loss")["status"] == "unverifiable"  # must pick a step
    assert E.resolve_metric(proj, multi, "loss", step=1)["value"] == 3.0
    assert E.resolve_metric(proj, multi, "loss", select="min")["value"] == 2.0
    # a claim cannot be 'read' from nothing
    with pytest.raises(ResearchError):
        E.bind(proj, f, run=run, metric="nope")


def test_artifact_mutation_and_missing(proj, exp, repo):
    run = mkrun(proj, exp, 12.8)
    (repo / "res.csv").write_text("rmse,12.8\n")
    a = R.register_artifact(proj, "res.csv", run=run)
    f = finding(proj, [run])
    E.bind(proj, f, run=run, metric="rmse", claim="12.8", artifact=a["id"])
    assert E.verify_finding(proj, f)["status"] == "verified"
    (repo / "res.csv").write_text("rmse,8.2\n")
    v = E.verify_finding(proj, f)
    assert v["status"] == "stale" and E.artifact_integrity(proj, a["id"])["status"] == "modified"
    assert [w["id"] for w in E.integrity_scan(proj)] == [a["id"]]  # historical warning for a cited, changed artifact
    assert R.get_artifact(proj, a["id"])["hash"] == a["hash"]  # registered metadata never rewritten
    os.remove(repo / "res.csv")
    assert E.artifact_integrity(proj, a["id"])["status"] == "missing"


def test_large_artifact_is_unhashed_then_pinned(proj, exp, repo):
    proj.config["artifacts"]["hash_max_bytes"] = 4
    run = mkrun(proj, exp, 1.0)
    (repo / "big.bin").write_bytes(b"0123456789")
    a = R.register_artifact(proj, "big.bin", run=run)
    assert a["hash"] is None and E.artifact_integrity(proj, a["id"])["status"] == "unhashed"
    E.pin_artifact(proj, a["id"])
    assert E.artifact_integrity(proj, a["id"])["status"] == "ok"
    (repo / "big.bin").write_bytes(b"01234567XX")
    assert E.artifact_integrity(proj, a["id"])["status"] == "modified"
    assert R.get_artifact(proj, a["id"])["hash"] is None


def test_test_partition_leak_blocked_until_selection(proj, exp):
    run = mkrun(proj, exp, 1.5)
    f = finding(proj, [run])
    O.tuning_declare(proj, exp, "min", {"a": "0:1"}, "grid", "min val", 0, 3)
    b = E.bind(proj, f, run=run, metric="rmse", partition="test")
    assert b["verification"]["status"] == "mismatch" and "leakage" in b["verification"]["issues"][0]


def test_provenance_flags_limitations(proj, exp):
    run = mkrun(proj, exp, 1.0)
    pv = E.provenance(proj, run)
    assert any("seed" in x for x in pv["limitations"]) and any("partition" in x for x in pv["limitations"])
    run2 = mkrun(proj, exp, 1.0, seed=7)
    assert E.provenance(proj, run2)["seeds"] == {"seed": 7}


def test_legacy_and_qualitative_findings_unchanged(proj, exp, monkeypatch):
    run = mkrun(proj, exp, 12.8)
    f = finding(proj, [run])
    assert E.verify_finding(proj, f)["status"] == "no_bindings"
    assert E.suggest(proj, f)["unbound_numbers"] == ["12.8"]
    as_agent(proj, "claude", "verifier", monkeypatch)
    assert V.review_finding(proj, f, "supported", "ok")["finding"]["status"] == "supported"
    text = _report(proj)  # report still builds; no evidence table for unbound findings
    assert f in text and "Verification" not in text


def test_report_shows_verified_and_failing_numbers(proj, exp, repo):
    run = mkrun(proj, exp, 12.8)
    f = finding(proj, [run])
    E.bind(proj, f, run=run, metric="rmse", claim="12.8", unit="m")
    E.bind(proj, f, run=run, metric="rmse", claim="8.2", unit="m")
    text = _report(proj)
    assert "VERIFIED" in text and "MISMATCH" in text and run in text


def test_supersede_corrects_without_editing_history(proj, exp, monkeypatch):
    run = mkrun(proj, exp, 12.8)
    f = finding(proj, [run])
    bad = E.bind(proj, f, run=run, metric="rmse", claim="8.2")
    E.bind(proj, f, run=run, metric="rmse", claim="12.8", supersedes=bad["id"])
    assert [b["id"] for b in E.list_bindings(proj, f)] == [f"{f}/B2"]
    assert len(E.list_bindings(proj, f, include_superseded=True)) == 2
    assert E.verify_finding(proj, f)["status"] == "verified"


def test_guide_status_exposes_numerical_state(proj, exp):
    run = mkrun(proj, exp, 12.8)
    S.synthesize(proj, exp, what_happened="ran")
    f = finding(proj, [exp, run])
    E.bind(proj, f, run=run, metric="rmse", claim="9")
    fs = G.status(proj)["pending"][0]["findings"]
    assert fs[0]["status"] == "preliminary" and fs[0]["numerical"] == "mismatch"
    E.bind(proj, f, run=run, metric="rmse", claim="12.8", supersedes=f"{f}/B1")
    assert G.status(proj)["pending"][0]["findings"][0]["numerical"] == "verified"


def test_sources_keep_identity_examination_and_support_separate(proj, exp):
    run = mkrun(proj, exp, 1.0)
    f = finding(proj, [run])
    with pytest.raises(ResearchError):
        E.source_add(proj, doi="not-a-doi")
    s = E.source_add(proj, doi="https://doi.org/10.1000/xyz123", title="T")
    assert s["identity"] == "format-valid" and s["doi"] == "10.1000/xyz123" and s["examined"] is None
    with pytest.raises(ResearchError, match="examined"):
        E.source_mark(proj, s["id"], finding=f, verdict="supports")
    s = E.source_mark(proj, s["id"], examined=True, locator="Fig. 3", finding=f, verdict="partially_supports", note="only the trend")
    assert s["identity"] == "format-valid" and s["examined"]["locator"] == "Fig. 3" and s["assessments"][0]["verdict"] == "partially_supports"
    assert E.source_mark(proj, s["id"], identity_verified=True)["identity"] == "verified"


def test_cli_roundtrip(proj, exp, capsys):
    run = mkrun(proj, exp, 12.8)
    f = finding(proj, [run])
    assert main(["evidence", "bind", f, "--run", run, "--metric", "rmse", "--claim", "8.2"]) == 1
    assert main(["evidence", "verify", f]) == 1
    assert main(["evidence", "value", run, "--metric", "rmse"]) == 0
    assert "12.8" in capsys.readouterr().out
