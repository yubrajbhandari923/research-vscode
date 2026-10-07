import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { Model } from './model';
import { renderArtifact, renderCheckpoint, renderDecision, renderExperiment, renderFinding, renderPlan, renderQuestion, renderRun, renderTask, RenderCtx } from './render/detail';
import { FormSpec, renderForm } from './render/form';
import { renderHome } from './render/home';
import { renderOverview, renderResume } from './render/resume';
import { esc, nonce } from './util';

const TITLES: Record<string, string> = {
  experiment: 'beaker', finding: 'lightbulb', question: 'question', decision: 'law', checkpoint: 'bookmark', run: 'play-circle', artifact: 'file',
  plan: 'list-ordered', task: 'list-ordered',
};

export function shell(webview: vscode.Webview, extUri: vscode.Uri, body: string, opts: { sidebar?: boolean; index?: any[]; title?: string } = {}): string {
  const n = nonce();
  const media = (f: string) => webview.asWebviewUri(vscode.Uri.joinPath(extUri, 'media', f)).toString();
  const csp = [
    `default-src 'none'`,
    `img-src ${webview.cspSource} https: data:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `font-src ${webview.cspSource}`,
    `script-src 'nonce-${n}'`,
  ].join('; ');
  return `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="${media('codicons/codicon.css')}" rel="stylesheet"><link href="${media('ui.css')}" rel="stylesheet">
<title>${esc(opts.title || 'Research')}</title></head>
<body class="${opts.sidebar ? 'sidebar' : ''}"><div id="app">${body}</div>
<script type="application/json" id="index">${JSON.stringify(opts.index || []).replace(/</g, '\\u003c')}</script>
<script nonce="${n}" src="${media('ui.js')}"></script></body></html>`;
}

type Handler = (values: any, context: any) => Promise<string | void>;

interface EntityPanel {
  panel: vscode.WebviewPanel;
  id: string;
  kind: 'entity' | 'resume' | 'form';
  handler?: Handler;
  extraRoots: Set<string>;
  loaded?: boolean;
}

export class Panels implements vscode.Disposable {
  private panels = new Map<string, EntityPanel>();
  private disposables: vscode.Disposable[] = [];

  constructor(private ctx: vscode.ExtensionContext, private model: Model, private out: vscode.OutputChannel) {
    this.disposables.push(model.onChange(() => this.refreshAll()));
  }

  private roots(extra: Set<string>): vscode.Uri[] {
    return [vscode.Uri.joinPath(this.ctx.extensionUri, 'media'), vscode.Uri.file(this.model.root), ...Array.from(extra).map((d) => vscode.Uri.file(d))];
  }

  private create(key: string, title: string, icon: string, kind: EntityPanel['kind'], side = false): EntityPanel {
    const existing = this.panels.get(key);
    if (existing) {
      existing.panel.reveal(existing.panel.viewColumn, false);
      return existing;
    }
    const extraRoots = new Set<string>();
    const panel = vscode.window.createWebviewPanel('research.' + kind, title, side ? vscode.ViewColumn.Beside : vscode.ViewColumn.Active, {
      enableScripts: true,
      retainContextWhenHidden: kind === 'form',
      localResourceRoots: this.roots(extraRoots),
      enableFindWidget: true,
    });
    panel.iconPath = new vscode.ThemeIcon(icon) as any;
    const ep: EntityPanel = { panel, id: key, kind, extraRoots };
    this.panels.set(key, ep);
    panel.onDidDispose(() => this.panels.delete(key));
    panel.onDidChangeViewState((e) => {
      if (!e.webviewPanel.visible) {
        if (kind !== 'form') ep.loaded = false; // hidden webviews drop messages; re-render fully when shown
        return;
      }
      if (kind === 'resume') this.renderResume(ep);
      else if (kind === 'entity') this.renderEntity(ep).catch(() => undefined);
    });
    panel.webview.onDidReceiveMessage((m) => this.onMessage(ep, m));
    return ep;
  }

  // ------------------------------------------------------------------ public API
  async openResume() {
    const ep = this.create('resume', 'Research Home · ' + (this.model.tree?.project.name || 'Research'), 'beaker', 'resume');
    ep.panel.webview.html = shell(ep.panel.webview, this.ctx.extensionUri, `<div class="page"><div class="row muted"><span class="spinner"></span> Loading…</div></div>`, { title: 'Research Home' });
    if (!this.model.resume) await this.model.refresh();
    this.renderResume(ep);
  }

  async openEntity(id: string, side = false) {
    if (id.startsWith('note:')) {
      const n = this.model.tree?.notes.find((x) => x.id === id);
      if (n) return vscode.commands.executeCommand('vscode.open', vscode.Uri.file(path.join(this.model.root, n.path)));
      return;
    }
    const brief = this.model.find(id);
    const type = brief?.type || guessType(id);
    const ep = this.create('e:' + id, `${id}${brief?.title ? ' · ' + brief.title : ''}`, TITLES[type] || 'file', 'entity', side);
    if (!ep.panel.webview.html) ep.panel.webview.html = shell(ep.panel.webview, this.ctx.extensionUri, `<div class="page"><div class="row muted"><span class="spinner"></span> Loading ${esc(id)}…</div></div>`);
    await this.renderEntity(ep);
  }

  openForm(key: string, spec: FormSpec, handler: Handler) {
    const ep = this.create('f:' + key, spec.title, 'edit', 'form');
    ep.handler = handler;
    ep.panel.title = spec.title;
    ep.panel.webview.html = shell(ep.panel.webview, this.ctx.extensionUri, renderForm(spec), { index: this.model.index?.items || [], title: spec.title });
    ep.panel.reveal();
  }

  closeForm(key: string) {
    this.panels.get('f:' + key)?.panel.dispose();
  }

  // ------------------------------------------------------------------ rendering
  private refreshAll() {
    for (const ep of this.panels.values()) {
      if (ep.kind === 'resume') this.renderResume(ep);
      else if (ep.kind === 'entity' && ep.panel.visible) this.renderEntity(ep).catch(() => undefined);
    }
  }

  private post(ep: EntityPanel, html: string) {
    if (!ep.loaded || !ep.panel.visible) {
      ep.panel.webview.html = shell(ep.panel.webview, this.ctx.extensionUri, html, { index: this.model.index?.items || [] });
      ep.loaded = ep.panel.visible;
    } else ep.panel.webview.postMessage({ t: 'render', html });
  }

  private async renderResume(ep: EntityPanel) {
    // Try new Research Home first, fall back to legacy Resume if home endpoint fails
    try {
      const home = await this.model.client.request('home');
      ep.panel.title = 'Research Home · ' + (home.project?.name || '');
      const ctx = this.rctx(ep);
      this.post(ep, renderHome(home, { uri: ctx.uri }));
    } catch {
      // Fall back to legacy Resume view
      const r = this.model.resume;
      if (!r) return;
      ep.panel.title = 'Resume · ' + (r.project?.name || '');
      this.post(ep, renderResume(r));
    }
  }

  private rctx(ep: EntityPanel): RenderCtx {
    const root = this.model.root;
    return {
      uri: (abs: string) => {
        if (!abs) return '';
        const inside = !path.relative(root, abs).startsWith('..');
        if (!inside) {
          const d = path.dirname(abs);
          if (!ep.extraRoots.has(d)) {
            ep.extraRoots.add(d);
            ep.panel.webview.options = { ...ep.panel.webview.options, localResourceRoots: this.roots(ep.extraRoots) };
          }
        }
        let mtime = 0;
        try { mtime = fs.statSync(abs).mtimeMs; } catch { /* */ }
        return ep.panel.webview.asWebviewUri(vscode.Uri.file(abs)).toString() + `?v=${Math.round(mtime)}`;
      },
    };
  }

  private async renderEntity(ep: EntityPanel) {
    const id = ep.id.slice(2);
    let d: any;
    try {
      d = await this.model.client.request('show', { id });
    } catch (e: any) {
      this.post(ep, `<div class="page"><div class="banner" style="--c:var(--red)"><i class="codicon codicon-error"></i><div class="grow">${esc(e.message)}</div></div></div>`);
      return;
    }
    const ctx = this.rctx(ep);
    let html = '';
    const t = d.type || d.kind;
    if (t === 'experiment') html = renderExperiment(d, ctx);
    else if (t === 'finding') html = renderFinding(d, ctx);
    else if (t === 'question') html = renderQuestion(d);
    else if (t === 'decision') html = renderDecision(d);
    else if (t === 'checkpoint') html = renderCheckpoint(d);
    else if (t === 'plan') html = renderPlan(d);
    else if (t === 'task') html = renderTask(d);
    else if (t === 'run') {
      ctx.logTail = { stdout: tail(d.stdout_path_abs), stderr: tail(d.stderr_path_abs) };
      html = renderRun(d, ctx);
    } else if (t === 'artifact' || d.kind === 'artifact') {
      ctx.preview = readPreview(d);
      html = renderArtifact(d, ctx);
    }
    ep.panel.title = `${d.id} · ${d.title || d.name || d.label || ''}`.trim();
    this.post(ep, html);
  }

  // ------------------------------------------------------------------ messages
  private async onMessage(ep: EntityPanel, m: any) {
    try {
      if (m.t === 'open') await this.openEntity(m.id, m.side);
      else if (m.t === 'cmd') await vscode.commands.executeCommand(m.command, ...(m.args || []));
      else if (m.t === 'file') await openPath(this.model.root, m.path);
      else if (m.t === 'copy') await vscode.env.clipboard.writeText(m.text || '');
      else if (m.t === 'cancel') ep.panel.dispose();
      else if (m.t === 'submit' && ep.handler) {
        try {
          const openId = await ep.handler(m.values, m.context);
          ep.panel.dispose();
          this.model.schedule(0);
          if (openId) setTimeout(() => this.openEntity(openId), 120);
        } catch (e: any) {
          ep.panel.webview.postMessage({ t: 'formError', message: e.message + (e.hint ? ` — ${e.hint}` : '') });
        }
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  dispose() {
    for (const p of this.panels.values()) p.panel.dispose();
    this.disposables.forEach((d) => d.dispose());
  }
}

function guessType(id: string): string {
  const p = id.split('-')[0];
  return { Q: 'question', EXP: 'experiment', RUN: 'run', F: 'finding', D: 'decision', CP: 'checkpoint', A: 'artifact', PLAN: 'plan', T: 'task' }[p] || 'file';
}

export async function openPath(root: string, p: string) {
  const abs = path.isAbsolute(p) ? p : path.join(root, p);
  const uri = vscode.Uri.file(abs);
  try {
    const st = fs.statSync(abs);
    if (st.isDirectory()) return vscode.commands.executeCommand('revealInExplorer', uri);
  } catch {
    return vscode.window.showWarningMessage(`File not found: ${p}`);
  }
  return vscode.commands.executeCommand('vscode.open', uri);
}

function tail(p?: string, bytes = 6000): string {
  if (!p) return '';
  try {
    const st = fs.statSync(p);
    const fd = fs.openSync(p, 'r');
    const len = Math.min(bytes, st.size);
    const buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, st.size - len);
    fs.closeSync(fd);
    let s = buf.toString('utf8');
    if (st.size > bytes) s = '…\n' + s.slice(s.indexOf('\n') + 1);
    return s;
  } catch {
    return '';
  }
}

/** Preview adapters. Add new formats here (e.g. NPY header, NIfTI header) — keep them cheap. */
const PREVIEW_ADAPTERS: { match: (a: any) => boolean; read: (abs: string, a: any) => any }[] = [
  {
    match: (a) => a.type === 'table' && /\.(csv|tsv)$/i.test(a.path),
    read: (abs) => {
      const txt = readHead(abs, 256 * 1024);
      const sep = abs.toLowerCase().endsWith('.tsv') ? '\t' : ',';
      const lines = txt.text.split(/\r?\n/).filter((l) => l.length);
      const rows = lines.slice(0, 201).map((l) => splitCsv(l, sep));
      return { kind: 'table', rows, truncated: lines.length > 201 || txt.truncated };
    },
  },
  {
    match: (a) => a.type === 'json',
    read: (abs) => {
      const t = readHead(abs, 400 * 1024);
      if (!t.truncated && abs.endsWith('.json')) {
        try { return { kind: 'text', text: JSON.stringify(JSON.parse(t.text), null, 2).slice(0, 200_000) }; } catch { /* raw */ }
      }
      return { kind: 'text', ...t };
    },
  },
  { match: (a) => a.type === 'markdown', read: (abs) => ({ kind: 'markdown', ...readHead(abs, 200 * 1024) }) },
  { match: (a) => ['text', 'log', 'code'].includes(a.type), read: (abs) => ({ kind: 'text', ...readHead(abs, 200 * 1024) }) },
  {
    match: (a) => a.type === 'array' && /\.npy$/i.test(a.path),
    read: (abs) => {
      const h = readHead(abs, 512).text;
      const m = h.match(/\{.*\}/);
      return { kind: 'text', text: m ? `NumPy array header:\n${m[0]}` : 'NumPy file (header not readable)' };
    },
  },
  {
    match: (a) => a.type === 'directory',
    read: (abs) => {
      try {
        const items = fs.readdirSync(abs).slice(0, 300);
        return { kind: 'text', text: items.join('\n'), truncated: items.length >= 300 };
      } catch (e: any) {
        return { kind: 'text', error: e.message };
      }
    },
  },
];

function readPreview(a: any) {
  if (!a.exists) return undefined;
  const ad = PREVIEW_ADAPTERS.find((x) => x.match(a));
  if (!ad) return undefined;
  try {
    return ad.read(a.abspath, a);
  } catch (e: any) {
    return { kind: 'text', error: e.message };
  }
}

function readHead(p: string, bytes: number): { text: string; truncated: boolean } {
  const st = fs.statSync(p);
  const fd = fs.openSync(p, 'r');
  const len = Math.min(bytes, st.size);
  const buf = Buffer.alloc(len);
  fs.readSync(fd, buf, 0, len, 0);
  fs.closeSync(fd);
  return { text: buf.toString('utf8'), truncated: st.size > bytes };
}

function splitCsv(line: string, sep: string): string[] {
  const out: string[] = [];
  let cur = '';
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') q = false;
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === sep) { out.push(cur); cur = ''; }
    else cur += c;
  }
  out.push(cur);
  return out;
}

// ------------------------------------------------------------------ sidebar overview
export class OverviewView implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  constructor(private ctx: vscode.ExtensionContext, private model: Model, private panels: Panels) {
    model.onChange(() => this.render());
  }
  resolveWebviewView(view: vscode.WebviewView) {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(this.ctx.extensionUri, 'media')] };
    view.webview.html = shell(view.webview, this.ctx.extensionUri, renderOverview(this.model.resume, this.model.error), { sidebar: true });
    view.webview.onDidReceiveMessage(async (m) => {
      try {
        if (m.t === 'open') await this.panels.openEntity(m.id, m.side);
        else if (m.t === 'cmd') await vscode.commands.executeCommand(m.command, ...(m.args || []));
        else if (m.t === 'file') await openPath(this.model.root, m.path);
      } catch (e: any) {
        vscode.window.showErrorMessage(e.message);
      }
    });
    view.onDidChangeVisibility(() => view.visible && this.render());
  }
  render() {
    if (!this.view) return;
    this.view.webview.postMessage({ t: 'render', html: renderOverview(this.model.resume, this.model.error) });
  }
}
