// Runs under Node (the Windows host's under WSL, where Chrome lives) with playwright-core.
//   node webdev-drive.cjs <port> <workspaceFolder> <outDir> <name> [hold <seconds>]
// Drives real VS Code (web) with the installed extension: opens the Control Center from the command palette and exercises it.
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');

const [, , port, ws, outDir, name, mode, holdSec] = process.argv;
const results = [];
let pageRef;
const check = (n, ok, extra) => { results.push({ name: n, ok: !!ok, extra }); console.log(`${ok ? '✓' : '✗'} ${n}${ok ? '' : '  ' + JSON.stringify(extra)}`); };

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--no-sandbox'] });
  const page = pageRef = await browser.newPage({ viewport: { width: 1360, height: 880 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`http://127.0.0.1:${port}/?folder=${encodeURIComponent(ws)}`);
  await page.waitForSelector('.monaco-workbench', { timeout: 90000 });
  if (mode === 'hold') { await page.waitForTimeout(Number(holdSec || 60) * 1000); await browser.close(); return; }
  await page.waitForTimeout(6000);   // extension host start + activation
  const shot = (n) => page.screenshot({ path: path.join(outDir, `${name}-${n}.png`) });

  const palette = async (text) => {
    await page.keyboard.press('F1');
    await page.keyboard.type(text);
    await page.waitForTimeout(700);
    await page.keyboard.press('Enter');
  };
  await palette('Research: Open Control Center');
  const outer = page.frameLocator('iframe.webview').first();
  const ui = outer.frameLocator('iframe#active-frame');
  await ui.getByRole('tab', { name: 'Home' }).waitFor({ timeout: 45000 });
  check('Control Center opens in the real workbench', true);
  await page.waitForTimeout(1500);
  await shot('home');
  const tab = async (t) => { await ui.getByRole('tab', { name: t }).click(); await page.waitForTimeout(1200); };

  const homeText = await ui.locator('main').innerText();
  check('Home shows project name and attention items', /alpha-sweep demo/.test(homeText) && /Needs your attention/.test(homeText), homeText.slice(0, 200));
  await tab('Work'); await shot('work');
  check('Work shows plans and optional job isolation', /Job isolation/.test(await ui.locator('main').innerText()));
  await tab('Research'); await shot('research');
  check('Research lists experiments', /EXP-001/.test(await ui.locator('main').innerText()));
  await tab('Settings'); await shot('settings');
  const help = ui.getByRole('button', { name: 'About Maximum runs without synthesis' });
  await help.hover(); await page.waitForTimeout(500);
  check('? help opens on hover in VS Code', await ui.getByRole('note').isVisible());
  await shot('settings-help');
  await page.mouse.move(5, 5); await page.waitForTimeout(400);
  await help.focus(); await page.waitForTimeout(300);
  check('? help opens on keyboard focus', await ui.getByRole('note').isVisible());
  await page.keyboard.press('Escape'); await page.waitForTimeout(300);
  check('Escape closes help', !(await ui.getByRole('note').isVisible()));
  const row = ui.locator('.setting', { hasText: 'Maximum runs without synthesis' });
  await row.locator('input[type=number]').fill('9');
  await page.waitForTimeout(500);
  await ui.getByRole('button', { name: 'Review changes…' }).click();
  await ui.getByRole('dialog').waitFor();
  check('review dialog shows the YAML diff', /max_runs_without_synthesis: 9/.test(await ui.getByRole('dialog').innerText()));
  await shot('settings-review');
  await ui.getByRole('button', { name: 'Cancel' }).click();
  await ui.getByRole('button', { name: 'Discard' }).click();
  await ui.getByRole('button', { name: 'Project health' }).click();
  await ui.getByText(/passing/).waitFor({ timeout: 30000 });
  await shot('health');
  check('Project health runs in the real extension host', /errors/.test(await ui.locator('main').innerText()));
  await tab('Home');
  await ui.getByRole('button', { name: 'New experiment' }).click();
  await ui.getByRole('dialog').waitFor();
  await shot('new-experiment');
  check('New experiment dialog opens', /Hypothesis/.test(await ui.getByRole('dialog').innerText()));
  await ui.getByRole('button', { name: 'Cancel' }).click();
  // narrow editor group
  await page.setViewportSize({ width: 760, height: 880 }); await page.waitForTimeout(1200);
  await tab('Settings'); await shot('settings-narrow');
  await tab('Work'); await shot('work-narrow');
  const overflow = await ui.locator('html').evaluate((h) => h.scrollWidth > h.clientWidth + 2);
  check('no horizontal overflow at a narrow width', !overflow);
  check('no uncaught page errors', errors.length === 0, errors.slice(0, 3));
  fs.writeFileSync(path.join(outDir, `${name}-results.json`), JSON.stringify(results, null, 2));
  await browser.close();
})().catch(async (e) => { console.error('driver failed:', e.message); try { await pageRef.screenshot({ path: path.join(outDir, `${name}-FAILED.png`) }); } catch {} fs.writeFileSync(path.join(outDir, `${name}-results.json`), JSON.stringify([...results, { name: 'driver crashed', ok: false, extra: e.message }])); process.exit(2); });
