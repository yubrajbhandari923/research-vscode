/** Shared HTML components for webviews. Pure functions: data → HTML string. */
import { ago, esc, fmtVal } from '../util';

export const STATUS_COLOR: Record<string, string> = {
  open: 'blue', investigating: 'yellow', answered: 'green', blocked: 'red', abandoned: 'gray',
  proposed: 'gray', ready: 'blue', running: 'yellow', needs_review: 'orange', completed: 'green', failed: 'red',
  queued: 'gray', cancelled: 'gray', unknown: 'red',
  preliminary: 'yellow', supported: 'green', contradicted: 'orange', superseded: 'gray',
  active: 'green', reversed: 'gray',
  todo: 'gray', verify: 'blue', done: 'green',
  pass: 'green', fail: 'red', needs_work: 'orange',
};

export const TYPE_ICON: Record<string, string> = {
  question: 'question', experiment: 'beaker', run: 'play-circle', finding: 'lightbulb', decision: 'law',
  checkpoint: 'bookmark', artifact: 'file', note: 'note', plan: 'list-ordered', task: 'tasklist', synthesis: 'sparkle',
  review: 'comment-discussion', skill: 'mortar-board', unknown: 'circle-outline',
};

export const ART_ICON: Record<string, string> = {
  image: 'file-media', table: 'table', json: 'json', markdown: 'markdown', text: 'output', log: 'output',
  pdf: 'file-pdf', array: 'symbol-array', nifti: 'symbol-misc', hdf5: 'database', netcdf: 'database',
  checkpoint: 'save', directory: 'folder', code: 'code', video: 'device-camera-video', notebook: 'notebook',
  html: 'globe', pickle: 'package', binary: 'file-binary', file: 'file',
};

export const ic = (name: string, cls = '') => `<i class="codicon codicon-${name} ${cls}" aria-hidden="true"></i>`;

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
  return `<button class="icon" data-cmd="${esc(cmd)}" data-args="${esc(JSON.stringify(args))}" title="${esc(title)}" aria-label="${esc(title)}">${ic(icon)}</button>`;
}

export function fileLink(path: string, label?: string): string {
  return `<a data-file="${esc(path)}" title="${esc(path)}" class="mono">${esc(label || path)}</a>`;
}

export type MenuItem = [string, string, any[], string?] | '-' | { header: string };

function menuItems(items: MenuItem[]): string {
  return items.map((it) => {
    if (it === '-') return '<hr>';
    if (!Array.isArray(it)) return `<div class="mh">${esc(it.header)}</div>`;
    const [l, c, a, i] = it;
    return `<button role="menuitem" data-cmd="${esc(c)}" data-args="${esc(JSON.stringify(a))}">${i ? ic(i) : ''}${esc(l)}</button>`;
  }).join('');
}

export function menu(label: string, items: MenuItem[], icon = 'chevron-down'): string {
  return `<span class="menu"><button data-menu aria-haspopup="menu" aria-expanded="false">${esc(label)} ${ic(icon)}</button><div class="pop hidden" role="menu">${menuItems(items)}</div></span>`;
}

/** Icon-only "⋯" overflow menu: where finer controls live. */
export function overflow(items: MenuItem[], label = 'More actions'): string {
  const it = items.filter(Boolean);
  if (!it.length) return '';
  return `<span class="menu"><button class="icon" data-menu aria-haspopup="menu" aria-expanded="false" title="${esc(label)}" aria-label="${esc(label)}">${ic('ellipsis')}</button><div class="pop hidden" role="menu">${menuItems(it)}</div></span>`;
}

/** Collapsed section for detail that most visits do not need. */
export function disclosure(summary: string, body: string, key: string, open = false): string {
  if (!body) return '';
  return `<details class="more" data-key="${esc(key)}"${open ? ' open' : ''}><summary>${ic('chevron-right')}${summary}</summary><div class="more-body">${body}</div></details>`;
}

/** Section heading with a tinted icon chip. */
export function blkHd(icon: string, title: string, color = 'var(--accent)', right = '', count?: number): string {
  return `<div class="blk-hd"><span class="ichip" style="--c:${color}">${ic(icon)}</span><h2>${esc(title)}${count !== undefined ? `<span class="count">${count}</span>` : ''}</h2><span class="spacer"></span>${right}</div>`;
}

/** Donut progress ring. segs = [value, cssColor][] drawn in order over a track. */
export function ring(segs: [number, string][], total: number, label: string, size = 44, title = ''): string {
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  let off = 0;
  const arcs = total > 0 ? segs.filter(([v]) => v > 0).map(([v, col]) => {
    const len = (c * v) / total;
    const a = `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${col}" stroke-width="5" stroke-dasharray="${Math.max(0, len - 1.5)} ${c}" stroke-dashoffset="${-off}" transform="rotate(-90 ${size / 2} ${size / 2})"></circle>`;
    off += len;
    return a;
  }).join('') : '';
  return `<svg class="ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="${esc(title || label)}"><circle class="track" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="5"></circle>${arcs}<text x="50%" y="50%" text-anchor="middle" dominant-baseline="central">${esc(label)}</text></svg>`;
}

/** Small line chart of one metric across runs (in run order). Lowest value is highlighted unless higherIsBetter. */
export function lineChart(points: { id: string; label: string; value: number | null }[], o: { higherIsBetter?: boolean; name?: string } = {}): string {
  const pts = points.filter((p) => typeof p.value === 'number') as { id: string; label: string; value: number }[];
  if (pts.length < 2) return '';
  const W = 300, H = 112, L = 6, R = 6, T = 16, B = 22;
  const vs = pts.map((p) => p.value);
  let lo = Math.min(...vs), hi = Math.max(...vs);
  if (hi === lo) { hi += Math.abs(hi) * 0.1 || 1; lo -= Math.abs(lo) * 0.1 || 1; }
  const pad = (hi - lo) * 0.12;
  lo -= pad; hi += pad;
  const x = (i: number) => L + (i * (W - L - R)) / (pts.length - 1);
  const y = (v: number) => T + ((hi - v) * (H - T - B)) / (hi - lo);
  const best = o.higherIsBetter ? Math.max(...vs) : Math.min(...vs);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const area = `${d} L${x(pts.length - 1).toFixed(1)},${H - B} L${x(0).toFixed(1)},${H - B} Z`;
  const short = (s: string) => (s.length > 12 ? s.slice(0, 11) + '…' : s);
  const g = pts.map((p, i) => {
    const isBest = p.value === best;
    const anchor = i === 0 ? 'start' : i === pts.length - 1 ? 'end' : 'middle';
    return `<g data-open="${esc(p.id)}"><title>${esc(p.label)}: ${esc(fmtVal(p.value))}</title>
      <circle class="pt${isBest ? ' best' : ''}" cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="${isBest ? 3.6 : 3}"></circle>
      ${isBest || i === 0 || i === pts.length - 1 ? `<text class="val${isBest ? ' best' : ''}" x="${x(i).toFixed(1)}" y="${(y(p.value) - 7).toFixed(1)}" text-anchor="${anchor}">${esc(fmtVal(p.value))}</text>` : ''}
      ${i === 0 || i === pts.length - 1 || (pts.length <= 4) ? `<text x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${esc(short(p.label))}</text>` : ''}</g>`;
  }).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.name || 'metric')} across ${pts.length} runs"><line class="grid" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"></line><path class="area" d="${area}"></path><path class="line" d="${d}"></path>${g}</svg>`;
}

export function h2(title: string, count?: number, right = ''): string {
  return `<h2>${esc(title)}${count !== undefined && count > 0 ? `<span class="count">${count}</span>` : ''}<span class="spacer"></span>${right}</h2>`;
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
  return `<div class="item" data-open="${esc(b.id)}" tabindex="0" role="link">
    <i class="codicon codicon-${icon} lead" style="color:${color}" aria-hidden="true"></i>
    <div class="grow"><div class="hl"><span class="title">${esc(b.title || '')}</span>${idLink(b.id, b.missing)}</div>${sub ? `<div class="sub">${sub}</div>` : ''}</div>
    <div class="trail">${trail}${b.status && !b.missing ? pill(b.status) : ''}</div>
  </div>`;
}

const ID_RE = /\b(EXP-\d{3,}|RUN-\d{4,}|CP-\d{3,}|Q-\d{3,}|F-\d{3,}|D-\d{3,}|A-\d{4,}|PLAN-\d{3,}|T-\d{3,})\b/g;

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
      (e) => `<div class="ev" style="--c:${eventColor(e.action)}">${inline(esc(e.summary || e.action))}${e.author_type === 'agent' ? ` <span class="author agent">${ic('hubot')}${esc((e.author_name || 'agent').split('/').pop())}</span>` : ''}<span class="t">${time(e.ts)}</span></div>`,
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
