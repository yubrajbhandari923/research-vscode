/** Shared HTML components for webviews. Pure functions: data → HTML string. */
import { ago, esc, fmtVal } from '../util';

export const STATUS_COLOR: Record<string, string> = {
  open: 'blue', investigating: 'yellow', answered: 'green', blocked: 'red', abandoned: 'gray',
  proposed: 'gray', ready: 'blue', running: 'yellow', needs_review: 'orange', completed: 'green', failed: 'red',
  queued: 'gray', cancelled: 'gray', unknown: 'red',
  preliminary: 'yellow', supported: 'green', contradicted: 'orange', superseded: 'gray',
  active: 'green', reversed: 'gray',
};

export const TYPE_ICON: Record<string, string> = {
  question: 'question', experiment: 'beaker', run: 'play-circle', finding: 'lightbulb', decision: 'law',
  checkpoint: 'bookmark', artifact: 'file', note: 'note', unknown: 'circle-outline',
};

export const ART_ICON: Record<string, string> = {
  image: 'file-media', table: 'table', json: 'json', markdown: 'markdown', text: 'output', log: 'output',
  pdf: 'file-pdf', array: 'symbol-array', nifti: 'symbol-misc', hdf5: 'database', netcdf: 'database',
  checkpoint: 'save', directory: 'folder', code: 'code', video: 'device-camera-video', notebook: 'notebook',
  html: 'globe', pickle: 'package', binary: 'file-binary', file: 'file',
};

export const ic = (name: string, cls = '') => `<i class="codicon codicon-${name} ${cls}"></i>`;

export function pill(status?: string | null, opts: { lg?: boolean; icon?: string; label?: string } = {}): string {
  if (!status) return '';
  const c = STATUS_COLOR[status] || 'gray';
  const pulse = status === 'running' ? '<span class="dot pulse" style="--c:var(--yellow)"></span>' : '';
  return `<span class="pill c-${c}${opts.lg ? ' lg' : ''}">${pulse}${opts.icon ? ic(opts.icon) : ''}${esc(opts.label || status.replace('_', ' '))}</span>`;
}

export function kindPill(f: any): string {
  return f.kind === 'failure' ? `<span class="pill c-red">${ic('close')}failed direction</span>` : `<span class="pill c-blue">${ic('lightbulb')}result</span>`;
}

export function idLink(id?: string | null, missing = false): string {
  if (!id) return '';
  return `<a class="id${missing ? ' missing' : ''}" data-open="${esc(id)}" title="Open ${esc(id)}">${esc(id)}</a>`;
}

export function author(o: any, verbose = false): string {
  if (!o) return '';
  const t = o.author_type || 'human';
  if (t === 'agent') {
    const name = o.author_name || 'agent';
    return `<span class="author agent" title="Written by an agent${o.author_model ? ' (' + esc(o.author_model) + ')' : ''}">${ic('hubot')}${esc(name)}${verbose && o.author_model ? ' · ' + esc(o.author_model) : ''}</span>`;
  }
  if (t === 'system') return `<span class="author system">${ic('gear')}system</span>`;
  return `<span class="author human" title="Written by a human">${ic('person')}${esc(o.author_name || 'you')}</span>`;
}

export function confidence(c?: string | null, color = 'var(--accent)'): string {
  if (!c) return '';
  return `<span class="row" style="gap:5px" title="${esc(c)} confidence"><span class="conf ${esc(c)}" style="--c:${color}"><i></i><i></i><i></i></span><span class="small muted">${esc(c)}</span></span>`;
}

export function time(iso?: string | null): string {
  if (!iso) return '';
  return `<span title="${esc(iso)}">${esc(ago(iso))}</span>`;
}

export function btn(label: string, cmd: string, args: any[] = [], o: { icon?: string; primary?: boolean; ghost?: boolean; title?: string; cls?: string } = {}): string {
  const cls = [o.primary ? 'primary' : '', o.ghost ? 'ghost' : '', o.cls || ''].join(' ').trim();
  return `<button class="${cls}" data-cmd="${esc(cmd)}" data-args="${esc(JSON.stringify(args))}" title="${esc(o.title || label)}">${o.icon ? ic(o.icon) : ''}${esc(label)}</button>`;
}

export function iconBtn(icon: string, cmd: string, args: any[] = [], title = ''): string {
  return `<button class="icon" data-cmd="${esc(cmd)}" data-args="${esc(JSON.stringify(args))}" title="${esc(title)}">${ic(icon)}</button>`;
}

export function fileLink(path: string, label?: string): string {
  return `<a data-file="${esc(path)}" title="${esc(path)}" class="mono">${esc(label || path)}</a>`;
}

export function menu(label: string, items: [string, string, any[], string?][], icon = 'chevron-down'): string {
  return `<span class="menu"><button data-menu>${esc(label)} ${ic(icon)}</button><div class="pop hidden">${items
    .map(([l, c, a, i]) => `<button data-cmd="${esc(c)}" data-args="${esc(JSON.stringify(a))}">${i ? ic(i) : ''}${esc(l)}</button>`)
    .join('')}</div></span>`;
}

export function h2(title: string, count?: number, right = ''): string {
  return `<h2>${esc(title)}${count !== undefined ? `<span class="count">${count}</span>` : ''}<span class="spacer"></span>${right}</h2>`;
}

export function empty(text: string, action = ''): string {
  return `<div class="empty-card">${ic('circle-large-outline')}<span class="grow">${esc(text)}</span>${action}</div>`;
}

/** Brief entity row (for any {id,type,title,status}). */
export function entityRow(b: any, sub = '', trail = ''): string {
  if (!b) return '';
  const t = b.type || 'unknown';
  let icon = TYPE_ICON[t] || 'circle-outline';
  let color = 'var(--muted)';
  if (t === 'finding') {
    icon = b.kind === 'failure' ? 'close' : b.status === 'supported' ? 'verified-filled' : 'lightbulb';
    color = b.kind === 'failure' ? 'var(--red)' : `var(--${STATUS_COLOR[b.status] || 'gray'})`;
  } else if (t === 'artifact') {
    icon = ART_ICON[b.artifact_type] || 'file';
  } else if (b.status) color = `var(--${STATUS_COLOR[b.status] || 'gray'})`;
  if (b.missing) {
    icon = 'warning';
    color = 'var(--red)';
  }
  return `<div class="item" data-open="${esc(b.id)}" tabindex="0">
    <i class="codicon codicon-${icon} lead" style="color:${color}"></i>
    <div class="grow"><div class="hl">${idLink(b.id, b.missing)}<span class="title">${esc(b.title || '')}</span></div>${sub ? `<div class="sub">${sub}</div>` : ''}</div>
    <div class="trail">${trail}${b.status && !b.missing ? pill(b.status) : ''}</div>
  </div>`;
}

const ID_RE = /\b(EXP-\d{3,}|RUN-\d{4,}|CP-\d{3,}|Q-\d{3,}|F-\d{3,}|D-\d{3,}|A-\d{4,})\b/g;

function inline(s: string): string {
  // s is already escaped
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2">$1</a>')
    .replace(ID_RE, (m) => `<a class="id" data-open="${m}">${m}</a>`);
}

/** Minimal, safe markdown: paragraphs, lists, code fences, inline code/bold/italic, auto-linked research IDs. */
export function md(text?: string | null): string {
  if (!text || !String(text).trim()) return '';
  const lines = String(text).replace(/\r\n/g, '\n').split('\n');
  const out: string[] = [];
  let list: 'ul' | 'ol' | null = null;
  let para: string[] = [];
  let code: string[] | null = null;
  const flushPara = () => {
    if (para.length) out.push(`<p>${inline(esc(para.join(' ')))}</p>`);
    para = [];
  };
  const closeList = () => {
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (const raw of lines) {
    if (code) {
      if (/^```/.test(raw)) {
        out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
        code = null;
      } else code.push(raw);
      continue;
    }
    if (/^```/.test(raw)) {
      flushPara();
      closeList();
      code = [];
      continue;
    }
    const ul = raw.match(/^\s*[-*•]\s+(.*)$/);
    const ol = raw.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const want = ul ? 'ul' : 'ol';
      if (list !== want) {
        closeList();
        out.push(`<${want}>`);
        list = want;
      }
      out.push(`<li>${inline(esc((ul || ol)![1]))}</li>`);
      continue;
    }
    if (!raw.trim()) {
      flushPara();
      closeList();
      continue;
    }
    const h = raw.match(/^#{1,6}\s+(.*)$/);
    if (h) {
      flushPara();
      closeList();
      out.push(`<p><b>${inline(esc(h[1]))}</b></p>`);
      continue;
    }
    closeList();
    para.push(raw.trim());
  }
  if (code) out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
  flushPara();
  closeList();
  return `<div class="prose">${out.join('')}</div>`;
}

export function field(label: string, text?: string | null, emptyText = ''): string {
  if (!text && !emptyText) return '';
  return `<div class="field"><div class="field-label">${esc(label)}</div>${text ? md(text) : `<div class="empty">${esc(emptyText)}</div>`}</div>`;
}

export function kvTable(obj: Record<string, any> | null | undefined, delta?: Record<string, any> | null): string {
  const ks = Object.keys(obj || {});
  if (!ks.length) return '<div class="empty small">none</div>';
  return `<div class="tablewrap"><table class="kv"><tbody>${ks
    .map((k) => {
      const d = delta && delta[k];
      const v = d ? `<span class="delta"><s>${esc(fmtVal(d.from))}</s> → ${esc(fmtVal(d.to))}</span>` : `<span class="mono">${esc(fmtVal(obj![k]))}</span>`;
      return `<tr><td>${esc(k)}</td><td>${v}</td></tr>`;
    })
    .join('')}</tbody></table></div>`;
}

export function eventColor(action: string): string {
  if (/fail|error|conflict/.test(action)) return 'var(--red)';
  if (/complete|synth/.test(action)) return 'var(--green)';
  if (/created|registered|submitted|started/.test(action)) return 'var(--blue)';
  if (/override/.test(action)) return 'var(--orange)';
  return 'var(--border)';
}

export function timeline(events: any[] | undefined, limit = 12): string {
  if (!events || !events.length) return '<div class="empty small">No activity yet.</div>';
  return `<div class="timeline">${events
    .slice(0, limit)
    .map(
      (e) => `<div class="ev" style="--c:${eventColor(e.action)}">${inline(esc(e.summary || e.action))}${e.author_type === 'agent' ? ` <span class="author agent" style="line-height:15px">${ic('hubot')}${esc(e.author_name || 'agent')}</span>` : ''}<span class="t">${time(e.ts)}</span></div>`,
    )
    .join('')}</div>`;
}

export function statLink(n: number | string, label: string, cmd: string, args: any[] = []): string {
  return `<div class="stat" data-cmd="${esc(cmd)}" data-args="${esc(JSON.stringify(args))}"><div class="n">${esc(n)}</div><div class="l">${esc(label)}</div></div>`;
}

/** Horizontal bar chart comparing one metric across runs. */
export function metricChart(name: string, rows: { label: string; value: number | null; id: string }[], unit?: string): string {
  const vals = rows.filter((r) => typeof r.value === 'number') as { label: string; value: number; id: string }[];
  if (vals.length < 2) return '';
  const max = Math.max(...vals.map((v) => Math.abs(v.value))) || 1;
  const min = Math.min(...vals.map((v) => v.value));
  const bestIdx = vals.findIndex((v) => v.value === min);
  const rowH = 20;
  const lw = 78;
  const w = 320;
  const h = vals.length * rowH + 6;
  const bars = vals
    .map((v, i) => {
      const bw = Math.max(2, ((w - lw - 64) * Math.abs(v.value)) / max);
      const y = i * rowH + 3;
      return `<g data-open="${esc(v.id)}" style="cursor:pointer"><text x="${lw - 8}" y="${y + 13}" text-anchor="end">${esc(v.label.length > 13 ? v.label.slice(0, 12) + '…' : v.label)}</text>
        <rect class="bar${i === bestIdx ? ' best' : ''}" x="${lw}" y="${y + 3}" width="${bw}" height="${rowH - 8}" rx="3"></rect>
        <text class="val" x="${lw + bw + 6}" y="${y + 13}">${esc(fmtVal(v.value))}${unit ? ' ' + esc(unit) : ''}</text></g>`;
    })
    .join('');
  return `<div class="card"><h3>${ic('graph', '')}<span class="mono">${esc(name)}</span><span class="muted small" style="font-weight:400;margin-left:auto">lowest highlighted</span></h3>
    <svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMinYMin meet" style="max-height:${h + 4}px">${bars}</svg></div>`;
}
