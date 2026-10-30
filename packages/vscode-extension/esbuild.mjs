import * as esbuild from 'esbuild';
import fs from 'fs';
import path from 'path';

const prod = process.argv.includes('--production');
const watch = process.argv.includes('--watch');

// codicons for webviews
const src = 'node_modules/@vscode/codicons/dist';
fs.mkdirSync('media/codicons', { recursive: true });
for (const f of ['codicon.css', 'codicon.ttf']) fs.copyFileSync(path.join(src, f), path.join('media/codicons', f));

const opts = {
  entryPoints: ['src/extension.ts'],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  outfile: 'dist/extension.js',
  external: ['vscode'],
  sourcemap: !prod,
  minify: prod,
  logLevel: 'info',
};
// The React Control Center runs in the webview (browser), separate from the extension host bundle.
const webview = {
  entryPoints: ['src/webview/main.tsx'], bundle: true, format: 'iife', platform: 'browser', target: 'es2022',
  jsx: 'automatic', outfile: 'media/app.js', sourcemap: false, minify: prod, logLevel: 'info',
  define: { 'process.env.NODE_ENV': prod ? '"production"' : '"development"' },
};
if (watch) {
  await (await esbuild.context(opts)).watch();
  await (await esbuild.context(webview)).watch();
} else {
  await esbuild.build(opts);
  await esbuild.build(webview);
}
