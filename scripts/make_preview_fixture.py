"""Build a realistic demo project (shared-ledger mode, workspaces, runs, findings, tasks) and dump the RPC payloads the
Control Center renders, for screenshots.  python scripts/make_preview_fixture.py <workdir> <out.json>"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "core"))
from research import control, plans, promotion as PR, registry, runs, services, workspace as W   # noqa: E402
from research.store import Project   # noqa: E402

os.environ["XDG_CONFIG_HOME"] = os.path.join(os.path.abspath(sys.argv[1]), "xdg-empty")   # keep the demo independent of the developer machine
work, out = os.path.abspath(sys.argv[1]), sys.argv[2]
os.makedirs(work, exist_ok=True)
repo = os.path.join(work, "ct-recon")
g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                                        env={**os.environ, "GIT_AUTHOR_NAME": "d", "GIT_AUTHOR_EMAIL": "d@d", "GIT_COMMITTER_NAME": "d", "GIT_COMMITTER_EMAIL": "d@d"})
os.makedirs(repo)
g("init", "-q", "-b", "main")
open(os.path.join(repo, "train.py"), "w").write("print('loss=0.12')\n")
g("add", ".")
g("commit", "-qm", "init")
Project.init(repo, name="CT reconstruction", goal="Find the regularization strength that suppresses streak artifacts without blurring edges.").close()
W.enable(repo, os.path.join(work, "ct-ledger"))
p = Project(repo)
p.author = {"author_type": "human", "author_name": "demo"}   # the script is run by a person
q = services.create_question(p, "Which regularization strength balances artifact suppression and edge sharpness?")
e1 = services.create_experiment(p, "Regularization sweep", question=q["id"], hypothesis="An intermediate lambda minimizes error", planned_runs=3)
e2 = services.create_experiment(p, "Learned prior vs TV", question=q["id"], hypothesis="A learned prior beats total variation at low dose")
for lam in (0.1, 0.5, 1.0):
    r = runs.exec_run(p, e1["id"], f"echo lambda={lam}", parameters={"lambda": lam}, tee=False)
    runs.log_metric(p, "final_error", 0.02 * (1 + abs(lam - 0.5)), run=r["id"])
services.create_finding(p, "Lambda near 0.5 minimizes error", supports=[e1["id"]], confidence="medium") if hasattr(services, "create_finding") else None
pl = plans.create_plan(p, "Compare priors", objective="Decide between TV and a learned prior")
t1 = plans.create_task(p, pl["id"], "Implement learned prior", goal="g")
t2 = plans.create_task(p, pl["id"], "Run low-dose comparison", goal="g", depends_on=[t1["id"]])
plans.start_task(p, t1["id"])
ws = W.create(p, e2["id"], name="learned-prior")
W.link_task(p, ws["id"], t1["id"])
open(os.path.join(ws["path"], "prior.py"), "w").write("def prior(x):\n    return 0.5 * x\n")
g("add", "prior.py", cwd=ws["path"])
g("commit", "-qm", "Add learned prior", cwd=ws["path"])
open(os.path.join(ws["path"], "notes.txt"), "a").write("wip\n")   # uncommitted work stays visible as such
registry.apply(p, {"agent_policy.max_runs_without_synthesis": "8", "promotion.test_command": "test -f prior.py"}, reason="longer sweeps")
sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws["path"], capture_output=True, text=True).stdout.strip()
cand = PR.prepare(p, ws["id"], commits=[sha], title="Learned prior module")
PR.verify(p, cand["id"])
data = {"home": control.home(p), "work": control.work(p), "research": control.research(p), "settings": {}}
for cat in ("policy", "sandbox", "git"):
    data["settings"][cat] = registry.describe(p, category=cat)
data["settings"]["all"] = registry.describe(p)
data["preview"] = registry.preview(p, {"agent_policy.max_runs_without_synthesis": "12"})
data["archive_impact"] = W.archive_impact(p, ws["id"])
data["promotion_diff"] = PR.diff(p, cand["id"])
json.dump(data, open(out, "w"), default=str, indent=1)
p.close()
print("ok", out)
