"""Dry-run demo: three dependent experiments promote through a throwaway repo (nothing in your real projects, no push, no live model).

    python examples/auto-promotion-demo/run_demo.py

The coding agent is a script that writes one file per task; the independent reviewer is a scripted stand-in that posts through the
real `promotion.review` as a different identity. Everything else (git, tests, gates, fast-forward) is the real code path.
"""
import os, subprocess, sys, tempfile, textwrap, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "packages", "core"))
from research import autopromote as AP, planspec as PS, promotion as PR, supervisor as SV, workspace as W
from research.store import Project

MOCK = ('import os,re,subprocess,sys\nb=re.search(r"brief at (\\S+)",sys.argv[-1]).group(1)\nt=open(b).read();s=set()\n'
        'for m in re.finditer(r"^MOCK_SH(?:@(\\d+|\\*))?: (.*)$",t,re.M):\n    if m.group(2) not in s:\n        s.add(m.group(2));r=subprocess.run(m.group(2),shell=True)\n'
        '        if r.returncode: sys.exit(r.returncode)\n')
G = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True,
                                    env={**os.environ, "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "d@d", "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "d@d"}).stdout.strip()
tmp = tempfile.mkdtemp(prefix="autopromote-demo-")
repo = os.path.join(tmp, "proj"); os.makedirs(repo)
G(repo, "init", "-q", "-b", "main"); open(os.path.join(repo, "train.py"), "w").write("print('hi')\n"); G(repo, "add", "."); G(repo, "commit", "-qm", "init")
os.chdir(repo)
Project.init(repo, name="demo", goal="demo").close(); G(repo, "add", "train.py")
W.enable(repo, os.path.join(tmp, "ledger"))
open(os.path.join(tmp, "agent.py"), "w").write(MOCK)
p = Project(repo)
p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [os.path.join(tmp, "agent.py")], "headless_args": ["--headless"]}
p.config["agents"]["profiles"]["rev"] = {"command": sys.executable}
p.config["auto_promotion"] = {"test_command": "ls lib*.py", "numerical_command": "true", "reviewer_profile": "rev"}
p.save_config()

def review(p_, item, pol):
    me, p_.author = p_.author, {"author_type": "agent", "author_name": "reviewer", "author_model": None}
    try: PR.review(p_, item["promotion"], "approve", "scripted independent review")
    finally: p_.author = me
    return {"at": "", "epoch": time.time(), "profile": "rev", "pid": 0}
AP._launch_reviewer = review

def t(k, sh, deps=""):
    return f'{{key: {k}, title: {k}, {f"depends_on: [{deps}], " if deps else ""}checks: ["file:lib-{k}.py"], agent: {{role: implementer, profile: fake}}, prompt: "Work.\\nMOCK_SH: {sh}"}}'
yaml = textwrap.dedent("""\
    schema_version: 1
    plans:
      - key: demo
        title: Three dependent experiments
        objective: o
        execution: {max_parallel_agents: 1, max_attempts_per_task: 1, auto_promotion: true}
        experiments:
          - {key: e1, title: Base solver, hypothesis: h1, tasks: [%s]}
          - {key: e2, title: Uses solver, hypothesis: h2, tasks: [%s]}
          - {key: e3, title: Uses both, hypothesis: h3, tasks: [%s]}
    """) % (t("a", "echo 1 > lib-a.py; cp lib-a.py lib1.py"), t("b", "ls lib1.py && echo 2 > lib-b.py; cp lib-b.py lib2.py", "a"),
            t("c", "ls lib1.py lib2.py && echo 3 > lib-c.py; cp lib-c.py lib3.py", "b"))
PS.apply(p, yaml); SV.plan_run(p, "demo", start_daemon=False)
print("1. before authorization:", SV.status(p, "demo")["auto_promotion"]["mode"], "(manual: nothing will be promoted)")
SV.autopromote(p, "demo", "authorize", note="demo run")
print("2. authorized by a human ->", SV.status(p, "demo")["auto_promotion"]["mode"])
pid = PS.load_state(p, "demo")["plan_id"]; last = None
for _ in range(400):
    SV.tick(p); rs = SV.load_run(p, pid)
    row = [(i["id"], i["experiment"], i["status"]) for i in rs["autopromote"]["items"]]
    ph = {t_["key"]: t_["phase"] for t_ in SV.status(p, "demo")["tasks"]}
    if (row, ph) != last: print("   tasks", ph, "| promotions", row); last = (row, ph)
    if rs["state"] == "completed": break
    time.sleep(0.2)
print("3. main history (linear, fast-forward only):"); print(textwrap.indent(G(repo, "log", "--oneline", "--graph", "main"), "   "))
print("   merge commits on main:", G(repo, "rev-list", "--merges", "--count", "main"), "| files:", sorted(f for f in os.listdir(repo) if f.startswith("lib")))
for i in rs["autopromote"]["items"]:
    print(f"   {i['id']} {i['experiment']}: base_main {i['base_main'][:8]} -> main {i['main_after'][:8]}  tests={i['tests']['state']} numerical={i['numerical']['state']} reviewer={i['review']['by']}")
for w in W.list_workspaces(p): print(f"   worktree {w['experiment']} started from main {w['base_rev'][:8]}")
print("4. nothing was pushed; scratch dir:", tmp)
p.close()
