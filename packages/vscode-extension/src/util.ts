import * as path from 'path';
export function esc(s: any): string {
  if (s === null || s === undefined) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function truncate(s: string, n: number): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

export function ago(iso?: string | null): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (isNaN(t)) return '';
  const s = (Date.now() - t) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  const d = Math.floor(s / 86400);
  if (d < 14) return `${d}d ago`;
  if (d < 60) return `${Math.floor(d / 7)}w ago`;
  return `${Math.floor(d / 30)}mo ago`;
}

export function fmtVal(v: any): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v);
    const a = Math.abs(v);
    if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(2);
    return String(Number(v.toPrecision(4)));
  }
  if (Array.isArray(v)) return '[' + v.map(fmtVal).join(', ') + ']';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function paramsSummary(p: any, max = 4): string {
  if (!p || typeof p !== 'object') return '';
  const ks = Object.keys(p);
  const s = ks.slice(0, max).map((k) => `${k}=${fmtVal(p[k])}`).join(' ');
  return ks.length > max ? s + ' …' : s;
}

export function dur(sec?: number | null): string {
  if (sec === null || sec === undefined || !isFinite(sec)) return '—';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${String(Math.round(sec % 60)).padStart(2, '0')}s`;
  return `${Math.floor(sec / 3600)}h ${String(Math.floor((sec % 3600) / 60)).padStart(2, '0')}m`;
}

export function nonce(): string {
  let t = '';
  const c = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) t += c.charAt(Math.floor(Math.random() * c.length));
  return t;
}

/** Same rule as research.selfupdate.core_satisfies: same major, core minor >= required minor. */
export function coreCompat(core: string, required: string): 'ok' | 'older' | 'newer' | 'invalid' {
  const p = (v: string) => (String(v).match(/^(\d+)\.(\d+)/) || []).slice(1).map(Number);
  const [cM, cm] = p(core), [rM, rm] = p(required);
  if (cM === undefined || rM === undefined) return 'invalid';
  if (cM !== rM) return cM > rM ? 'newer' : 'older';
  return cm >= rm ? 'ok' : 'older';
}

/** Walk up from `start` looking for `.research/config.yaml`; returns the directory or undefined. */
export function findProjectRoot(start: string, exists: (p: string) => boolean): string | undefined {
  let d = path.resolve(start);
  for (;;) {
    if (exists(path.join(d, '.research', 'config.yaml'))) return d;
    const up = path.dirname(d);
    if (up === d) return undefined;
    d = up;
  }
}

export function sh(s: string): string {
  if (/^[\w@%+=:,./-]+$/.test(s)) return s;
  return `'${s.replace(/'/g, `'\\''`)}'`;
}
