"""Build a realistic demo project and dump the RPC payloads the Control Center renders (for screenshots).

    python scripts/make_preview_fixture.py <workdir> <out.json> [--empty]

Populated: shared-ledger mode, 5 experiments, ~16 runs (completed / failed / running), metrics, findings in several states,
two plans with ~9 tasks (one blocked, one in progress), 3 experiment workspaces and 3 promotion candidates at different gates.
`--empty` writes the same payloads for a freshly initialised project. The populated project is left in <workdir>/ct-recon
so it can be opened in VS Code."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "core"))
from research import control, plans, promotion as PR, registry, runs, services, workspace as W   # noqa: E402
from research.store import Project   # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
EMPTY = "--empty" in sys.argv
work, out = os.path.abspath(args[0]), args[1]
os.environ["XDG_CONFIG_HOME"] = os.path.join(work, "xdg-empty")   # keep the demo independent of the developer machine
os.makedirs(work, exist_ok=True)
repo = os.path.join(work, "ct-recon" if not EMPTY else "empty")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "d", "GIT_AUTHOR_EMAIL": "d@d", "GIT_COMMITTER_NAME": "d", "GIT_COMMITTER_EMAIL": "d@d"}
g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=ENV)
os.makedirs(repo)
g("init", "-q", "-b", "main")
open(os.path.join(repo, "train.py"), "w").write("print('loss=0.12')\n")
g("add", ".")
g("commit", "-qm", "init")
goal = "Find the regularization strength that suppresses streak artifacts without blurring edges."
Project.init(repo, name="CT reconstruction", goal=goal).close()
W.enable(repo, os.path.join(work, ("ct-ledger" if not EMPTY else "empty-ledger")))
p = Project(repo)
p.author = {"author_type": "human", "author_name": "demo"}


def write(ws, name, body, msg):
    open(os.path.join(ws["path"], name), "w").write(body)
    g("add", name, cwd=ws["path"])
    g("commit", "-qm", msg, cwd=ws["path"])
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws["path"], capture_output=True, text=True).stdout.strip()


if not EMPTY:
    registry.apply(p, {"agent_policy.max_runs_without_synthesis": "8", "promotion.test_command": "test -f prior.py"}, reason="longer sweeps")
    q1 = services.create_question(p, "Which regularization strength balances artifact suppression and edge sharpness?")
    q2 = services.create_question(p, "Does a learned prior beat total variation at low dose?")
    q3 = services.create_question(p, "Is the error dominated by the detector noise model?")
    e1 = services.create_experiment(p, "Regularization sweep", question=q1["id"], hypothesis="An intermediate lambda minimizes reconstruction error", planned_runs=6, metrics=["final_error"])
    e2 = services.create_experiment(p, "Learned prior vs TV", question=q2["id"], hypothesis="A learned prior beats total variation at low dose", planned_runs=4, metrics=["psnr"])
    e3 = services.create_experiment(p, "Poisson vs Gaussian noise model", question=q3["id"], hypothesis="A Poisson likelihood removes the dose-dependent bias", planned_runs=3, metrics=["bias"])
    e4 = services.create_experiment(p, "Warm-start from FBP", question=q1["id"], hypothesis="Warm-starting halves iterations to convergence", planned_runs=2, metrics=["iters"])
    e5 = services.create_experiment(p, "Edge-aware TV weights", question=q1["id"], hypothesis="Spatially varying TV weights preserve edges", status="proposed")
    for lam in (0.05, 0.1, 0.25, 0.5, 1.0, 2.0):
        r = runs.exec_run(p, e1["id"], f"echo lambda={lam}", parameters={"lambda": lam}, tee=False)
        runs.log_metric(p, "final_error", round(0.012 + 0.03 * abs(lam - 0.5) ** 1.3, 4), run=r["id"])
    services.synthesize(p, e1["id"], what_happened="Six values of lambda between 0.05 and 2.0.", what_worked="Error is minimal near lambda 0.5.",
                        interpretation="The optimum is broad; 0.25–1.0 is within noise of each other.", mark="completed")
    for i, (k, v) in enumerate((("tv", 31.2), ("tv", 31.6), ("learned", 33.4))):
        r = runs.exec_run(p, e2["id"], f"echo prior={k}", parameters={"prior": k, "dose": 0.1}, tee=False)
        runs.log_metric(p, "psnr", v, run=r["id"])
    r = runs.exec_run(p, e3["id"], "exit 1", parameters={"model": "poisson"}, tee=False) if False else runs.create_run(p, e3["id"], "python noise.py", parameters={"model": "poisson"}, status="failed", exit_code=1, backend="local")
    r = runs.create_run(p, e3["id"], "python noise.py", parameters={"model": "gaussian"}, status="completed", backend="local", exit_code=0)
    runs.log_metric(p, "bias", 0.041, run=r["id"])
    for n in (1, 2):
        r = runs.create_run(p, e4["id"], "python warm.py", parameters={"init": "fbp", "seed": n}, status="running", backend="slurm", slurm_job_id=str(48210 + n))
    services.create_finding(p, "Lambda near 0.5 minimizes reconstruction error", supports=[e1["id"]], confidence="medium", status="supported", limitations="Single phantom, one noise level.")
    services.create_finding(p, "A learned prior gains about 2 dB over TV at 10% dose", supports=[e2["id"]], confidence="low")
    services.create_finding(p, "Gaussian noise model leaves a dose-dependent bias", supports=[e3["id"]], confidence="medium")
    services.create_finding(p, "Warm-starting from FBP does not change the optimum", contradicts=[e4["id"]], confidence="low", status="contradicted")
    services.create_decision(p, "Fix lambda at 0.5 for all later comparisons", reason="Broad optimum; removes a nuisance variable.", supporting_findings=["F-001"], experiments=[e1["id"]])
    pl1 = plans.create_plan(p, "Compare priors", objective="Decide between TV and a learned prior at low dose")
    t = [plans.create_task(p, pl1["id"], title, goal="g", depends_on=dep) for title, dep in (
        ("Implement learned prior", None), ("Run low-dose comparison", None), ("Validate on held-out phantom", None), ("Write the comparison note", None))]
    plans.start_task(p, t[0]["id"])
    plans.complete_task(p, t[0]["id"], result="Prior module merged into the experiment workspace")
    plans.start_task(p, t[1]["id"])
    plans.block_task(p, t[2]["id"], "Held-out phantom data has not arrived")
    pl2 = plans.create_plan(p, "Noise model audit", objective="Quantify dose-dependent bias")
    t2 = [plans.create_task(p, pl2["id"], title, goal="g") for title in ("Collect Poisson baseline", "Fit bias vs dose", "Review the fit with a second agent")]
    plans.start_task(p, t2[0]["id"], assigned_role="analyst")
    ws1 = W.create(p, e2["id"], name="learned-prior")
    ws2 = W.create(p, e3["id"], name="poisson-model")
    ws3 = W.create(p, e5["id"], name="edge-weights")
    W.link_task(p, ws1["id"], t[0]["id"])
    W.link_task(p, ws2["id"], t2[0]["id"])
    sha1 = write(ws1, "prior.py", "def prior(x):\n    return 0.5 * x\n", "Add learned prior")
    sha2 = write(ws2, "prior.py", "def likelihood(y, x):\n    return y * x\n", "Add Poisson likelihood")
    write(ws3, "weights.py", "def weights(img):\n    return img\n", "Sketch edge weights")
    open(os.path.join(ws1["path"], "notes.txt"), "a").write("wip\n")
    c1 = PR.prepare(p, ws1["id"], commits=[sha1], title="Learned prior module")
    PR.verify(p, c1["id"])
    c2 = PR.prepare(p, ws2["id"], commits=[sha2], title="Poisson likelihood")
    c3 = PR.prepare(p, ws3["id"], commits=[subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws3["path"], capture_output=True, text=True).stdout.strip()], title="Edge-aware weights (draft)")
    PR.abandon(p, c3["id"]) if hasattr(PR, "abandon") else None
    cand = c1
else:
    ws1 = None

data = {"home": control.home(p), "work": control.work(p), "research": control.research(p), "settings": {}}
for cat in ("policy", "sandbox", "git"):
    data["settings"][cat] = registry.describe(p, category=cat)
data["settings"]["all"] = registry.describe(p)
from research import doctor   # noqa: E402
data["doctor"] = doctor.run(repo)
from research import configfile   # noqa: E402
from research.store import DEFAULT_CONFIG   # noqa: E402
data["check"] = configfile.check(p.raw_config, DEFAULT_CONFIG, open(p.config_path).read())
data["preview"] = registry.preview(p, {"agent_policy.max_runs_without_synthesis": "12"})
if not EMPTY:
    data["archive_impact"] = W.archive_impact(p, ws1["id"])
    data["promotion_diff"] = PR.diff(p, cand["id"])
else:
    data["archive_impact"] = {"workspace": "WS-001", "blockers": [], "would": [], "unmerged_commits": 0}
    data["promotion_diff"] = {"stat": "", "patch": ""}
json.dump(data, open(out, "w"), default=str, indent=1)
p.close()
print("ok", out)
