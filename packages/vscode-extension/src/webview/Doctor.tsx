import { useState } from 'react';
import { Badge, Breadcrumbs, State, useAsync } from './common';
import type { Host } from './host';

const ICON: Record<string, string> = { ok: '✓', info: '·', warn: '!', error: '✗' };

/** Project health. Read-only; the few generated-file fixes run only when you press them. */
export function Doctor({ host, embedded }: { host: Host; embedded?: boolean }) {
  const a = useAsync<any>(() => host.call('doctor'), []);
  const [all, setAll] = useState(false);
  const [msg, setMsg] = useState('');
  const fix = async (id: string) => {
    try { const r = await host.call('doctor', { fix: id }); setMsg(`Fixed: wrote ${r.wrote.join(', ')}`); a.reload(); } catch (e: any) { setMsg(e.message); }
  };
  return (
    <div className={embedded ? '' : 'page-body'}>
      {!embedded && <Breadcrumbs trail={['Project', 'Health']} />}
      <State a={a}>{(d) => {
        const s = d.summary;
        const shown = d.checks.filter((c: any) => all || c.level !== 'ok');
        return (
          <>
            <p role="status" className={s.errors ? 'help-warn' : ''}><b>{s.errors ? `${s.errors} problem${s.errors > 1 ? 's' : ''}` : s.warnings ? 'Needs attention' : 'Healthy'}</b>
              <span className="muted"> · {s.errors} errors, {s.warnings} warnings, {s.info} optional, {s.ok} passing</span></p>
            {msg && <div className="banner ok" role="status">{msg}</div>}
            <label className="check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Show passing checks</label>
            <ul className="doctor">
              {shown.map((c: any) => (
                <li key={c.id} className={c.level}>
                  <span className="mark" aria-hidden>{ICON[c.level]}</span>
                  <div className="grow"><b>{c.title}</b> <Badge kind={c.level === 'error' || c.level === 'warn' ? 'warn' : c.level === 'ok' ? 'ok' : ''}>{c.level === 'info' ? 'optional' : c.level}</Badge> <span className="muted small">{c.area}</span>
                    {c.detail && <div className="muted small">{c.detail}</div>}
                    {c.fix && <div className="small">{c.fix.text}{c.fix.safe && <> <button type="button" onClick={() => fix(c.id)}>Fix now</button></>}</div>}</div>
                </li>))}
            </ul>
            <button type="button" onClick={a.reload}>Check again</button>
          </>);
      }}</State>
    </div>
  );
}

/** A quiet banner for Home: appears only when the doctor has something to say. */
export function HealthBanner({ host, open }: { host: Host; open: () => void }) {
  const a = useAsync<any>(() => host.call('doctor'), []);
  const s = a.data?.summary;
  if (!s || (!s.errors && !s.warnings)) return null;
  return <div className={'banner ' + (s.errors ? 'bad' : 'warn')} role="status">Project health: {s.errors ? `${s.errors} problem(s)` : ''}{s.errors && s.warnings ? ', ' : ''}{s.warnings ? `${s.warnings} warning(s)` : ''}. <button className="link" onClick={open}>Review</button></div>;
}
