// Renders webviews outside VS Code (for screenshots / design review): node dist-preview/preview.js <data.json> <outdir>
import * as fs from 'fs';
import * as path from 'path';
import { renderArtifact, renderCheckpoint, renderDecision, renderExperiment, renderFinding, renderQuestion, renderRun } from '../src/render/detail';
import { renderForm } from '../src/render/form';
import { renderOverview, renderResume } from '../src/render/resume';

const [, , dataFile, outDir, mediaDir, theme] = process.argv;
const data = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
fs.mkdirSync(outDir, { recursive: true });
const themeCss = fs.readFileSync(path.join(__dirname, `theme-${theme || 'dark'}.css`), 'utf8');
const ctx: any = { uri: (abs: string) => 'file://' + abs };
function page(name: string, body: string, sidebar = false, index: any[] = []) {
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${themeCss}</style>
  <link rel="stylesheet" href="file://${mediaDir}/codicons/codicon.css"><link rel="stylesheet" href="file://${mediaDir}/ui.css"></head>
  <body class="${sidebar ? 'sidebar' : ''}"><div id="app">${body}</div><script type="application/json" id="index">${JSON.stringify(index)}</script>
  <script>window.acquireVsCodeApi=()=>({postMessage:()=>{}});</script><script src="file://${mediaDir}/ui.js"></script></body></html>`;
  fs.writeFileSync(path.join(outDir, name + '.html'), html);
}
page('resume', renderResume(data.resume));
page('overview', renderOverview(data.resume), true);
page('experiment', renderExperiment(data.experiment, ctx));
page('experiment2', renderExperiment(data.experiment2, ctx));
page('finding', renderFinding(data.finding, ctx));
page('failure', renderFinding(data.failure, ctx));
page('question', renderQuestion(data.question));
page('decision', renderDecision(data.decision));
page('checkpoint', renderCheckpoint(data.checkpoint));
page('run', renderRun(data.run, { ...ctx, logTail: { stdout: 'alpha=0.5 final_error=0.001238 oscillation=0.000 runtime=0.27s\n', stderr: '' } }));
page('artifact', renderArtifact(data.artifact, ctx));
page('artifact_csv', renderArtifact(data.artifact_csv, { ...ctx, preview: { kind: 'table', rows: [['step', 'error'], ['0', '1.21'], ['1', '0.83'], ['2', '0.57'], ['3', '0.40']], truncated: true } }));
const idx = data.index.items;
page('form_finding', renderForm({ kind: 'finding', title: 'New finding', icon: 'lightbulb', submit: 'Create finding', subtitle: 'A finding is a <b>reusable, interpreted result</b> backed by evidence. Failed directions are findings too.', groups: [
  { fields: [{ name: 'title', label: 'Short title', type: 'text', required: true, placeholder: 'e.g. λ≈1e-3 reduces mixing at <2% MAE cost' },
    { name: 'statement', label: 'Statement', type: 'textarea', rows: 3 },
    [{ name: 'kind', label: 'Kind', type: 'seg', value: 'result', options: [['result', 'result', 'blue'], ['failure', 'failed direction', 'red']] },
     { name: 'status', label: 'Status', type: 'seg', value: 'preliminary', options: [['preliminary', 'preliminary', 'yellow'], ['supported', 'supported', 'green'], ['contradicted', 'contradicted', 'orange']] },
     { name: 'confidence', label: 'Confidence', type: 'seg', value: 'medium', options: [['low', 'low', 'orange'], ['medium', 'medium', 'yellow'], ['high', 'high', 'green']] }]] },
  { title: 'Evidence', icon: 'link', fields: [{ name: 'supports', label: 'Supporting evidence', type: 'entities', value: ['EXP-001', 'RUN-0002', 'A-0007'], hint: 'experiments, runs, artifacts' }] },
] }), false, idx);
page('form_run', renderForm({ kind: 'run', title: 'Run EXP-002', icon: 'play', submit: 'Start run', subtitle: '<b>Locate the stability edge</b> · 0 runs so far of 3 planned.', context: {}, groups: [
  { fields: [{ name: 'command', label: 'Command', type: 'textarea', mono: true, rows: 2, required: true, value: 'python3 sim.py --alpha 1.2 --out outputs/alpha_1.2' },
    [{ name: 'label', label: 'Label', type: 'text', value: 'alpha=1.2' }, { name: 'working_dir', label: 'Working directory', type: 'text', mono: true, value: '.', hint: 'relative to project' }],
    { name: 'parameters', label: 'Run parameters', type: 'kv', value: { alpha: 1.2, steps: 60, seed: 0 }, base: { alpha: 1.0, steps: 60, seed: 0 } },
    { name: 'mode', label: 'Where', type: 'seg', value: 'slurm', options: [['terminal', 'Terminal (live output)', 'blue'], ['background', 'Background', 'purple'], ['slurm', 'SLURM', 'orange']] }] },
  { title: 'SLURM resources', icon: 'server', cls: 'slurm-only', fields: [[{ name: 'partition', label: 'Partition', type: 'text', value: 'gpu-common' }, { name: 'time', label: 'Time', type: 'text', value: '01:00:00' }, { name: 'account', label: 'Account', type: 'text', value: 'cvit' }]] },
] }), false, idx);
console.log('wrote previews to', outDir);
