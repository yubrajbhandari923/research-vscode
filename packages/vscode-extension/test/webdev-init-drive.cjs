// node webdev-init-drive.cjs <port> <emptyFolder> <outDir> <name>
// Real VS Code (web) + installed VSIX: Research: Initialize Project from an EMPTY folder, step by step, then the Agents tab.
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');
const [, , port, ws, outDir, name, phase2] = process.argv;
const results = [];
let pageRef;
const check = (n, ok, extra) => { results.push({ name: n, ok: !!ok, extra }); console.log(`${ok ? '✓' : '✗'} ${n}${ok ? '' : '  ' + JSON.stringify(extra)}`); };
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--no-sandbox'] });
  const page = pageRef = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = []; page.on('pageerror', (e) => errors.push(String(e)));
  const shot = (n) => page.screenshot({ path: path.join(outDir, `${name}-${n}.png`) });
  const palette = async (t) => { await page.keyboard.press('F1'); await page.keyboard.type(t); await page.waitForTimeout(800); await page.keyboard.press('Enter'); };
  const frame = () => page.frameLocator('iframe.webview').first().frameLocator('iframe#active-frame');
  const open = async (folder) => { await page.goto(`http://127.0.0.1:${port}/?folder=${encodeURIComponent(folder)}`); await page.waitForSelector('.monaco-workbench', { timeout: 90000 }); await page.waitForTimeout(3000);
    // This VS Code build opens folders in Restricted Mode and ignores the harness settings; trust the folder like a user would.
    try { await page.getByRole('link', { name: 'Manage' }).first().click({ timeout: 5000 }); await page.getByRole('button', { name: /Trust Folder/ }).first().click({ timeout: 8000 });
      await page.waitForTimeout(1500); const y = page.getByRole('button', { name: /^Yes/ }); if (await y.count()) await y.first().click(); } catch (e) { console.log('trust step skipped: ' + e.message.split('\n')[0]); }
    await page.waitForTimeout(15000); };
  if (!phase2) {
    await open(ws);
    await palette('Research: Initialize Project');
    const ui = frame();
    await ui.getByText('Initialize Research Project').waitFor({ timeout: 45000 });
    check('Initialize Project opens in an empty folder', true);
    const next = async () => { await ui.getByRole('button', { name: 'Next' }).click(); await page.waitForTimeout(500); };
    await shot('1-learn');
    check('first screen teaches how Research OS works', /How Research OS works/.test(await ui.locator('.wizard').innerText()));
    await next(); await ui.locator('#ip-goal').fill('Find the best alpha'); await shot('2-project');
    await next(); await shot('3-folders');
    check('folders step explains GitHub root', /top level/.test(await ui.locator('.wizard').innerText()));
    await next(); await shot('4-agents');
    const agentsText = await ui.locator('.wizard').innerText();
    check('agents step lists all three providers', /Claude Code/.test(agentsText) && /OpenAI Codex/.test(agentsText) && /Google Antigravity/.test(agentsText));
    await next(); await ui.getByLabel('Set up an HPC cluster').check(); await page.waitForTimeout(400); await shot('5-execution-hpc');
    await next(); await ui.getByText('do exactly this').waitFor({ timeout: 20000 }); await shot('6-review');
    await ui.getByRole('button', { name: 'Create project' }).click();
    await ui.getByText('Project created').waitFor({ timeout: 60000 }); await shot('7-done');
    check('project created, next steps shown', /Your first 5 minutes/.test(await ui.locator('.wizard').innerText()));
  } else {
    await open(ws);   // phase 2: reopen the created main/ checkout
    await palette('Research: Open Control Center');
    const ui = frame();
    await ui.getByRole('tab', { name: 'Home' }).waitFor({ timeout: 45000 });
    await ui.getByRole('tab', { name: 'Agents' }).click(); await page.waitForTimeout(2500); await shot('8-agents');
    const t = await ui.locator('main').innerText();
    check('Agents tab shows providers and states', /Agents & Providers/.test(t) && /Google Antigravity/.test(t) && /Completed = the agent process exited/.test(t), t.slice(0, 200));
  }
  check('no uncaught page errors', errors.length === 0, errors.slice(0, 3));
  fs.writeFileSync(path.join(outDir, `${name}-results.json`), JSON.stringify(results, null, 2));
  await browser.close();
})().catch(async (e) => { console.error('driver failed:', e.message); try { await pageRef.screenshot({ path: path.join(outDir, `${name}-FAILED.png`) }); } catch {} fs.writeFileSync(path.join(outDir, `${name}-results.json`), JSON.stringify([...results, { name: 'driver crashed', ok: false, extra: e.message }])); process.exit(2); });
