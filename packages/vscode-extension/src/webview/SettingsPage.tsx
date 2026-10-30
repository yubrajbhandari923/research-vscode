import { useMemo, useState } from 'react';
import { SettingRow } from './Controls';
import { Breadcrumbs, Dialog, Diff, State, useAsync } from './common';
import { Doctor } from './Doctor';
import type { Host } from './host';
import type { Preview, SettingOption, SettingsPayload } from './types';

export function SettingsPage({ host, initialCategory }: { host: Host; initialCategory?: string }) {
  const saved = host.getState();
  const [cat, setCat] = useState<string>(initialCategory || saved.cat || 'policy');
  const [query, setQuery] = useState<string>(saved.query || '');
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [review, setReview] = useState<{ preview: Preview; scope: string } | null>(null);
  const [advanced, setAdvanced] = useState<boolean>(!!saved.advanced);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const a = useAsync<SettingsPayload>(() => host.call('settings'), []);

  const remember = (c: string, q: string) => host.setState({ ...host.getState(), cat: c, query: q });
  const pendingKeys = Object.keys(draft);

  const options = (d: SettingsPayload) => {
    const q = query.trim().toLowerCase();
    return d.options.filter((o) => q ? [o.key, o.label, o.help, o.why].join(' ').toLowerCase().includes(q) : o.category === cat && (advanced || !o.advanced));
  };

  const change = async (o: SettingOption, v: any) => {
    setDraft((d) => ({ ...d, [o.key]: v }));
    try {
      const p: Preview = await host.call('settings_preview', { changes: { [o.key]: v }, scope: 'project' });
      setErrors((e) => { const n = { ...e }; if (p.errors[o.key]) n[o.key] = p.errors[o.key]; else delete n[o.key]; return n; });
    } catch (e: any) { setErrors((x) => ({ ...x, [o.key]: String(e.message) })); }
  };
  const discard = (k: string) => { setDraft((d) => { const n = { ...d }; delete n[k]; return n; }); setErrors((e) => { const n = { ...e }; delete n[k]; return n; }); };

  const openReview = async (scope: string, changes = draft, remove = false) => {
    setBusy(true);
    try { setReview({ preview: await host.call('settings_preview', { changes, scope, remove }), scope }); }
    catch (e: any) { setNotice({ ok: false, text: String(e.message) }); }
    finally { setBusy(false); }
  };

  const resetOne = (o: SettingOption) => openReview('project', { [o.key]: null }, true).then(() => setRemoving(true));
  const [removing, setRemoving] = useState(false);

  const apply = async (reason: string, confirm: boolean) => {
    if (!review) return;
    setBusy(true);
    try {
      const changes = removing ? Object.fromEntries(Object.keys(review.preview.values).map((k) => [k, null])) : draft;
      const r = await host.call('settings_apply', { changes, scope: review.scope, reason: reason || undefined, confirm, remove: removing });
      setNotice({ ok: true, text: r.changed ? `Saved to ${review.scope === 'machine' ? 'the machine' : 'this project'} configuration. Agent policy file regenerated.` : 'Nothing changed.' });
      setDraft({}); setReview(null); setRemoving(false); a.reload();
    } catch (e: any) { setNotice({ ok: false, text: String(e.message) }); setReview(null); setRemoving(false); }
    finally { setBusy(false); }
  };

  return (
    <div className="settings">
      <Breadcrumbs trail={['Project', 'Settings', query ? 'Search' : (a.data?.categories.find((c) => c.id === cat)?.label || '')]} />
      <State a={a}>{(d) => (
        <>
          <div className="settings-bar">
            <label className="sr" htmlFor="setting-search">Search settings</label>
            <input id="setting-search" type="search" placeholder="Search settings (e.g. budget, review, sandbox)" value={query}
              onChange={(e) => { setQuery(e.target.value); remember(cat, e.target.value); }} />
            <span className="muted small">Settings fingerprint <code>{d.fingerprint}</code></span>
          </div>
          <div className="settings-grid">
            <nav aria-label="Settings categories" className="cats">
              <button type="button" aria-current={!query && cat === 'health' ? 'true' : undefined} className={!query && cat === 'health' ? 'on' : ''}
                onClick={() => { setQuery(''); setCat('health'); remember('health', ''); }}>Project health</button>
              {d.categories.map((c) => {
                const n = d.options.filter((o) => o.category === c.id && (advanced || !o.advanced)).length;
                return n ? <button key={c.id} type="button" aria-current={!query && cat === c.id ? 'true' : undefined} className={!query && cat === c.id ? 'on' : ''}
                  onClick={() => { setQuery(''); setCat(c.id); remember(c.id, ''); }}>{c.label}<span className="muted">{n}</span></button> : null;
              })}
              <label className="check cats-note"><input type="checkbox" checked={advanced} onChange={(e) => { setAdvanced(e.target.checked); host.setState({ ...host.getState(), advanced: e.target.checked }); }} /> Show advanced settings</label>
              <p className="muted small cats-note">Other keys remain editable in <code>.research/config.yaml</code>.</p>
            </nav>
            <section aria-label="Settings" className="list">
              {notice && <div className={'banner ' + (notice.ok ? 'ok' : 'bad')} role={notice.ok ? 'status' : 'alert'}>{notice.text} <button className="link" onClick={() => setNotice(null)}>Dismiss</button></div>}
              {!query && cat === 'health' && <Doctor host={host} embedded />}
              {!(cat === 'health' && !query) && options(d).length === 0 && <p className="muted">No settings match “{query}”.</p>}
              {options(d).map((o) => {
                const dep = o.depends_on ? d.options.find((x) => x.key === o.depends_on) : null;
                const depOff = dep && dep.type === 'bool' && !(draft[dep.key] ?? dep.value);
                return <SettingRow key={o.key} option={o} draft={draft[o.key]} error={errors[o.key]} onChange={(v) => change(o, v)}
                  onReset={() => resetOne(o)} onDoc={(p) => host.openDoc(p)} disabledReason={depOff ? `Applies only while “${dep!.label}” is on.` : undefined} />;
              })}
            </section>
          </div>
          {pendingKeys.length > 0 && (
            <div className="savebar" role="region" aria-label="Unsaved changes">
              <span>{pendingKeys.length} unsaved change{pendingKeys.length > 1 ? 's' : ''}</span>
              <span className="spacer" />
              <button type="button" onClick={() => { setDraft({}); setErrors({}); }}>Discard</button>
              <button type="button" className="primary" disabled={busy || Object.keys(errors).length > 0} onClick={() => { setRemoving(false); openReview('project'); }}>Review changes…</button>
            </div>
          )}
          {review && <ReviewDialog preview={review.preview} initialScope={review.scope} removing={removing} busy={busy}
            onScope={(s) => openReview(s, removing ? Object.fromEntries(Object.keys(review.preview.values).map((k) => [k, null])) : draft, removing)}
            onClose={() => { setReview(null); setRemoving(false); }} onApply={apply} />}
        </>
      )}</State>
    </div>
  );
}

function ReviewDialog({ preview: p, initialScope, removing, busy, onScope, onClose, onApply }: {
  preview: Preview; initialScope: string; removing: boolean; busy: boolean; onScope: (s: string) => void; onClose: () => void; onApply: (reason: string, confirm: boolean) => void;
}) {
  const [reason, setReason] = useState('');
  const [sure, setSure] = useState(false);
  const bad = Object.entries(p.errors);
  const needsSure = p.needs_confirmation;
  const blocked = bad.length > 0 || (needsSure && !sure) || busy;
  const keys = useMemo(() => Object.keys(p.values), [p.values]);
  return (
    <Dialog title={removing ? 'Remove override' : 'Review changes'} onClose={onClose} actions={<>
      <button type="button" onClick={onClose}>Cancel</button>
      <button type="button" className="primary" data-autofocus disabled={blocked} onClick={() => onApply(reason, sure)}>{removing ? 'Remove override' : 'Save changes'}</button></>}>
      <p>{keys.length} setting{keys.length === 1 ? '' : 's'}: {keys.map((k, i) => <span key={k}>{i > 0 && ', '}<code>{k}</code></span>)}</p>
      <div className="row"><label htmlFor="scope">Save to</label>
        <select id="scope" value={initialScope} onChange={(e) => onScope(e.target.value)}>
          <option value="project">This project (.research/config.yaml)</option><option value="machine">This machine (all projects)</option></select></div>
      {bad.map(([k, m]) => <div key={k} className="field-error" role="alert"><code>{k}</code> {m}</div>)}
      <Diff text={p.diff} />
      {p.affects.length > 0 && <p className="muted small">Also updates: {p.affects.join(', ')}</p>}
      {p.policy_keys.length > 0 && <p className="help-warn">Policy settings affect what agents are allowed to do. Future dispatches pick this up; sessions already running keep the policy recorded when they started.</p>}
      {needsSure && <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> I understand this change can discard or expose work.</label>}
      <label htmlFor="why">Reason <span className="muted">(optional, kept in the audit log)</span></label>
      <input id="why" type="text" value={reason} onChange={(e) => setReason(e.target.value)} />
    </Dialog>
  );
}
