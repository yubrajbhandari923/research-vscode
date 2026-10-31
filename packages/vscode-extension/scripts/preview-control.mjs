// Screenshots of the Control Center in light and dark VS Code themes using the real core's data and headless Chrome.
//   node scripts/preview-control.mjs <fixture.json> <outDir> [--stage <dir for chrome, e.g. /mnt/c/Windows/Temp/rp-shots>]
// Chrome: $CHROME, else the Windows host's Chrome when run under WSL, else `google-chrome`.
import * as esbuild from 'esbuild';
import fs from 'fs';
import path from 'path';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ext = path.resolve(here, '..');
const [, , fixtureFile, outDir, ...rest] = process.argv;
const flag = (n, d) => (rest.includes(n) ? rest[rest.indexOf(n) + 1] : d);
const stage = flag('--stage', null) || fs.mkdtempSync('/tmp/rp-shots-');
const only = flag('--only', '').split(',').filter(Boolean);
const W = flag('--width', '1180'), H = flag('--height', '860');
const themes = flag('--themes', 'dark,light').split(',');
const tag = flag('--tag', '');
const CHROME = process.env.CHROME || (fs.existsSync('/mnt/c/Program Files/Google/Chrome/Application/chrome.exe') ? '/mnt/c/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome');
const win = (p) => (p.startsWith('/mnt/c/') ? 'C:\\' + p.slice(7).replace(/\//g, '\\') : p);
const fileUrl = (p) => (p.startsWith('/mnt/c/') ? 'file:///C:/' + p.slice(7) : 'file://' + p);

const scenarios = {
  home: { initial: { page: 'home' } },
  work: { initial: { page: 'work' } },
  research: { initial: { page: 'research' } },
  settings: { initial: { page: 'settings', target: 'policy' } },
  'settings-help': { initial: { page: 'settings', target: 'policy' }, steps: [{ hover: 'About Maximum runs without synthesis' }] },
  'settings-review': { initial: { page: 'settings', target: 'policy' }, steps: [{ type: ['Maximum runs without synthesis', '12'] }, { click: 'Review changes' }] },
  'settings-search': { initial: { page: 'settings' }, steps: [{ search: 'sandbox' }] },
  'work-archive': { initial: { page: 'work' }, steps: [{ click: 'Archive' }] },
  'work-promotion': { initial: { page: 'work' }, steps: [{ click: 'Open…' }] },
};
fs.mkdirSync(stage, { recursive: true });
fs.mkdirSync(outDir, { recursive: true });
const fixture = fs.readFileSync(fixtureFile, 'utf8');
const css = (f) => fs.readFileSync(path.join(ext, f), 'utf8');
const results = [];
for (const [name, sc] of Object.entries(scenarios)) {
  if (only.length && !only.includes(name)) continue;
  const js = path.join(stage, `${name}.js`);
  await esbuild.build({ entryPoints: [path.join(here, 'preview-harness.tsx')], bundle: true, format: 'iife', platform: 'browser', jsx: 'automatic', outfile: js, logLevel: 'silent',
    define: { __FIXTURE__: fixture, __SCENARIO__: JSON.stringify(sc), 'process.env.NODE_ENV': '"development"' } });
  for (const theme of themes) {
    const html = path.join(stage, `${name}-${theme}.html`);
    fs.writeFileSync(html, `<!doctype html><html><head><meta charset="utf-8"><style>${css(`scripts/theme-${theme}.css`)}</style><style>${css('media/app.css')}</style></head>
<body><div id="root"></div><script src="${path.basename(js)}"></script></body></html>`);
    const png = path.join(stage, `${name}${tag}-${theme}.png`);
    execFileSync(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars', `--screenshot=${win(png)}`, `--window-size=${W},${H}`, '--virtual-time-budget=6000', fileUrl(html)], { stdio: 'ignore', timeout: 90000 });
    fs.copyFileSync(png, path.join(outDir, `${name}${tag}-${theme}.png`));
    results.push(`${name}${tag}-${theme}.png`);
  }
}
console.log(results.join('\n'));
