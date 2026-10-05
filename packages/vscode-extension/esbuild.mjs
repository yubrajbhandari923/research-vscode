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
if (watch) {
  const ctx = await esbuild.context(opts);
  await ctx.watch();
} else {
  await esbuild.build(opts);
}
