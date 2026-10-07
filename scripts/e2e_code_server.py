"""End-to-end UI test against a running code-server (VS Code in the browser; the extension host runs
server-side exactly like Remote SSH). Usage:

    code-server --install-extension dist/research-panel-*.vsix
    code-server --auth none --bind-addr 127.0.0.1:8123 <demo-project> &
    python scripts/e2e_code_server.py <demo-project> <screenshot-dir>
"""
import glob
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

PROJ = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else "e2e-shots"
URL = os.environ.get("CODE_SERVER_URL", "http://127.0.0.1:8123")
os.makedirs(OUT, exist_ok=True)
res = []


def ok(n, c):
    res.append((n, bool(c)))
    print(("✓ " if c else "✗ ") + n, flush=True)


def frame_with(pg, sel, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for f in pg.frames:
            try:
                if f.query_selector(sel):
                    return f
            except Exception:
                pass
        time.sleep(0.5)
    return None


def cmd(pg, text):
    pg.keyboard.press("F1")
    time.sleep(0.8)
    pg.keyboard.type(text)
    time.sleep(1)
    pg.keyboard.press("Enter")


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 950})
    pg.goto(f"{URL}/?folder={PROJ}")
    pg.wait_for_selector(".monaco-workbench", timeout=60000)
    time.sleep(5)
    pg.locator('.activitybar [aria-label^="Research"]').first.click()
    time.sleep(6)
    cmd(pg, "View: Close Secondary Side Bar")
    time.sleep(1)
    pg.screenshot(path=f"{OUT}/10_sidebar.png")
    ok("sidebar shows tree items", pg.locator('.monaco-list-row:has-text("EXP-001")').count() > 0)
    ok("overview webview rendered", frame_with(pg, "text=Open Research Home") is not None)

    cmd(pg, "Research: New Question")
    time.sleep(3)
    f = frame_with(pg, "#form")
    ok("question form opened", f is not None)
    if f:
        f.fill("[data-field=title]", "Is the stability edge seed-dependent?")
        f.fill("[data-field=description]", "Created through the VS Code form in the E2E test.")
        pg.screenshot(path=f"{OUT}/11_form.png")
        f.click("button[type=submit]")
        time.sleep(4)
    qs = sorted(glob.glob(f"{PROJ}/.research/questions/*.md"))
    ok("Q-003 file written by form", any("Q-003" in q for q in qs))
    ok("question detail panel opened", frame_with(pg, "text=Created through the VS Code form", 10) is not None)
    pg.screenshot(path=f"{OUT}/12_question.png")

    cmd(pg, "Research: Run…")
    time.sleep(2)
    pg.keyboard.type("EXP-002")
    time.sleep(1)
    pg.keyboard.press("Enter")
    time.sleep(4)
    f = frame_with(pg, "#form")
    ok("run form opened", f is not None)
    if f:
        f.fill("[data-field=command]", "python3 sim.py --alpha 1.2 --out outputs/alpha_1.2")
        f.fill("[data-field=label]", "alpha=1.2")
        rows = f.query_selector_all(".kvrow")
        for r in rows:
            if r.query_selector("[data-k]").input_value() == "alpha":
                r.query_selector("[data-v]").fill("1.2")
        pg.screenshot(path=f"{OUT}/13_runform.png")
        f.click("button[type=submit]")
        time.sleep(14)
    st = glob.glob(f"{PROJ}/.research/runs/RUN-0004/status.json")
    ok("RUN-0004 executed in a VS Code terminal", bool(st) and json.load(open(st[0]))["state"] == "completed")
    pg.screenshot(path=f"{OUT}/14_terminal.png")
    cmd(pg, "Research: Refresh")
    time.sleep(3)
    pg.locator('.monaco-list-row:visible:has-text("EXP-002")').first.click()
    time.sleep(5)
    ok("EXP-002 detail shows RUN-0004", frame_with(pg, "text=RUN-0004", 15) is not None)
    pg.screenshot(path=f"{OUT}/15_exp2.png")
    b.close()
json.dump(res, open(f"{OUT}/e2e.json", "w"))
print(f"{sum(1 for _, c in res if c)}/{len(res)} checks passed")
sys.exit(0 if all(c for _, c in res) else 1)
