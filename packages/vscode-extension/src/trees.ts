import * as path from 'path';
import * as vscode from 'vscode';
import { Model } from './model';
import { ago, paramsSummary, truncate } from './util';

type Icon = [string, string?];

export interface Node {
  key: string;
  label: string | vscode.TreeItemLabel;
  description?: string;
  tooltip?: string | vscode.MarkdownString;
  icon?: Icon | vscode.Uri;
  context?: string;
  expanded?: boolean;
  children?: () => Node[];
  command?: vscode.Command;
  resourceUri?: vscode.Uri;
}

const I = (name: string, color?: string): Icon => [name, color];

export const STATUS_ICONS: Record<string, Record<string, Icon>> = {
  question: {
    open: I('question', 'charts.blue'),
    investigating: I('eye', 'charts.yellow'),
    answered: I('pass-filled', 'charts.green'),
    blocked: I('error', 'charts.red'),
    abandoned: I('circle-slash', 'disabledForeground'),
  },
  experiment: {
    proposed: I('beaker', 'disabledForeground'),
    ready: I('beaker', 'charts.blue'),
    running: I('sync~spin', 'charts.yellow'),
    needs_review: I('bell-dot', 'charts.orange'),
    completed: I('pass-filled', 'charts.green'),
    failed: I('error', 'charts.red'),
    abandoned: I('circle-slash', 'disabledForeground'),
  },
  run: {
    queued: I('clock', 'disabledForeground'),
    running: I('sync~spin', 'charts.yellow'),
    completed: I('check', 'charts.green'),
    failed: I('close', 'charts.red'),
    cancelled: I('circle-slash', 'disabledForeground'),
    unknown: I('question', 'charts.red'),
  },
  finding: {
    supported: I('verified-filled', 'charts.green'),
    preliminary: I('lightbulb', 'charts.yellow'),
    contradicted: I('warning', 'charts.orange'),
    superseded: I('history', 'disabledForeground'),
  },
  decision: {
    active: I('law', 'charts.green'),
    reversed: I('discard', 'disabledForeground'),
    superseded: I('history', 'disabledForeground'),
  },
};

const ART_ICONS: Record<string, string> = {
  image: 'file-media', table: 'table', json: 'json', markdown: 'markdown', text: 'output', log: 'output',
  pdf: 'file-pdf', array: 'symbol-array', nifti: 'symbol-misc', hdf5: 'database', netcdf: 'database',
  checkpoint: 'save', directory: 'folder', code: 'code', video: 'device-camera-video', notebook: 'notebook',
  html: 'globe', pickle: 'package', binary: 'file-binary', file: 'file',
};

export function findingIcon(f: any): Icon {
  if (f.kind === 'failure' && f.status !== 'superseded') return I('close', 'charts.red');
  return STATUS_ICONS.finding[f.status] || I('lightbulb');
}

function author(o: any): string {
  return o?.author_type === 'agent' ? ` · ${o.author_name || 'agent'}` : '';
}

function open(id: string): vscode.Command {
  return { command: 'research.open', title: 'Open', arguments: [id] };
}

function md(s: string): vscode.MarkdownString {
  const m = new vscode.MarkdownString(s);
  m.supportThemeIcons = true;
  return m;
}

export class ResearchTree implements vscode.TreeDataProvider<Node> {
  private _em = new vscode.EventEmitter<Node | undefined | void>();
  readonly onDidChangeTreeData = this._em.event;
  constructor(private model: Model, private roots: (m: Model) => Node[]) {
    model.onChange(() => this._em.fire());
  }
  getTreeItem(n: Node): vscode.TreeItem {
    const hasKids = !!n.children;
    const it = new vscode.TreeItem(
      n.label,
      hasKids ? (n.expanded ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.Collapsed) : vscode.TreeItemCollapsibleState.None,
    );
    it.id = n.key;
    it.description = n.description;
    it.tooltip = n.tooltip;
    it.contextValue = n.context;
    it.command = n.command;
    it.resourceUri = n.resourceUri;
    if (Array.isArray(n.icon)) it.iconPath = new vscode.ThemeIcon(n.icon[0], n.icon[1] ? new vscode.ThemeColor(n.icon[1]) : undefined);
    else if (n.icon) it.iconPath = n.icon;
    return it;
  }
  getChildren(n?: Node): Node[] {
    if (!this.model.initialized || !this.model.tree) return [];
    if (!n) return this.roots(this.model);
    return n.children ? n.children() : [];
  }
}

// --------------------------------------------------------------------------- node builders

export function artifactNode(m: Model, a: any, parentKey: string): Node {
  const missing = !a.exists;
  return {
    key: `${parentKey}/${a.id}`,
    label: a.name,
    description: `${a.id}${a.size_h && a.size_h !== '—' ? ' · ' + a.size_h : ''}${missing ? ' · missing' : ''}`,
    tooltip: md(`**${a.id}** \`${a.path}\`\n\n${a.description || ''}\n\n$(file) ${a.type}${missing ? '\n\n$(warning) file not found' : ''}`),
    icon: missing ? I('warning', 'charts.red') : I(ART_ICONS[a.type] || 'file'),
    context: 'artifact',
    command: open(a.id),
  };
}

export function runNode(m: Model, r: any, parentKey: string): Node {
  const active = r.status === 'running' || r.status === 'queued';
  const params = paramsSummary(r.parameters);
  const dur = r.started_at && r.ended_at ? durStr((Date.parse(r.ended_at) - Date.parse(r.started_at)) / 1000) : active ? 'running' : '';
  const label = r.label ? `${r.id} · ${r.label}` : r.id;
  const arts: any[] = r.artifacts || [];
  return {
    key: `${parentKey}/${r.id}`,
    label,
    description: [params, dur, r.exit_code != null && r.exit_code !== 0 ? `exit ${r.exit_code}` : '', r.slurm_job_id ? `job ${r.slurm_job_id}` : '']
      .filter(Boolean).join(' · ') + author(r),
    tooltip: md(`**${r.id}** — ${r.status}\n\n\`${truncate(r.command || '', 300)}\`\n\n${r.backend}${r.slurm_job_id ? ' · SLURM ' + r.slurm_job_id : ''}`),
    icon: STATUS_ICONS.run[r.status] || I('circle-outline'),
    context: active ? 'run.active' : 'run',
    command: open(r.id),
    children: arts.length ? () => arts.map((a) => artifactNode(m, a, `${parentKey}/${r.id}`)) : undefined,
  };
}

function durStr(s: number) {
  if (!isFinite(s)) return '';
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`;
}

export function experimentNode(m: Model, e: any, parentKey = 'exp'): Node {
  const st = e.state || {};
  const needs = (m.tree?.needs_synthesis || []).includes(e.id) && st.unsynthesized > 0;
  const bits: string[] = [];
  if (e.is_baseline) bits.push('★ baseline');
  if (needs) bits.push(`⚠ ${st.unsynthesized} to synthesize`);
  else if (st.runs) bits.push(`${st.runs} run${st.runs === 1 ? '' : 's'}`);
  if (e.status !== 'completed') bits.push(e.status.replace('_', ' '));
  if (e.parent_id) bits.push(`↳ ${e.parent_id}`);
  const runs: any[] = e.runs || [];
  const arts: any[] = e.artifacts || [];
  const key = `${parentKey}/${e.id}`;
  return {
    key,
    label: `${e.id}  ${e.title}`,
    description: bits.join(' · ') + author(e),
    tooltip: md(
      `**${e.id} · ${e.title}**  \n_${e.status}_${e.is_baseline ? ' · $(star-full) baseline' : ''}\n\n` +
        (e.hypothesis ? `**Hypothesis:** ${truncate(e.hypothesis, 300)}\n\n` : '') +
        (Object.keys(e.parameters || {}).length ? `**Params:** ${paramsSummary(e.parameters, 8)}\n\n` : '') +
        `$(play) ${st.runs || 0} runs · $(check) ${st.completed || 0} · $(close) ${st.failed || 0}` +
        (needs ? `\n\n$(warning) **Needs synthesis** — ${st.unsynthesized} unsynthesized runs` : ''),
    ),
    icon: needs && e.status !== 'running' ? I('bell-dot', 'charts.orange') : STATUS_ICONS.experiment[e.status] || I('beaker'),
    context: needs ? 'experiment.synth' : 'experiment',
    command: open(e.id),
    children: () => {
      const kids: Node[] = [];
      if (needs)
        kids.push({
          key: `${key}/synth`,
          label: 'Needs synthesis',
          description: `${st.unsynthesized} unsynthesized run${st.unsynthesized === 1 ? '' : 's'}${st.over_run_budget ? ' · budget reached' : ''}`,
          icon: I('warning', 'charts.orange'),
          command: { command: 'research.experiment.synthesize', title: 'Synthesize', arguments: [e.id] },
        });
      if (runs.length) {
        const active = runs.filter((r) => r.status === 'running' || r.status === 'queued').length;
        kids.push({
          key: `${key}/runs`,
          label: `Runs`,
          description: `${runs.length}${active ? ` · ${active} active` : ''}`,
          icon: I('list-unordered'),
          expanded: active > 0,
          children: () => runs.slice().reverse().map((r) => runNode(m, r, `${key}/runs`)),
        });
      } else
        kids.push({
          key: `${key}/norun`,
          label: 'No runs yet',
          description: 'Run…',
          icon: I('play', 'disabledForeground'),
          command: { command: 'research.experiment.run', title: 'Run', arguments: [e.id] },
        });
      for (const a of arts) kids.push(artifactNode(m, a, key));
      return kids;
    },
  };
}

function group(key: string, label: string, items: Node[], icon: Icon, expanded = true, description?: string): Node {
  return { key, label, description: description ?? String(items.length), icon, expanded, children: () => items };
}

// --------------------------------------------------------------------------- roots per view

export function questionRoots(m: Model): Node[] {
  const qs = m.tree!.questions;
  const byParent = new Map<string, any[]>();
  for (const q of qs) {
    const k = q.parent_id || '';
    if (!byParent.has(k)) byParent.set(k, []);
    byParent.get(k)!.push(q);
  }
  const order = ['investigating', 'open', 'blocked', 'answered', 'abandoned'];
  const sort = (a: any, b: any) => order.indexOf(a.status) - order.indexOf(b.status) || a.id.localeCompare(b.id);
  const exps = m.tree!.experiments;
  const build = (q: any, pk: string): Node => {
    const key = `${pk}/${q.id}`;
    const subs = (byParent.get(q.id) || []).sort(sort);
    const qe = exps.filter((e) => e.question_id === q.id).sort((a, b) => a.id.localeCompare(b.id));
    return {
      key,
      label: `${q.id}  ${q.title}`,
      description: [q.status, qe.length ? `${qe.length} exp` : ''].filter(Boolean).join(' · ') + author(q),
      tooltip: md(`**${q.id} · ${q.title}**\n\n_${q.status}_\n\n${truncate(q.description || '', 400)}`),
      icon: STATUS_ICONS.question[q.status],
      context: 'question',
      command: open(q.id),
      expanded: q.status === 'investigating',
      children: subs.length || qe.length ? () => [...subs.map((s) => build(s, key)), ...qe.map((e) => experimentNode(m, e, key))] : undefined,
    };
  };
  const roots = (byParent.get('') || []).sort(sort);
  const orphans = qs.filter((q) => q.parent_id && !qs.find((x) => x.id === q.parent_id));
  return [...roots, ...orphans].map((q) => build(q, 'q'));
}

export function experimentRoots(m: Model): Node[] {
  const ex = m.tree!.experiments;
  const act = ['running', 'needs_review', 'ready', 'proposed'];
  const rank = (e: any) => act.indexOf(e.status);
  const active = ex.filter((e) => act.includes(e.status)).sort((a, b) => rank(a) - rank(b) || b.id.localeCompare(a.id));
  const done = ex.filter((e) => e.status === 'completed');
  const bad = ex.filter((e) => e.status === 'failed' || e.status === 'abandoned');
  if (ex.length <= 5) return [...active, ...done, ...bad].map((e) => experimentNode(m, e));
  const out: Node[] = [];
  if (active.length) out.push(group('g/active', 'In progress', active.map((e) => experimentNode(m, e, 'g/active')), I('pulse', 'charts.yellow')));
  if (done.length) out.push(group('g/done', 'Completed', done.map((e) => experimentNode(m, e, 'g/done')), I('pass', 'charts.green'), !active.length));
  if (bad.length) out.push(group('g/bad', 'Failed & abandoned', bad.map((e) => experimentNode(m, e, 'g/bad')), I('error', 'disabledForeground'), false));
  return out;
}

export function findingNode(m: Model, f: any, pk = 'f'): Node {
  const ev = (f.links?.supports || []).length;
  return {
    key: `${pk}/${f.id}`,
    label: `${f.id}  ${f.title}`,
    description: [f.confidence ? `${f.confidence} confidence` : '', ev ? `${ev} evidence` : 'no evidence'].filter(Boolean).join(' · ') + author(f),
    tooltip: md(`**${f.id} · ${f.title}**\n\n${truncate(f.statement || '', 500)}\n\n_${f.kind === 'failure' ? 'failed direction' : 'result'} · ${f.status}${f.confidence ? ' · ' + f.confidence : ''}_` +
      (f.limitations ? `\n\n**Limitations:** ${truncate(f.limitations, 200)}` : '')),
    icon: findingIcon(f),
    context: 'finding',
    command: open(f.id),
  };
}

export function findingRoots(m: Model): Node[] {
  const fs = m.tree!.findings;
  const est = fs.filter((f) => f.kind !== 'failure' && f.status === 'supported');
  const pre = fs.filter((f) => f.kind !== 'failure' && f.status === 'preliminary');
  const fail = fs.filter((f) => f.kind === 'failure' && f.status !== 'superseded');
  const old = fs.filter((f) => (f.kind !== 'failure' && (f.status === 'contradicted' || f.status === 'superseded')) || (f.kind === 'failure' && f.status === 'superseded'));
  const out: Node[] = [];
  if (est.length) out.push(group('fg/est', 'Established', est.map((f) => findingNode(m, f, 'fg/est')), I('verified-filled', 'charts.green')));
  if (pre.length) out.push(group('fg/pre', 'Preliminary', pre.map((f) => findingNode(m, f, 'fg/pre')), I('lightbulb', 'charts.yellow')));
  if (fail.length) out.push(group('fg/fail', 'Failed directions', fail.map((f) => findingNode(m, f, 'fg/fail')), I('close', 'charts.red')));
  if (old.length) out.push(group('fg/old', 'Contradicted & superseded', old.map((f) => findingNode(m, f, 'fg/old')), I('history', 'disabledForeground'), false));
  return out;
}

export function decisionRoots(m: Model): Node[] {
  return m.tree!.decisions.map((d) => ({
    key: `d/${d.id}`,
    label: `${d.id}  ${d.title || d.statement}`,
    description: [d.date, d.status !== 'active' ? d.status : ''].filter(Boolean).join(' · ') + author(d),
    tooltip: md(`**${d.id}** ${d.statement}\n\n${d.reason ? '**Why:** ' + truncate(d.reason, 400) : ''}`),
    icon: STATUS_ICONS.decision[d.status],
    context: 'decision',
    command: open(d.id),
  }));
}

export function checkpointRoots(m: Model): Node[] {
  return m.tree!.checkpoints.map((c, i) => ({
    key: `cp/${c.id}`,
    label: `${c.id}  ${c.title}`,
    description: (i === 0 ? 'latest · ' : '') + ago(c.created_at) + author(c),
    tooltip: md(`**${c.title}**\n\n${truncate(c.understanding || '', 400)}\n\n${c.next_experiment ? '**Next:** ' + truncate(c.next_experiment, 200) : ''}`),
    icon: I('bookmark', i === 0 ? 'charts.green' : 'charts.blue'),
    context: 'checkpoint',
    command: open(c.id),
  }));
}

export function noteRoots(m: Model): Node[] {
  return m.tree!.notes.map((n) => {
    const uri = vscode.Uri.file(path.join(m.root, n.path));
    return {
      key: `n/${n.id}`,
      label: n.title,
      description: [n.links.join(', '), ago(new Date(n.modified * 1000).toISOString())].filter(Boolean).join(' · '),
      tooltip: md(`**${n.title}**\n\n${n.excerpt || ''}\n\n\`${n.path}\``),
      icon: n.pinned ? I('pinned', 'charts.purple') : I('note'),
      context: n.pinned ? 'note.pinned' : 'note',
      command: { command: 'vscode.open', title: 'Open', arguments: [uri] },
      resourceUri: uri,
    };
  });
}

export function artifactRoots(m: Model): Node[] {
  return m.tree!.artifacts.map((a) => {
    const n = artifactNode(m, a, 'art');
    n.description = `${a.run_id || a.experiment_id || ''} · ${n.description}`;
    return n;
  });
}

export function agentRoots(m: Model): Node[] {
  const file = (rel: string, key: string, label?: string, desc?: string, icon: Icon = I('file')): Node => {
    const uri = vscode.Uri.file(path.join(m.root, rel));
    return { key, label: label || path.basename(rel), description: desc, icon, context: 'agentfile', resourceUri: uri,
      command: { command: 'vscode.open', title: 'Open', arguments: [uri] } };
  };
  const t = m.tree!;
  const skills: Node[] = t.skills.map((s) => ({
    key: `ag/sk/${s.name}`,
    label: s.name,
    description: truncate(s.description, 80),
    tooltip: md(`**${s.name}**\n\n${s.description}\n\n\`${s.path}\``),
    icon: I('mortar-board', 'charts.purple'),
    context: 'skill',
    resourceUri: vscode.Uri.file(path.join(m.root, s.path)),
    command: { command: 'vscode.open', title: 'Open', arguments: [vscode.Uri.file(path.join(m.root, s.path))] },
    children: s.files.length > 1 ? () => s.files.map((f: string) => file(f, `ag/sk/${s.name}/${f}`, path.basename(f), path.dirname(f).split('/').slice(3).join('/'))) : undefined,
  }));
  const list = (k: 'context' | 'prompts' | 'templates') => t.agent[k].map((f) => file(f.path, `ag/${k}/${f.name}`, f.name, undefined, I(k === 'context' ? 'symbol-structure' : k === 'prompts' ? 'comment' : 'file-code')));
  return [
    file('AGENTS.md', 'ag/agents', 'AGENTS.md', 'agent instructions', I('book', 'charts.blue')),
    file('.research/config.yaml', 'ag/config', 'config.yaml', 'run budgets · backends', I('gear')),
    group('ag/skills', 'Skills', skills, I('mortar-board'), true),
    group('ag/context', 'Project context', list('context'), I('symbol-structure'), false),
    group('ag/prompts', 'Prompts', list('prompts'), I('comment'), false),
    group('ag/templates', 'Templates', list('templates'), I('file-code'), false),
  ];
}
