"""A realistic multi-experiment fixture for Scientist Guide tests: competing findings, a later review correction, an
archived figure and a derived-data figure. Domain-agnostic (an identifiability study of a generic forward model)."""
import base64
import os

from research import evidence as E
from research import runs as R
from research import services as S
from research import verification as V
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

from test_research_os import as_agent, as_human


def build(proj, monkeypatch):
    root = proj.root
    q = S.create_question(proj, "Which perturbations of the model can the measurements identify?")["id"]
    e1 = S.create_experiment(proj, "Baseline reconstruction", question=q, hypothesis="Uniform changes are recoverable")["id"]
    e2 = S.create_experiment(proj, "Sensitivity to structured perturbations", question=q,
                             hypothesis="Structured changes alter the data far less than uniform ones")["id"]
    r1 = R.create_run(proj, e1, command="sim", status="completed")["id"]
    R.log_metric(proj, "rmse", 12.8, unit="HU", run=r1)
    r2 = R.create_run(proj, e2, command="sim", status="completed")["id"]
    R.log_metric(proj, "sensitivity_ratio", 0.04, run=r2)
    os.makedirs(os.path.join(root, "out"), exist_ok=True)
    with open(os.path.join(root, "out", "sens.png"), "wb") as f:
        f.write(PNG)
    art = R.register_artifact(proj, os.path.join(root, "out", "sens.png"), run=r2)["id"]
    s1 = S.synthesize(proj, e1, what_happened="Reconstructed the uniform case.", interpretation="Uniform change recoverable.")["id"]
    s2 = S.synthesize(proj, e2, what_happened="Structured perturbations changed the data weakly.",
                      interpretation="Weak observability.", limitations="One phantom.")["id"]
    as_agent(proj, "claude", "implementer", monkeypatch)
    f1 = S.create_finding(proj, "Uniform perturbation recoverable", statement="RMSE is 12.8 HU for the uniform case",
                          supports=[r1])["id"]
    E.bind(proj, f1, run=r1, metric="rmse", claim="12.8", unit="HU")
    f2 = S.create_finding(proj, "Structured perturbation weakly observable",
                          statement="The sensitivity ratio is 0.04", supports=[r2, art])["id"]
    E.bind(proj, f2, run=r2, metric="sensitivity_ratio", claim="0.04")
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, f1, "supported", "Interpretation reasonable for the stated setup.")
    V.review_finding(proj, f2, "supported", "Ratio consistent with the design; single phantom noted.")
    as_human(proj)
    return {"q": q, "e1": e1, "e2": e2, "r1": r1, "r2": r2, "art": art, "s1": s1, "s2": s2, "f1": f1, "f2": f2}


def write_chapter(proj, name="investigations/sensitivity.qmd", body=None):
    gd = os.path.join(proj.root, "docs", "scientist-guide")
    path = os.path.join(gd, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body or "# Sensitivity\n\nStructured perturbations change the data weakly [@x]{#clm-0001 .claim}.\n")
    return name


def make_figure(proj, same_as_artifact=True):
    gd = os.path.join(proj.root, "docs", "scientist-guide", "figures")
    os.makedirs(gd, exist_ok=True)
    with open(os.path.join(gd, "sens.png"), "wb") as f:
        f.write(PNG if same_as_artifact else PNG + b"\0")
    return "figures/sens.png"
