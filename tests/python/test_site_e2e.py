"""End to end: a synthetic diffusion experiment is run and verified by the real supervisor (scripted agent), publishes a committed Quarto
report and a real figure, appears in the locally built website, and would deploy through the generated GitHub Actions workflow."""
import os
import re
import subprocess
import sys
import textwrap

import pytest

from conftest import git
from research import fork as FK, guide_quarto as GQ, planspec as PS, provenance as PV, site, supervisor as SV, workspace as W
from research.store import Project
from test_supervisor import MOCK, run_until

needs_quarto = pytest.mark.skipif(GQ.find_quarto() is None, reason="Quarto is not installed")

GEN = '''"""Synthetic 1-D diffusion: writes a real PNG figure, a CSV table and a Quarto report. Pure standard library."""
import math, os, struct, zlib

os.makedirs("reports/quarto/chapters", exist_ok=True)
os.makedirs("reports/quarto/figures", exist_ok=True)
W, H = 120, 80
rows = []
for y in range(H):
    t = 0.05 + 0.5 * y / H
    rows.append(b"\\x00" + bytes(int(255 * math.exp(-((x - W / 2) / (W * t * 0.6)) ** 2)) for x in range(W)))
raw = zlib.compress(b"".join(rows))
def chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
png = b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 0, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
open("reports/quarto/figures/diffusion.png", "wb").write(png)
open("reports/quarto/figures/widths.csv", "w").write("t,width\\n" + "\\n".join(f"{t},{math.sqrt(t):.4f}" for t in (1, 2, 4)) + "\\n")
open("reports/quarto/chapters/00-smoke.qmd", "w").write("""# Diffusion smoke test

The width of a spreading spike grows with the square root of time.

```{python}
import numpy as np   # would run the simulation: the website never executes it
```

![Temperature against position as time advances downward: the spike spreads and flattens, as the diffusion equation predicts.](../figures/diffusion.png){#fig-diffusion fig-alt="Spreading spike"}

As @fig-diffusion shows, the spike widens with time.
""")
'''


@pytest.fixture
def lab(repo, tmp_path):
    (repo / "gen_diffusion.py").write_text(GEN)
    git(repo, "add", "gen_diffusion.py")
    git(repo, "commit", "-qm", "diffusion generator")
    Project.init(str(repo), name="Diffusion study", goal="How does a spike spread?").close()
    git(repo, "add", "train.py")
    W.enable(str(repo), str(tmp_path / "ledger"))
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    p = Project(str(repo))
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.config["supervisor"] = {**(p.config.get("supervisor") or {}), "poll_seconds": 1, "launch_grace_seconds": 5}
    p.save_config()
    yield p
    p.close()


PLAN = textwrap.dedent("""\
    schema_version: 1
    plans:
      - key: diff
        title: Diffusion
        objective: o
        execution: {max_parallel_agents: 1, max_attempts_per_task: 1}
        experiments:
          - key: smoke
            title: Heat diffusion smoke test
            hypothesis: The width of the spike grows like the square root of time.
            tasks:
              - {key: run, title: Generate the diffusion figure and report, checks: ['file:reports/quarto/figures/diffusion.png'], agent: {role: implementer, profile: fake}, prompt: "Work.\\nMOCK_SH: python3 gen_diffusion.py"}
    """)


@needs_quarto
def test_verified_diffusion_experiment_reaches_the_local_site_and_would_deploy(lab, repo):
    p = lab
    PS.apply(p, PLAN)
    st = PS.load_state(p, "diff")
    exp = st["experiments"]["smoke"]
    # not verified yet: the website refuses it
    site.init(p, apply=True)
    with pytest.raises(Exception, match="not verified yet"):
        site.add(p, exp)
    SV.plan_run(p, "diff", start_daemon=False)
    assert run_until(p, lambda: all(p.get("task", t)["status"] == "done" for t in st["tasks"].values()), 120)
    assert FK.unverified(p, exp) is None
    r = site.add(p, exp)
    gd = PV.guide_dir(p)
    assert r["source"] == "verified" and not r["kept_yours"]
    base = os.path.join(gd, "experiments", "heat-diffusion-smoke-test")
    assert {"reports/quarto/chapters/00-smoke.qmd", "reports/quarto/figures/diffusion.png", "reports/quarto/figures/widths.csv", "index.qmd"} <= \
        {os.path.relpath(os.path.join(d, f), base) for d, _, fs in os.walk(base) for f in fs}
    assert open(os.path.join(base, "reports/quarto/figures/diffusion.png"), "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    assert "square root of time" in open(os.path.join(base, "index.qmd")).read() and "_TODO" in open(os.path.join(base, "index.qmd")).read()
    # local build: the experiment page, its report chapter and the figure are in the site, links resolve, no code ran
    b = site.build(p)
    assert b["ok"], b
    out = os.path.join(gd, "_output", "scientific")
    pages = {f for d, _, fs in os.walk(out) for f in fs if f.endswith(".html")}
    assert "00-smoke.html" in pages and b["links"] == [] and b["leaks"] == []
    html = open(os.path.join(out, "experiments", "heat-diffusion-smoke-test", "reports", "quarto", "chapters", "00-smoke.html")).read()
    assert "diffusion.png" in html and "Spreading spike" in html and "numpy" in html and "Traceback" not in html, "the cell is shown, never run"
    assert os.path.exists(os.path.join(out, "experiments", "heat-diffusion-smoke-test", "reports", "quarto", "figures", "diffusion.png"))
    # the generated workflow would deploy exactly this directory, using the same command, never running research code
    wf = open(os.path.join(repo, ".github", "workflows", "publish-guide.yml")).read()
    rel = os.path.relpath(gd, repo)
    cmd = re.search(r"run: (quarto render .*)", wf).group(1)
    assert cmd == f"quarto render {rel} --profile scientific --to html --no-execute"
    assert f"path: {rel}/_output/scientific" in wf and os.path.isdir(os.path.join(repo, rel, "_output", "scientific"))
    runs = [l.split("run:", 1)[1].strip() for l in wf.splitlines() if l.strip().startswith("run:")]
    assert [r_.split()[0] for r_ in runs] == ["quarto", "test"] and not re.search(r"python|pip|gen_diffusion|sbatch", wf)
    # nothing was committed, pushed or configured on GitHub by any of this
    assert subprocess.run(["git", "remote"], cwd=repo, capture_output=True, text=True).stdout.strip() == ""
    assert not subprocess.run(["git", "log", "--oneline", "-1", "--grep", "website"], cwd=repo, capture_output=True, text=True).stdout.strip()
    # unpublish: gone from the rebuilt site, kept on disk, restorable
    site.remove(p, exp, apply=True)
    assert site.build(p)["ok"] and not os.path.exists(os.path.join(out, "experiments"))
    assert os.path.exists(os.path.join(gd, "_unpublished", "heat-diffusion-smoke-test", "index.qmd"))
    assert site.add(p, exp)["restored"] and site.build(p)["ok"] and os.path.exists(os.path.join(out, "experiments", "heat-diffusion-smoke-test"))
