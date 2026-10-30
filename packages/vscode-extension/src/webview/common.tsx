import { ReactNode, useCallback, useEffect, useRef, useState } from 'react';

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

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><b>{title}</b>{children && <p className="muted">{children}</p>}</div>;
}

export function Badge({ kind, children }: { kind?: string; children: ReactNode }) {
  return <span className={'badge ' + (kind ? 'k-' + kind : '')}>{children}</span>;
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
        <h2>{title}</h2>
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
  return <nav aria-label="Breadcrumb" className="crumbs">{trail.map((t, i) => <span key={i} aria-current={i === trail.length - 1 ? 'page' : undefined}>{i > 0 && <span aria-hidden> › </span>}{t}</span>)}</nav>;
}
