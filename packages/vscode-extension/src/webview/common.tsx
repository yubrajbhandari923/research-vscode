import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';

/** Ops the core lists in its ready event (undefined = unknown, show everything). A feature whose op is missing is hidden, not broken. */
export const OpsContext = createContext<string[] | undefined>(undefined);
export const useHasOp = () => { const ops = useContext(OpsContext); return (op: string) => !ops || ops.includes(op); };

export interface Async<T> { data?: T; error?: string; loading: boolean; reload: () => void }
export function useAsync<T>(fn: () => Promise<T>, deps: any[]): Async<T> {
  const [s, set] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    set((p) => ({ ...p, loading: true }));
    fn().then((data) => live && set({ data, loading: false }), (e) => live && set({ error: String(e?.message || e), loading: false }));
    return () => { live = false; };
  }, [...deps, n]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...s, reload: useCallback(() => setN((x) => x + 1), []) };
}

/** Loading / error / empty states in one place so every page behaves the same way. */
export function State<T>({ a, children, empty }: { a: Async<T>; children: (d: T) => ReactNode; empty?: (d: T) => ReactNode | null }) {
  if (a.loading && !a.data) return <div className="state" role="status" aria-live="polite"><span className="spinner" aria-hidden /> Loading…</div>;
  if (a.error && !a.data) return <div className="state error" role="alert"><b>Could not load.</b> {a.error} <button className="link" onClick={a.reload}>Try again</button></div>;
  const e = a.data !== undefined && empty ? empty(a.data) : null;
  return <>{e || (a.data !== undefined ? children(a.data) : null)}</>;
}

export type Kind = 'ok' | 'warn' | 'bad' | 'info' | 'pend' | '';

/** One mapping from a record's status word to its semantic colour, so a status looks the same on every page. */
export function kindOf(status: string | null | undefined): Kind {
  const s = String(status || '').toLowerCase().replace(/ /g, '_');
  if (['completed', 'done', 'passed', 'approved', 'supported', 'clean', 'promoted', 'merged', 'reviewed', 'active_ok'].includes(s)) return 'ok';
  if (['failed', 'blocked', 'contradicted', 'stale', 'missing', 'error', 'cancelled'].includes(s)) return 'bad';
  if (['needs_review', 'uncommitted', 'warn', 'unreviewed', 'changes_requested', 'unverified_dirty'].includes(s)) return 'warn';
  if (['running', 'queued', 'in_progress', 'investigating', 'ready'].includes(s)) return 'info';
  if (['verified', 'prepared', 'pending', 'preliminary'].includes(s)) return 'pend';
  return '';
}

/** Dot + label. `live` pulses for work in flight. */
export function Badge({ kind, children, live }: { kind?: string; children: ReactNode; live?: boolean }) {
  return <span className={'badge ' + (kind ? 'k-' + kind : '')}>{kind === 'info' ? <i className={'dot k-info' + (live ? ' live' : '')} aria-hidden /> : null}{children}</span>;
}
export const Status = ({ value, label }: { value: string; label?: string }) => <Badge kind={kindOf(value)} live={value === 'running'}>{label ?? value.replace(/_/g, ' ')}</Badge>;

export const IdLink = ({ id, host }: { id: string; host: { openEntity(id: string): void } }) => <button type="button" className="link idlink" onClick={() => host.openEntity(id)}>{id}</button>;

const ICONS: Record<string, string> = {
  chevron: 'm9 18 6-6-6-6', check: 'M20 6 9 17l-5-5', x: 'M18 6 6 18M6 6l12 12', plus: 'M5 12h14M12 5v14',
  alert: 'm21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3zM12 9v4M12 17h.01',
  play: 'M6 3l14 9-14 9z', branch: 'M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9',
  flask: 'M10 2v7.5a2 2 0 0 1-.2.9L4.7 20.5a1 1 0 0 0 .9 1.5h12.8a1 1 0 0 0 .9-1.5l-5.1-10.1a2 2 0 0 1-.2-.9V2M8.5 2h7M7 16h10',
  tasks: 'M3 5l2 2 4-4M3 13l2 2 4-4M13 6h8M13 14h8M3 21h18', search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3',
  file: 'M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7zM14 2v4a2 2 0 0 0 2 2h4', info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01',
  refresh: 'M3 12a9 9 0 0 1 9-9 9.8 9.8 0 0 1 6.7 2.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-9 9 9.8 9.8 0 0 1-6.7-2.7L3 16M8 16H3v5',
  bulb: 'M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z',
  merge: 'M18 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 9v12M18 15V9a6 6 0 0 0-6-6h-1',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z', cog: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
};
export function Icon({ name }: { name: keyof typeof ICONS | string }) {
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={ICONS[name] || ICONS.info} /></svg>;
}

export function ago(ts?: string | null, now = Date.now()): string {
  if (!ts) return '';
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, (now - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return ts.slice(0, 10);
}

export function PageHeader({ title, sub, actions, crumbs }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumbs?: string[] }) {
  return <header className="page-header"><div className="titles">{crumbs && <Breadcrumbs trail={crumbs} />}<h1>{title}</h1>{sub && <p className="sub">{sub}</p>}</div>{actions && <div className="actions">{actions}</div>}</header>;
}

export function Section({ title, count, hint, actions, boxed, lead, children, id }: { title: ReactNode; count?: number | string; hint?: ReactNode; actions?: ReactNode; boxed?: boolean; lead?: ReactNode; children: ReactNode; id?: string }) {
  const hid = id || undefined;
  return (
    <section className="section" aria-labelledby={hid}>
      <header><h2 id={hid}>{title}{count !== undefined && <span className="count">{count}</span>}</h2>{hint && <span className="hint">{hint}</span>}<span className="spacer" />{actions}</header>
      {lead && <p className="lead">{lead}</p>}
      <div className={'body' + (boxed ? ' boxed' : '')}>{children}</div>
    </section>
  );
}

export function Stat({ value, label, sub, warn }: { value: ReactNode; label: string; sub?: ReactNode; warn?: boolean }) {
  return <div className={'kpi' + (warn ? ' warn' : '')}><div className="v">{value}{sub && <> <small>{sub}</small></>}</div><div className="l">{label}</div></div>;
}

export function Progress({ value, max, label, ok }: { value: number; max?: number | null; label?: string; ok?: boolean }) {
  const pct = max ? Math.min(100, Math.round((value / max) * 100)) : value > 0 ? 100 : 0;
  return <div className="prog" role="img" aria-label={label || `${value}${max ? ' of ' + max : ''}`}><div className={'bar' + (ok ? ' ok' : '')} style={{ width: 64 }}><i style={{ width: pct + '%' }} /></div><span className="t">{value}{max ? `/${max}` : ''}</span></div>;
}

/** Useful empty state: what this is, why it is empty, and the next action. */
export function EmptyState({ icon, title, children, actions, center, inline }: { icon?: string; title: string; children?: ReactNode; actions?: ReactNode; center?: boolean; inline?: boolean }) {
  return <div className={'empty' + (center ? ' center' : '') + (inline ? ' inline' : '')}>{icon && <div className="ico"><Icon name={icon} /></div>}<b>{title}</b>{children && <p>{children}</p>}{actions && <div className="actions">{actions}</div>}</div>;
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { id: T; label: string; n?: number }[]; onChange: (v: T) => void; label: string }) {
  return <div className="segmented" role="group" aria-label={label}>{options.map((o) => <button key={o.id} type="button" aria-pressed={value === o.id} onClick={() => onChange(o.id)}>{o.label}{o.n !== undefined && <span className="n">{o.n}</span>}</button>)}</div>;
}

export function Banner({ kind, children, onDismiss, role }: { kind: 'ok' | 'bad' | 'warn' | ''; children: ReactNode; onDismiss?: () => void; role?: 'status' | 'alert' }) {
  return <div className={'banner ' + kind} role={role || (kind === 'bad' ? 'alert' : 'status')}><Icon name={kind === 'ok' ? 'check' : kind === '' ? 'info' : 'alert'} /><div className="grow">{children}</div>{onDismiss && <button type="button" className="link" onClick={onDismiss}>Dismiss</button>}</div>;
}

/** Modal with focus trap, Escape to close, and focus return — used for diffs and impact previews. */
export function Dialog({ title, onClose, children, actions }: { title: string; onClose: () => void; children: ReactNode; actions: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current!;
    (el.querySelector('[data-autofocus]') as HTMLElement || el).focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
      if (e.key === 'Tab') {
        const f = Array.from(el.querySelectorAll<HTMLElement>('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')).filter((x) => !x.hasAttribute('disabled'));
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    el.addEventListener('keydown', key);
    return () => { el.removeEventListener('keydown', key); prev?.focus(); };
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title} ref={ref} tabIndex={-1}>
        <header><h2>{title}</h2><button type="button" className="ghost icon-btn" aria-label="Close dialog" onClick={onClose}><Icon name="x" /></button></header>
        <div className="dialog-body">{children}</div>
        <div className="dialog-actions">{actions}</div>
      </div>
    </div>
  );
}

export function Diff({ text }: { text: string }) {
  if (!text) return <p className="muted">No file change.</p>;
  return <pre className="diff" aria-label="Proposed change to the configuration file">{text.split('\n').map((l, i) =>
    <div key={i} className={l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : ''}>{l || ' '}</div>)}</pre>;
}

export function Breadcrumbs({ trail }: { trail: string[] }) {
  return <nav aria-label="Breadcrumb" className="crumbs">{trail.map((t, i) => <span key={i} aria-current={i === trail.length - 1 ? 'page' : undefined}>{i > 0 && <span className="sep" aria-hidden> / </span>}{t}</span>)}</nav>;
}

// ---- PIN confirmation: a protected action is tried first; if the core asks for the PIN we prompt once and retry. The PIN lives only in this dialog.
let askPin: ((why: string) => Promise<string | null>) | null = null;

export function PinHost() {
  const [req, setReq] = useState<{ why: string; done: (p: string | null) => void } | null>(null);
  const [pin, setPin] = useState('');
  useEffect(() => { askPin = (why) => new Promise((done) => setReq({ why, done })); return () => { askPin = null; }; }, []);
  if (!req) return null;
  const close = (v: string | null) => { req.done(v); setReq(null); setPin(''); };
  return (
    <Dialog title="Confirm with your PIN" onClose={() => close(null)} actions={<>
      <button type="button" onClick={() => close(null)}>Cancel</button>
      <button type="button" className="primary" disabled={!pin} onClick={() => close(pin)}>Confirm</button></>}>
      <form onSubmit={(e) => { e.preventDefault(); if (pin) close(pin); }}>
        <p>{req.why}</p>
        <label htmlFor="pin-in">Approval PIN</label>
        <input id="pin-in" type="password" autoComplete="off" data-autofocus value={pin} onChange={(e) => setPin(e.target.value)} />
        <p className="muted small">Set or change it in Settings. It is checked on this machine and never saved in the project.</p>
      </form>
    </Dialog>);
}

/** Call a protected method; when the core answers with a PIN error, ask for the PIN once and retry. */
export async function callPin<T = any>(host: { call(m: string, p?: Record<string, any>): Promise<any> }, method: string, params: Record<string, any>, why: string): Promise<T> {
  try { return await host.call(method, params); }
  catch (e: any) {
    if (!/PIN/.test(String(e?.message)) || !askPin) throw e;
    const pin = await askPin(why);
    if (!pin) throw e;
    return host.call(method, { ...params, pin });
  }
}
